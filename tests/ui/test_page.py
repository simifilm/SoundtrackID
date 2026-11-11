"""The page as it opens."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def test_page_opens_ready_for_a_file(page, fakes):
    page.goto("/")

    expect(page.locator("#drop-zone")).to_be_visible()
    expect(page.locator("#history-panel")).to_be_hidden()  # no runs yet
    expect(page.locator("#results-view")).to_be_hidden()
