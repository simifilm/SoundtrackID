"""Resuming a run a provider's limit stopped: the banner and the resume."""

import pytest
from playwright.sync_api import expect

from tests.ui.conftest import cue, make_run, open_run, saved

pytestmark = pytest.mark.browser


@pytest.fixture
def run(runs):
    """One cue identified, two left pending at the provider's limit."""
    return make_run(
        runs,
        [
            cue(1, "Known"),
            cue(2, pending=True, audio_file="segments/seg_002.wav"),
            cue(3, pending=True, audio_file="segments/seg_003.wav"),
        ],
    )


def test_the_banner_counts_the_pending_cues(page, run):
    open_run(page)

    expect(page.locator("#ratelimit-text")).to_have_text(
        "Detection stopped early: a provider hit its limit or couldn't be reached. "
        "1 of 3 identified; 2 still pending."
    )


def test_no_banner_without_pending_cues(page, runs):
    make_run(runs, [cue(1, "Known"), cue(2)])

    open_run(page)

    expect(page.locator("#ratelimit-banner")).to_be_hidden()


def test_resume_identifies_the_pending_cues(page, fakes, run):
    fakes.tracks = {0.0: ("Found", "Artist", {})}  # every saved clip starts at 0
    open_run(page)

    page.locator("#resume-btn").click()

    expect(page.locator("#ratelimit-banner")).to_be_hidden()
    expect(page.locator("#tracks-body")).to_contain_text("Found")
    cues = saved(run)["cues"]
    assert [(c["title"], c["pending"]) for c in cues] == [
        ("Known", False),
        ("Found", False),
        ("Found", False),
    ]


def test_the_table_is_read_only_while_resuming(page, fakes, run):
    """Its results replace the table, so an edit meanwhile would be lost."""
    fakes.identifying.clear()  # the provider doesn't answer yet
    open_run(page)
    title = page.locator('#tracks-body td[data-field="title"]').first

    page.locator("#resume-btn").click()

    expect(page.locator("#ratelimit-text")).to_have_text(
        "Resuming identification... 0/2"
    )
    title.dblclick()
    expect(page.locator("input.cell-edit")).to_have_count(0)
    expect(page.locator("#tracks-body .track-remove").first).to_be_hidden()
    fakes.identifying.set()
    expect(page.locator("#ratelimit-banner")).to_be_hidden()
    title.dblclick()
    expect(page.locator("input.cell-edit")).to_have_count(1)
