"""Data models for the film music analysis pipeline."""

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from soundtrackID.metadata import rights


@dataclass
class AudioChunk:
    data: NDArray[np.float32]
    start_time: float
    end_time: float
    sample_rate: int


@dataclass
class ClassificationResult:
    chunk: AudioChunk
    is_music: bool
    confidence: float
    probs: NDArray[np.float32] | None = None


@dataclass
class MusicSegment:
    audio: NDArray[np.float32]
    start_time: float
    end_time: float
    sample_rate: int


@dataclass
class DetectionResult:
    segment: MusicSegment
    title: str | None
    artist: str | None
    provider: str = "shazam"
    metadata: dict[str, Any] = field(default_factory=dict)
    skipped: bool = False  # not identified: the provider didn't answer (limit, error)


# ── A cue of results.json ─────────────────────────────────────────────────────
# A stretch of music in the film and what identified it: a dict with these
# fields, which the app, its exports and the CLI all read.
Cue = dict[str, Any]

# Where the cue is in the film, and whether a provider still has to answer it
# (with its audio, segments/seg_NNN.wav, for a resume). Set by the analysis and
# a resume, never by a hand edit.
ANALYSIS_KEYS = ("segment_id", "start", "duration", "audio_file", "pending")

# What identified the cue, all from one provider's answer (match_fields), or
# corrected by hand. agreement and votes come from an ensemble (how many
# providers agreed, and each one's answer); candidates from ACRCloud (its top
# matches, each with its score), which the date re-ranking chooses from.
MATCH_KEYS = (
    "provider",
    "title",
    "artist",
    "album",
    "release_year",
    "isrc",
    "genre",
    "photo_url",
    "youtube_link",
    "spotify_url",
    "apple_music_url",
    "song_link",
    "musicbrainz_recording_id",
    "musicbrainz_url",
    "agreement",
    "votes",
    "candidates",
)

# Then "enrichment", the composers (metadata.enricher), and "temporal_pick" when
# the date re-ranking chose another candidate (temporal).

# The fields a hand edit changes, and what each must be when it isn't None.
EDITED_TYPES = {"title": str, "artist": str, "album": str, "enrichment": dict}


def match_fields(det: DetectionResult | None) -> dict[str, Any]:
    """A cue's MATCH_KEYS from a provider's answer; all None for none (nothing
    identified). New cues (pipeline) and resumed ones (analysis) both take
    theirs from here, so they get the same fields."""
    if det is None or not det.title:
        return dict.fromkeys(MATCH_KEYS)
    fields = {key: det.metadata.get(key) for key in MATCH_KEYS}
    fields.update(provider=det.provider, title=det.title, artist=det.artist)
    return fields


def apply_edits(saved: list[Cue], edited: list[Any]) -> list[Cue]:
    """A run's cues after a hand edit: `edited`, its cues as the user left them
    (corrected, some removed), checked against the `saved` ones. Where a cue is
    and whether it is pending stay the analysis's (ANALYSIS_KEYS), whatever the
    edit says. A cue that isn't one of the run's, or an edited field of the wrong
    type, raises ValueError."""
    by_id = {cue.get("segment_id"): cue for cue in saved}
    seen: set = set()
    cues = []
    for cue in edited:
        segment_id = cue.get("segment_id") if isinstance(cue, dict) else None
        if segment_id not in by_id or segment_id in seen:
            raise ValueError(f"Not a cue of this run, or twice: {segment_id!r}")
        seen.add(segment_id)
        wrong = [
            key
            for key, kind in EDITED_TYPES.items()
            if cue.get(key) is not None and not isinstance(cue[key], kind)
        ]
        if wrong:
            raise ValueError(f"{segment_id}: {', '.join(wrong)} of the wrong type")
        original = by_id[segment_id]
        cues.append({**cue, **{key: original.get(key) for key in ANALYSIS_KEYS}})
    return cues


@dataclass
class ResultsOutput:
    """Complete output for a processed video."""

    source: str
    cues: list[Cue]
    video_name: str | None = None  # run folder name, used in URLs
    waveform: dict | None = None
    film: dict[str, Any] | None = None
    classification: list[dict[str, Any]] | None = None
    classification_windows: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        """The run's results.json."""
        result: dict[str, Any] = {
            "version": RESULTS_VERSION,
            "source": self.source,
            "video_name": self.video_name,
            "film": self.film,
            "cues": self.cues,
        }
        return result

    def timeline(self) -> dict[str, Any]:
        """The run's timeline.json: what the app draws the timeline from."""
        return {
            "waveform": self.waveform,
            "classification": self.classification,
            "classification_windows": self.classification_windows,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_csv(self) -> str:
        return results_to_csv(self.to_dict())


# Format of results.json. Runs outlive app versions, so older files are upgraded
# when read (load_results) and the rest of the code only sees this format.
#   1: no number.
#   2: every cue says whether it is pending (skipped at a rate limit, so a resume
#      identifies it); only pending cues have an audio_file; no ID / fiwi_id.
#   3: settings holds everything the run was analyzed with, also whether vocals
#      were removed (isolation) instead of every cue's conditioning.
#   4: no rate_limited: a resume is due while any cue is pending. The waveform
#      and the classification are in timeline.json (load_results moves them).
#   5: no confidence, in the cues or their votes: it was ACRCloud's score, a
#      constant 1.0 for Shazam and AudD, the share of agreeing providers for an
#      ensemble, and nothing used it. ACRCloud's score is in the candidates.
RESULTS_VERSION = 5

# What a run's timeline.json holds.
TIMELINE_KEYS = ("waveform", "classification", "classification_windows")


def upgrade_results(data: dict[str, Any]) -> dict[str, Any]:
    """Bring a results.json dict to the current format, in place, and return it."""
    cues = data.get("cues") or []
    if data.get("version", 1) < 2:
        # Runs saved before skipped cues were marked: in a rate-limited run any
        # unidentified cue may be one. Runs since then have the mark already.
        limited = bool(data.get("rate_limited"))
        for cue in cues:
            cue.setdefault("pending", limited and not cue.get("title"))
        data.pop("ID", None)
        data.pop("fiwi_id", None)
    if data.get("version", 1) < 3:
        conditionings = {c.pop("conditioning") for c in cues if "conditioning" in c}
        if conditionings:
            settings = data.setdefault("settings", {})
            settings.setdefault("isolation", "vocals_removed" in conditionings)
    if data.get("version", 1) < 4:
        data.pop("rate_limited", None)
    if data.get("version", 1) < 5:
        for cue in cues:
            cue.pop("confidence", None)
            for vote in cue.get("votes") or []:
                vote.pop("confidence", None)
    data["version"] = RESULTS_VERSION
    return data


def load_results(path: Path) -> dict[str, Any]:
    """Read a results.json, upgraded to the current format, with the rights
    verdicts of this year."""
    data = json.loads(path.read_text())
    timeline = {k: data.pop(k) for k in TIMELINE_KEYS if k in data}
    timeline_path = path.parent / "timeline.json"
    if timeline and not timeline_path.exists():
        # Saved before the timeline had a file of its own: moved there now, as
        # the next save of these results leaves it out.
        save_json(timeline_path, timeline)
    return _assess_rights(upgrade_results(data))


def _assess_rights(data: dict[str, Any]) -> dict[str, Any]:
    """Assess every cue's rights again, as of this year: the verdict changes
    with the year, so the one saved may be out of date. An enrichment saved
    without its authors keeps its verdict, with nothing to assess it from."""
    year = rights.current_year()
    for cue in data.get("cues") or []:
        enrichment = cue.get("enrichment")
        if isinstance(enrichment, dict) and "authors" in enrichment:
            partial = bool(enrichment.get("authors_partial"))
            enrichment.update(rights.verdict(enrichment["authors"], partial, year))
    return data


def load_timeline(run_dir: Path) -> dict[str, Any]:
    """A run's timeline.json, also for a run saved before it had one."""
    path = run_dir / "timeline.json"
    if path.exists():
        return json.loads(path.read_text())
    old = json.loads((run_dir / "results.json").read_text())
    return {k: old[k] for k in TIMELINE_KEYS if k in old}


def save_json(path: Path, data: dict[str, Any]) -> None:
    """Write a run's results.json or timeline.json at once: replaced whole,
    never left half-written."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


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
