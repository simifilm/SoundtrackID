"""The exports of a run's results: a CSV, and the TSV and mapping MAVA imports.

MAVA (https://github.com/sdsc-ordes/mava-api) has no flat JSON export schema of
its own — its API builds an in-memory RDF graph from either raw Turtle or a TSV
file paired with a JSON "mapping" (`POST /graph/import_tsv`). Each row becomes a
`mava:AnnotationSegment` (startTime/endTime/stringValue) belonging to a
`mava:AnnotationSeries`. This module produces that TSV + mapping pair for a run's
music cues so they can be imported straight into a MAVA graph, and from there
into VIAN.
"""

import csv
import io
import json
from typing import Any


def results_to_csv(results: dict[str, Any]) -> str:
    """The cues of a results.json as CSV: one row per cue, a column per field.

    Built from the results as they are now (also after edits and a resume), so
    it holds the same data as the JSON. Nested fields get a column each (e.g.
    enrichment.composer); lists, such as the candidates, are written as JSON.
    """
    rows = [_flatten(cue) for cue in results.get("cues") or []]
    columns = list(dict.fromkeys(key for row in rows for key in row))
    # A field nested in one cue and empty in another (e.g. enrichment) gets only
    # the nested columns.
    columns = [c for c in columns if not any(o.startswith(c + ".") for o in columns)]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _flatten(fields: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{prefix}{key}."))
        elif isinstance(value, list):
            flat[prefix + key] = json.dumps(value, ensure_ascii=False)
        else:
            flat[prefix + key] = value
    return flat


# ── MAVA ──────────────────────────────────────────────────────────────────────

MAVA_TIME_COLUMN = "start_in_seconds"
MAVA_DURATION_COLUMN = "duration_in_seconds"
MAVA_VALUE_COLUMN = "annotation"


def _cue_label(cue: dict[str, Any]) -> str:
    title = cue.get("title")
    artist = cue.get("artist")
    if title and artist:
        return f"{title} — {artist}"
    return title or "Unidentified music"


def build_mava_tsv(cues: list[dict[str, Any]]) -> str:
    """Build the TSV body expected by MAVA's `POST /graph/import_tsv`."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
    writer.writerow([MAVA_TIME_COLUMN, MAVA_DURATION_COLUMN, MAVA_VALUE_COLUMN])
    for cue in cues:
        writer.writerow([cue.get("start", 0), cue.get("duration", 0), _cue_label(cue)])
    return buf.getvalue()


def build_mava_mapping(video_name: str | None = None) -> dict[str, Any]:
    """Build the mapping JSON MAVA expects alongside the TSV.

    MAVA takes it as its `mapping_json` form field.
    """
    description = (
        f"SoundtrackID cues — {video_name}" if video_name else "SoundtrackID cues"
    )
    return {
        "series_description": description,
        "value_description": "Identified music cue (title — artist)",
        "value_type": "string",
        "time_column": MAVA_TIME_COLUMN,
        "value_column": MAVA_VALUE_COLUMN,
        "duration_column": MAVA_DURATION_COLUMN,
    }
