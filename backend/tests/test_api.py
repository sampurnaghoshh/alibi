"""API routes, including the end-to-end pipeline with the stub witnesses."""

import hashlib
import io
import json
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.main import app
from app.schemas import Investigation

client = TestClient(app)
pytestmark = pytest.mark.usefixtures("offline")   # no live weather calls from pipeline tests
FIXTURE = json.loads(config.FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def runs_in_tmp(tmp_path, monkeypatch):
    """Never write test runs into the real data/runs/."""
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (40, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _post(caption="Flooding in Chennai yesterday", posted_at="2026-11-19", files=None):
    files = [("files", ("tiny.png", _png(), "image/png"))] if files is None else files
    return client.post("/api/investigations", data={"caption": caption, "posted_at": posted_at}, files=files)


def test_bracket_field_name_files_is_not_dropped():
    """FormData.append("files[]", f) (as the frontend sends it) must store the file, not run caption-only."""
    png = _png()
    r = _post(files=[("files[]", ("a.png", png, "image/png")), ("files", ("b.png", _png(), "image/png"))])
    assert r.status_code == 200, r.text
    assert sorted(i["name"] for i in r.json()["inputs"]) == ["a.png", "b.png"]


def test_end_to_end_with_stubs():
    png = _png()
    r = _post(files=[("files", ("tiny.png", png, "image/png"))])
    assert r.status_code == 200, r.text
    inv = Investigation.model_validate(r.json())          # valid against the contract

    # Original preserved: hashed, read-only, servable.
    [inp] = inv.inputs
    assert inp.sha256 == hashlib.sha256(png).hexdigest() and inp.mime == "image/png"
    stored = config.RUNS_DIR / inv.id / inp.stored_path
    assert stored.read_bytes() == png and not os.access(stored, os.W_OK)
    assert client.get(f"/api/files/{inv.id}/{inp.stored_path}").content == png

    # Every witness reported; stubs never contradict.
    assert {f.witness for f in inv.findings} == {"metadata", "weather", "prior_appearance", "lipsync", "enf", "ela"}
    assert inv.assessment.counts.contradiction == 0
    assert inv.assessment.headline == "insufficient_evidence"
    assert inv.assessment.human_review == "standard"

    # Chain of custody, in order.
    events = [h.event for h in inv.history]
    assert events[0] == "file_received" and events[1] == "claims_extracted"
    assert events.count("witness_started") == 6 and events.count("witness_completed") == 6
    assert events[-2:] == ["assessment_computed", "graph_ready"]   # "done" is logged after saving

    # Saved run can be fetched back, listed and reported.
    assert client.get(f"/api/investigations/{inv.id}").json() == r.json()
    rows = client.get("/api/investigations").json()
    assert [row["id"] for row in rows] == [inv.id]
    assert set(rows[0]) == {"id", "created_at", "parent_id", "caption", "inputs", "headline", "headline_text",
                            "counts", "human_review"}
    rep = client.get(f"/api/investigations/{inv.id}/report")
    assert rep.status_code == 200 and "Human review required" in rep.text


def test_rejects_bad_date_and_empty_request():
    assert _post(posted_at="last tuesday").status_code == 422
    assert _post(caption="", files=[]).status_code == 422


def test_fixture_route():
    assert client.get("/api/fixture").json() == FIXTURE


def test_unknown_investigation_404():
    assert client.get("/api/investigations/inv_20200101_000000_zzzz").status_code == 404
    assert client.get("/api/investigations/not-an-id").status_code == 404
    assert client.get(f"/api/investigations/{FIXTURE['id']}").status_code == 404   # fixture is at /api/fixture now


def test_health():
    r = client.get("/api/health").json()
    assert r["status"] == "ok" and r["mode"] == "live"
    assert {w["name"] for w in r["witnesses"]} == {"metadata", "weather", "prior_appearance", "lipsync", "enf", "ela"}


@pytest.mark.parametrize("path", ["../../backend/.env", "history.jsonl", "originals/x.mp4"])
def test_files_cannot_escape_or_guess(path):
    inv_id = _post().json()["id"]
    assert client.get(f"/api/files/{inv_id}/{path}").status_code == 404


def test_cors_allows_frontend():
    r = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
