"""The video preview."""

import shutil

import pytest
from playwright.sync_api import expect

from tests.ui.conftest import cue, make_run, open_run

pytestmark = pytest.mark.browser

PAUSED = "document.querySelector('#video-preview').paused"


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
