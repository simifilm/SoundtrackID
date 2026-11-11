"""Analyzing a video: from the file, through the film's confirmation, to the
results."""

import re
from datetime import datetime
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect

from soundtrackID import app as app_module
from tests.ui.conftest import saved

pytestmark = pytest.mark.browser

MOONLIGHT = (
    "Moonlight Serenade",
    "Glenn Miller",
    {"album": "Hits", "isrc": "US1", "release_year": 1939},
)
LATER = ("Later Song", "Someone", {"isrc": "US2", "release_year": 2020})
GLENN_MILLER = {
    "composer": "Glenn Miller",
    "composer_match": "isrc",
    "authors": [
        {
            "name": "Glenn Miller",
            "roles": ["composer"],
            "birth_year": 1904,
            "death_year": 1944,
            "wikidata_url": None,
        }
    ],
    "latest_author_death_year": 1944,
    "public_domain": True,
}


def upload(page, video):
    """Pick the file; returns once the film's confirmation is filled in."""
    page.locator("#file-input").set_input_files(video)
    expect(page.locator("#fc-status")).to_contain_text("Please")


def analyze(page, video, year="1950", chunk="10"):
    """Analyze the video as a film of this year, its music identified in pieces
    of `chunk` seconds; returns once the results show."""
    page.goto("/")
    page.locator("#settings-panel > summary").click()
    page.locator("#opt-chunk").select_option(chunk)
    upload(page, video)
    page.locator("#fc-year").fill(year)
    page.locator("#fc-start").click()
    expect(page.locator("#results-view")).to_be_visible(timeout=20_000)


def test_the_film_is_prefilled_from_the_files_tags(page, fakes, tagged_video):
    page.goto("/")

    upload(page, tagged_video)

    expect(page.locator("#fc-title")).to_have_value("Sunset Boulevard")
    expect(page.locator("#fc-year")).to_have_value("1950")
    expect(page.locator("#fc-status")).to_have_text(
        "Prefilled from file tags. Please review, especially the year."
    )


def test_look_up_online_fills_only_the_empty_fields(page, fakes, tagged_video, runs):
    """And the analysis keeps all it found, also what the form doesn't show."""
    fakes.film = {
        "title": "Sunset Blvd.",
        "year": 1950,
        "director": "Billy Wilder",
        "director_death_year": 2002,
    }
    page.goto("/")
    upload(page, tagged_video)

    page.locator("#fc-lookup").click()

    expect(page.locator("#fc-status")).to_have_text(
        "Online data applied (empty fields filled)."
    )
    expect(page.locator("#fc-title")).to_have_value("Sunset Boulevard")
    expect(page.locator("#fc-director")).to_have_value("Billy Wilder")
    page.locator("#fc-start").click()
    expect(page.locator("#film-metadata-header")).to_contain_text(
        "Sunset Boulevard (1950)"
    )
    [run] = runs.iterdir()
    film = saved(run)["film"]
    assert (film["title"], film["director_death_year"]) == ("Sunset Boulevard", 2002)


def test_the_results_show_tracks_timeline_and_classes(page, fakes, video):
    fakes.tracks = {0.0: MOONLIGHT, 10.0: MOONLIGHT, 20.0: LATER}
    fakes.composers = {"US1": GLENN_MILLER}

    analyze(page, video, year="1950", chunk="10")

    tracks = page.locator("#tracks-body tr:not(.cue-detail)")
    expect(tracks).to_have_count(2)
    moonlight = tracks.filter(has_text="Moonlight Serenade")
    expect(moonlight.locator('td[data-field="composer"]')).to_have_text("Glenn Miller")
    expect(moonlight.locator(".rights-pill")).to_have_text("Public domain")
    expect(page.locator("#waveform-container")).to_be_visible()
    page.locator('.results-tab[data-view="timeline"]').click()
    expect(page.locator("#results-body tr")).to_have_count(2)
    page.locator('.results-tab[data-view="classes"]').click()
    expect(page.locator("#classes-body tr")).to_have_count(5)
    expect(page.locator("#ratelimit-banner")).to_be_hidden()


def test_the_analysis_uses_the_chosen_settings(page, fakes, video, runs):
    page.goto("/")
    page.locator("#settings-panel > summary").click()
    page.locator("#opt-chunk").select_option("15")
    page.locator("#opt-hop").select_option("2.5")
    page.locator("#opt-bridge").select_option("2")
    page.locator("#opt-threshold").fill("35")
    expect(page.locator("#opt-threshold-val")).to_have_text("35%")
    upload(page, video)

    page.locator("#fc-start").click()

    expect(page.locator("#results-view")).to_be_visible(timeout=20_000)
    [run] = runs.iterdir()
    results = saved(run)
    assert results["providers"] == ["shazam"]
    settings = results["settings"]
    assert (settings["threshold"], settings["hop"]) == (0.35, 2.5)
    assert settings["bridge_gap"] == 5.0  # 2 hops of 2.5 s
    assert settings["max_segment_duration"] == 15.0


def test_whole_music_segment_identifies_each_stretch_of_music_in_one_piece(
    page, fakes, video, runs
):
    fakes.tracks = {0.0: MOONLIGHT}

    analyze(page, video, chunk="0")

    [run] = runs.iterdir()
    results = saved(run)
    assert results["settings"]["max_segment_duration"] is None
    [cue] = results["cues"]
    assert (cue["title"], cue["start"], cue["duration"]) == (
        "Moonlight Serenade",
        0.0,
        30.0,
    )


def test_the_finished_run_is_in_recent_runs(page, fakes, video, runs):
    analyze(page, video)

    [run] = runs.iterdir()
    page.locator("#history-panel > summary").click()
    expect(page.locator(".history-item")).to_have_text([re.compile(run.name)])


def test_cancel_stops_the_analysis_and_keeps_nothing(page, fakes, video, runs):
    fakes.classifying.clear()  # the analysis waits after its first window
    page.goto("/")
    upload(page, video)
    page.locator("#fc-start").click()
    expect(page.locator('.step[data-step="classifying"]')).to_have_class(
        re.compile("active")
    )

    page.locator("#cancel-btn").click()
    expect(page.locator("#cancel-btn")).to_have_text("Cancelling…")
    fakes.classifying.set()

    expect(page.locator("#drop-zone")).to_be_visible()
    expect(page.locator("#progress-view")).to_be_hidden()
    assert list(runs.iterdir()) == []


def test_a_refused_analysis_shows_why(page, fakes, video):
    page.goto("/")
    upload(page, video)
    app_module._staging.clear()  # the staged upload expired meanwhile

    page.locator("#fc-start").click()

    expect(page.locator("#error-area")).to_have_text(
        "ERROR: Staged upload not found or expired"
    )


def test_a_provider_needs_its_api_keys(page, fakes, video):
    page.goto("/")
    page.locator("#settings-panel > summary").click()
    page.locator('.opt-provider[value="acrcloud"]').check()
    upload(page, video)
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))

    page.locator("#fc-start").click()

    expect(page.locator("#api-keys")).to_have_attribute("open", "")
    assert dialogs == [
        "API keys missing for ACRCloud. Add them under Settings → API keys, "
        "or unselect the provider."
    ]
    expect(page.locator("#film-confirm")).to_be_visible()  # not started


def test_rerun_analyzes_the_stored_video_again_as_a_new_run(
    page, fakes, video, runs, monkeypatch
):
    times = iter([datetime(2026, 11, 11, 10, 0, 0), datetime(2026, 11, 11, 10, 5, 0)])
    monkeypatch.setattr(
        "soundtrackID.analysis.datetime", SimpleNamespace(now=lambda: next(times))
    )
    analyze(page, video)

    with page.expect_request_finished(lambda request: "/rerun/" in request.url):
        page.locator("#rerun-btn").click()

    expect(page.locator("#results-view")).to_be_visible()
    assert sorted(run.name for run in runs.iterdir()) == [
        "film_2026-11-11_10-00-00",
        "film_2026-11-11_10-05-00",
    ]
    assert saved(runs / "film_2026-11-11_10-05-00")["video"]["file"] == "source.mp4"
