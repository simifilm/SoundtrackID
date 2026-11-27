"""Analyzing a video: from the file, through the film's confirmation, to the
results."""

import re
from datetime import datetime
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect

from soundtrackid.defaults import (
    BRIDGE_GAP_S,
    CLASSIFICATION_HOP_S,
    DEFAULT_API,
    MAX_SEGMENT_S,
    MUSIC_THRESHOLD,
)
from soundtrackid.web import analyze as analyze_routes
from tests.media import FPS
from tests.ui.helpers import cue, make_run, saved

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


def test_cancelling_goes_back_to_the_start_with_the_recent_runs(
    page, fakes, video, runs
):
    make_run(runs, [cue(1)])
    page.goto("/")
    upload(page, video)
    expect(page.locator("#history-panel")).to_be_hidden()

    page.locator("#fc-cancel").click()

    expect(page.locator("#film-confirm")).to_be_hidden()
    expect(page.locator("#drop-zone")).to_be_visible()
    expect(page.locator("#history-panel")).to_be_visible()


def test_look_up_online_lets_you_choose_the_film(page, fakes, tagged_video, runs):
    """The chosen film replaces the form, also a title the tags spell otherwise,
    and the analysis keeps all of it, also what the form doesn't show."""
    fakes.candidates = [
        {
            "tmdb_id": 599,
            "media_type": "movie",
            "title": "Sunset Blvd.",
            "year": 1950,
            "match": "tmdb_id",
        },
        {"tmdb_id": 42, "media_type": "movie", "title": "Sunset", "year": 1988},
    ]
    fakes.film = {
        "title": "Sunset Blvd.",
        "year": 1950,
        "director": "Billy Wilder",
        "director_death_year": 2002,
        "tmdb_id": 599,
    }
    page.goto("/")
    upload(page, tagged_video)

    page.locator("#fc-lookup").click()
    choices = page.locator("#fc-candidates button")
    expect(choices).to_have_count(2)
    expect(choices.first).to_contain_text("Sunset Blvd. (1950)")
    expect(choices.first).to_contain_text("Matches the TMDb ID")
    choices.first.click()

    expect(page.locator("#film-choose")).not_to_be_visible()
    expect(page.locator("#fc-title")).to_have_value("Sunset Blvd.")
    expect(page.locator("#fc-director")).to_have_value("Billy Wilder")
    expect(page.locator("#fc-status")).to_have_text(
        'Applied. Changed: Title "Sunset Boulevard" → "Sunset Blvd."; '
        'Director (empty) → "Billy Wilder"; TMDb ID (empty) → "599".'
    )
    # Looked up by the chosen film's ID alone
    assert fakes.film_hints == [
        {
            "imdb_id": None,
            "tmdb_id": 599,
            "title": None,
            "year": None,
            "media_type": "movie",
        }
    ]
    page.locator("#fc-start").click()
    expect(page.locator("#film-metadata-header")).to_contain_text("Sunset Blvd. (1950)")
    [run] = runs.iterdir()
    film = saved(run)["film"]
    assert (film["title"], film["director_death_year"]) == ("Sunset Blvd.", 2002)


def test_a_series_is_chosen_as_one_and_its_directors_linked(page, fakes, video):
    """Breaking Bad's TMDb ID is also a film's (Mirror)."""
    fakes.candidates = [
        {"tmdb_id": 1396, "media_type": "movie", "title": "Mirror", "year": 1975},
        {"tmdb_id": 1396, "media_type": "tv", "title": "Breaking Bad", "year": 2008},
    ]
    fakes.film = {
        "title": "Breaking Bad",
        "year": 2008,
        "director": "Michelle MacLaren, Adam Bernstein",
        "tmdb_id": 1396,
        "media_type": "tv",
    }
    page.goto("/")
    upload(page, video)

    page.locator("#fc-lookup").click()
    choices = page.locator("#fc-candidates button")
    expect(choices.first).to_contain_text("Film")
    expect(choices.last).to_contain_text("Series")
    choices.last.click()

    expect(page.locator("#fc-title")).to_have_value("Breaking Bad")
    assert fakes.film_hints[0]["media_type"] == "tv"
    expect(page.locator("#fc-tmdb-link")).to_have_attribute(
        "href", "https://www.themoviedb.org/tv/1396"
    )
    page.locator("#fc-start").click()
    header = page.locator("#film-metadata-header")
    expect(header).to_contain_text("Directors: Michelle MacLaren, Adam Bernstein")
    links = header.locator("a")
    expect(links.filter(has_text="Adam Bernstein")).to_have_attribute(
        "href", "https://en.wikipedia.org/wiki/Adam_Bernstein"
    )
    expect(links.filter(has_text="1396")).to_have_attribute(
        "href", "https://www.themoviedb.org/tv/1396"
    )


def test_a_typed_tmdb_id_is_no_longer_the_chosen_series(page, fakes, video):
    fakes.candidates = [
        {"tmdb_id": 1396, "media_type": "tv", "title": "Breaking Bad", "year": 2008}
    ]
    fakes.film = {
        "title": "Breaking Bad",
        "year": 2008,
        "tmdb_id": 1396,
        "media_type": "tv",
    }
    page.goto("/")
    upload(page, video)
    page.locator("#fc-lookup").click()
    page.locator("#fc-candidates button").click()
    link = page.locator("#fc-tmdb-link")
    expect(link).to_have_attribute("href", "https://www.themoviedb.org/tv/1396")

    page.locator("#fc-tmdb").fill("599")

    expect(link).to_have_attribute("href", "https://www.themoviedb.org/movie/599")
    page.locator("#fc-start").click()
    expect(
        page.locator("#film-metadata-header a").filter(has_text="599")
    ).to_have_attribute("href", "https://www.themoviedb.org/movie/599")


def test_the_id_fields_link_to_the_ids_pages(page, fakes, video):
    page.goto("/")
    upload(page, video)
    imdb = page.locator("#fc-imdb-link")
    expect(imdb).to_be_hidden()

    page.locator("#fc-imdb").fill("tt0043014")
    page.locator("#fc-tmdb").fill("599")

    expect(imdb).to_be_visible()
    expect(imdb).to_have_attribute("href", "https://www.imdb.com/title/tt0043014/")
    expect(page.locator("#fc-tmdb-link")).to_have_attribute(
        "href", "https://www.themoviedb.org/movie/599"
    )
    page.locator("#fc-imdb").fill("")
    expect(imdb).to_be_hidden()


def test_a_film_found_without_tmdb_is_applied_as_found(page, fakes, tagged_video):
    film = {"title": "Sunset Boulevard", "year": 1950, "director": "Billy Wilder"}
    fakes.candidates = [{"title": "Sunset Boulevard", "year": 1950, "film": film}]
    page.goto("/")
    upload(page, tagged_video)

    page.locator("#fc-lookup").click()
    page.locator("#fc-candidates button").click()

    expect(page.locator("#fc-director")).to_have_value("Billy Wilder")
    assert fakes.film_hints == []


def test_choosing_no_film_leaves_the_form(page, fakes, tagged_video):
    fakes.candidates = [{"tmdb_id": 42, "title": "Sunset", "year": 1988}]
    page.goto("/")
    upload(page, tagged_video)

    page.locator("#fc-lookup").click()
    page.locator("#fc-choose-cancel").click()

    expect(page.locator("#film-choose")).not_to_be_visible()
    expect(page.locator("#fc-title")).to_have_value("Sunset Boulevard")
    expect(page.locator("#fc-status")).to_have_text(
        "Nothing chosen; the form is unchanged."
    )


def test_look_up_online_finding_nothing_says_so(page, fakes, tagged_video):
    page.goto("/")
    upload(page, tagged_video)

    page.locator("#fc-lookup").click()

    expect(page.locator("#fc-status")).to_have_text(
        "Nothing found online. Please fill in manually."
    )
    expect(page.locator("#film-choose")).not_to_be_visible()


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


def test_the_page_starts_from_the_analysis_defaults(page, fakes, video, runs):
    """The ones the CLI and the server use, so all analyze alike."""
    page.goto("/")
    upload(page, video)

    page.locator("#fc-start").click()

    expect(page.locator("#results-view")).to_be_visible(timeout=20_000)
    [run] = runs.iterdir()
    results = saved(run)
    assert results["providers"] == [DEFAULT_API]
    settings = results["settings"]
    assert (settings["threshold"], settings["hop"]) == (
        MUSIC_THRESHOLD,
        CLASSIFICATION_HOP_S,
    )
    assert (settings["bridge_gap"], settings["max_segment_duration"]) == (
        BRIDGE_GAP_S,
        MAX_SEGMENT_S,
    )


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


def test_the_run_keeps_the_videos_frame_rate(page, fakes, video, runs):
    analyze(page, video)

    [run] = runs.iterdir()
    assert saved(run)["video"]["fps"] == FPS  # the arrow keys step by its frames


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


def test_a_reloaded_page_shows_the_analysis_under_way(page, fakes, video, runs):
    """Where it is, instead of the recent runs, and on to its results."""
    make_run(runs, [cue(1)], name="other_2026-11-11_11-00-00")
    fakes.classifying.clear()  # the analysis waits after its first window
    page.goto("/")
    upload(page, video)
    page.locator("#fc-start").click()
    classifying = page.locator('.step[data-step="classifying"]')
    expect(classifying).to_have_class(re.compile("active"))

    page.reload()

    expect(classifying).to_have_class(re.compile("active"))
    expect(page.locator('.step[data-step="loading"]')).to_have_class(re.compile("done"))
    expect(page.locator("#drop-zone")).to_be_hidden()
    expect(page.locator("#history-panel")).to_be_hidden()
    fakes.classifying.set()
    expect(page.locator("#results-view")).to_be_visible(timeout=20_000)


def test_an_analysis_can_be_cancelled_after_a_reload(page, fakes, video, runs):
    fakes.classifying.clear()  # the analysis waits after its first window
    page.goto("/")
    upload(page, video)
    page.locator("#fc-start").click()
    classifying = page.locator('.step[data-step="classifying"]')
    expect(classifying).to_have_class(re.compile("active"))
    page.reload()
    expect(classifying).to_have_class(re.compile("active"))

    page.locator("#cancel-btn").click()
    expect(page.locator("#cancel-btn")).to_have_text("Cancelling…")
    fakes.classifying.set()

    expect(page.locator("#drop-zone")).to_be_visible()
    expect(page.locator("#progress-view")).to_be_hidden()
    assert list(runs.iterdir()) == []


def test_a_refused_analysis_shows_why(page, fakes, video):
    page.goto("/")
    upload(page, video)
    analyze_routes._staging.clear()  # the staged upload expired meanwhile

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
        "soundtrackid.analysis.datetime", SimpleNamespace(now=lambda: next(times))
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
