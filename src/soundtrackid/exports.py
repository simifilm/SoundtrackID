"""The exports of a run's results: a CSV, a VIAN project and a MAVA .mediapkg.

VIAN (https://github.com/Movie-Analytics/VIAN) and MAVA
(https://github.com/sdsc-ordes/mava-exchange) each get the run's music cues as
one track, a segment per cue labelled "title — artist".
"""

import csv
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
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


# ── VIAN and MAVA ─────────────────────────────────────────────────────────────


def _cue_label(cue: dict[str, Any]) -> str:
    title = cue.get("title")
    artist = cue.get("artist")
    if title and artist:
        return f"{title} — {artist}"
    return title or "Unidentified music"


def _cue_spans(cues: list[dict[str, Any]]) -> list[tuple[float, float, str]]:
    """Each cue's start and end (s) and label, in time order."""
    return sorted(
        (cue["start"], cue["start"] + cue["duration"], _cue_label(cue)) for cue in cues
    )


def _zip(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def build_vian_project(cues: list[dict[str, Any]], video: dict[str, Any]) -> bytes:
    """The cues as a VIAN project: the zip VIAN's "Import project" takes, with
    the video, which the user picks there.

    A VIAN project is its two stores, main.json and undoable.json, at the root
    of the zip. A track's segments are in frames, and VIAN takes the frame rate
    from main.json, not from the video. `video` is thumbnails.video_info().
    """
    fps = video["fps"]
    main = {
        "fps": fps,
        "numFrames": video["frames"],
        "videoDuration": video["duration"],
        "width": video["width"],
        "height": video["height"],
    }
    undoable = {
        "subtitles": None,  # VIAN's import links a subtitles file unless null
        "subtitlesVisible": True,
        "timelines": [
            {
                "data": [
                    {
                        "annotation": label,
                        "end": round(end * fps),
                        "id": str(uuid.uuid4()),
                        "start": round(start * fps),
                        "vocabAnnotation": [],
                    }
                    for start, end, label in _cue_spans(cues)
                ],
                "hidden": False,
                "id": str(uuid.uuid4()),
                "locked": False,
                "name": "Music cues (SoundtrackID)",
                "type": "shots",
                "vocabulary": None,
            }
        ],
        "vocabularies": [],
    }
    return _zip({"main.json": json.dumps(main), "undoable.json": json.dumps(undoable)})


MAVA = "http://example.org/mava/ontology#"
MEDIAPKG_TRACK = "music_cues"


def build_mediapkg(
    cues: list[dict[str, Any]],
    video_id: str,
    src: str,
    video: dict[str, Any] | None,
) -> bytes:
    """The cues as a .mediapkg (format 0.2), MAVA's exchange format, which VIAN
    imports too: an AnnotationSeries track.

    A zip of manifest.json and a Parquet file per track, in a folder named after
    the video; see spec/SPEC.md of mava-exchange. `src` is the video's file
    name, `video` thumbnails.video_info() or None without the video.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
            ("start_seconds", pa.float64()),
            ("end_seconds", pa.float64()),
            ("annotations", pa.string()),
        ]
    )
    rows = [
        {"start_seconds": start, "end_seconds": end, "annotations": label}
        for start, end, label in _cue_spans(cues)
    ]
    parquet = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), parquet)

    path = f"{video_id}/{MEDIAPKG_TRACK}.parquet"
    entry: dict[str, Any] = {"id": video_id, "src": src}
    if video:
        entry |= {
            "width": video["width"],
            "height": video["height"],
            "fps": video["fps"],
            "duration_seconds": video["duration"],
        }
    manifest = {
        "version": "0.2",
        "created": datetime.now(timezone.utc).isoformat(),
        "description": f"Music cues identified by SoundtrackID in {src}",
        "ontology": MAVA,
        "context": {
            "@context": {
                "xsd": "http://www.w3.org/2001/XMLSchema#",
                "mava": MAVA,
                "start_seconds": {"@id": "mava:atTime", "@type": "xsd:decimal"},
                "end_seconds": {"@id": "mava:endTime", "@type": "xsd:decimal"},
                "annotations": {"@id": "mava:stringValue", "@type": "xsd:string"},
            }
        },
        "tracks": {
            MEDIAPKG_TRACK: {
                "type": "mava:AnnotationSeries",
                "description": "Music cues identified by SoundtrackID (title — artist)",
                "columns": schema.names,
            }
        },
        "videos": [{**entry, "files": {MEDIAPKG_TRACK: path}}],
    }
    return _zip(
        {"manifest.json": json.dumps(manifest, indent=2), path: parquet.getvalue()}
    )
