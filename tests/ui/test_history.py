"""Recent runs: listing, opening and deleting earlier analyses."""

import json
import re
import shutil

import pytest
from playwright.sync_api import expect

from tests.ui.conftest import TIMELINE, cue, make_run, open_run

pytestmark = pytest.mark.browser


def test_recent_runs_list_every_run(page, runs):
    make_run(runs, [cue(1, "Song"), cue(2)], name="film_2026-11-11_10-00-00")
    make_run(runs, [cue(1)], name="other_2026-11-11_11-00-00")

    page.goto("/")
    page.locator("#history-panel > summary").click()

    items = page.locator(".history-item")
    expect(items).to_have_count(2)
    expect(items.filter(has_text="film_2026-11-11_10-00-00")).to_contain_text(
        "2 segs · 1 identified"
    )


def test_opening_a_run_shows_its_results_waveform_and_classes(page, runs):
    make_run(runs, [cue(1, "Song"), cue(2)], film={"title": "Film", "year": 1950})

    open_run(page)

    expect(page.locator("#film-metadata-header")).to_contain_text("Film (1950)")
    expect(page.locator("#tracks-body tr")).to_have_text([re.compile("Song")])
    expect(page.locator("#waveform-container")).to_be_visible()
    page.locator('.results-tab[data-view="classes"]').click()
    expect(page.locator("#classes-body tr")).to_have_count(5)


def test_clicking_a_class_window_scrolls_up_to_the_video(page, runs, video):
    run = make_run(runs, [cue(1)], video={"file": "source.mp4", "filmstrip": None})
    shutil.copy(video, run / "source.mp4")
    page.set_viewport_size({"width": 1280, "height": 400})
    open_run(page)
    page.locator('.results-tab[data-view="classes"]').click()
    row = page.locator("#classes-body tr").last
    row.scroll_into_view_if_needed()
    player = page.locator("#video-preview")
    expect(player).not_to_be_in_viewport()

    row.click()

    expect(player).to_be_in_viewport()


def test_deleting_a_run(page, runs):
    gone = make_run(runs, [cue(1)], name="film_2026-11-11_10-00-00")
    make_run(runs, [cue(1)], name="other_2026-11-11_11-00-00")
    page.goto("/")
    page.locator("#history-panel > summary").click()

    item = page.locator(".history-item", has_text=gone.name)
    item.hover()  # shows the delete button
    item.locator(".history-delete-btn").click()

    expect(page.locator(".history-item")).to_have_text([re.compile("other_")])
    assert not gone.exists()


def test_deleting_all_runs_asks_first(page, runs):
    make_run(runs, [cue(1)], name="film_2026-11-11_10-00-00")
    make_run(runs, [cue(1)], name="other_2026-11-11_11-00-00")
    page.goto("/")
    page.locator("#history-panel > summary").click()
    asked, deletes = [], []
    page.on(
        "request", lambda r: deletes.append(r.url) if r.method == "DELETE" else None
    )

    page.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    page.locator("#delete-all-runs").click()
    expect(page.locator(".history-item")).to_have_count(2)

    page.once("dialog", lambda d: (asked.append(d.message), d.accept()))
    page.locator("#delete-all-runs").click()

    expect(page.locator("#history-panel")).to_be_hidden()
    assert asked == ["Delete all 2 runs? This can't be undone."] * 2
    assert len(deletes) == 1  # none for the cancelled one
    assert list(runs.iterdir()) == []


def test_a_run_saved_before_the_timeline_had_its_own_file(page, runs):
    """Its waveform and classes still show, from the old results.json."""
    run = runs / "old_run"
    run.mkdir(parents=True)
    old = {"source": "film.mp4", "video_name": "old_run", "cues": [cue(1, "Song")]}
    (run / "results.json").write_text(json.dumps({**old, **TIMELINE}))

    open_run(page, "old_run")

    expect(page.locator("#waveform-container")).to_be_visible()
    page.locator('.results-tab[data-view="classes"]').click()
    expect(page.locator("#classes-body tr")).to_have_count(5)
