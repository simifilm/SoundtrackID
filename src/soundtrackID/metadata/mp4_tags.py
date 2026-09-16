"""Read MP4 container metadata (iTunes/Subler tags) for film identification.

Returns a partial film hint that the enrichment step can use to pre-fill the
manual ID entry form. Best-effort: any failure returns an empty dict so the
caller can still proceed with manual entry.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path
from typing import Any


def read_mp4_tags(path: Path) -> dict[str, Any]:
    try:
        from mutagen.mp4 import MP4
    except ImportError:
        return {}

    try:
        mp4 = MP4(str(path))
    except Exception:
        return {}

    tags = mp4.tags or {}
    out: dict[str, Any] = {}

    if title := _first_str(tags.get("\xa9nam")):
        out["title"] = title

    if day := _first_str(tags.get("\xa9day")):
        if year := _extract_year(day):
            out["year"] = year

    itunmovi = tags.get("----:com.apple.iTunes:iTunMOVI")
    if itunmovi:
        director = _parse_itunmovi_director(itunmovi[0])
        if director:
            out["director"] = director

    if imdb := _read_freeform(tags, "IMDB ID") or _read_freeform(tags, "imdb id"):
        if imdb.startswith("tt"):
            out["imdb_id"] = imdb

    if tmdb := _read_freeform(tags, "TMDB ID") or _read_freeform(tags, "tmdb id"):
        try:
            out["tmdb_id"] = int(tmdb)
        except ValueError:
            pass

    return out


def _first_str(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8").strip() or None
        except UnicodeDecodeError:
            return None
    return str(value).strip() or None


def _extract_year(day: str) -> int | None:
    m = re.search(r"\d{4}", day)
    return int(m.group()) if m else None


def _parse_itunmovi_director(raw: Any) -> str | None:
    if isinstance(raw, (bytes, bytearray)):
        try:
            data = plistlib.loads(bytes(raw))
        except Exception:
            return None
    elif isinstance(raw, dict):
        data = raw
    else:
        return None

    directors = data.get("directors")
    if not directors:
        return None
    first = directors[0]
    if isinstance(first, dict):
        return _first_str(first.get("name"))
    return _first_str(first)


def _read_freeform(tags: dict, key: str) -> str | None:
    full_key = f"----:com.apple.iTunes:{key}"
    raw = tags.get(full_key)
    return _first_str(raw)
