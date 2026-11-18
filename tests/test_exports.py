"""Tests for the exports (exports.py).

Covers exports.results_to_csv, which the CLI's --csv also writes with, and
GET /output/{video_name}/csv (the UI's download); and the VIAN project and MAVA
.mediapkg exports, GET /output/{video_name}/vian and .../mediapkg.
"""

import csv
import io
import json
import zipfile

import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from soundtrackid.exports import build_mediapkg, build_vian_project, results_to_csv
from soundtrackid.web.app import app
from tests.media import FPS, write_video

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


# ── VIAN and MAVA ──────────────────────────────────────────────────────────

# Not in time order: the exports put them in it.
EXPORT_CUES = [
    {"start": 100.0, "duration": 5.0, "title": None, "artist": None},
    {"start": 12.5, "duration": 30.0, "title": "Main Theme", "artist": "John Williams"},
]
VIDEO = {"fps": 24.0, "frames": 2880, "width": 640, "height": 360, "duration": 120.0}


def _unzip(content):
    return zipfile.ZipFile(io.BytesIO(content))


def _run_with_a_cue(runs_dir):
    """A run with a cue from 1 s to 3 s, of a film.mp4 (not kept)."""
    run_dir = runs_dir / "myrun"
    run_dir.mkdir()
    cue = {"start": 1.0, "duration": 2.0, "title": "T", "artist": "A"}
    results = {"source": "film.mp4", "cues": [cue]}
    (run_dir / "results.json").write_text(json.dumps(results))
    return run_dir


def test_vian_project_holds_the_cues_as_a_track_in_frames():
    zf = _unzip(build_vian_project(EXPORT_CUES, VIDEO))

    # VIAN's two stores, at the root
    assert sorted(zf.namelist()) == ["main.json", "undoable.json"]
    assert json.loads(zf.read("main.json")) == {
        "fps": 24.0,
        "numFrames": 2880,
        "videoDuration": 120.0,
        "width": 640,
        "height": 360,
    }
    undoable = json.loads(zf.read("undoable.json"))
    assert undoable["subtitles"] is None  # else VIAN's import links a subtitles file
    [track] = undoable["timelines"]
    assert track["type"] == "shots"
    assert [(s["start"], s["end"], s["annotation"]) for s in track["data"]] == [
        (300, 1020, "Main Theme — John Williams"),
        (2400, 2520, "Unidentified music"),
    ]


def test_mediapkg_holds_the_cues_as_an_annotation_series():
    zf = _unzip(build_mediapkg(EXPORT_CUES, "my_video", "my_video.mp4", VIDEO))

    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["version"] == "0.2"
    track = manifest["tracks"]["music_cues"]
    assert track["type"] == "mava:AnnotationSeries"
    assert manifest["videos"] == [
        {
            "id": "my_video",
            "src": "my_video.mp4",
            "width": 640,
            "height": 360,
            "fps": 24.0,
            "duration_seconds": 120.0,
            "files": {"music_cues": "my_video/music_cues.parquet"},
        }
    ]
    table = pq.read_table(io.BytesIO(zf.read("my_video/music_cues.parquet")))
    # The declared columns, in their order, each mapped to the ontology
    assert table.column_names == track["columns"]
    assert set(track["columns"]) <= set(manifest["context"]["@context"])
    assert table.to_pydict() == {
        "start_seconds": [12.5, 100.0],
        "end_seconds": [42.5, 105.0],
        "annotations": ["Main Theme — John Williams", "Unidentified music"],
    }


def test_mediapkg_without_the_video_leaves_out_its_frames_and_size():
    zf = _unzip(build_mediapkg(EXPORT_CUES, "my_video", "my_video.mp4", None))

    [video] = json.loads(zf.read("manifest.json"))["videos"]
    assert set(video) == {"id", "src", "files"}


def test_vian_export_counts_in_the_frames_of_the_runs_video(runs_dir):
    run_dir = _run_with_a_cue(runs_dir)
    write_video(run_dir / "source.mp4", seconds=2)

    r = client.get("/output/myrun/vian")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == (
        'attachment; filename="myrun_vian_project.zip"'
    )
    zf = _unzip(r.content)
    main = json.loads(zf.read("main.json"))
    assert (main["fps"], main["numFrames"]) == (FPS, 2 * FPS)
    [segment] = json.loads(zf.read("undoable.json"))["timelines"][0]["data"]
    assert (segment["start"], segment["end"]) == (1 * FPS, 3 * FPS)


def test_vian_export_needs_the_runs_video(runs_dir):
    _run_with_a_cue(runs_dir)

    assert client.get("/output/myrun/vian").status_code == 404


def test_mediapkg_export_names_the_film_and_its_video(runs_dir):
    run_dir = _run_with_a_cue(runs_dir)
    write_video(run_dir / "source.mp4", seconds=2)

    r = client.get("/output/myrun/mediapkg")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.mava.mediapkg+zip"
    assert r.headers["content-disposition"] == 'attachment; filename="myrun.mediapkg"'
    [video] = json.loads(_unzip(r.content).read("manifest.json"))["videos"]
    assert (video["id"], video["src"], video["fps"]) == ("myrun", "film.mp4", FPS)


def test_exports_of_a_missing_run_are_404(runs_dir):
    assert client.get("/output/nope/vian").status_code == 404
    assert client.get("/output/nope/mediapkg").status_code == 404
