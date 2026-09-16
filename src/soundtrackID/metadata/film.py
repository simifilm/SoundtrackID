"""Film metadata lookup: TMDb → Wikidata → imdbapi.dev fallback chain."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class FilmInfo:
    title: str | None = None
    year: int | None = None
    director: str | None = None
    director_death_year: int | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    music_composer: str | None = None   # credited "Original Music Composer" (TMDb)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "year": self.year,
            "director": self.director,
            "director_death_year": self.director_death_year,
            "imdb_id": self.imdb_id,
            "tmdb_id": self.tmdb_id,
            "music_composer": self.music_composer,
            "sources": self.sources,
        }

    def is_complete(self) -> bool:
        return all([self.title, self.year, self.director, self.director_death_year])

    def merge(self, other: FilmInfo, source_tag: str) -> None:
        """Fill in missing fields from `other`. First non-null wins."""
        changed = False
        for fname in ("title", "year", "director", "director_death_year", "imdb_id", "tmdb_id", "music_composer"):
            if getattr(self, fname) is None and getattr(other, fname) is not None:
                setattr(self, fname, getattr(other, fname))
                changed = True
        if changed and source_tag not in self.sources:
            self.sources.append(source_tag)


class FilmLookup:
    def __init__(
        self,
        client: httpx.Client,
        tmdb_bearer_token: str | None = None,
        tmdb_api_key: str | None = None,
    ) -> None:
        self._client = client
        self._tmdb_bearer = tmdb_bearer_token
        self._tmdb_key = tmdb_api_key

    @property
    def _tmdb_headers(self) -> dict[str, str]:
        if self._tmdb_bearer:
            return {"Authorization": f"Bearer {self._tmdb_bearer}", "accept": "application/json"}
        return {"accept": "application/json"}

    def _tmdb_auth_params(self) -> dict[str, str]:
        if self._tmdb_bearer:
            return {}
        if self._tmdb_key:
            return {"api_key": self._tmdb_key}
        return {}

    def _has_tmdb_creds(self) -> bool:
        return bool(self._tmdb_bearer or self._tmdb_key)

    def lookup(
        self,
        imdb_id: str | None = None,
        tmdb_id: int | None = None,
        title: str | None = None,
        year: int | None = None,
    ) -> FilmInfo:
        info = FilmInfo(imdb_id=imdb_id, tmdb_id=tmdb_id)

        if self._has_tmdb_creds() and (tmdb_id or imdb_id or title):
            try:
                tmdb = self._lookup_tmdb(tmdb_id=tmdb_id, imdb_id=imdb_id, title=title, year=year)
                if tmdb:
                    info.merge(tmdb, "tmdb")
            except (httpx.HTTPError, ValueError):
                pass

        if not info.is_complete():
            try:
                wd = self._lookup_wikidata(imdb_id=info.imdb_id or imdb_id, title=info.title or title, year=info.year or year)
                if wd:
                    info.merge(wd, "wikidata")
            except (httpx.HTTPError, ValueError):
                pass

        if not info.is_complete() and (info.imdb_id or imdb_id):
            try:
                im = self._lookup_imdbapi(info.imdb_id or imdb_id)
                if im:
                    info.merge(im, "imdbapi")
            except (httpx.HTTPError, ValueError):
                pass

        return info

    # ── TMDb ─────────────────────────────────────────────────────────────

    def _lookup_tmdb(
        self,
        tmdb_id: int | None,
        imdb_id: str | None,
        title: str | None,
        year: int | None,
    ) -> FilmInfo | None:
        if not tmdb_id and imdb_id:
            tmdb_id = self._tmdb_find_by_imdb(imdb_id)
        if not tmdb_id and title:
            tmdb_id = self._tmdb_search_movie(title, year)
        if not tmdb_id:
            return None

        movie = self._tmdb_get(f"/3/movie/{tmdb_id}")
        if not movie:
            return None
        credits = self._tmdb_get(f"/3/movie/{tmdb_id}/credits") or {}
        info = _parse_tmdb_movie(movie, credits)
        info.tmdb_id = tmdb_id

        if info.director and not info.director_death_year:
            director_person_id = _extract_tmdb_director_id(credits)
            if director_person_id:
                person = self._tmdb_get(f"/3/person/{director_person_id}")
                if person:
                    info.director_death_year = _parse_year(person.get("deathday"))

        return info

    def _tmdb_get(self, path: str, params: dict | None = None) -> dict | None:
        full = {**self._tmdb_auth_params(), **(params or {})}
        r = self._client.get(
            f"https://api.themoviedb.org{path}",
            headers=self._tmdb_headers,
            params=full,
        )
        if r.status_code != 200:
            return None
        return r.json()

    def _tmdb_find_by_imdb(self, imdb_id: str) -> int | None:
        data = self._tmdb_get(f"/3/find/{imdb_id}", params={"external_source": "imdb_id"})
        if not data:
            return None
        results = data.get("movie_results") or []
        if results:
            return results[0].get("id")
        return None

    def _tmdb_search_movie(self, title: str, year: int | None) -> int | None:
        params: dict[str, Any] = {"query": title}
        if year:
            params["year"] = year
        data = self._tmdb_get("/3/search/movie", params=params)
        if not data:
            return None
        results = data.get("results") or []
        return results[0].get("id") if results else None

    # ── Wikidata ──────────────────────────────────────────────────────────

    def _lookup_wikidata(
        self,
        imdb_id: str | None,
        title: str | None,
        year: int | None,
    ) -> FilmInfo | None:
        if imdb_id:
            sparql = _sparql_film_by_imdb(imdb_id)
        elif title:
            sparql = _sparql_film_by_title(title, year)
        else:
            return None

        r = self._client.get(
            "https://query.wikidata.org/sparql",
            params={"query": sparql, "format": "json"},
            headers={"accept": "application/sparql-results+json", "user-agent": "SoundtrackID/0.1 (stmh@zhaw.ch)"},
        )
        if r.status_code != 200:
            return None
        return _parse_wikidata_film(r.json())

    # ── imdbapi.dev (tertiary fallback) ──────────────────────────────────

    def _lookup_imdbapi(self, imdb_id: str) -> FilmInfo | None:
        try:
            r = self._client.get(f"https://api.imdbapi.dev/titles/{imdb_id}", timeout=8.0)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        return _parse_imdbapi_title(r.json())


# ── Pure parsers (testable without network) ──────────────────────────────


def _parse_tmdb_movie(movie: dict, credits: dict) -> FilmInfo:
    info = FilmInfo()
    info.title = movie.get("title") or movie.get("original_title")
    info.year = _parse_year(movie.get("release_date"))
    info.imdb_id = movie.get("imdb_id")
    crew = (credits or {}).get("crew") or []
    director = next((c for c in crew if c.get("job") == "Director"), None)
    if director:
        info.director = director.get("name")
    composers = [c.get("name") for c in crew if c.get("job") == "Original Music Composer" and c.get("name")]
    if composers:
        info.music_composer = ", ".join(dict.fromkeys(composers))
    return info


def _extract_tmdb_director_id(credits: dict) -> int | None:
    crew = (credits or {}).get("crew") or []
    director = next((c for c in crew if c.get("job") == "Director"), None)
    if director:
        return director.get("id")
    return None


def _parse_wikidata_film(data: dict) -> FilmInfo | None:
    bindings = (data.get("results") or {}).get("bindings") or []
    if not bindings:
        return None
    b = bindings[0]
    info = FilmInfo()
    info.title = _wd_value(b, "filmLabel")
    info.year = _parse_year(_wd_value(b, "pubdate"))
    info.director = _wd_value(b, "directorLabel")
    info.director_death_year = _parse_year(_wd_value(b, "death"))
    imdb = _wd_value(b, "imdb")
    if imdb and imdb.startswith("tt"):
        info.imdb_id = imdb
    return info


def _wd_value(binding: dict, key: str) -> str | None:
    cell = binding.get(key)
    if not cell:
        return None
    return cell.get("value")


def _parse_imdbapi_title(data: dict) -> FilmInfo | None:
    if not data:
        return None
    info = FilmInfo()
    info.title = data.get("primaryTitle") or data.get("originalTitle")
    year = data.get("startYear")
    if isinstance(year, int):
        info.year = year
    elif isinstance(year, str):
        info.year = _parse_year(year)
    imdb = data.get("id")
    if isinstance(imdb, str) and imdb.startswith("tt"):
        info.imdb_id = imdb
    directors = data.get("directors") or []
    if directors:
        first = directors[0]
        if isinstance(first, dict):
            info.director = first.get("displayName") or first.get("primaryName")
            death = first.get("deathYear")
            if isinstance(death, int):
                info.director_death_year = death
            elif isinstance(death, str):
                info.director_death_year = _parse_year(death)
    return info


def _parse_year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    m = re.search(r"\d{4}", value)
    return int(m.group()) if m else None


def _sparql_film_by_imdb(imdb_id: str) -> str:
    return f"""
SELECT ?filmLabel ?pubdate ?director ?directorLabel ?death ?imdb WHERE {{
  ?film wdt:P345 "{imdb_id}".
  OPTIONAL {{ ?film wdt:P577 ?pubdate. }}
  OPTIONAL {{
    ?film wdt:P57 ?director.
    OPTIONAL {{ ?director wdt:P570 ?death. }}
  }}
  OPTIONAL {{ ?film wdt:P345 ?imdb. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT 1
""".strip()


def _sparql_film_by_title(title: str, year: int | None) -> str:
    safe_title = title.replace('"', '\\"')
    year_clause = ""
    if year:
        year_clause = f"FILTER(YEAR(?pubdate) = {year})"
    return f"""
SELECT ?filmLabel ?pubdate ?director ?directorLabel ?death ?imdb WHERE {{
  ?film wdt:P31/wdt:P279* wd:Q11424;
        rdfs:label "{safe_title}"@en.
  OPTIONAL {{ ?film wdt:P577 ?pubdate. }}
  {year_clause}
  OPTIONAL {{
    ?film wdt:P57 ?director.
    OPTIONAL {{ ?director wdt:P570 ?death. }}
  }}
  OPTIONAL {{ ?film wdt:P345 ?imdb. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT 1
""".strip()
