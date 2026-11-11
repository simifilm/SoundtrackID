"""A run's folder (runs.Run): its results, timeline, film and pending audio."""

import json
import threading
import time

import numpy as np

from soundtrackID.models import MusicSegment
from soundtrackID.runs import Run

SR = 1000


def _clip(seconds=1.0):
    audio = np.linspace(-0.5, 0.5, int(seconds * SR), dtype=np.float32)
    return MusicSegment(audio=audio, start_time=12.0, end_time=13.0, sample_rate=SR)


def test_pending_audio_is_kept_for_a_resume(tmp_path):
    """What a resume sends is what the provider would have heard."""
    run = Run.create(tmp_path / "film_run")

    audio_file = run.write_audio("seg_002", _clip())
    clip = run.read_audio(audio_file)

    assert audio_file == "segments/seg_002.wav"
    assert (clip.start_time, clip.sample_rate) == (0.0, SR)
    np.testing.assert_allclose(clip.audio, _clip().audio, atol=1e-4)
    run.remove_audio(audio_file)
    assert run.read_audio(audio_file) is None


def test_audio_outside_the_runs_segments_is_neither_read_nor_removed(tmp_path):
    """A resume reads and removes the audio a cue names."""
    run = Run.create(tmp_path / "film_run")
    elsewhere = tmp_path / "elsewhere.wav"
    elsewhere.write_bytes(b"not the run's")

    assert run.read_audio("../elsewhere.wav") is None
    run.remove_audio("../elsewhere.wav")
    assert elsewhere.exists()


def test_reading_an_old_run_changes_nothing_on_disk(tmp_path):
    """Its timeline, saved in results.json then, moves to timeline.json when the
    results are next saved, as they leave it out."""
    run = Run.create(tmp_path / "old_run")
    timeline = {"waveform": {"samples": [1, 2, 3]}}
    run.results_path.write_text(json.dumps({"cues": [], **timeline}))

    results = run.load()

    assert "waveform" not in results
    assert run.timeline() == timeline
    assert sorted(p.name for p in run.path.iterdir()) == ["results.json"]
    run.save(results)
    assert json.loads((run.path / "timeline.json").read_text()) == timeline
    assert "waveform" not in json.loads(run.results_path.read_text())


def test_updates_of_a_run_dont_overwrite_each_other(tmp_path):
    """Each reads the results only once the other has saved them."""
    run = Run.create(tmp_path / "film_run")
    run.save({"cues": [], "edits": 0})

    def edit(results):
        edits = results["edits"]
        time.sleep(0.05)  # another save would come in here
        results["edits"] = edits + 1

    threads = [threading.Thread(target=run.update, args=(edit,)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert run.load()["edits"] == 2


def test_the_film_and_its_filmstrip(tmp_path):
    run = Run.create(tmp_path / "film_run")
    film = tmp_path / "My Film.mkv"
    film.write_bytes(b"film")

    assert run.video is None
    kept = run.keep_video(film)

    assert kept == run.video == run.path / "source.mkv"
    assert kept.read_bytes() == b"film" and not film.exists()
    assert run.filmstrip_path == run.path / "filmstrip.jpg"
