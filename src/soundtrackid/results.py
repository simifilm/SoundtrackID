"""A run's results.json: the fields of its cues, its format and the upgrades
of older ones, reading and saving it, and checking a hand edit."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from soundtrackid.models import DetectionResult, MusicSegment

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

# Then "alternatives", every song the providers named in the cue's windows, to
# choose from (alternatives); "enrichment", the composers (metadata.enricher);
# and "temporal_pick", whether the date re-ranking chose another candidate
# (temporal), False once a song is chosen by hand. "anachronistic" and
# "track_key" follow from the rest, worked out whenever a run is read or saved
# (derived).

# The fields a cue takes over from another song than the one it was identified
# as (the date re-ranking, a choice by hand): all of the match but the provider,
# ACRCloud's candidates and each provider's answer (votes), which still hold for
# the cue. Those the song lacks (a cover, the providers' agreement) belonged to
# the song replaced, and are cleared.
SONG_KEYS = tuple(
    key for key in MATCH_KEYS if key not in ("provider", "candidates", "votes")
)

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


def alternatives(answers: list[Cue]) -> list[dict[str, Any]]:
    """Every song the providers named in `answers`, the match fields of each of
    a cue's windows (match_fields): each provider's of an ensemble (votes),
    ACRCloud's top matches (candidates), else the one answer. Each once per
    provider, in the order first named, with ACRCloud's best score for it, and
    in how many windows it was named. Entries of a recording on different albums
    or in different years stay apart: the date tells an original from a
    reissue."""
    songs: dict[tuple, dict[str, Any]] = {}
    for fields in answers:
        named: dict[tuple, dict[str, Any]] = {}  # in this window, each once
        for provider, song in _songs_named(fields):
            entry = {
                "provider": provider,
                **{key: song.get(key) for key in SONG_KEYS if key != "agreement"},
                "score": song.get("score"),
            }
            key = (provider, *(entry[k] for k in _SAME_ENTRY))
            named.setdefault(key, entry)
        for key, entry in named.items():
            if key not in songs:
                songs[key] = {**entry, "windows": 1}
                continue
            songs[key]["windows"] += 1
            scores = [s for s in (songs[key]["score"], entry["score"]) if s is not None]
            songs[key]["score"] = max(scores, default=None)
    return list(songs.values())


# What makes two of a provider's entries one, in alternatives.
_SAME_ENTRY = ("title", "artist", "isrc", "album", "release_year")


def _songs_named(fields: Cue) -> list[tuple[str | None, dict[str, Any]]]:
    """(provider, song) for each song one window's answer names."""
    votes = fields.get("votes")
    if not votes:
        songs = fields.get("candidates") or ([fields] if fields.get("title") else [])
        return [(fields.get("provider"), song) for song in songs]
    return [
        (vote.get("provider"), song)
        for vote in votes
        if vote.get("matched")
        # Runs saved before the votes kept ACRCloud's candidates have them only
        # at the cue, when ACRCloud's answer won the vote.
        for song in vote.get("candidates")
        or (vote.get("provider") == "acrcloud" and fields.get("candidates"))
        or [vote]
    ]


def chosen_song(cue: Cue, choice: dict[str, Any]) -> dict[str, Any]:
    """The song a hand choice names for `cue`: one of its alternatives,
    {"alternative": <its index>}, or one typed in, {"title": ..., "artist": ...}.
    ValueError for anything else."""
    if "alternative" in choice:
        index = choice["alternative"]
        songs = cue.get("alternatives") or []
        if type(index) is not int or not 0 <= index < len(songs):
            raise ValueError(f"Not one of the cue's alternatives: {index!r}")
        return songs[index]
    title, artist = choice.get("title"), choice.get("artist")
    if not (isinstance(title, str) and title.strip()) or not isinstance(
        artist, (str, type(None))
    ):
        raise ValueError("A song typed in needs a title, and its artist as text")
    return {"title": title.strip(), "artist": (artist or "").strip() or None}


def take_song(cue: Cue, song: dict[str, Any]) -> None:
    """Make `song` the cue's, in place (SONG_KEYS)."""
    for key in SONG_KEYS:
        cue[key] = song.get(key)


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
#   6: every cue lists the songs the providers named in it (alternatives).
RESULTS_VERSION = 6

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
    if data.get("version", 1) < 6:
        for cue in cues:
            # From what the run kept of the answers, of the cue's first window
            # only: each provider's title, artist and ISRC (votes) and ACRCloud's
            # top matches (candidates). Not the cue's own song alone: it may
            # have been edited by hand since.
            if cue.get("votes") or cue.get("candidates"):
                cue["alternatives"] = alternatives([cue])
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
    """Write a JSON file at once, e.g. a run's results.json or the saved API
    keys: replaced whole, never left half-written."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)
