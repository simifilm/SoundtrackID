"""How the results show: dates, rights and text from outside sources."""

import re

import pytest
from playwright.sync_api import expect

from tests.ui.conftest import cue, make_run, open_run

pytestmark = pytest.mark.browser

ANACHRONISTIC = re.compile(r"\banachronistic\b")


def test_matches_released_after_the_film_are_marked_and_can_be_hidden(page, runs):
    """More than 3 years after the film: likely a later work, not the score."""
    make_run(
        runs,
        [
            cue(1, "Old Song", release_year=1939),
            cue(2, "Later Song", release_year=2020),
        ],
        film={"title": "Film", "year": 1950},
    )
    open_run(page)
    later = page.locator("#tracks-body tr", has_text="Later Song")
    old = page.locator("#tracks-body tr", has_text="Old Song")
    expect(later).to_have_class(ANACHRONISTIC)
    expect(old).not_to_have_class(ANACHRONISTIC)

    page.locator("#opt-hide-anachronism").check()

    expect(page.locator("#anachronism-toggle-label")).to_have_text(
        "Hide 1 temporally implausible match"
    )
    expect(later).to_be_hidden()
    expect(old).to_be_visible()
    page.locator('.results-tab[data-view="timeline"]').click()
    expect(page.locator("#results-body tr", has_text="Later Song")).to_be_hidden()


def test_a_track_shows_its_authorship_and_copyright(page, runs):
    authors = [
        {"name": "Glenn Miller", "roles": ["composer"], "death_year": 1944},
    ]
    enrichment = {
        "composer": "Glenn Miller",
        "authors": authors,
        "latest_author_death_year": 1944,
        "public_domain": True,
    }
    make_run(runs, [cue(1, "Moonlight Serenade", enrichment=enrichment)])
    open_run(page)
    detail = page.locator("#tracks-body tr.cue-detail")
    expect(detail).to_be_hidden()

    page.locator("#tracks-body .rights-pill").click()

    expect(detail).to_contain_text("Glenn Miller")
    expect(detail).to_contain_text("d. 1944")
    expect(detail).to_contain_text("Public domain since 2015.")


def test_text_from_providers_is_shown_as_text_not_as_html(page, runs):
    """Provider, MusicBrainz and Wikidata data is open to anyone's edits."""
    title = '<img src=x onerror="window.injected = true">'
    make_run(runs, [cue(1, title, youtube_link="javascript:window.injected=true")])

    open_run(page)

    expect(page.locator('#tracks-body td[data-field="title"]')).to_have_text(title)
    expect(page.locator("#tracks-body img")).to_have_count(0)
    expect(page.locator("#tracks-body a.track-link")).to_have_count(0)
    assert page.evaluate("window.injected") is None
