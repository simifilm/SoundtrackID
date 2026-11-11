"""Editing the identified tracks: corrections are saved to the run."""

import re

import pytest
from playwright.sync_api import expect

from tests.ui.conftest import cue, make_run, open_run, saved

pytestmark = pytest.mark.browser

COMPOSER = {
    "composer": "Glenn Miller",
    "composer_death_year": 1944,
    "authors": [
        {"name": "Glenn Miller", "roles": ["composer"], "death_year": 1944},
        {"name": "Mitchell Parish", "roles": ["lyricist"], "death_year": 1993},
    ],
    "latest_author_death_year": 1993,
    "public_domain": False,
    "original_year": 1939,
}


@pytest.fixture
def run(runs):
    return make_run(
        runs,
        [
            cue(1, "Moonlight Serenade", enrichment=dict(COMPOSER)),
            cue(2, "Other Song"),
        ],
    )


def edit(page, track, field, value, key="Enter"):
    """Double-click a track's field, type `value` and press `key`."""
    row = page.locator("#tracks-body tr:not(.cue-detail)", has_text=track)
    row.locator(f'td[data-field="{field}"]').dblclick()
    page.locator("input.cell-edit").fill(value)
    page.locator("input.cell-edit").press(key)


def edit_and_save(page, track, field, value):
    with page.expect_response(
        lambda r: r.request.method == "POST" and "/results/" in r.url
    ):
        edit(page, track, field, value)


@pytest.mark.parametrize(
    "field, value, stored",
    [
        ("title", "Moonlight Serenade (1939)", lambda c: c["title"]),
        ("artist", "Glenn Miller Orchestra", lambda c: c["artist"]),
        ("album", "The Best Of", lambda c: c["album"]),
        ("date", "1938", lambda c: c["enrichment"]["original_year"]),
    ],
)
def test_an_edited_field_is_saved(page, run, field, value, stored):
    open_run(page)

    edit_and_save(page, "Moonlight Serenade", field, value)

    expect(page.locator(f'#tracks-body td[data-field="{field}"]').first).to_have_text(
        value
    )
    assert str(stored(saved(run)["cues"][0])) == value


def test_a_hand_edited_composer_drops_the_life_dates_found_before(page, run):
    """They were someone else's; the lyricist found before stays, and their
    death in 1993 keeps the work protected, which the page shows at once."""
    open_run(page)

    edit_and_save(page, "Moonlight Serenade", "composer", "Someone Else")

    enrichment = saved(run)["cues"][0]["enrichment"]
    assert enrichment["composer"] == "Someone Else"
    assert enrichment["composer_death_year"] is None
    assert enrichment["public_domain"] is False
    names = [a["name"] for a in enrichment["authors"]]
    assert names == ["Someone Else", "Mitchell Parish"]
    row = page.locator("#tracks-body tr", has_text="Moonlight Serenade")
    expect(row.locator(".rights-pill")).to_have_text("Protected")


def test_a_date_after_the_film_marks_the_track_as_later(page, runs):
    """Whether a track postdates the film follows from its date: the server
    works it out when the edit is saved, and the page shows it."""
    make_run(
        runs,
        [cue(1, "Moonlight Serenade", enrichment=dict(COMPOSER))],
        film={"title": "Film", "year": 1950},
    )
    open_run(page)
    row = page.locator("#tracks-body tr", has_text="Moonlight Serenade")
    expect(row).not_to_have_class(re.compile(r"\banachronistic\b"))

    edit_and_save(page, "Moonlight Serenade", "date", "1975")

    expect(row).to_have_class(re.compile(r"\banachronistic\b"))


def test_escape_keeps_the_value(page, run):
    open_run(page)

    edit(page, "Moonlight Serenade", "title", "Typo", key="Escape")

    expect(page.locator("input.cell-edit")).to_have_count(0)
    expect(page.locator("#tracks-body")).to_contain_text("Moonlight Serenade")
    assert saved(run)["cues"][0]["title"] == "Moonlight Serenade"


def test_removing_a_track(page, run):
    open_run(page)
    row = page.locator("#tracks-body tr", has_text="Moonlight Serenade")

    with page.expect_response(
        lambda r: r.request.method == "POST" and "/results/" in r.url
    ):
        row.hover()
        row.locator(".track-remove").click()

    expect(page.locator("#tracks-body")).not_to_contain_text("Moonlight Serenade")
    assert [c["title"] for c in saved(run)["cues"]] == ["Other Song"]
