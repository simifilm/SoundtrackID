"""Tests for the command line (soundtrackid.cli): the same analysis as the app."""

import csv
import json
from types import SimpleNamespace

import pytest

from soundtrackid import cli, paths
from soundtrackid.defaults import (
    BRIDGE_GAP_S,
    CLASSIFICATION_HOP_S,
    DEFAULT_API,
    MAX_SEGMENT_S,
    MUSIC_THRESHOLD,
)
from soundtrackid.isolators import BaseMusicIsolator
from soundtrackid.models import DetectionResult
from tests.fakes import FakeClassifier, FakeLoader, ThrottledClient, fake_components


def _analyze(tmp_path, monkeypatch, *options, client=None):
    """Run `cli analyze film.mp4` on 30 s of music; returns the run's folder."""
    classifier = FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0))
    get_classifier = fake_components(monkeypatch, client, classifier)
    monkeypatch.setattr(cli, "load_classifier", lambda hf: get_classifier())

    cli.main(["analyze", "film.mp4", "--output-dir", str(tmp_path)] + list(options))

    [run_dir] = tmp_path.iterdir()
    return run_dir


class FakeDemucs(BaseMusicIsolator):
    built = 0

    def __init__(self):
        FakeDemucs.built += 1

    def isolate(self, audio, sample_rate):
        return audio


@pytest.mark.parametrize("options", [[], ["--no-isolation"]], ids=["default", "old"])
def test_vocals_stay_unless_isolation_is_asked_for(tmp_path, monkeypatch, options):
    """Demucs needs the demucs extra, which the documented install lacks; the
    old --no-isolation is still accepted."""
    monkeypatch.setattr("soundtrackid.isolators.DemucsIsolator", FakeDemucs)
    FakeDemucs.built = 0

    run_dir = _analyze(tmp_path, monkeypatch, *options)

    assert FakeDemucs.built == 0
    settings = json.loads((run_dir / "results.json").read_text())["settings"]
    assert settings["isolation"] is False


def test_isolation_removes_the_vocals_when_asked_for(tmp_path, monkeypatch):
    monkeypatch.setattr("soundtrackid.isolators.DemucsIsolator", FakeDemucs)
    FakeDemucs.built = 0

    run_dir = _analyze(tmp_path, monkeypatch, "--isolation")

    assert FakeDemucs.built == 1
    settings = json.loads((run_dir / "results.json").read_text())["settings"]
    assert settings["isolation"] is True


def test_analyze_saves_a_run_like_the_app(tmp_path, monkeypatch):
    run_dir = _analyze(
        tmp_path, monkeypatch, "--title", "Sunset Boulevard", "--year", "1950", "--csv"
    )

    assert run_dir.name.startswith("film_")  # the file's name and the time
    results = json.loads((run_dir / "results.json").read_text())
    assert results["film"] == {"title": "Sunset Boulevard", "year": 1950}
    assert results["settings"]["window"] == 10.0
    assert results["settings"]["isolation"] is False
    with (run_dir / "results.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["segment_id"] for r in rows] == [c["segment_id"] for c in results["cues"]]


def test_threshold_decides_what_is_music(tmp_path, monkeypatch):
    """The film's windows all score 0.6: at a threshold of 0.7, none is music."""
    run_dir = _analyze(tmp_path, monkeypatch, "--threshold", "0.7")

    results = json.loads((run_dir / "results.json").read_text())
    assert results["settings"]["threshold"] == 0.7
    assert results["cues"] == []


def test_by_default_the_cli_analyzes_like_the_app_into_its_runs(data_dir, monkeypatch):
    """With the app's settings, and in the app's runs folder, so its history
    lists the run."""
    from fastapi.testclient import TestClient

    from soundtrackid.web import data
    from soundtrackid.web.app import app

    classifier = FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0))
    classifier.window_duration = 10.23  # the model's own, not a round 10 s
    get_classifier = fake_components(monkeypatch, None, classifier)
    monkeypatch.setattr(cli, "load_classifier", lambda hf: get_classifier())
    # The project's runs, in a temporary data folder.
    monkeypatch.setattr(paths, "SOURCE_RUNS_DIR", data.runs_dir())

    cli.main(["analyze", "film.mp4"])

    client = TestClient(app)
    [run] = client.get("/history").json()
    results = client.get(f"/results/{run['video_name']}").json()
    assert results["providers"] == [DEFAULT_API]
    assert results["settings"] == {
        "classifier": "FakeClassifier",
        "threshold": MUSIC_THRESHOLD,
        "window": 10.23,
        "hop": CLASSIFICATION_HOP_S,
        "bridge_gap": BRIDGE_GAP_S,
        "isolation": False,
        "max_segment_duration": MAX_SEGMENT_S,
    }


def test_the_clis_runs_are_the_apps_from_any_folder(tmp_path, monkeypatch):
    """From source, the CLI's runs go where the app's history reads them: the
    project's output/cache, not one in whichever folder either is started from."""
    from soundtrackid.web import data

    folders = []

    def analyze(pipeline, video_path, api, output_dir, **kwargs):
        folders.append(output_dir)
        return {"video_name": "film"}

    monkeypatch.setattr(cli, "load_classifier", lambda hf: None)
    monkeypatch.setattr(cli, "pipeline_for", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "analyze", analyze)
    monkeypatch.setattr(cli, "_summarize", lambda *args: None)
    monkeypatch.chdir(tmp_path)

    cli.main(["analyze", "film.mp4"])

    project_runs = paths.SOURCE_ROOT / "output" / "cache"
    assert folders == [project_runs]
    assert data.runs_dir() == project_runs


def test_whole_music_segments_are_max_segment_0(tmp_path, monkeypatch):
    """As the app's "Whole music segment"."""
    run_dir = _analyze(tmp_path, monkeypatch, "--max-segment", "0")

    results = json.loads((run_dir / "results.json").read_text())
    assert results["settings"]["max_segment_duration"] is None
    assert [(c["start"], c["duration"]) for c in results["cues"]] == [(0.0, 30.0)]


def test_lookup_film_fills_in_what_is_missing(tmp_path, monkeypatch):
    """What is known stays: no one is there to choose among the films found."""
    hints = []

    def lookup_film(**hint):
        hints.append(hint)
        found = {"title": "Sunset Blvd.", "year": 1950, "director": "Billy Wilder"}
        return {**found, "imdb_id": None}

    monkeypatch.setattr("soundtrackid.metadata.film.lookup_film", lookup_film)

    run_dir = _analyze(
        tmp_path, monkeypatch, "--title", "Sunset Boulevard", "--lookup-film"
    )

    results = json.loads((run_dir / "results.json").read_text())
    assert hints == [
        {"imdb_id": None, "tmdb_id": None, "title": "Sunset Boulevard", "year": None}
    ]
    assert results["film"] == {
        "title": "Sunset Boulevard",
        "year": 1950,
        "director": "Billy Wilder",
    }


def _batch_components(monkeypatch, broken=(), client=None):
    """Each film 30 s of music; those `broken` can't be read."""
    classifier = FakeClassifier(music_starts=(0.0, 5.0, 10.0, 15.0, 20.0))
    get_classifier = fake_components(monkeypatch, client, classifier)
    monkeypatch.setattr(cli, "load_classifier", lambda hf: get_classifier())

    class Loader(FakeLoader):
        def load(self, path):
            if path.name in broken:
                raise ValueError(f"Could not read the audio of {path.name}")
            return super().load(path)

    monkeypatch.setattr("soundtrackid.pipeline.VideoLoader", Loader)


def _analyze_batch(tmp_path, monkeypatch, *paths, broken=(), client=None):
    """Run `cli analyze` on these paths into tmp_path/runs: a file's name is
    one made in tmp_path. Returns the batch's folder and record."""
    _batch_components(monkeypatch, broken, client)
    files = []
    for path in paths:
        if isinstance(path, str):
            path = tmp_path / path
            path.write_bytes(b"video")
        files.append(path)
    runs = tmp_path / "runs"

    cli.main(["analyze", *map(str, files), "--output-dir", str(runs), "--csv"])

    [batch_dir] = runs.iterdir()
    return batch_dir, json.loads((batch_dir / "batch.json").read_text())


def test_several_films_are_analyzed_as_a_batch(tmp_path, monkeypatch, capsys):
    """Each a run in the batch's folder, as a single film's."""
    batch_dir, record = _analyze_batch(tmp_path, monkeypatch, "a.mp4", "b.mp4")

    assert batch_dir.name.startswith("batch_")
    assert [(f["source"], f["status"]) for f in record["films"]] == [
        ("a.mp4", "done"),
        ("b.mp4", "done"),
    ]
    for film in record["films"]:
        assert (batch_dir / film["run"] / "results.csv").exists()
    out = capsys.readouterr().out
    # The provider finds nothing in the film's one stretch of music.
    assert f"a.mp4: 0 of 1 cues identified. Results in: {batch_dir}" in out
    assert f"2 of 2 films analyzed. The batch: {batch_dir}" in out


def test_a_folder_is_analyzed_as_its_films(tmp_path, monkeypatch):
    """Its video and audio files, by name."""
    folder = tmp_path / "films"
    folder.mkdir()
    for name in ("b.MKV", "a.mp4", "notes.txt"):
        (folder / name).write_bytes(b"")

    _, record = _analyze_batch(tmp_path, monkeypatch, folder)

    assert [f["source"] for f in record["films"]] == ["a.mp4", "b.MKV"]


def test_a_folder_without_films_is_refused(tmp_path, capsys):
    (tmp_path / "notes.txt").write_text("")

    with pytest.raises(SystemExit) as exc:
        cli.main(["analyze", str(tmp_path)])

    assert exc.value.code == 2
    assert f"no video or audio files in {tmp_path}" in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--title=X", "--year=1950", "--imdb-id=tt1"])
def test_the_options_of_one_film_are_refused_for_several(option, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["analyze", "a.mp4", "b.mp4", option])

    assert exc.value.code == 2
    assert "describe one film, not several" in capsys.readouterr().err


def test_a_film_that_fails_fails_the_command_after_the_others(
    tmp_path, monkeypatch, capsys
):
    with pytest.raises(SystemExit) as exc:
        _analyze_batch(tmp_path, monkeypatch, "a.mp4", "b.mp4", broken=["a.mp4"])

    assert exc.value.code == 1
    [batch_dir] = (tmp_path / "runs").iterdir()
    record = json.loads((batch_dir / "batch.json").read_text())
    assert [f["status"] for f in record["films"]] == ["failed", "done"]
    out = capsys.readouterr().out
    assert "a.mp4: failed: Could not read the audio of a.mp4" in out
    assert "1 of 2 films analyzed" in out
    assert f'Continue it with: soundtrackid continue "{batch_dir}"' in out


def test_a_batch_is_continued_where_it_stopped(tmp_path, monkeypatch, capsys):
    """Its films that failed, or that it didn't get to, from where they are."""
    with pytest.raises(SystemExit):
        _analyze_batch(tmp_path, monkeypatch, "a.mp4", "b.mp4", broken=["a.mp4"])
    [batch_dir] = (tmp_path / "runs").iterdir()
    _batch_components(monkeypatch)
    capsys.readouterr()

    cli.main(["continue", str(batch_dir)])

    record = json.loads((batch_dir / "batch.json").read_text())
    assert [f["status"] for f in record["films"]] == ["done", "done"]
    assert "2 of 2 films analyzed" in capsys.readouterr().out


def test_continue_needs_a_batch(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["continue", str(tmp_path)])

    assert exc.value.code == 2
    assert f"no batch in {tmp_path}" in capsys.readouterr().err


def test_resuming_a_batch_resumes_each_of_its_runs(tmp_path, monkeypatch, capsys):
    batch_dir, record = _analyze_batch(
        tmp_path, monkeypatch, "a.mp4", "b.mp4", client=ThrottledClient()
    )
    monkeypatch.setattr(
        "soundtrackid.detection.build_detection_client",
        lambda api: SimpleNamespace(
            detect=lambda seg: DetectionResult(seg, "Song", "A", "acrcloud")
        ),
    )
    capsys.readouterr()

    cli.main(["resume", str(batch_dir)])

    for film in record["films"]:
        cues = json.loads((batch_dir / film["run"] / "results.json").read_text())
        assert [c["title"] for c in cues["cues"]] == ["Song"]
    out = capsys.readouterr().out
    assert "a.mp4: 1 of 1 cues identified" in out
    assert "b.mp4: 1 of 1 cues identified" in out


def test_resume_identifies_what_a_limit_left_pending(tmp_path, monkeypatch, capsys):
    run_dir = _analyze(
        tmp_path, monkeypatch, "--max-segment", "5", client=ThrottledClient(answers=2)
    )
    assert "soundtrackid resume" in capsys.readouterr().out
    pending = [
        c["segment_id"]
        for c in json.loads((run_dir / "results.json").read_text())["cues"]
        if c["pending"]
    ]
    assert pending
    asked = []

    def build_detection_client(api):
        asked.append(api)
        return SimpleNamespace(
            detect=lambda seg: DetectionResult(seg, "Song", "A", "acrcloud")
        )

    monkeypatch.setattr(
        "soundtrackid.detection.build_detection_client", build_detection_client
    )

    cli.main(["resume", str(run_dir)])

    results = json.loads((run_dir / "results.json").read_text())
    assert [c["title"] for c in results["cues"] if c["segment_id"] in pending] == [
        "Song"
    ] * len(pending)
    assert not any(c["pending"] for c in results["cues"])
    assert asked == [",".join(results["providers"])]  # the run's own
