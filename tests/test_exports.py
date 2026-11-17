"""Tests for the exports (exports.py).

Covers exports.results_to_csv, which the CLI's --csv also writes with, and
GET /output/{video_name}/csv (the UI's download); and the MAVA/VIAN-Export
compatible export and GET /output/{video_name}/mava.
"""

import csv
import io
import json
import zipfile

from fastapi.testclient import TestClient

from soundtrackid.exports import build_mava_mapping, build_mava_tsv, results_to_csv
from soundtrackid.web.app import app

client = TestClient(app)

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


def test_download_holds_the_saved_results(runs_dir):
    """Also after edits: the CSV holds what the JSON holds now."""
    run_dir = runs_dir / "myrun"
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


def test_download_of_a_missing_run_is_404(runs_dir):
    assert client.get("/output/nope/csv").status_code == 404


# ── MAVA ───────────────────────────────────────────────────────────────────


def test_build_mava_tsv_uses_title_artist_label():
    cues = [
        {
            "start": 12.5,
            "duration": 30.0,
            "title": "Main Theme",
            "artist": "John Williams",
        },
        {"start": 100.0, "duration": 5.0, "title": None, "artist": None},
    ]
    tsv = build_mava_tsv(cues)
    lines = tsv.strip("\n").split("\n")
    assert lines[0].split("\t") == [
        "start_in_seconds",
        "duration_in_seconds",
        "annotation",
    ]
    assert lines[1] == "12.5\t30.0\tMain Theme — John Williams"
    assert lines[2] == "100.0\t5.0\tUnidentified music"


def test_build_mava_mapping_matches_import_tsv_contract():
    mapping = build_mava_mapping("my_video")
    assert mapping["time_column"] == "start_in_seconds"
    assert mapping["duration_column"] == "duration_in_seconds"
    assert mapping["value_column"] == "annotation"
    assert mapping["value_type"] == "string"
    assert "my_video" in mapping["series_description"]


def test_export_endpoint_returns_zip_with_tsv_and_mapping(runs_dir):
    run_dir = runs_dir / "myrun"
    run_dir.mkdir()
    (run_dir / "results.json").write_text(
        json.dumps(
            {
                "cues": [{"start": 1.0, "duration": 2.0, "title": "T", "artist": "A"}],
            }
        )
    )

    r = client.get("/output/myrun/mava")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    assert names == {"mava_annotations.tsv", "mava_mapping.json"}
    tsv_body = zf.read("mava_annotations.tsv").decode("utf-8")
    assert "T — A" in tsv_body
    mapping = json.loads(zf.read("mava_mapping.json"))
    assert mapping["time_column"] == "start_in_seconds"


def test_export_endpoint_missing_run_is_404(runs_dir):
    r = client.get("/output/nope/mava")
    assert r.status_code == 404
