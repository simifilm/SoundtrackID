"""Browser tests of a batch's page: its films' runs to open, also while the
batch runs on, and what it offers once it ended: its results to download, its
films not done to continue, its runs' pending cues to resume."""

import io
import re
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from playwright.sync_api import expect

from soundtrackid.locks import FolderLock
from soundtrackid.web import jobs
from tests.ui.helpers import cue, make_batch

pytestmark = pytest.mark.browser


@contextmanager
def batch_under_way(batch):
    """A job analyzing the batch meanwhile: it reports the batch as it is on
    disk, then waits until the block ends."""
    gate = threading.Event()

    def work(on_progress, cancel):
        on_progress("batch", name=batch.name, **batch.load())
        gate.wait(10)
        return {"name": batch.name, **batch.load()}

    jobs.start("batch", work, name=batch.name)
    try:
        yield
    finally:
        gate.set()


def open_batch(page):
    """Open the batch's page from Recent runs."""
    page.goto("/")
    page.locator("#history-panel > summary").click()
    page.locator(".history-batch .history-open-btn").click()


def test_a_film_done_opens_while_the_batch_runs_on(page, runs):
    """And "Back to the batch" shows it again, where it is."""
    batch = make_batch(
        runs, [("a.mp4", "done", [cue(1, "Song")]), ("b.mp4", "analyzing", None)]
    )
    films = page.locator(".batch-film")
    with batch_under_way(batch):
        page.goto("/")  # follows the job under way
        expect(films).to_have_text(
            [
                re.compile(r"a\.mp4.*1 of 1 cues identified.*Open"),
                re.compile(r"b\.mp4.*Analyzing"),
            ]
        )

        films.first.get_by_role("button", name="Open").click()

        expect(page.locator("#results-view")).to_be_visible()
        expect(page.locator("#tracks-body")).to_contain_text("Song")
        page.locator("#batch-back").click()
        expect(films.last).to_contain_text("Analyzing")
        expect(page.locator("#cancel-btn")).to_be_visible()

    # Once it ended: what it offers.
    expect(page.locator("#batch-heading")).to_have_text("Batch: 1 of 2 films analyzed")
    expect(page.locator("#batch-download")).to_be_visible()
    expect(page.locator("#progress-back")).to_have_text("Analyze other files")


def test_a_run_of_a_batch_opened_from_recent_runs_goes_back_to_it(page, runs):
    make_batch(runs, [("a.mp4", "done", [cue(1, "Song")]), ("b.mp4", "failed", None)])
    page.goto("/")
    page.locator("#history-panel > summary").click()
    page.locator(".history-batch").click()

    page.locator(".history-batch-films .history-item", has_text="a.mp4").click()
    page.locator("#batch-back").click()

    expect(page.locator(".batch-film")).to_have_text(
        [re.compile(r"a\.mp4.*1 of 1 cues identified"), re.compile(r"b\.mp4.*Failed")]
    )


def test_a_single_run_has_no_way_back_to_a_batch(page, runs):
    from tests.ui.helpers import make_run, open_run

    make_run(runs, [cue(1, "Song")])

    open_run(page)

    expect(page.locator("#batch-back")).to_be_hidden()


def test_a_batch_is_continued_from_its_page(page, fakes, runs):
    """Its films that failed or that it didn't get to, from the files it kept."""
    fakes.tracks = {0.0: ("Song", "Artist", {})}
    make_batch(
        runs,
        [
            ("a.mp4", "done", [cue(1, "Song")]),
            ("b.mp4", "failed", None),
            ("c.mp4", "queued", None),
        ],
    )
    open_batch(page)
    expect(page.locator(".batch-film").last).to_contain_text("Not analyzed")

    page.locator("#batch-continue").click()

    expect(page.locator("#batch-heading")).to_have_text(
        "Batch: 3 of 3 films analyzed", timeout=20_000
    )
    expect(page.locator(".batch-film")).to_have_text([re.compile("Open")] * 3)
    expect(page.locator("#batch-continue")).to_be_hidden()


def test_a_batchs_pending_cues_are_resumed_from_its_page(page, fakes, runs):
    fakes.tracks = {0.0: ("Song", "Artist", {})}
    pending = cue(1, pending=True, audio_file="segments/seg_001.wav")
    make_batch(runs, [("a.mp4", "done", [pending]), ("b.mp4", "done", [cue(1, "X")])])
    open_batch(page)
    films = page.locator(".batch-film")
    expect(films.first).to_contain_text("0 of 1 cues identified · 1 pending")

    page.locator("#batch-resume").click()

    expect(films.first).to_contain_text("1 of 1 cues identified", timeout=20_000)
    expect(page.locator("#batch-resume")).to_be_hidden()


def test_all_the_batchs_results_download_in_a_zip(page, runs):
    make_batch(
        runs,
        [
            ("a.mp4", "done", [cue(1, "Song")]),
            ("b.mp4", "failed", None),
            ("c.mp4", "done", [cue(1, "Other")]),
        ],
    )
    open_batch(page)
    menu = page.locator("#batch-download-options")
    expect(menu).to_be_hidden()

    page.locator("#batch-download-btn").click()
    expect(menu.get_by_role("menuitem")).to_have_text(
        ["JSON (zip)", "CSV (zip)", "VIAN projects (zip)", "MAVA .mediapkg (zip)"]
    )
    with page.expect_download() as info:
        menu.get_by_role("menuitem", name="CSV (zip)").click()

    assert re.fullmatch(r"batch_.*_results_csv\.zip", info.value.suggested_filename)
    archive = zipfile.ZipFile(io.BytesIO(Path(info.value.path()).read_bytes()))
    assert archive.namelist() == [
        "a_2026-11-11_12-00-00_results.csv",
        "c_2026-11-11_12-00-00_results.csv",
    ]


def test_a_failed_analysis_leads_back_to_the_start(page, fakes, video, runs):
    from soundtrackid.detection import ProviderAuthError

    def reject(segment):
        raise ProviderAuthError("ACRCloud rejected the API keys (error 3014)")

    fakes.detect = reject
    page.goto("/")
    page.locator("#file-input").set_input_files(video)
    page.locator("#fc-start").click()

    expect(page.locator("#error-area")).to_contain_text("rejected the API keys")
    expect(page.locator("#reconnect-btn")).to_be_hidden()
    page.locator("#progress-back").click()
    expect(page.locator("#drop-zone")).to_be_visible()


def test_a_lost_connection_can_be_followed_again(page, fakes, video, runs):
    """The analysis ran on: following it again shows where it is."""
    fakes.classifying.clear()  # the analysis waits after its first window
    page.route("**/jobs/*/events", lambda route: route.abort())
    page.goto("/")
    page.locator("#file-input").set_input_files(video)
    page.locator("#fc-start").click()
    expect(page.locator("#error-area")).to_contain_text("Lost connection")
    expect(page.locator("#progress-back")).to_be_visible()

    page.unroute("**/jobs/*/events")
    fakes.classifying.set()
    page.locator("#reconnect-btn").click()

    expect(page.locator("#results-view")).to_be_visible(timeout=20_000)
    expect(page.locator("#error-area")).to_be_hidden()


def test_what_another_process_analyzes_shows_as_under_way(page, runs):
    """E.g. the command line: a batch, and a run not saved yet, each holding
    its lock. Nothing to continue or delete meanwhile."""
    batch = make_batch(
        runs, [("a.mp4", "done", [cue(1, "Song")]), ("b.mp4", "analyzing", None)]
    )
    run = runs / "film_2026-11-11_10-00-00"
    run.mkdir()
    with FolderLock(batch.path), FolderLock(run):
        open_batch(page)

        expect(page.locator("#batch-heading")).to_contain_text(
            "under way in another process"
        )
        expect(page.locator(".batch-film").last).to_contain_text("Analyzing")
        expect(page.locator("#batch-continue")).to_be_hidden()
        page.locator("#progress-back").click()  # Recent runs still open
        row = page.locator(".history-item", has_text="film_2026-11-11_10-00-00")
        expect(row).to_contain_text("Being analyzed")
        row.hover()
        expect(row.locator(".history-delete-btn")).to_have_count(0)


def test_a_run_the_app_closed_on_is_listed_to_delete(page, runs):
    """It was never saved, so it can't be opened."""
    (runs / "film_2026-11-11_10-00-00" / "segments").mkdir(parents=True)
    page.goto("/")
    page.locator("#history-panel > summary").click()
    row = page.locator(".history-item")
    expect(row).to_contain_text("Interrupted")

    row.hover()
    row.locator(".history-delete-btn").click()

    expect(page.locator("#history-panel")).to_be_hidden()
    assert not (runs / "film_2026-11-11_10-00-00").exists()
