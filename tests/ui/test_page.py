"""The page as it opens."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def test_page_opens_ready_for_a_file(page, fakes):
    page.goto("/")

    expect(page.locator("#drop-zone")).to_be_visible()
    expect(page.locator("#history-panel")).to_be_hidden()  # no runs yet
    expect(page.locator("#results-view")).to_be_hidden()


def menu_opens(page, locator):
    """Whether right-clicking there leaves the browser its menu (Save as, …)."""
    page.evaluate(
        "addEventListener('contextmenu', e => window.menuOpens = !e.defaultPrevented)"
    )
    locator.click(button="right")
    return page.evaluate("window.menuOpens")


def test_right_clicking_the_page_opens_no_menu(page, fakes):
    page.goto("/")

    assert not menu_opens(page, page.locator("h1"))


def test_right_clicking_a_text_field_keeps_its_menu(page, fakes):
    """For pasting an API key, say."""
    page.goto("/")
    page.locator("#settings-panel > summary").click()
    page.locator("#api-keys > summary").click()

    assert menu_opens(page, page.locator('input[data-key="audd_api_token"]'))
