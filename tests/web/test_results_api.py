"""Tests for the manual-edit save endpoint POST /results/{video_name}."""

import csv
import io
import json
import os
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from scipy.io import wavfile

from soundtrackid.derived import refresh
from soundtrackid.detection import RateLimitError
from soundtrackid.models import DetectionResult, MusicSegment
from soundtrackid.pipeline import build_cues
from soundtrackid.results import (
    ANALYSIS_KEYS,
    MATCH_KEYS,
    RESULTS_VERSION,
    SONG_KEYS,
    ResultsOutput,
    upgrade_results,
)
from soundtrackid.web.app import app
from tests.jobs import job_events

# Plain TestClient (no `with`) so the lifespan model-load never runs.
client = TestClient(app)


def _make_run(runs_dir, name="myrun"):
    run_dir = runs_dir / name
    run_dir.mkdir()
    (run_dir / "results.json").write_text(
        json.dumps(
            {
                "ID": "abc",
                "film": {"title": "Test", "year": 1950},
                "waveform": {"samples": [1, 2, 3]},
                "cues": [{"segment_id": "seg_001", "title": "Old", "artist": "A"}],
            }
        )
    )
    return run_dir


def test_save_replaces_cues_and_preserves_rest(runs_dir):
    run_dir = _make_run(runs_dir)

    new_cues = [
        {
            "segment_id": "seg_001",
            "title": "New Title",
            "artist": "A",
            "enrichment": {"original_year": 1916},
        },
    ]
    r = client.post("/results/myrun", json={"cues": new_cues})
    assert r.status_code == 200
    assert r.json()["cues"] == 1

    saved = json.loads((run_dir / "results.json").read_text())
    [cue] = saved["cues"]  # replaced
    assert {key: cue[key] for key in new_cues[0]} == new_cues[0]
    assert saved["film"] == {"title": "Test", "year": 1950}  # preserved
    # Preserved, in the timeline's own file now.
    timeline = json.loads((run_dir / "timeline.json").read_text())
    assert timeline == {"waveform": {"samples": [1, 2, 3]}}


def _run_with_cue(runs_dir, cue, film=None):
    run_dir = runs_dir / "myrun"
    run_dir.mkdir()
    (run_dir / "results.json").write_text(json.dumps({"film": film, "cues": [cue]}))
    return run_dir


def test_a_saved_edit_comes_back_with_what_follows_from_it(runs_dir):
    """A work dated after the film by hand is marked as later than it, in the
    answer (which the page shows) and on disk."""
    cue = {"segment_id": "seg_001", "title": "Song", "artist": "A", "enrichment": {}}
    run_dir = _run_with_cue(runs_dir, cue, film={"title": "F", "year": 1950})
    edited = {**cue, "enrichment": {"original_year": 1975}}

    r = client.post("/results/myrun", json={"cues": [edited]})

    [answered] = r.json()["results"]["cues"]
    [saved] = json.loads((run_dir / "results.json").read_text())["cues"]
    assert answered["anachronistic"] is saved["anachronistic"] is True
    assert answered["track_key"] == "song|a"


def test_a_hand_edited_composer_keeps_the_co_authors_and_their_verdict(runs_dir):
    """The life dates were the composer looked up, so they go; the lyricist
    found before stays, and their death in 1993 keeps the work protected."""
    enrichment = {
        "composer": "Glenn Miller",
        "composer_death_year": 1944,
        "authors": [
            {"name": "Glenn Miller", "roles": ["composer"], "death_year": 1944},
            {"name": "Mitchell Parish", "roles": ["lyricist"], "death_year": 1993},
        ],
    }
    cue = {"segment_id": "seg_001", "title": "Moonlight Serenade"}
    _run_with_cue(runs_dir, {**cue, "enrichment": enrichment})
    edited = {**cue, "enrichment": {**enrichment, "composer": "Someone Else"}}

    r = client.post("/results/myrun", json={"cues": [edited]})

    saved = r.json()["results"]["cues"][0]["enrichment"]
    assert [a["name"] for a in saved["authors"]] == ["Someone Else", "Mitchell Parish"]
    assert saved["authors"][0]["death_year"] is None
    assert saved["composer_death_year"] is None
    assert saved["authors_partial"] is True
    assert (saved["latest_author_death_year"], saved["public_domain"]) == (1993, False)


def test_an_edit_keeps_where_a_cue_is_and_whether_it_is_pending(runs_dir):
    """Those are the analysis's: a resume reads and removes the audio_file."""
    run_dir = runs_dir / "myrun"
    run_dir.mkdir()
    cue = {
        "segment_id": "seg_001",
        "start": 10.0,
        "duration": 5.0,
        "audio_file": "segments/seg_001.wav",
        "pending": True,
        "title": None,
    }
    (run_dir / "results.json").write_text(json.dumps({"cues": [cue]}))
    edited = {
        **cue,
        "start": 0.0,
        "audio_file": "../../elsewhere.wav",
        "pending": False,
        "title": "Typed in",
    }

    r = client.post("/results/myrun", json={"cues": [edited]})

    assert r.status_code == 200
    [saved] = json.loads((run_dir / "results.json").read_text())["cues"]
    assert {key: saved[key] for key in ANALYSIS_KEYS} == {
        key: cue[key] for key in ANALYSIS_KEYS
    }
    assert saved["title"] == "Typed in"


@pytest.mark.parametrize(
    "cues",
    [
        [{"segment_id": "seg_999", "title": "Not of this run"}],
        [{"segment_id": "seg_001"}, {"segment_id": "seg_001"}],
        ["not a cue"],
        [{"segment_id": "seg_001", "title": 42}],
        [{"segment_id": "seg_001", "enrichment": "not an object"}],
    ],
    ids=["unknown", "twice", "not-an-object", "title-number", "enrichment-text"],
)
def test_an_edit_that_doesnt_fit_the_run_is_refused(runs_dir, cues):
    """And nothing is saved."""
    run_dir = _make_run(runs_dir)
    before = (run_dir / "results.json").read_text()

    r = client.post("/results/myrun", json={"cues": cues})

    assert r.status_code == 422
    assert (run_dir / "results.json").read_text() == before


def test_save_can_remove_all_cues(runs_dir):
    run_dir = _make_run(runs_dir)
    r = client.post("/results/myrun", json={"cues": []})
    assert r.status_code == 200
    assert json.loads((run_dir / "results.json").read_text())["cues"] == []


def test_missing_run_is_404(runs_dir):
    r = client.post("/results/nope", json={"cues": []})
    assert r.status_code == 404


def test_bad_body_is_422(runs_dir):
    _make_run(runs_dir)
    r = client.post("/results/myrun", json={"not_cues": 1})
    assert r.status_code == 422


@pytest.mark.parametrize("name", ["%2e", "%2e%2e"])
def test_path_traversal_rejected(data_dir, runs_dir, name):
    # "." and ".." would reach these results files without the check
    cache = runs_dir
    for folder in (cache, data_dir):
        (folder / "results.json").write_text('{"cues": ["keep"]}')
    r = client.post(f"/results/{name}", json={"cues": []})
    assert r.status_code == 400
    for folder in (cache, data_dir):
        assert json.loads((folder / "results.json").read_text()) == {"cues": ["keep"]}


# ── A cue's song chosen by hand: POST /results/{video_name}/cues/{id}/song ──

COVER = {
    "provider": "acrcloud",
    "title": "It Had to Be You",
    "artist": "Infinity Zan",
    "isrc": "QZ1",
    "release_year": 2017,
    "spotify_url": "https://open.spotify.com/track/1",
    "score": 100.0,
    "windows": 1,
}
ORIGINAL = {
    "provider": "shazam",
    "title": "It Had to Be You",
    "artist": "Harry Connick, Jr.",
    "isrc": "USSM18900491",
    "release_year": 1989,
    "apple_music_url": "https://music.apple.com/song/1",
    "score": None,
    "windows": 1,
}


def _run_with_covers(runs_dir, monkeypatch, composers):
    """A run of a 1989 film with two cues identified as a cover, the original
    among the songs the providers named; a composer lookup finds `composers`
    by ISRC."""

    def enrich(results, **kw):
        for cue in results["cues"]:
            if cue.get("isrc") in composers:
                cue["enrichment"] = {"composer": composers[cue["isrc"]]}

    monkeypatch.setattr("soundtrackid.metadata.enricher.enrich", enrich)
    cue = {
        **{k: COVER[k] for k in ("title", "artist", "isrc", "release_year")},
        "spotify_url": COVER["spotify_url"],
        "provider": "ensemble",
        "agreement": {"count": 1, "total": 3, "providers": ["acrcloud"]},
        "temporal_pick": True,
        "enrichment": {"composer": "Looked up for the cover"},
        "alternatives": [COVER, ORIGINAL],
    }
    run_dir = runs_dir / "myrun"
    run_dir.mkdir()
    results = {
        "version": RESULTS_VERSION,
        "film": {"title": "F", "year": 1989},
        "cues": [{**cue, "segment_id": "seg_001"}, {**cue, "segment_id": "seg_002"}],
    }
    (run_dir / "results.json").write_text(json.dumps(results))
    return run_dir


def test_a_chosen_song_replaces_the_match_and_composer_of_its_cue_only(
    runs_dir, monkeypatch
):
    """What the cover had and the original lacks (its Spotify link, the
    providers' agreement) goes, and the date re-ranking no longer claims the
    pick. The songs to choose from stay, the cover marked as later than the
    film."""
    run_dir = _run_with_covers(runs_dir, monkeypatch, {"USSM18900491": "Isham Jones"})

    r = client.post("/results/myrun/cues/seg_001/song", json={"alternative": 1})

    assert r.status_code == 200
    chosen, other = json.loads((run_dir / "results.json").read_text())["cues"]
    assert {k: chosen[k] for k in ("artist", "isrc", "release_year")} == {
        "artist": "Harry Connick, Jr.",
        "isrc": "USSM18900491",
        "release_year": 1989,
    }
    assert chosen["apple_music_url"] == ORIGINAL["apple_music_url"]
    assert (chosen["spotify_url"], chosen["agreement"]) == (None, None)
    assert (chosen["temporal_pick"], chosen["provider"]) == (False, "ensemble")
    assert chosen["enrichment"] == {"composer": "Isham Jones"}
    assert [s["anachronistic"] for s in chosen["alternatives"]] == [True, False]
    assert (other["artist"], other["enrichment"]["composer"]) == (
        "Infinity Zan",
        "Looked up for the cover",
    )
    assert r.json()["results"]["cues"][0]["artist"] == "Harry Connick, Jr."


def test_a_typed_in_song_keeps_nothing_of_the_old_match(runs_dir, monkeypatch):
    """Without an ISRC no composer is looked up, as after an analysis."""
    run_dir = _run_with_covers(runs_dir, monkeypatch, {"QZ1": "Looked up again"})

    r = client.post(
        "/results/myrun/cues/seg_001/song", json={"title": " Original ", "artist": ""}
    )

    assert r.status_code == 200
    chosen = json.loads((run_dir / "results.json").read_text())["cues"][0]
    assert {k: chosen[k] for k in SONG_KEYS} == {
        **dict.fromkeys(SONG_KEYS),
        "title": "Original",
    }
    assert chosen["enrichment"] is None


@pytest.mark.parametrize(
    "choice",
    [
        {"alternative": 2},
        {"alternative": -1},
        {"alternative": True},
        {"alternative": "1"},
        {"title": " "},
        {"title": "Song", "artist": 3},
        {},
    ],
    ids=["past-end", "negative", "bool", "text", "blank", "artist-number", "empty"],
)
def test_a_choice_that_isnt_one_is_refused(runs_dir, monkeypatch, choice):
    """And nothing is saved."""
    run_dir = _run_with_covers(runs_dir, monkeypatch, {})
    before = (run_dir / "results.json").read_text()

    r = client.post("/results/myrun/cues/seg_001/song", json=choice)

    assert r.status_code == 422
    assert (run_dir / "results.json").read_text() == before


def test_choosing_the_song_of_an_unknown_cue_is_404(runs_dir, monkeypatch):
    _run_with_covers(runs_dir, monkeypatch, {})

    r = client.post("/results/myrun/cues/seg_999/song", json={"alternative": 0})

    assert r.status_code == 404


# ── Reading runs: GET /results/{video_name} and the history list ───────────


def test_get_returns_the_run_results_in_the_current_format(runs_dir):
    run_dir = _make_run(runs_dir)  # saved without a version

    r = client.get("/results/myrun")

    assert r.status_code == 200
    saved = json.loads((run_dir / "results.json").read_text())
    del saved["waveform"]  # in the run's timeline
    assert r.json() == refresh(upgrade_results(saved))
    assert r.json()["version"] == RESULTS_VERSION


def _run_with_enrichment(runs_dir, enrichment):
    run_dir = runs_dir / "myrun"
    run_dir.mkdir()
    cue = {"segment_id": "seg_001", "title": "Song", "enrichment": enrichment}
    (run_dir / "results.json").write_text(json.dumps({"cues": [cue]}))


def test_the_rights_verdict_is_that_of_the_year_the_run_is_read(runs_dir):
    """The last author died 71 years ago: still protected when the run was
    saved last year, in the public domain now, also in the CSV."""
    died = date.today().year - 71
    _run_with_enrichment(
        runs_dir,
        {
            "composer": "A",
            "authors": [{"name": "A", "roles": ["composer"], "death_year": died}],
            "latest_author_death_year": died,
            "authors_complete": True,
            "authors_partial": False,
            "public_domain": False,  # as assessed last year
            "pd_presumption": None,
        },
    )

    cue = client.get("/results/myrun").json()["cues"][0]
    [row] = csv.DictReader(io.StringIO(client.get("/output/myrun/csv").text))

    assert cue["enrichment"]["public_domain"] is True
    assert row["enrichment.public_domain"] == "True"


def test_a_verdict_saved_without_its_authors_is_kept(runs_dir):
    """Nothing to assess it from again."""
    enrichment = {"composer": "A", "composer_death_year": 1900, "public_domain": True}
    _run_with_enrichment(runs_dir, enrichment)

    cue = client.get("/results/myrun").json()["cues"][0]

    assert cue["enrichment"] == enrichment


def test_results_carry_their_format_version():
    results = ResultsOutput("film.mp4", []).to_dict()

    assert results["version"] == RESULTS_VERSION
    assert "ID" not in results and "fiwi_id" not in results


def test_older_results_are_upgraded_when_read():
    """Saved before skipped cues were marked: in a rate-limited run any
    unidentified cue may be one. Marks saved since are kept."""
    limited = {
        "ID": None,
        "fiwi_id": None,
        "rate_limited": True,
        "cues": [{"title": "Song"}, {"title": None}],
    }
    finished = {"rate_limited": False, "cues": [{"title": None}]}
    marked = {"rate_limited": True, "cues": [{"title": None, "pending": False}]}

    assert upgrade_results(limited) == {
        "version": RESULTS_VERSION,
        "cues": [{"title": "Song", "pending": False}, {"title": None, "pending": True}],
    }
    assert upgrade_results(finished)["cues"] == [{"title": None, "pending": False}]
    assert upgrade_results(marked)["cues"] == [{"title": None, "pending": False}]


def test_vocal_isolation_moves_from_the_cues_into_the_settings():
    """Every cue said whether vocals were removed (conditioning); it's a setting."""
    cue = {"title": "Song", "pending": False}
    isolated = {"version": 2, "cues": [{**cue, "conditioning": "vocals_removed"}]}
    original = {
        "version": 2,
        "settings": {"hop": 5.0},
        "cues": [{**cue, "conditioning": "original"}],
    }

    assert upgrade_results(isolated) == {
        "version": RESULTS_VERSION,
        "settings": {"isolation": True},
        "cues": [cue],
    }
    assert upgrade_results(original)["settings"] == {"hop": 5.0, "isolation": False}


def test_rate_limit_flag_gives_way_to_the_pending_cues():
    """A resume is due while any cue is pending; the run-wide flag could
    disagree with the cues."""
    data = {"version": 3, "rate_limited": True, "cues": [{"pending": False}]}

    assert upgrade_results(data) == {
        "version": RESULTS_VERSION,
        "cues": [{"pending": False}],
    }


def test_older_results_lose_their_confidence():
    """It meant something else for each provider (a constant 1.0 for Shazam and
    AudD), and nothing used it."""
    vote = {"provider": "shazam", "title": "Song", "confidence": 1.0}
    data = {
        "version": 4,
        "cues": [{"title": "Song", "confidence": 1.0, "votes": [vote]}],
    }

    assert upgrade_results(data)["cues"] == [
        {
            "title": "Song",
            "votes": [{"provider": "shazam", "title": "Song"}],
            "alternatives": [],
        }
    ]


def test_older_results_list_the_songs_they_kept_to_choose_from():
    """Of the cue's first window: ACRCloud's top matches, at the cue when its
    answer won the vote, and each provider's title, artist and ISRC. A cue
    without them gets none: its own song may have been edited by hand."""

    def vote(provider, title):
        return {
            "provider": provider,
            "title": title,
            "artist": "A",
            "isrc": None,
            "matched": bool(title),
            "error": None,
        }

    won = {
        "title": "Cover",
        "candidates": [
            {"title": "Cover", "artist": "A", "score": 80.0},
            {"title": "Original", "artist": "A", "score": 60.0},
        ],
        "votes": [
            vote("acrcloud", "Cover"),
            vote("shazam", "Other"),
            vote("audd", None),
        ],
    }
    lost = {
        "title": "Other",
        "votes": [vote("acrcloud", "Cover"), vote("shazam", "Other")],
    }
    edited = {"title": "Typed in"}

    cues = upgrade_results({"version": 5, "cues": [won, lost, edited]})["cues"]

    def songs(cue):
        return [(s["provider"], s["title"], s["score"]) for s in cue["alternatives"]]

    assert songs(cues[0]) == [
        ("acrcloud", "Cover", 80.0),
        ("acrcloud", "Original", 60.0),
        ("shazam", "Other", None),
    ]
    assert songs(cues[1]) == [("acrcloud", "Cover", None), ("shazam", "Other", None)]
    assert "alternatives" not in cues[2]


def test_timeline_of_a_run_saved_before_it_had_its_own_file(runs_dir):
    """Read from results.json, and moved into timeline.json when the results
    are next saved, as they leave it out."""
    run_dir = _make_run(runs_dir)  # its waveform in results.json
    timeline = {"waveform": {"samples": [1, 2, 3]}}

    assert client.get("/output/myrun/timeline").json() == timeline
    client.post("/results/myrun", json={"cues": []})  # an edit saves the results

    assert "waveform" not in json.loads((run_dir / "results.json").read_text())
    assert json.loads((run_dir / "timeline.json").read_text()) == timeline
    assert client.get("/output/myrun/timeline").json() == timeline


def test_timeline_of_a_missing_run_is_404(runs_dir):
    assert client.get("/output/nope/timeline").status_code == 404


def test_get_missing_run_is_404(runs_dir):
    assert client.get("/results/nope").status_code == 404


@pytest.mark.parametrize("name", ["%2e", "%2e%2e"])
def test_get_path_traversal_rejected(data_dir, runs_dir, name):
    # "." and ".." would reach these results files without the check
    cache = runs_dir
    for folder in (cache, data_dir):
        (folder / "results.json").write_text("{}")
    assert client.get(f"/results/{name}").status_code == 400


def test_history_lists_every_run_newest_first(runs_dir):
    """Not just the last few; without their results, which GET /results loads."""
    for i in range(12):
        run_dir = _make_run(runs_dir, f"run{i:02d}")
        os.utime(run_dir, (1000 + i, 1000 + i))  # run11 is the newest

    items = client.get("/history").json()

    assert [item["video_name"] for item in items] == [
        f"run{i:02d}" for i in reversed(range(12))
    ]
    assert {k: v for k, v in items[0].items() if k != "timestamp"} == {
        "video_name": "run11",
        "total": 1,
        "identified": 1,
    }


# ── Resume endpoint ────────────────────────────────────────────────────────


def test_resume_missing_run_404(runs_dir):
    r = client.post("/resume/nope", json={})
    assert r.status_code == 404


def test_resume_with_nothing_pending_changes_nothing(runs_dir):
    run = runs_dir / "r"
    run.mkdir()
    results = {
        "version": RESULTS_VERSION,
        "cues": [{"segment_id": "s1", "title": "X", "pending": False}],
    }
    (run / "results.json").write_text(json.dumps(results))

    events = job_events(client, client.post("/resume/r", json={}))

    assert events[-1]["step"] == "done"
    assert json.loads((run / "results.json").read_text()) == results


def test_resumed_cue_gets_the_same_fields_as_an_analyzed_one(runs_dir, monkeypatch):
    """E.g. its candidates, without which the date re-ranking skips it, and the
    songs to choose from."""
    metadata = {
        key: f"<{key}>"
        for key in MATCH_KEYS
        if key not in ("provider", "title", "artist")
    } | {"votes": [], "candidates": [{"title": "T", "artist": "A", "score": 90.0}]}

    def detect(segment):
        return DetectionResult(segment, "T", "A", "acrcloud", dict(metadata))

    clip = MusicSegment(np.zeros(1000, np.float32), 0.0, 1.0, 1000)
    analyzed, _ = build_cues([(clip, detect(clip))], bridge_gap=2.0)
    [analyzed] = refresh({"cues": analyzed})["cues"]  # as saved
    cues = [{"segment_id": "s1", "title": None, "pending": True}]
    [resumed] = _resume(runs_dir, monkeypatch, detect, cues)["cues"]

    found = (*MATCH_KEYS, "alternatives")
    assert {key: resumed[key] for key in found} == {key: analyzed[key] for key in found}
    assert [song["score"] for song in resumed["alternatives"]] == [90.0]


def _resume(runs_dir, monkeypatch, detect, cues, enrich=None, asked=None, **results):
    """POST /resume for a run with these cues (each with a short silent clip),
    identifying them with `detect` and looking up composers with `enrich` (by
    default not at all); returns the saved results. `asked` collects the
    providers the resume asks for."""

    def build_detection_client(api):
        if asked is not None:
            asked.append(api)
        return SimpleNamespace(detect=detect)

    monkeypatch.setattr(
        "soundtrackid.detection.build_detection_client", build_detection_client
    )
    monkeypatch.setattr(
        "soundtrackid.metadata.enricher.enrich", enrich or (lambda **kw: None)
    )
    run = runs_dir / "r"
    (run / "segments").mkdir(parents=True)
    for cue in cues:
        cue["audio_file"] = f"segments/{cue['segment_id']}.wav"
        wavfile.write(run / cue["audio_file"], 1000, np.zeros(1000, dtype=np.int16))
    (run / "results.json").write_text(json.dumps({**results, "cues": cues}))

    job_events(client, client.post("/resume/r", json={}))

    return json.loads((run / "results.json").read_text())


def test_a_resume_continues_with_the_runs_own_providers(runs_dir, monkeypatch):
    """Unless the page asks for others; as the CLI's resume."""
    asked = []
    cues = [{"segment_id": "s1", "title": None, "pending": True}]

    _resume(
        runs_dir,
        monkeypatch,
        lambda seg: DetectionResult(seg, "T", "A", "audd"),
        cues,
        asked=asked,
        providers=["audd", "shazam"],
    )

    assert asked == ["audd,shazam"]


def test_resume_reranks_newly_identified_cues(runs_dir, monkeypatch):
    """A resumed cue whose top match postdates the film gets the period pick."""
    candidates = [
        {"title": "Sampler", "release_year": 2022, "score": 90.0},
        {"title": "Original", "release_year": 1971, "score": 70.0},
    ]

    def detect(segment):
        metadata = {"release_year": 2022, "candidates": candidates}
        return DetectionResult(segment, "Sampler", "A", "acrcloud", metadata)

    cues = [{"segment_id": "s1", "title": None}]
    saved = _resume(
        runs_dir, monkeypatch, detect, cues, film={"year": 1971}, rate_limited=True
    )

    [cue] = saved["cues"]
    assert (cue["title"], cue["release_year"]) == ("Original", 1971)
    assert cue["temporal_pick"] is True


def test_resume_asks_only_about_pending_cues(runs_dir, monkeypatch):
    """A cue the provider already answered with "no match" isn't asked again."""

    def detect(segment):
        return DetectionResult(segment, "Song", "A", "acrcloud", {})

    cues = [
        {"segment_id": "s1", "title": None, "pending": False},
        {"segment_id": "s2", "title": None, "pending": True},
    ]
    saved = _resume(runs_dir, monkeypatch, detect, cues, rate_limited=True)

    assert [(c["title"], c["pending"]) for c in saved["cues"]] == [
        (None, False),
        ("Song", False),
    ]


def test_resume_of_an_older_run_keeps_its_unasked_cues_pending(runs_dir, monkeypatch):
    """Its cues aren't marked, so every unidentified one counts as pending,
    including those a renewed rate limit leaves unasked."""

    def detect(segment):
        raise RateLimitError("quota exceeded")

    cues = [
        {"segment_id": "s1", "title": "Song"},
        {"segment_id": "s2", "title": None},
        {"segment_id": "s3", "title": None},
    ]
    saved = _resume(runs_dir, monkeypatch, detect, cues, rate_limited=True)

    assert [c["pending"] for c in saved["cues"]] == [False, True, True]


def test_resume_gives_up_on_a_cue_whose_audio_is_gone(runs_dir, monkeypatch):
    """No resume could identify it, so it no longer counts as pending."""
    monkeypatch.setattr(
        "soundtrackid.detection.build_detection_client",
        lambda api: SimpleNamespace(detect=lambda seg: pytest.fail("nothing to send")),
    )
    monkeypatch.setattr("soundtrackid.metadata.enricher.enrich", lambda **kw: None)
    run = runs_dir / "r"
    run.mkdir()
    cue = {"segment_id": "s1", "pending": True, "audio_file": "segments/s1.wav"}
    results = {"version": RESULTS_VERSION, "cues": [cue]}
    (run / "results.json").write_text(json.dumps(results))

    job_events(client, client.post("/resume/r", json={}))

    [saved] = json.loads((run / "results.json").read_text())["cues"]
    assert (saved["pending"], saved["audio_file"]) == (False, None)


def test_resume_changes_only_the_cues_it_identifies(runs_dir, monkeypatch):
    """The others keep their composers, e.g. one edited by hand."""

    def detect(segment):
        return DetectionResult(segment, "New", "B", "acrcloud", {"isrc": "X2"})

    def enrich(results, **kw):
        for cue in results["cues"]:
            cue["enrichment"] = {"composer": "Looked up"}

    cues = [
        {
            "segment_id": "s1",
            "title": "Old",
            "isrc": "X1",
            "pending": False,
            "enrichment": {"composer": "Edited by hand"},
        },
        {"segment_id": "s2", "title": None, "pending": True},
    ]
    saved = _resume(
        runs_dir, monkeypatch, detect, cues, enrich=enrich, rate_limited=True
    )

    assert [c["enrichment"]["composer"] for c in saved["cues"]] == [
        "Edited by hand",
        "Looked up",
    ]


def test_resume_removes_the_audio_of_answered_cues(runs_dir, monkeypatch):
    """Only pending cues keep their audio, for the next resume."""
    calls = []

    def detect(segment):
        calls.append(segment)
        if len(calls) > 1:
            raise RateLimitError("quota exceeded")
        return DetectionResult(segment, None, None)  # no match

    cues = [
        {"segment_id": "s1", "title": None, "pending": True},
        {"segment_id": "s2", "title": None, "pending": True},
    ]
    saved = _resume(runs_dir, monkeypatch, detect, cues, rate_limited=True)

    assert [(c["pending"], c["audio_file"]) for c in saved["cues"]] == [
        (False, None),
        (True, "segments/s2.wav"),
    ]
    assert [p.name for p in (runs_dir / "r" / "segments").iterdir()] == ["s2.wav"]
