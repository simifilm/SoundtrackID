"""The settings: API keys and the providers that need them."""

import json

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def open_api_keys(page):
    page.goto("/")
    page.locator("#settings-panel > summary").click()
    page.locator("#api-keys > summary").click()


def test_an_api_key_is_saved_and_shown_masked(page, fakes, tmp_path):
    open_api_keys(page)
    key = page.locator('input[data-key="acrcloud_access_key"]')
    expect(key).to_have_attribute("placeholder", "not set")

    key.fill("secret-1234")
    page.locator("#save-keys").click()

    expect(page.locator("#keys-status")).to_have_text(
        "Saved. New analyses will use these keys."
    )
    expect(key).to_have_value("")
    expect(key).to_have_attribute("placeholder", "••••1234")
    stored = json.loads((tmp_path / "settings.json").read_text())
    assert stored == {"acrcloud_access_key": "secret-1234"}


def test_saving_without_a_new_key_saves_nothing(page, fakes, tmp_path):
    open_api_keys(page)

    page.locator("#save-keys").click()

    expect(page.locator("#keys-status")).to_have_text("Nothing to save.")
    assert not (tmp_path / "settings.json").exists()


def test_a_provider_without_its_keys_is_flagged(page, fakes):
    page.goto("/")
    page.locator("#settings-panel > summary").click()
    warning = page.locator("#provider-key-warning")
    expect(warning).to_be_hidden()  # Shazam needs no keys

    page.locator('.opt-provider[value="audd"]').check()

    expect(warning).to_have_text(
        "API keys missing for AudD. Add them under API keys below."
    )
    page.locator('.opt-provider[value="audd"]').uncheck()
    expect(warning).to_be_hidden()
