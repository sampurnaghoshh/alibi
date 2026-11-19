"""API routes in fixture mode."""

import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

client = TestClient(app)
FIXTURE = json.loads(config.FIXTURE_PATH.read_text(encoding="utf-8"))


def test_health():
    r = client.get("/api/health").json()
    assert r["status"] == "ok"
    assert {w["name"] for w in r["witnesses"]} == {"metadata", "weather", "prior_appearance", "lipsync", "enf", "ela"}


def test_create_returns_fixture():
    r = client.post(
        "/api/investigations",
        data={"caption": FIXTURE["caption"], "posted_at": "2026-11-19"},
        files=[("files", ("clip.mp4", b"abc", "video/mp4"))],
    )
    assert r.status_code == 200
    assert r.json() == FIXTURE


def test_create_rejects_bad_date():
    r = client.post("/api/investigations", data={"posted_at": "last tuesday"})
    assert r.status_code == 422


def test_get_and_list():
    assert client.get(f"/api/investigations/{FIXTURE['id']}").json() == FIXTURE
    assert client.get("/api/investigations/inv_20200101_000000_zzzz").status_code == 404
    rows = client.get("/api/investigations").json()
    row = next(s for s in rows if s["id"] == FIXTURE["id"])
    assert set(row) == {"id", "created_at", "caption", "inputs", "headline", "headline_text", "counts", "human_review"}


def test_report():
    r = client.get(f"/api/investigations/{FIXTURE['id']}/report")
    assert r.status_code == 200 and "Human review required" in r.text


@pytest.mark.parametrize("path", ["../../backend/.env", "originals/x.mp4"])
def test_files_404(path):
    assert client.get(f"/api/files/{FIXTURE['id']}/{path}").status_code == 404


def test_cors_allows_frontend():
    r = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
