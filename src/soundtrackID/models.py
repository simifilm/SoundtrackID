"""Data models for the film music analysis pipeline."""

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.io import wavfile


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

    def write_wav(self, path: str | Path) -> None:
        """The clip as a 16-bit WAV file, as the providers get it and a run
        keeps it for a resume; louder than full scale is clipped."""
        pcm = (np.clip(self.audio, -1.0, 1.0) * 32767).astype(np.int16)
        wavfile.write(path, self.sample_rate, pcm)


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
# the date re-ranking chose another candidate (temporal). "anachronistic" and
# "track_key" follow from the rest, worked out whenever a run is read or saved
# (derived).

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


def parse_year(value: Any) -> int | None:
    """The year of a date or a year, as the providers, TMDb, Wikidata,
    MusicBrainz and MP4 tags give it (e.g. "1950-08-04" or 1950): a number
    as it is, else the first four digits of a string."""
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    m = re.search(r"\d{4}", value)
    return int(m.group()) if m else None


def apply_edits(saved: list[Cue], edited: list[Any]) -> list[Cue]:
    """A run's cues after a hand edit: `edited`, its cues as the user left them
    (corrected, some removed), checked against the `saved` ones. Where a cue is
    and whether it is pending stay the analysis's (ANALYSIS_KEYS), whatever the
    edit says. A composer changed by hand gets an author entry of their own
    (_hand_edited_composer). A cue that isn't one of the run's, or an edited
    field of the wrong type, raises ValueError."""
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
        cue = {**cue, **{key: original.get(key) for key in ANALYSIS_KEYS}}
        enrichment = cue.get("enrichment")
        looked_up = (original.get("enrichment") or {}).get("composer")
        if isinstance(enrichment, dict) and enrichment.get("composer") != looked_up:
            _hand_edited_composer(enrichment)
        cues.append(cue)
    return cues


def _hand_edited_composer(enrichment: dict[str, Any]) -> None:
    """A composer edited by hand is another person than the one looked up, so
    that one's life dates no longer apply: the composer's author entry is
    replaced by one without dates. Co-authors found before (a lyricist, say)
    stay, as the edit says nothing about them, and the author set counts as
    partial from now on. The rights verdict follows from the authors when the
    results are saved (derived)."""
    name = enrichment.get("composer") or None
    others = [
        author
        for author in enrichment.get("authors") or []
        if "composer" not in (author.get("roles") or [])
    ]
    composer = {
        "name": name,
        "roles": ["composer"],
        "birth_year": None,
        "death_year": None,
        "wikidata_url": None,
    }
    enrichment["composer"] = name
    enrichment["authors"] = ([composer] if name else []) + others
    enrichment["composer_birth_year"] = None
    enrichment["composer_death_year"] = None
    enrichment["authors_partial"] = True


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
    # The audio of each pending cue, by segment_id, which the run keeps for a
    # resume (runs.Run.write_audio).
    pending_audio: dict[str, MusicSegment] = field(default_factory=dict)

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


# Format of results.json. Runs outlive app versions, so older files are upgraded
# when read (load_results) and the rest of the code only sees this format.
#   1: no number.
#   2: every cue says whether it is pending (skipped at a rate limit, so a resume
#      identifies it); only pending cues have an audio_file; no ID / fiwi_id.
#   3: settings holds everything the run was analyzed with, also whether vocals
#      were removed (isolation) instead of every cue's conditioning.
#   4: no rate_limited: a resume is due while any cue is pending. The waveform
#      and the classification are in timeline.json (runs.Run moves them).
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
    """Read a results.json, upgraded to the current format. A timeline saved in
    it is left out: runs.Run reads it, and moves it to timeline.json when the
    results are saved. What follows from the results (derived) is runs.Run's to
    work out."""
    data = json.loads(path.read_text())
    for key in TIMELINE_KEYS:
        data.pop(key, None)
    return upgrade_results(data)


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
