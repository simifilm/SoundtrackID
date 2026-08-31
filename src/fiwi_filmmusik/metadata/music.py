"""Composer lookup: MusicBrainz (primary, ISRC chain) → Wikidata fallback."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

MB_BASE = "https://musicbrainz.org/ws/2"
MB_USER_AGENT = "FIWI-Filmmusik/0.1 (stmh@zhaw.ch)"
MB_RATE_LIMIT_SEC = 1.0
WD_BASE = "https://query.wikidata.org/sparql"


@dataclass
class ComposerInfo:
    composer: str | None = None
    composer_death_year: int | None = None
    work_title: str | None = None
    original_year: int | None = None   # first publication/composition year of the work (Wikidata)
    composer_match: Literal["isrc", "search"] | None = None
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "composer": self.composer,
            "composer_death_year": self.composer_death_year,
            "work_title": self.work_title,
            "original_year": self.original_year,
            "composer_match": self.composer_match,
            "sources": self.sources,
        }


class MusicLookup:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._last_mb_call = 0.0
        # Per-instance caches; lifetime = one enrich request
        self._cache_recording: dict[str, dict] = {}
        self._cache_work: dict[str, dict] = {}
        self._cache_artist: dict[str, dict] = {}
        self._cache_wikidata_death: dict[str, int | None] = {}
        self._cache_work_year: dict[tuple, int | None] = {}

    def lookup_composer(
        self,
        isrc: str | None = None,
        title: str | None = None,
        artist: str | None = None,
    ) -> ComposerInfo | None:
        info: ComposerInfo | None = None

        if isrc:
            candidate, composer_mbid = self._lookup_via_isrc(isrc)
            if candidate and candidate.composer:
                if not candidate.composer_death_year and composer_mbid:
                    death = self._wikidata_death_year_by_mbid(composer_mbid)
                    if death:
                        candidate.composer_death_year = death
                        if "wikidata" not in candidate.sources:
                            candidate.sources.append("wikidata")
                info = candidate

        if info is None and title and artist:
            info = self._lookup_via_wikidata_search(title, artist)

        # Original work date (Wikidata publication/inception) — independent of the
        # composer resolution, and the primary signal for anachronism detection.
        original_year = None
        if title:
            names = [n for n in [(info.composer if info else None), artist] if n]
            if names:
                original_year = self._wikidata_work_year(title, names)

        if info is None:
            if original_year is None:
                return None
            info = ComposerInfo()

        info.original_year = original_year
        if original_year and "wikidata" not in info.sources:
            info.sources.append("wikidata")
        return info

    # ── MusicBrainz chain ────────────────────────────────────────────────

    def _lookup_via_isrc(self, isrc: str) -> tuple[ComposerInfo | None, str | None]:
        """Return (info, composer_mbid). composer_mbid is None when MB had no composer."""
        recording = self._mb_recording_by_isrc(isrc)
        if not recording:
            return None, None
        work = self._mb_work_for_recording(recording.get("id"))
        if not work:
            return None, None
        artist_id, composer_name = _extract_composer(work)
        if not composer_name:
            return None, None
        info = ComposerInfo(
            composer=composer_name,
            work_title=work.get("title"),
            composer_match="isrc",
            sources=["musicbrainz"],
        )
        if artist_id:
            artist = self._mb_artist(artist_id)
            if artist:
                info.composer_death_year = _parse_year(_artist_death(artist))
        return info, artist_id

    def _mb_recording_by_isrc(self, isrc: str) -> dict | None:
        cached = self._cache_recording.get(isrc)
        if cached is not None:
            return cached or None
        # The /isrc/{isrc} endpoint already nests recordings — no inc param needed
        data = self._mb_get(f"/isrc/{isrc}")
        recordings = (data or {}).get("recordings") or []
        first = recordings[0] if recordings else {}
        self._cache_recording[isrc] = first
        return first or None

    def _mb_work_for_recording(self, recording_id: str | None) -> dict | None:
        if not recording_id:
            return None
        cached = self._cache_work.get(recording_id)
        if cached is not None:
            return cached or None
        data = self._mb_get(f"/recording/{recording_id}", params={"inc": "work-rels"})
        relations = (data or {}).get("relations") or []
        work = next((r.get("work") for r in relations if r.get("type") == "performance" and r.get("work")), None)
        if not work:
            self._cache_work[recording_id] = {}
            return None
        full_work = self._mb_get(f"/work/{work['id']}", params={"inc": "artist-rels"})
        result = full_work or work
        self._cache_work[recording_id] = result
        return result

    def _mb_artist(self, artist_id: str) -> dict | None:
        cached = self._cache_artist.get(artist_id)
        if cached is not None:
            return cached or None
        data = self._mb_get(f"/artist/{artist_id}")
        self._cache_artist[artist_id] = data or {}
        return data

    def _mb_get(self, path: str, params: dict | None = None) -> dict | None:
        # Rate-limit politely
        elapsed = time.monotonic() - self._last_mb_call
        if elapsed < MB_RATE_LIMIT_SEC:
            time.sleep(MB_RATE_LIMIT_SEC - elapsed)
        self._last_mb_call = time.monotonic()
        full = {"fmt": "json", **(params or {})}
        try:
            r = self._client.get(
                f"{MB_BASE}{path}",
                params=full,
                headers={"user-agent": MB_USER_AGENT, "accept": "application/json"},
            )
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    # ── Wikidata fallbacks ───────────────────────────────────────────────

    def _wikidata_death_year_by_mbid(self, mb_artist_id: str) -> int | None:
        """Look up death year via Wikidata's P434 (MusicBrainz artist ID) — avoids name collisions."""
        cache_key = f"mbid:{mb_artist_id}"
        if cache_key in self._cache_wikidata_death:
            return self._cache_wikidata_death[cache_key]
        sparql = _sparql_person_death_by_mbid(mb_artist_id)
        data = self._wd_query(sparql)
        year = _parse_first_death_year(data) if data else None
        self._cache_wikidata_death[cache_key] = year
        return year

    def _lookup_via_wikidata_search(self, title: str, artist: str) -> ComposerInfo | None:
        sparql = _sparql_work_by_title_artist(title, artist)
        data = self._wd_query(sparql)
        return _parse_wikidata_composer(data) if data else None

    def _wikidata_work_year(self, title: str, names: list[str]) -> int | None:
        """Earliest publication/inception year of a musical work with this title,
        constrained to a composer/performer we already know (avoids matching the
        wrong same-titled work). Returns None when Wikidata has no such work."""
        names = [n for n in dict.fromkeys(n.strip() for n in names if n and n.strip())][:4]
        if not title or not names:
            return None
        key = (title.lower(), tuple(sorted(n.lower() for n in names)))
        if key in self._cache_work_year:
            return self._cache_work_year[key]
        data = self._wd_query(_sparql_work_year_by_title_people(title, names))
        year = None
        if data:
            for b in (data.get("results") or {}).get("bindings") or []:
                y = _parse_year((b.get("date") or {}).get("value"))
                if y:
                    year = y  # ORDER BY ASC → first bound date is the earliest
                    break
        self._cache_work_year[key] = year
        return year

    def _wd_query(self, sparql: str) -> dict | None:
        time.sleep(0.05)
        try:
            r = self._client.get(
                WD_BASE,
                params={"query": sparql, "format": "json"},
                headers={
                    "accept": "application/sparql-results+json",
                    "user-agent": MB_USER_AGENT,
                },
                timeout=15.0,
            )
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None


# ── Pure parsers (testable without network) ──────────────────────────────


def _extract_composer(work: dict) -> tuple[str | None, str | None]:
    """Return (composer_artist_mbid, composer_name) from a work's relations."""
    relations = work.get("relations") or []
    for rel in relations:
        if rel.get("type") != "composer":
            continue
        artist = rel.get("artist") or {}
        return artist.get("id"), artist.get("name")
    return None, None


def _artist_death(artist: dict) -> str | None:
    life = artist.get("life-span") or {}
    return life.get("end")


def _parse_year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    m = re.search(r"\d{4}", value)
    return int(m.group()) if m else None


def _parse_first_death_year(data: dict) -> int | None:
    bindings = (data.get("results") or {}).get("bindings") or []
    for b in bindings:
        cell = b.get("death") or b.get("deathYear")
        if cell and (val := cell.get("value")):
            year = _parse_year(val)
            if year:
                return year
    return None


def _parse_wikidata_composer(data: dict) -> ComposerInfo | None:
    bindings = (data.get("results") or {}).get("bindings") or []
    if not bindings:
        return None
    b = bindings[0]
    composer = (b.get("composerLabel") or {}).get("value")
    if not composer:
        return None
    work = (b.get("workLabel") or {}).get("value")
    death = (b.get("death") or {}).get("value")
    return ComposerInfo(
        composer=composer,
        composer_death_year=_parse_year(death),
        work_title=work,
        composer_match="search",
        sources=["wikidata"],
    )


def _escape_sparql_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _sparql_person_death_by_mbid(mb_artist_id: str) -> str:
    """Find death year for the person identified by MusicBrainz artist ID — avoids name collisions."""
    safe = _escape_sparql_string(mb_artist_id)
    return f"""
SELECT ?death WHERE {{
  ?person wdt:P434 "{safe}";
          wdt:P570 ?death.
}}
LIMIT 1
""".strip()


def _sparql_work_year_by_title_people(title: str, names: list[str]) -> str:
    """Earliest date of a musical work with `title`, whose composer (P86) or
    performer (P175) is one of `names`. Constraining by the people keeps the match
    fast and unambiguous."""
    safe_title = _escape_sparql_string(title)
    values = " ".join(f'"{_escape_sparql_string(n)}"@en' for n in names)
    return f"""
SELECT ?date WHERE {{
  VALUES ?pname {{ {values} }}
  ?person rdfs:label ?pname.
  ?work wdt:P31/wdt:P279* wd:Q2188189;
        rdfs:label "{safe_title}"@en.
  {{ ?work wdt:P86 ?person. }} UNION {{ ?work wdt:P175 ?person. }}
  OPTIONAL {{ ?work wdt:P577 ?pub. }}
  OPTIONAL {{ ?work wdt:P571 ?inc. }}
  BIND(COALESCE(?pub, ?inc) AS ?date)
  FILTER(BOUND(?date))
}}
ORDER BY ASC(?date)
LIMIT 3
""".strip()


def _sparql_work_by_title_artist(title: str, artist: str) -> str:
    safe_title = _escape_sparql_string(title)
    safe_artist = _escape_sparql_string(artist)
    return f"""
SELECT ?workLabel ?composerLabel ?death WHERE {{
  ?work wdt:P31/wdt:P279* wd:Q207628;
        rdfs:label "{safe_title}"@en.
  OPTIONAL {{ ?work wdt:P86 ?composer.
             OPTIONAL {{ ?composer wdt:P570 ?death. }} }}
  OPTIONAL {{ ?work wdt:P175 ?performer.
             ?performer rdfs:label "{safe_artist}"@en. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT 1
""".strip()
