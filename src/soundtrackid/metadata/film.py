"""Film metadata lookup: TMDb → Wikidata → imdbapi.dev fallback chain."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from soundtrackid.metadata import USER_AGENT, escape_sparql_string
from soundtrackid.models import parse_year

log = logging.getLogger(__name__)

SEARCH_LIMIT = 10  # title matches offered to choose from
TMDB_POSTER_URL = "https://image.tmdb.org/t/p/w92"  # + poster_path


@dataclass
class FilmInfo:
    title: str | None = None
    year: int | None = None
    director: str | None = None
    director_death_year: int | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    music_composer: str | None = None  # credited "Original Music Composer" (TMDb)
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
        for fname in (
            "title",
            "year",
            "director",
            "director_death_year",
            "imdb_id",
            "tmdb_id",
            "music_composer",
        ):
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
            return {
                "Authorization": f"Bearer {self._tmdb_bearer}",
                "accept": "application/json",
            }
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
        info = FilmInfo()

        if not self._has_tmdb_creds():
            log.info("No TMDb key (FIWI_TMDB_API_TOKEN, FIWI_TMDB_KEY): TMDb skipped")
        elif tmdb_id or imdb_id or title:
            try:
                tmdb = self._lookup_tmdb(
                    tmdb_id=tmdb_id, imdb_id=imdb_id, title=title, year=year
                )
                if tmdb:
                    info.merge(tmdb, "tmdb")
            except (httpx.HTTPError, ValueError):
                log.warning("TMDb lookup failed", exc_info=True)

        return self._complete(
            info, imdb_id=imdb_id, tmdb_id=tmdb_id, title=title, year=year
        )

    def _complete(
        self,
        info: FilmInfo,
        imdb_id: str | None,
        tmdb_id: int | None,
        title: str | None,
        year: int | None,
    ) -> FilmInfo:
        """Fill in what `info` lacks from Wikidata, then imdbapi.dev, and the
        given IDs last: a source's IDs are the film's own, a given one can be
        wrong."""
        if not info.is_complete():
            try:
                wd = self._lookup_wikidata(
                    imdb_id=info.imdb_id or imdb_id,
                    title=info.title or title,
                    year=info.year or year,
                )
                if wd:
                    info.merge(wd, "wikidata")
            except (httpx.HTTPError, ValueError):
                log.warning("Wikidata film lookup failed", exc_info=True)

        if not info.is_complete() and (known_imdb_id := info.imdb_id or imdb_id):
            try:
                im = self._lookup_imdbapi(known_imdb_id)
                if im:
                    info.merge(im, "imdbapi")
            except (httpx.HTTPError, ValueError):
                log.warning("imdbapi.dev lookup failed", exc_info=True)

        info.imdb_id = info.imdb_id or imdb_id
        info.tmdb_id = info.tmdb_id or tmdb_id
        return info

    def search(
        self,
        imdb_id: str | None = None,
        tmdb_id: int | None = None,
        title: str | None = None,
        year: int | None = None,
    ) -> list[dict[str, Any]]:
        """The films that may be the one meant, for the user to choose from:
        TMDb's films of the given IDs first, then up to SEARCH_LIMIT matches of
        the title. When TMDb has none (no key, no match, unreachable), the one
        film Wikidata and imdbapi.dev find, already looked up: its record is the
        candidate's "film"."""
        candidates: list[dict[str, Any]] = []
        if self._has_tmdb_creds():
            try:
                candidates = self._tmdb_candidates(tmdb_id, imdb_id, title, year)
            except (httpx.HTTPError, ValueError):
                log.warning("TMDb search failed", exc_info=True)
        if candidates:
            return candidates

        info = self._complete(
            FilmInfo(), imdb_id=imdb_id, tmdb_id=tmdb_id, title=title, year=year
        )
        if not info.sources:
            return []
        return [
            {
                "tmdb_id": info.tmdb_id,
                "title": info.title,
                "original_title": None,
                "year": info.year,
                "overview": None,
                "poster_url": None,
                "match": None,
                "film": info.to_dict(),
            }
        ]

    # ── TMDb ─────────────────────────────────────────────────────────────

    def _lookup_tmdb(
        self,
        tmdb_id: int | None,
        imdb_id: str | None,
        title: str | None,
        year: int | None,
    ) -> FilmInfo | None:
        # The TMDb ID's film, else the IMDb ID's, else the title's first match:
        # a given ID can be wrong, so the next is tried when one finds no film.
        movie = self._tmdb_get(f"/3/movie/{tmdb_id}") if tmdb_id else None
        if not movie and imdb_id and (found := self._tmdb_find_by_imdb(imdb_id)):
            movie = self._tmdb_get(f"/3/movie/{found.get('id')}")
        if not movie and title and (results := self._tmdb_search(title, year)):
            movie = self._tmdb_get(f"/3/movie/{results[0].get('id')}")
        if not movie:
            return None
        tmdb_id = movie.get("id")
        credits = self._tmdb_get(f"/3/movie/{tmdb_id}/credits") or {}
        info = _parse_tmdb_movie(movie, credits)
        info.tmdb_id = tmdb_id

        if info.director and not info.director_death_year:
            director_person_id = _extract_tmdb_director_id(credits)
            if director_person_id:
                person = self._tmdb_get(f"/3/person/{director_person_id}")
                if person:
                    info.director_death_year = parse_year(person.get("deathday"))

        return info

    def _tmdb_get(self, path: str, params: dict | None = None) -> dict | None:
        full = {**self._tmdb_auth_params(), **(params or {})}
        r = self._client.get(
            f"https://api.themoviedb.org{path}",
            headers=self._tmdb_headers,
            params=full,
        )
        if r.status_code != 200:
            # The path only: the parameters can hold the key.
            log.warning("TMDb answered HTTP %s for %s", r.status_code, path)
            return None
        return r.json()

    def _tmdb_find_by_imdb(self, imdb_id: str) -> dict | None:
        """The IMDb ID's film, as a search result."""
        data = self._tmdb_get(
            f"/3/find/{imdb_id}", params={"external_source": "imdb_id"}
        )
        if not data:
            return None
        results = data.get("movie_results") or []
        if results:
            return results[0]
        return None

    def _tmdb_search(self, title: str, year: int | None) -> list[dict]:
        """The films matching the title, best first."""
        params: dict[str, Any] = {"query": title}
        if year:
            params["year"] = year
        data = self._tmdb_get("/3/search/movie", params=params)
        if not data:
            return []
        return data.get("results") or []

    def _tmdb_candidates(
        self,
        tmdb_id: int | None,
        imdb_id: str | None,
        title: str | None,
        year: int | None,
    ) -> list[dict[str, Any]]:
        found: list[tuple[dict, str | None]] = []
        if tmdb_id and (movie := self._tmdb_get(f"/3/movie/{tmdb_id}")):
            found.append((movie, "tmdb_id"))
        if imdb_id and (movie := self._tmdb_find_by_imdb(imdb_id)):
            found.append((movie, "imdb_id"))
        if title:
            results = self._tmdb_search(title, year)
            if not results and year:
                # The year may be the wrong one.
                results = self._tmdb_search(title, None)
            found += [(movie, None) for movie in results[:SEARCH_LIMIT]]
        # A film found twice is listed once, where it was found first.
        candidates: dict[Any, dict[str, Any]] = {}
        for movie, match in found:
            candidates.setdefault(movie.get("id"), _parse_tmdb_candidate(movie, match))
        return list(candidates.values())

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
            headers={
                "accept": "application/sparql-results+json",
                "user-agent": USER_AGENT,
            },
        )
        if r.status_code != 200:
            return None
        return _parse_wikidata_film(r.json())

    # ── imdbapi.dev (tertiary fallback) ──────────────────────────────────

    def _lookup_imdbapi(self, imdb_id: str) -> FilmInfo | None:
        try:
            r = self._client.get(
                f"https://api.imdbapi.dev/titles/{imdb_id}", timeout=8.0
            )
        except httpx.HTTPError:
            log.warning("imdbapi.dev lookup failed", exc_info=True)
            return None
        if r.status_code != 200:
            return None
        return _parse_imdbapi_title(r.json())


def lookup_film(
    imdb_id: str | None = None,
    tmdb_id: int | None = None,
    title: str | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    """Look a film up online from what is known about it, with the TMDb keys
    from the environment: the film chosen in the app's "Look up online" and the
    CLI's --lookup-film.
    """
    client = httpx.Client(timeout=15.0)
    try:
        info = _lookup_from_env(client).lookup(
            imdb_id=imdb_id, tmdb_id=tmdb_id, title=title, year=year
        )
        return info.to_dict()
    finally:
        client.close()


def search_films(
    imdb_id: str | None = None,
    tmdb_id: int | None = None,
    title: str | None = None,
    year: int | None = None,
) -> list[dict[str, Any]]:
    """The films the app's "Look up online" offers to choose from
    (FilmLookup.search), with the TMDb keys from the environment."""
    client = httpx.Client(timeout=15.0)
    try:
        return _lookup_from_env(client).search(
            imdb_id=imdb_id, tmdb_id=tmdb_id, title=title, year=year
        )
    finally:
        client.close()


def _lookup_from_env(client: httpx.Client) -> FilmLookup:
    return FilmLookup(
        client,
        tmdb_bearer_token=os.environ.get("FIWI_TMDB_API_TOKEN"),
        tmdb_api_key=os.environ.get("FIWI_TMDB_KEY"),
    )


# ── Pure parsers (testable without network) ──────────────────────────────


def _parse_tmdb_candidate(movie: dict, match: str | None) -> dict[str, Any]:
    """A film to choose from, from a TMDb movie or search result; `match` names
    the given ID that points to it ("tmdb_id", "imdb_id"), None for a title
    match."""
    poster = movie.get("poster_path")
    return {
        "tmdb_id": movie.get("id"),
        "title": movie.get("title") or movie.get("original_title"),
        "original_title": movie.get("original_title"),
        "year": parse_year(movie.get("release_date")),
        "overview": movie.get("overview") or None,
        "poster_url": f"{TMDB_POSTER_URL}{poster}" if poster else None,
        "match": match,
    }


def _parse_tmdb_movie(movie: dict, credits: dict) -> FilmInfo:
    info = FilmInfo()
    info.title = movie.get("title") or movie.get("original_title")
    info.year = parse_year(movie.get("release_date"))
    info.imdb_id = movie.get("imdb_id")
    crew = (credits or {}).get("crew") or []
    director = next((c for c in crew if c.get("job") == "Director"), None)
    if director:
        info.director = director.get("name")
    composers = [
        c.get("name")
        for c in crew
        if c.get("job") == "Original Music Composer" and c.get("name")
    ]
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
    info.year = parse_year(_wd_value(b, "pubdate"))
    info.director = _wd_value(b, "directorLabel")
    info.director_death_year = parse_year(_wd_value(b, "death"))
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
        info.year = parse_year(year)
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
                info.director_death_year = parse_year(death)
    return info


def _sparql_film_by_imdb(imdb_id: str) -> str:
    return f"""
SELECT ?filmLabel ?pubdate ?director ?directorLabel ?death ?imdb WHERE {{
  ?film wdt:P345 "{escape_sparql_string(imdb_id)}".
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
    safe_title = escape_sparql_string(title)
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
