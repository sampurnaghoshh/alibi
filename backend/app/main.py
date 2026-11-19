"""
ALIBI API (CLAUDE.md §8). Owner: Sampurna.

STATUS: fixture mode. POST /api/investigations returns shared/fixtures/sample_investigation.json
so the frontend can build against the real routes. Integration (storage -> claims -> runner ->
rules -> graph) replaces `_fixture()` in create_investigation at H10.

Run (from backend/):  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import html
import shutil
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from app import config
from app.runner import all_witnesses
from app.schemas import Investigation, InvestigationSummary
from app.storage import RunStore, list_investigations, parse_posted_at

app = FastAPI(title="ALIBI", description="Explainable multimedia verification", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _fixture() -> Investigation:
    """The Case A sample investigation, validated against the contract on every load."""
    return Investigation.model_validate_json(config.FIXTURE_PATH.read_text(encoding="utf-8"))


def _get(inv_id: str) -> Investigation:
    """Saved run first, then the fixture, else 404."""
    try:
        store = RunStore(inv_id)
    except ValueError:
        raise HTTPException(404, "investigation not found")
    if store.exists():
        return store.load_investigation()
    fixture = _fixture()
    if inv_id == fixture.id:
        return fixture
    raise HTTPException(404, "investigation not found")


# --- routes -----------------------------------------------------------------

@app.post("/api/investigations", response_model=Investigation)
async def create_investigation(
    files: Optional[list[UploadFile]] = File(default=None),   # FormData.append("files", file) per file
    caption: str = Form(default=""),
    posted_at: Optional[str] = Form(default=None),            # "2026-11-19" or ISO datetime; default now
) -> Investigation:
    try:
        parse_posted_at(posted_at)
    except ValueError:
        raise HTTPException(422, "posted_at must be an ISO date or datetime, e.g. 2026-11-19")
    # TODO(H10): store originals, extract claims, run witnesses, rules, graph, save.
    return _fixture()


@app.get("/api/investigations", response_model=list[InvestigationSummary])
def list_runs() -> list[InvestigationSummary]:
    """Past runs, newest first (the fixture is always included for now)."""
    runs = list_investigations()
    fixture = _fixture()
    if all(r.id != fixture.id for r in runs):
        runs.append(fixture)
    return [InvestigationSummary.from_investigation(r) for r in runs]


@app.get("/api/investigations/{inv_id}", response_model=Investigation)
def get_investigation(inv_id: str) -> Investigation:
    return _get(inv_id)


@app.get("/api/files/{inv_id}/{path:path}")
def get_file(inv_id: str, path: str) -> FileResponse:
    """Originals (read-only) and artifacts of a saved run, e.g. /api/files/<id>/originals/in_1_clip.mp4."""
    try:
        return FileResponse(RunStore(inv_id).resolve(path))
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "file not found")


@app.get("/api/investigations/{inv_id}/report", response_class=HTMLResponse)
def get_report(inv_id: str) -> str:
    """Printable report (browser -> Save as PDF). Placeholder until report.py exists."""
    inv = _get(inv_id)
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(f.witness)}</td><td>{e(f.status)}</td><td>{e(f.reliability)}</td>"
        f"<td><b>{e(f.title)}</b><br>{e(f.summary)}</td></tr>"
        for f in inv.findings
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>ALIBI report {e(inv.id)}</title>
<style>body{{font-family:system-ui;margin:2rem;max-width:60rem}}td,th{{border:1px solid #ccc;padding:.4rem;vertical-align:top}}table{{border-collapse:collapse}}</style>
</head><body>
<h1>ALIBI investigation report</h1>
<p><b>Human review required. This is an evidence report, not a verdict.</b></p>
<p>ID: <code>{e(inv.id)}</code><br>Caption: {e(inv.caption)}</p>
<h2>{e(inv.assessment.headline_text)}</h2>
<table><tr><th>Witness</th><th>Status</th><th>Reliability</th><th>Finding</th></tr>{rows}</table>
</body></html>"""


@app.get("/api/health")
def health() -> dict[str, Any]:
    """What is available on this machine. The frontend can show this on the upload page."""
    return {
        "status": "ok",
        "mode": "fixture",
        "tools": {t: shutil.which(t) is not None for t in ("ffmpeg", "ffprobe", "exiftool")},
        "llm": {"provider": config.LLM_PROVIDER, "configured": config.llm_configured()},
        "witnesses": [
            {"name": w.name, "family": w.family, "label": w.label, "implemented": w.implemented}
            for w in all_witnesses()
        ],
    }
