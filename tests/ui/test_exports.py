"""Downloading a run's results: JSON, CSV and the VIAN and MAVA exports."""

import csv
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import expect

from soundtrackid.derived import refresh
from tests.ui.helpers import cue, make_run, open_run, saved

pytestmark = pytest.mark.browser


@pytest.fixture
def run(runs):
    return make_run(runs, [cue(1, "Moonlight Serenade"), cue(2)])


def download(page, click):
    """Click (a function) and return the downloaded file's name and content."""
    with page.expect_download() as info:
        click()
    return info.value.suggested_filename, Path(info.value.path()).read_bytes()


def test_the_download_menu_offers_json_and_csv_of_the_same_results(page, run):
    open_run(page)
    menu = page.locator("#download-options")
    expect(menu).to_be_hidden()

    page.locator("#download-btn").click()
    expect(menu).to_be_visible()
    name, content = download(page, menu.locator('[data-format="json"]').click)

    assert name == f"{run.name}_results.json"
    # As the app shows them: with what follows from them as of now.
    assert json.loads(content) == refresh(saved(run))
    expect(menu).to_be_hidden()
    page.locator("#download-btn").click()
    name, content = download(page, menu.locator('[data-format="csv"]').click)
    assert name == f"{run.name}_results.csv"
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
    assert [r["title"] for r in rows] == ["Moonlight Serenade", ""]


def test_a_click_elsewhere_closes_the_download_menu(page, run):
    open_run(page)
    page.locator("#download-btn").click()

    page.locator("header").click()

    expect(page.locator("#download-options")).to_be_hidden()


def test_one_menu_is_open_at_a_time(page, run):
    open_run(page)
    page.locator("#download-btn").click()

    page.locator("#export-btn").click()

    expect(page.locator("#export-options")).to_be_visible()
    expect(page.locator("#download-options")).to_be_hidden()
    page.locator("#export-btn").click()
    expect(page.locator("#export-options")).to_be_hidden()


def test_the_mava_export_downloads_a_mediapkg(page, run):
    open_run(page)
    menu = page.locator("#export-options")
    expect(menu).to_be_hidden()

    page.locator("#export-btn").click()
    name, content = download(page, page.locator("#mava-export-btn").click)

    assert name == f"{run.name}.mediapkg"
    files = zipfile.ZipFile(io.BytesIO(content)).namelist()
    assert set(files) == {"manifest.json", f"{run.name}/music_cues.parquet"}
    expect(menu).to_be_hidden()


def test_the_vian_export_downloads_a_project_for_the_runs_video(page, runs, video):
    run = make_run(
        runs,
        [cue(1, "Moonlight Serenade")],
        video={"file": "source.mp4", "filmstrip": None, "fps": 10},
    )
    shutil.copy(video, run / "source.mp4")
    open_run(page)

    page.locator("#export-btn").click()
    name, content = download(page, page.locator("#vian-export-btn").click)

    assert name == f"{run.name}_vian_project.zip"
    files = zipfile.ZipFile(io.BytesIO(content)).namelist()
    assert sorted(files) == ["main.json", "undoable.json"]


def test_without_its_video_a_run_has_no_vian_export(page, run):
    open_run(page)

    page.locator("#export-btn").click()

    expect(page.locator("#vian-export-btn")).to_be_hidden()
    expect(page.locator("#mava-export-btn")).to_be_visible()
