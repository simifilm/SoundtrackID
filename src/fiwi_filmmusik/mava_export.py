"""Export detection results in a MAVA/VIAN-Export compatible format.

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
from pathlib import Path
from typing import Any

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
    """Build the mapping JSON MAVA expects alongside the TSV (its `mapping_json` form field)."""
    description = f"FIWI Filmmusik cues — {video_name}" if video_name else "FIWI Filmmusik cues"
    return {
        "series_description": description,
        "value_description": "Identified music cue (title — artist)",
        "value_type": "string",
        "time_column": MAVA_TIME_COLUMN,
        "value_column": MAVA_VALUE_COLUMN,
        "duration_column": MAVA_DURATION_COLUMN,
    }


def write_mava_export(cues: list[dict[str, Any]], output_dir: Path, video_name: str | None = None) -> tuple[Path, Path]:
    """Write mava_annotations.tsv + mava_mapping.json into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = output_dir / "mava_annotations.tsv"
    mapping_path = output_dir / "mava_mapping.json"
    tsv_path.write_text(build_mava_tsv(cues), encoding="utf-8")
    mapping_path.write_text(json.dumps(build_mava_mapping(video_name), indent=2), encoding="utf-8")
    return tsv_path, mapping_path
