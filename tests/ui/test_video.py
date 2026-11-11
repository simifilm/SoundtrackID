"""The video preview."""

import json
import shutil

import pytest
from playwright.sync_api import expect

from tests.ui.conftest import TIMELINE, cue, make_run, open_run

pytestmark = pytest.mark.browser

PAUSED = "document.querySelector('#video-preview').paused"
# Over an hour, and long enough for the zoom bar (it shows above 30 s).
LONG_TIMELINE = {**TIMELINE, "waveform": {"samples": [0.5] * 150, "duration": 4000.0}}


@pytest.fixture
def run_with_video(page, runs, video):
    run = make_run(
        runs, [cue(1, "Song")], video={"file": "source.mp4", "filmstrip": None}
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


def test_the_zoom_bar_shows_the_video_time_and_length(page, runs, video):
    run = make_run(
        runs, [cue(1, "Song")], video={"file": "source.mp4", "filmstrip": None}
    )
    shutil.copy(video, run / "source.mp4")
    (run / "timeline.json").write_text(json.dumps(LONG_TIMELINE))
    open_run(page)
    readout = page.locator("#wf-time")
    expect(readout).to_have_text("0:00 / 1:06:40")

    page.wait_for_function("document.querySelector('#video-preview').readyState >= 1")
    page.evaluate("document.querySelector('#video-preview').currentTime = 1.5")

    expect(readout).to_have_text("0:01 / 1:06:40")


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
