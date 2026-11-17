"""The video preview."""

import json
import shutil

import pytest
from playwright.sync_api import expect

from tests.media import write_video
from tests.ui.helpers import TIMELINE, cue, make_run, open_run

pytestmark = pytest.mark.browser

PAUSED = "document.querySelector('#video-preview').paused"
CURRENT_TIME = "document.querySelector('#video-preview').currentTime"
LOADED = "document.querySelector('#video-preview').readyState >= 1"
# Over an hour, and long enough for the zoom bar (it shows above 30 s).
LONG_TIMELINE = {**TIMELINE, "waveform": {"samples": [0.5] * 150, "duration": 4000.0}}


@pytest.fixture
def run_with_video(page, runs, video):
    run = make_run(
        runs,
        [cue(1, "Song")],
        video={"file": "source.mp4", "filmstrip": None, "fps": 10},
    )
    shutil.copy(video, run / "source.mp4")
    open_run(page)


def test_space_plays_and_pauses_the_video(page, run_with_video):
    page.keyboard.press("Space")
    page.wait_for_function(f"!{PAUSED}")

    page.keyboard.press("Space")
    page.wait_for_function(PAUSED)


def test_space_in_a_text_field_types_a_space(page, run_with_video):
    row = page.locator("#tracks-body tr:not(.cue-detail)", has_text="Song")
    row.locator('td[data-field="title"]').dblclick()
    field = page.locator("input.cell-edit")
    field.press("End")

    field.press("Space")

    expect(field).to_have_value("Song ")
    assert page.evaluate(PAUSED)


def test_arrow_keys_step_one_frame(page, run_with_video):
    page.wait_for_function(LOADED)

    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    # Into the middle of frame 2 at 10 fps, so rounding can't land on a neighbour.
    assert page.evaluate(CURRENT_TIME) == pytest.approx(0.25)

    page.keyboard.press("ArrowLeft")
    assert page.evaluate(CURRENT_TIME) == pytest.approx(0.15)


@pytest.fixture(scope="module")
def minute_video(tmp_path_factory):
    """A minute of video: the player's own arrow-key jump, by 1% of its length,
    is 0.6 s, so more than one frame."""
    return write_video(tmp_path_factory.mktemp("media") / "minute.mp4", seconds=60)


def test_arrow_keys_step_one_frame_also_when_the_player_has_focus(
    page, runs, minute_video
):
    run = make_run(
        runs,
        [cue(1, "Song")],
        video={"file": "source.mp4", "filmstrip": None, "fps": 10},
    )
    shutil.copy(minute_video, run / "source.mp4")
    open_run(page)
    page.wait_for_function(LOADED)
    page.locator("#video-preview").focus()  # as clicking it to play or pause does

    page.keyboard.press("ArrowRight")

    # One frame, without the player's own jump.
    assert page.evaluate(CURRENT_TIME) == pytest.approx(0.15)


def test_an_arrow_key_pauses_the_video(page, run_with_video):
    page.keyboard.press("Space")
    page.wait_for_function(f"!{PAUSED}")

    page.keyboard.press("ArrowRight")

    assert page.evaluate(PAUSED)  # right away, before the 2 s video ends by itself


def test_arrow_keys_step_at_25_fps_for_runs_without_a_frame_rate(page, runs, video):
    run = make_run(
        runs, [cue(1, "Song")], video={"file": "source.mp4", "filmstrip": None}
    )
    shutil.copy(video, run / "source.mp4")
    open_run(page)
    page.wait_for_function(LOADED)

    page.keyboard.press("ArrowRight")

    assert page.evaluate(CURRENT_TIME) == pytest.approx(0.06)  # middle of frame 1


def test_the_zoom_bar_shows_the_video_time_with_its_frame_and_length(page, runs, video):
    run = make_run(
        runs,
        [cue(1, "Song")],
        video={"file": "source.mp4", "filmstrip": None, "fps": 10},
    )
    shutil.copy(video, run / "source.mp4")
    (run / "timeline.json").write_text(json.dumps(LONG_TIMELINE))
    open_run(page)
    readout = page.locator("#wf-time")
    expect(readout).to_have_text("0:00.00 / 1:06:40")

    page.wait_for_function(LOADED)
    page.evaluate("document.querySelector('#video-preview').currentTime = 1.5")

    expect(readout).to_have_text("0:01.05 / 1:06:40")  # frame 5 of 0–9 at 10 fps


def test_a_run_without_video_shows_only_the_length(page, runs, video):
    with_video = make_run(
        runs, [cue(1, "Song")], video={"file": "source.mp4", "filmstrip": None}
    )
    shutil.copy(video, with_video / "source.mp4")
    without = make_run(runs, [cue(1, "Song")], name="other_2026-11-11_11-00-00")
    for run in (with_video, without):
        (run / "timeline.json").write_text(json.dumps(LONG_TIMELINE))
    open_run(page)

    page.locator("#history-panel > summary").click()
    page.locator(".history-item", has_text=without.name).click()

    expect(page.locator("#wf-time")).to_have_text("1:06:40")
