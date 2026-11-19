"""
ALIBI API (CLAUDE.md §8). Owner: Sampurna.

POST /api/investigations runs the real pipeline (app/pipeline.py) and saves the run under data/runs/.
GET /api/fixture serves the Case A sample investigation for frontend work.

Run (from backend/):  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import html
import logging
import shutil
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import ValidationError

from app import config
from app.pipeline import Upload, run_investigation
from app.runner import all_witnesses
from app.schemas import Investigation, InvestigationSummary
from app.storage import RunStore, list_investigations, parse_posted_at

log = logging.getLogger("alibi.api")

app = FastAPI(title="ALIBI", description="Explainable multimedia verification", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get(inv_id: str) -> Investigation:
    """A saved run from data/runs/, else 404."""
    try:
        store = RunStore(inv_id)
    except ValueError:
        raise HTTPException(404, "investigation not found")
    if not store.exists():
        raise HTTPException(404, "investigation not found")
    return store.load_investigation()


# --- routes -----------------------------------------------------------------

@app.post("/api/investigations", response_model=Investigation)
def create_investigation(   # sync on purpose: FastAPI runs it in a worker thread
    files: Optional[list[UploadFile]] = File(default=None),   # FormData.append("files", file) per file
    caption: str = Form(default=""),
    posted_at: Optional[str] = Form(default=None),            # "2026-11-19" or ISO datetime; default now
) -> Investigation:
    try:
        posted = parse_posted_at(posted_at)
    except ValueError:
        raise HTTPException(422, "posted_at must be an ISO date or datetime, e.g. 2026-11-19")
    files = files or []
    if not files and not caption.strip():
        raise HTTPException(422, "provide at least one file or a caption")

    uploads = [
        Upload(filename=f.filename or f"upload_{n}", stream=f.file, mime=f.content_type)
        for n, f in enumerate(files, 1)
    ]
    try:
        return run_investigation(uploads, caption, posted)
    except ValidationError as exc:  # an assembled investigation broke the contract: a bug, not user error
        log.exception("investigation failed contract validation")
        raise HTTPException(500, f"investigation failed contract validation: {exc.error_count()} errors")


@app.get("/api/investigations", response_model=list[InvestigationSummary])
def list_runs() -> list[InvestigationSummary]:
    """Saved runs, newest first."""
    return [InvestigationSummary.from_investigation(r) for r in list_investigations()]


@app.get("/api/investigations/{inv_id}", response_model=Investigation)
def get_investigation(inv_id: str) -> Investigation:
    return _get(inv_id)


@app.get("/api/fixture", response_model=Investigation)
def get_fixture() -> Investigation:
    """The Case A sample investigation (shared/fixtures/sample_investigation.json), validated on load."""
    return Investigation.model_validate_json(config.FIXTURE_PATH.read_text(encoding="utf-8"))


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
        "mode": "live",
        "tools": {t: shutil.which(t) is not None for t in ("ffmpeg", "ffprobe", "exiftool")},
        "llm": {"provider": config.LLM_PROVIDER, "configured": config.llm_configured()},
        "witnesses": [
            {"name": w.name, "family": w.family, "label": w.label, "implemented": w.implemented}
            for w in all_witnesses()
        ],
    }
