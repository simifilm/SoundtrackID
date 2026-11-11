"""Tests for the CSV export.

Covers models.results_to_csv, ResultsOutput.to_csv (the CLI's --csv) and
GET /output/{video_name}/csv (the UI's download).
"""

import csv
import io
import json

from fastapi.testclient import TestClient

from soundtrackID import app as app_module
from soundtrackID.models import ResultsOutput, results_to_csv

client = TestClient(app_module.app)

CUES = [
    {
        "segment_id": "seg_001",
        "start": 12.5,
        "duration": 30.0,
        "provider": "ensemble",
        "agreement": {"count": 2, "total": 3},
        "title": "Diane",
        "artist": "Erno Rapee",
        "album": "OST",
        "release_year": 1927,
        "enrichment": {
            "original_year": 1927,
            "composer": "Erno Rapee",
            "authors": [
                {
                    "name": "Erno Rapee",
                    "roles": ["composer"],
                    "birth_year": 1891,
                    "death_year": 1945,
                },
                {
                    "name": "Lew Pollack",
                    "roles": ["lyricist"],
                    "birth_year": None,
                    "death_year": 1946,
                },
            ],
            "latest_author_death_year": 1946,
            "public_domain": True,
        },
    },
    {
        "segment_id": "seg_002",
        "start": 60.0,
        "duration": 5.0,
        "title": None,
        "agreement": None,
        "enrichment": None,
    },
]


def _rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_csv_has_a_column_for_every_field_of_the_json():
    """In the JSON's order; nested fields as "parent.field", lists as JSON."""
    diane, unidentified = _rows(results_to_csv({"cues": CUES}))

    assert list(diane) == [
        "segment_id",
        "start",
        "duration",
        "provider",
        "agreement.count",
        "agreement.total",
        "title",
        "artist",
        "album",
        "release_year",
        "enrichment.original_year",
        "enrichment.composer",
        "enrichment.authors",
        "enrichment.latest_author_death_year",
        "enrichment.public_domain",
    ]
    assert (diane["start"], diane["agreement.count"]) == ("12.5", "2")
    assert diane["enrichment.composer"] == "Erno Rapee"
    authors = json.loads(diane["enrichment.authors"])
    assert authors == CUES[0]["enrichment"]["authors"]
    # Fields the unidentified cue lacks or has empty stay empty.
    assert (unidentified["title"], unidentified["enrichment.composer"]) == ("", "")


def test_pipeline_results_as_csv():
    """What the CLI writes with --csv."""
    cue = {"segment_id": "seg_001", "title": "T", "artist": "A", "album": "Al"}
    results = ResultsOutput("film.mp4", [cue])

    [row] = _rows(results.to_csv())

    assert (row["segment_id"], row["title"], row["album"]) == ("seg_001", "T", "Al")


def test_download_holds_the_saved_results(tmp_path, monkeypatch):
    """Also after edits: the CSV holds what the JSON holds now."""
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run_dir = tmp_path / "myrun"
    run_dir.mkdir()
    (run_dir / "results.json").write_text(json.dumps({"cues": CUES}))
    client.post("/results/myrun", json={"cues": [{**CUES[0], "title": "Edited"}]})

    r = client.get("/output/myrun/csv")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == (
        'attachment; filename="myrun_results.csv"'
    )
    assert [row["title"] for row in _rows(r.text)] == ["Edited"]


def test_download_of_a_missing_run_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    assert client.get("/output/nope/csv").status_code == 404
