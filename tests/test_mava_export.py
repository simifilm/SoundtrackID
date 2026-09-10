"""Tests for MAVA/VIAN-Export compatible export (mava_export.py + GET /output/{video_name}/mava)."""

import io
import json
import zipfile

from fastapi.testclient import TestClient

from fiwi_filmmusik import app as app_module
from fiwi_filmmusik.mava_export import build_mava_mapping, build_mava_tsv

client = TestClient(app_module.app)


def test_build_mava_tsv_uses_title_artist_label():
    cues = [
        {"start": 12.5, "duration": 30.0, "title": "Main Theme", "artist": "John Williams"},
        {"start": 100.0, "duration": 5.0, "title": None, "artist": None},
    ]
    tsv = build_mava_tsv(cues)
    lines = tsv.strip("\n").split("\n")
    assert lines[0].split("\t") == ["start_in_seconds", "duration_in_seconds", "annotation"]
    assert lines[1] == "12.5\t30.0\tMain Theme — John Williams"
    assert lines[2] == "100.0\t5.0\tUnidentified music"


def test_build_mava_mapping_matches_import_tsv_contract():
    mapping = build_mava_mapping("my_video")
    assert mapping["time_column"] == "start_in_seconds"
    assert mapping["duration_column"] == "duration_in_seconds"
    assert mapping["value_column"] == "annotation"
    assert mapping["value_type"] == "string"
    assert "my_video" in mapping["series_description"]


def test_export_endpoint_returns_zip_with_tsv_and_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    run_dir = tmp_path / "myrun"
    run_dir.mkdir()
    (run_dir / "results.json").write_text(json.dumps({
        "cues": [{"start": 1.0, "duration": 2.0, "title": "T", "artist": "A"}],
    }))

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


def test_export_endpoint_missing_run_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_OUTPUT_DIR", tmp_path)
    r = client.get("/output/nope/mava")
    assert r.status_code == 404
