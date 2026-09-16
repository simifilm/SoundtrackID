"""Composer lookup: MusicBrainz (primary, ISRC chain) → Wikidata fallback."""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

MB_BASE = "https://musicbrainz.org/ws/2"
MB_USER_AGENT = "SoundtrackID/0.1 (stmh@zhaw.ch)"
MB_RATE_LIMIT_SEC = 1.0
WD_BASE = "https://query.wikidata.org/sparql"


@dataclass
class ComposerInfo:
    composer: str | None = None
    composer_death_year: int | None = None
    work_title: str | None = None
    original_year: int | None = None   # first publication/composition year of the work (Wikidata)
    composer_match: Literal["isrc", "work", "search"] | None = None
    # All copyright-relevant authors of the work (Composer/Lyricist/Writer) with
    # their death years — the basis for public-domain assessment. Performers are
    # deliberately excluded (their 50-year related right is shorter than the
    # 70-year post-mortem term on the authors).
    authors: list[dict[str, Any]] = field(default_factory=list)
    latest_author_death_year: int | None = None  # PD clock runs from the last surviving author
    authors_complete: bool = False               # every author has a known death year
    public_domain: bool | None = None            # None = cannot determine (unknown/living author)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "composer": self.composer,
            "composer_death_year": self.composer_death_year,
            "work_title": self.work_title,
            "original_year": self.original_year,
            "composer_match": self.composer_match,
            "authors": self.authors,
            "latest_author_death_year": self.latest_author_death_year,
            "authors_complete": self.authors_complete,
            "public_domain": self.public_domain,
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
            candidate = self._lookup_via_isrc(isrc)
            if candidate and candidate.composer:
                info = candidate

        # ISRC missed (common for reissues absent from MusicBrainz, e.g. Sunset
        # Boulevard). Fall back to finding the *work* by title and matching its
        # authors against the identified artist — this reaches the original piece.
        if info is None and title:
            info = self._lookup_via_work_title(title, artist)

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

        info.latest_author_death_year, info.authors_complete, info.public_domain = _pd_status(
            info.authors, _current_year()
        )
        return info

    # ── MusicBrainz chain ────────────────────────────────────────────────

    def _lookup_via_isrc(self, isrc: str) -> ComposerInfo | None:
        """Resolve every copyright-relevant author (Composer/Lyricist/Writer) of
        the work behind an ISRC, with each one's death year — the basis for PD."""
        recording = self._mb_recording_by_isrc(isrc)
        if not recording:
            return None
        work = self._mb_work_for_recording(recording.get("id"))
        if not work:
            return None
        authors, used_wikidata = self._authors_from_work(work)
        if not authors:
            return None
        return self._build_from_authors(work, authors, used_wikidata, match="isrc")

    def _lookup_via_work_title(self, title: str, artist: str | None) -> ComposerInfo | None:
        """Find the work by title in MusicBrainz and pick the candidate whose
        authors best match the identified artist. Recovers the original piece
        when the ISRC of a reissue isn't in MusicBrainz."""
        if not artist:  # without an artist we can't disambiguate same-titled works
            return None
        candidates = self._mb_work_search(title)
        best: dict | None = None
        best_authors: list[dict[str, Any]] | None = None
        best_wd = False
        best_score = 0
        for cand in candidates[:4]:  # bound the per-cue MB calls
            work = self._mb_work(cand.get("id"))
            if not work:
                continue
            authors, used_wd = self._authors_from_work(work)
            if not authors:
                continue
            score = _score_work_authors(authors, artist)
            if score > best_score:
                best, best_authors, best_wd, best_score = work, authors, used_wd, score
        if not best or best_score <= 0:  # require at least one author-name match
            return None
        return self._build_from_authors(best, best_authors, best_wd, match="work")

    def _authors_from_work(self, work: dict) -> tuple[list[dict[str, Any]], bool]:
        """Collapse a work's author relations to one entry per person (merging
        roles) and resolve each one's death year (MB life-span → Wikidata MBID).
        Returns (authors, used_wikidata)."""
        merged: dict[str, dict[str, Any]] = {}
        for role, artist_id, name in _extract_authors(work):
            key = artist_id or name.lower()
            entry = merged.setdefault(
                key, {"name": name, "artist_id": artist_id, "roles": []}
            )
            if role not in entry["roles"]:
                entry["roles"].append(role)

        used_wikidata = False
        authors: list[dict[str, Any]] = []
        for entry in merged.values():
            death = None
            aid = entry["artist_id"]
            if aid:
                artist = self._mb_artist(aid)
                if artist:
                    death = _parse_year(_artist_death(artist))
                if death is None:  # MB lacked a death date — try Wikidata via MBID
                    wd = self._wikidata_death_year_by_mbid(aid)
                    if wd:
                        death, used_wikidata = wd, True
            authors.append({
                "name": entry["name"],
                "roles": entry["roles"],
                "death_year": death,
            })
        return authors, used_wikidata

    def _build_from_authors(
        self, work: dict, authors: list[dict[str, Any]], used_wikidata: bool, match: str
    ) -> ComposerInfo:
        # Primary composer (first author credited as composer) for display/back-compat.
        primary = next((a for a in authors if "composer" in a["roles"]), authors[0])
        sources = ["musicbrainz"] + (["wikidata"] if used_wikidata else [])
        return ComposerInfo(
            composer=primary["name"],
            composer_death_year=primary["death_year"],
            work_title=work.get("title"),
            composer_match=match,  # type: ignore[arg-type]
            authors=authors,
            sources=sources,
        )

    def _mb_work_search(self, title: str) -> list[dict]:
        query = 'work:"%s"' % title.replace('"', "")
        data = self._mb_get("/work", params={"query": query, "limit": 8})
        return (data or {}).get("works") or []

    def _mb_work(self, work_id: str | None) -> dict | None:
        if not work_id:
            return None
        cache_key = f"w:{work_id}"
        cached = self._cache_work.get(cache_key)
        if cached is not None:
            return cached or None
        data = self._mb_get(f"/work/{work_id}", params={"inc": "artist-rels"})
        self._cache_work[cache_key] = data or {}
        return data

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
        full = {"fmt": "json", **(params or {})}
        url = f"{MB_BASE}{path}"
        headers = {"user-agent": MB_USER_AGENT, "accept": "application/json"}
        # The /work search endpoint is much slower than direct lookups and, under
        # load, MusicBrainz throttles with 503s — so use a generous timeout and
        # retry once on timeout/503 rather than silently yielding no result.
        for attempt in range(2):
            # Rate-limit politely (MB asks for ~1 req/s sustained).
            elapsed = time.monotonic() - self._last_mb_call
            if elapsed < MB_RATE_LIMIT_SEC:
                time.sleep(MB_RATE_LIMIT_SEC - elapsed)
            self._last_mb_call = time.monotonic()
            try:
                r = self._client.get(url, params=full, headers=headers, timeout=20.0)
            except httpx.HTTPError:
                continue  # timeout / network hiccup — retry once
            if r.status_code == 503:
                time.sleep(1.5)  # throttled — back off, then retry
                continue
            if r.status_code != 200:
                return None
            try:
                return r.json()
            except ValueError:
                return None
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


# Copyright-relevant authorship roles on a MusicBrainz *work*. "writer" covers a
# person who wrote both music and lyrics. Performer/arranger etc. are excluded:
# performers hold a shorter (50-year) related right, not the 70-year author term.
_AUTHOR_ROLES = ("composer", "lyricist", "writer")


def _extract_authors(work: dict) -> list[tuple[str, str | None, str]]:
    """Return (role, artist_mbid, name) for each author relation of a work."""
    authors: list[tuple[str, str | None, str]] = []
    for rel in work.get("relations") or []:
        role = rel.get("type")
        if role not in _AUTHOR_ROLES:
            continue
        artist = rel.get("artist") or {}
        name = artist.get("name")
        if name:
            authors.append((role, artist.get("id"), name))
    return authors


def _normalize_name(s: str | None) -> str:
    """Lowercase, strip accents and punctuation — for matching person names
    across sources (e.g. 'Ernö Rapée' ↔ 'Erno Rapee')."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def _score_work_authors(authors: list[dict[str, Any]], artist_str: str | None) -> int:
    """How many of a work's authors appear in the identified artist string — used
    to pick the right same-titled MusicBrainz work. Matches on the surname (last
    name token) or any distinctive name token (len ≥ 5), which tolerates provider
    misspellings and compound surnames (e.g. 'Matos Rodríguez' ↔ 'Matos Rodriquez')."""
    tokens = set(_normalize_name(artist_str).split())
    if not tokens:
        return 0
    score = 0
    for a in authors:
        parts = _normalize_name(a.get("name")).split()
        if not parts:
            continue
        if parts[-1] in tokens or any(p in tokens for p in parts if len(p) >= 5):
            score += 1
    return score


def _current_year() -> int:
    from datetime import date

    return date.today().year


def _pd_status(
    authors: list[dict[str, Any]], current_year: int, term: int = 70
) -> tuple[int | None, bool, bool | None]:
    """Public-domain assessment from a work's authors (PMLE, `term` years post
    mortem auctoris — 70 in the EU/CH/US). Returns
    (latest_author_death_year, authors_complete, public_domain).

    public_domain is True only when every author's death year is known and the
    last of them died more than `term` years ago; False when known-but-too-recent;
    None when any author's death year is unknown (living or missing data), i.e.
    the status cannot be asserted."""
    if not authors:
        return None, False, None
    deaths = [a.get("death_year") for a in authors]
    known = [d for d in deaths if d]
    complete = len(known) == len(authors)
    latest = max(known) if known else None
    if not complete or latest is None:
        return latest, complete, None
    return latest, complete, (current_year - latest) > term


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
