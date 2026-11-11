"""Downloading a run's results: JSON, CSV and the MAVA export."""

import csv
import io
import json
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.conftest import cue, make_run, open_run, saved

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
    assert json.loads(content) == saved(run)
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


def test_the_mava_export_downloads_its_tsv_and_mapping(page, run):
    open_run(page)

    name, content = download(page, page.locator("#mava-export-btn").click)

    assert name == f"{run.name}_mava_export.zip"
    files = zipfile.ZipFile(io.BytesIO(content)).namelist()
    assert sorted(files) == ["mava_annotations.tsv", "mava_mapping.json"]
