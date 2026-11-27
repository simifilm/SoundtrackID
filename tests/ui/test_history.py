"""Recent runs: listing, opening and deleting earlier analyses."""

import json
import re
import shutil

import pytest
from playwright.sync_api import expect

from tests.ui.helpers import TIMELINE, cue, make_batch, make_run, open_run

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


def test_recent_runs_say_how_much_they_take_on_disk(page, runs):
    """Next to "Delete all runs"; the folder they are in on hover."""
    run = make_run(runs, [cue(1, "Song")])
    (run / "source.mp4").write_bytes(b"v" * 3 * 1024 * 1024)

    page.goto("/")
    page.locator("#history-panel > summary").click()

    size = page.locator("#history-size")
    expect(size).to_have_text(re.compile(r"^3\.0 MB on disk$"))
    expect(size).to_have_attribute("title", f"In {runs}")


def test_a_batch_shows_its_films_under_it(page, runs):
    """Each with its run, or what became of it."""
    make_run(runs, [cue(1)], name="single_2026-11-11_10-00-00")
    make_batch(
        runs,
        [
            ("a.mp4", "done", [cue(1, "Song"), cue(2)]),
            ("b.mp4", "failed", None),
            ("c.mp4", "queued", None),  # the batch stopped before it
        ],
    )
    page.goto("/")
    page.locator("#history-panel > summary").click()
    batch = page.locator(".history-batch")
    expect(batch).to_contain_text("Batch: a.mp4, b.mp4, c.mp4")
    expect(batch).to_contain_text("1 of 3 films · 1 identified")
    films = page.locator(".history-batch-films .history-item")
    expect(films.first).to_be_hidden()

    batch.click()

    expect(films).to_have_text(
        [
            re.compile(r"a\.mp4.*2 segs · 1 identified"),
            re.compile(r"b\.mp4.*Failed: Could not read the audio"),
            re.compile(r"c\.mp4.*Not analyzed"),
        ]
    )
    films.filter(has_text="a.mp4").click()
    expect(page.locator("#tracks-body tr")).to_have_text([re.compile("Song")])


def test_deleting_a_films_run_keeps_its_batch_open(page, runs):
    batch = make_batch(runs, [("a.mp4", "done", [cue(1)]), ("b.mp4", "done", [cue(1)])])
    page.goto("/")
    page.locator("#history-panel > summary").click()
    page.locator(".history-batch").click()
    film = page.locator(".history-batch-films .history-item", has_text="a.mp4")

    film.hover()
    film.locator(".history-delete-btn").click()

    expect(film).to_contain_text("Run deleted")
    assert not (batch.path / "a_2026-11-11_12-00-00").exists()
    expect(page.locator(".history-batch")).to_contain_text("1 of 2 films")


def test_deleting_a_batch_asks_first(page, runs):
    make_run(runs, [cue(1)], name="single_2026-11-11_10-00-00")
    make_batch(runs, [("a.mp4", "done", [cue(1)]), ("b.mp4", "failed", None)])
    page.goto("/")
    page.locator("#history-panel > summary").click()
    batch = page.locator(".history-batch")
    asked = []

    page.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    batch.hover()
    batch.locator(".history-delete-btn").click()
    expect(batch).to_have_count(1)

    page.once("dialog", lambda d: (asked.append(d.message), d.accept()))
    batch.locator(".history-delete-btn").click()

    expect(batch).to_have_count(0)
    assert asked == ["Delete this batch and its run? This can't be undone."] * 2
    assert [p.name for p in runs.iterdir()] == ["single_2026-11-11_10-00-00"]


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


def test_recent_runs_are_under_the_results_buttons(page, runs):
    make_run(runs, [cue(1, "Song")])

    open_run(page)

    buttons = page.locator(".results-actions").bounding_box()
    panel = page.locator("#history-panel").bounding_box()
    assert panel["y"] >= buttons["y"] + buttons["height"]


def test_another_run_opens_from_the_results(page, runs):
    make_run(runs, [cue(1, "First Song")])
    make_run(runs, [cue(1, "Second Song")], name="other_2026-11-11_11-00-00")
    open_run(page)

    page.locator("#history-panel > summary").click()
    page.locator(".history-item", has_text="other_").click()

    expect(page.locator("#tracks-body tr")).to_have_text([re.compile("Second Song")])
    expect(page.locator("#history-panel > summary")).to_be_visible()


def test_a_run_without_a_waveform_shows_none_of_the_last_one(page, runs):
    make_run(runs, [cue(1)])
    other = make_run(runs, [cue(1)], name="other_2026-11-11_11-00-00")
    (other / "timeline.json").unlink()
    open_run(page)
    expect(page.locator("#waveform-container")).to_be_visible()

    page.locator("#history-panel > summary").click()
    page.locator(".history-item", has_text="other_").click()

    expect(page.locator("#waveform-container")).to_be_hidden()


def test_deleting_the_open_run_goes_back_to_the_start(page, runs):
    shown = make_run(runs, [cue(1)])
    make_run(runs, [cue(1)], name="other_2026-11-11_11-00-00")
    open_run(page)
    page.locator("#history-panel > summary").click()

    item = page.locator(".history-item", has_text=shown.name)
    item.hover()
    item.locator(".history-delete-btn").click()

    expect(page.locator("#results-view")).to_be_hidden()
    expect(page.locator("#drop-zone")).to_be_visible()
    expect(page.locator(".history-item")).to_have_text([re.compile("other_")])


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
