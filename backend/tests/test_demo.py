"""Demo presets: GET /api/demo and POST /api/demo/{case}."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.demo import load_cases
from app.main import app
from app.schemas import Investigation

client = TestClient(app)
pytestmark = pytest.mark.usefixtures("offline")   # no live weather calls from pipeline tests

CAPTIONS = """# Demo captions

## case_ok
caption: Massive flooding in Chennai yesterday evening
posted_at: 2026-11-19
files: demo/case_ok/photo.png
expect: whatever

## case_missing
caption: Flooding in Pune today
posted_at: 2026-11-19
files: demo/case_missing/clip.mp4

## case_todo
caption: TODO
posted_at: TODO
files: demo/case_b/

## case_folder
caption: Rain in Mumbai
posted_at: 2026-11-19
files: demo/controls/

## case_outside
caption: Rain in Mumbai
posted_at: 2026-11-19
files: demo/../secret.png
"""


@pytest.fixture(autouse=True)
def demo_in_tmp(tmp_path, monkeypatch):
    demo = tmp_path / "demo"
    (demo / "case_ok").mkdir(parents=True)
    (demo / "controls").mkdir()
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (40, 90, 160)).save(buf, format="PNG")
    (demo / "case_ok" / "photo.png").write_bytes(buf.getvalue())
    (tmp_path / "secret.png").write_bytes(buf.getvalue())
    (demo / "captions.md").write_text(CAPTIONS, encoding="utf-8")
    monkeypatch.setattr(config, "DEMO_CAPTIONS", demo / "captions.md")
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")


def test_list_cases_with_ready_flags_and_reasons():
    cases = {c["name"]: c for c in client.get("/api/demo").json()}
    assert list(cases) == ["case_ok", "case_missing", "case_todo", "case_folder", "case_outside"]
    assert cases["case_ok"]["ready"] is True and cases["case_ok"]["problem"] is None
    assert cases["case_ok"]["files"] == ["demo/case_ok/photo.png"]
    assert "does not exist yet" in cases["case_missing"]["problem"]
    assert "caption is not filled in" in cases["case_todo"]["problem"]
    assert "is a folder" in cases["case_folder"]["problem"]
    assert "inside the demo/ folder" in cases["case_outside"]["problem"]
    assert not any(cases[n]["ready"] for n in ("case_missing", "case_todo", "case_folder", "case_outside"))


def test_run_ready_case():
    r = client.post("/api/demo/case_ok")
    assert r.status_code == 200, r.text
    inv = Investigation.model_validate(r.json())
    assert inv.caption == "Massive flooding in Chennai yesterday evening"
    assert inv.posted_at.date().isoformat() == "2026-11-19"
    assert [i.name for i in inv.inputs] == ["photo.png"] and inv.inputs[0].mime == "image/png"
    assert [c.source.text for c in inv.claims] == ["flooding", "Chennai", "yesterday evening"]
    assert client.get(f"/api/investigations/{inv.id}").status_code == 200   # saved like any run


def test_missing_file_is_clear_404():
    r = client.post("/api/demo/case_missing")
    assert r.status_code == 404
    assert r.json()["detail"] == (
        "Demo case 'case_missing' is not ready: The demo file 'demo/case_missing/clip.mp4' "
        "does not exist yet. Add it to the repo, then try again."
    )


@pytest.mark.parametrize("name", ["case_todo", "case_folder", "case_outside"])
def test_not_ready_cases_are_404(name):
    r = client.post(f"/api/demo/{name}")
    assert r.status_code == 404 and "is not ready" in r.json()["detail"]


def test_unknown_case_lists_available():
    r = client.post("/api/demo/case_zzz")
    assert r.status_code == 404
    assert "No demo case named 'case_zzz'" in r.json()["detail"] and "case_ok" in r.json()["detail"]


def test_real_captions_file_parses(monkeypatch):
    monkeypatch.setattr(config, "DEMO_CAPTIONS", config.REPO_ROOT / "demo" / "captions.md")
    cases = load_cases()
    assert {"case_a", "case_b", "control_1", "control_2", "control_3"} <= set(cases)
    assert cases["case_a"].caption.startswith("Massive flooding in Chennai")
