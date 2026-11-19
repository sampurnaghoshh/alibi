"""
ALIBI API (CLAUDE.md §8). Owner: Sampurna.

POST /api/investigations runs the real pipeline (app/pipeline.py) and saves the run under data/runs/.
Uploads are validated first (app/uploads.py): max 5 files, 200 MB each, real image/video/audio content.
GET /api/demo + POST /api/demo/{case} run the presets in demo/captions.md (no file picking).
GET /api/fixture serves the Case A sample investigation for frontend work.
Live Desk (§14): ?async=true, GET .../events (SSE), POST .../rerun, GET /api/live/feed (SSE).

Run (from backend/):  uvicorn app.main:app --reload --port 8000   (one worker: the live feed is in-process)
"""

from __future__ import annotations

import logging
import shutil
from typing import Any, Callable, Optional

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from app import config
from app.demo import load_cases
from app.live import events as live_events
from app.live import feed as live_feed
from app.live import jobs
from app.live.sse import SSE_HEADERS
from app.pipeline import Prepared, Upload, execute, prepare, prepare_rerun, run_investigation
from app.report import render_report
from app.runner import all_witnesses
from app.schemas import Investigation, InvestigationSummary
from app.storage import RunStore, list_investigations, parse_posted_at
from app.uploads import UploadError, check_count, validate_upload

log = logging.getLogger("alibi.api")

app = FastAPI(title="ALIBI", description="Explainable multimedia verification", version=config.APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get(inv_id: str) -> Investigation:
    """A finished run from data/runs/, else 404 (409 while it is still running)."""
    try:
        store = RunStore(inv_id)
    except ValueError:
        raise HTTPException(404, "investigation not found")
    if not store.exists():
        if store.status() == "running":
            raise HTTPException(409, f"Investigation {inv_id} is still running.")
        raise HTTPException(404, "investigation not found")
    return store.load_investigation()


# --- routes -----------------------------------------------------------------

@app.post("/api/investigations", response_model=Investigation)
def create_investigation(   # sync on purpose: FastAPI runs it in a worker thread
    files: Optional[list[UploadFile]] = File(default=None),   # FormData.append("files", file) per file
    caption: str = Form(default=""),
    posted_at: Optional[str] = Form(default=None),            # "2026-11-19" or ISO datetime; default now
    run_async: bool = Query(default=False, alias="async"),    # ?async=true -> 202 + id, runs in background
):
    try:
        posted = parse_posted_at(posted_at)
    except ValueError:
        raise HTTPException(422, "The 'Shared on' date must be a date like 2026-11-19.")
    files = files or []
    if not files and not caption.strip():
        raise HTTPException(422, "Add at least one file or a caption to investigate.")

    # Validate everything BEFORE storing anything: count, size, real content type.
    try:
        check_count(len(files))
        checked = [
            validate_upload(f.filename or f"upload_{n}", f.file, f.content_type, f.size)
            for n, f in enumerate(files, 1)
        ]
    except UploadError as exc:
        raise HTTPException(exc.status, exc.message)
    uploads = [Upload(c.filename, c.stream, c.mime, c.declared_mime) for c in checked]
    if run_async:
        return _start_async(lambda: prepare(uploads, caption, posted))
    return _run(uploads, caption, posted)


def _start_async(make: Callable[[], Prepared]) -> JSONResponse:
    """Store the originals now (the request's files are still open), analyse in the background."""
    try:
        p = make()
    except Exception as exc:  # noqa: BLE001
        log.exception("could not start investigation")
        raise HTTPException(500, f"The investigation could not be started: {type(exc).__name__}: {exc}")
    jobs.submit(lambda: execute(p))
    return JSONResponse(status_code=202, content={
        "id": p.inv_id, "status": "running", "parent_id": p.parent.id if p.parent else None,
        "events": f"/api/investigations/{p.inv_id}/events",
        "investigation": f"/api/investigations/{p.inv_id}",
    })


def _run(uploads: list[Upload], caption: str, posted) -> Investigation:
    """Run the pipeline. Witness problems never get here (they become inconclusive findings);
    anything that does is a server bug, reported as a 500 with a readable message."""
    try:
        return run_investigation(uploads, caption, posted)
    except ValidationError as exc:  # an assembled investigation broke the contract: a bug, not user error
        log.exception("investigation failed contract validation")
        raise HTTPException(500, f"The investigation could not be assembled ({exc.error_count()} contract errors). This is a server bug.")
    except Exception as exc:  # noqa: BLE001
        log.exception("investigation failed")
        raise HTTPException(500, f"The investigation could not be completed: {type(exc).__name__}: {exc}")


@app.get("/api/demo")
def list_demo_cases() -> list[dict[str, Any]]:
    """Preset cases from demo/captions.md: [{name, caption, posted_at, files, expect, ready, problem}]."""
    return [c.public() for c in load_cases().values()]


@app.post("/api/demo/{case_name}", response_model=Investigation)
def run_demo_case(case_name: str) -> Investigation:
    """Run the pipeline on a preset case (its files, caption and posted_at) with no file picking."""
    cases = load_cases()
    case = cases.get(case_name)
    if case is None:
        known = ", ".join(cases) or "none"
        raise HTTPException(404, f"No demo case named '{case_name}'. Available: {known}.")
    if not case.ready:
        raise HTTPException(404, f"Demo case '{case_name}' is not ready: {case.problem}")
    posted = parse_posted_at(None if case.posted_at.upper() in ("", "TODO") else case.posted_at)

    handles = [open(p, "rb") for p in case.paths]
    try:
        checked = [validate_upload(p.name, fh) for p, fh in zip(case.paths, handles)]
        return _run([Upload(c.filename, c.stream, c.mime) for c in checked], case.caption, posted)
    except UploadError as exc:
        raise HTTPException(exc.status, f"Demo case '{case_name}': {exc.message}")
    finally:
        for fh in handles:
            fh.close()


@app.get("/api/investigations", response_model=list[InvestigationSummary])
def list_runs() -> list[InvestigationSummary]:
    """Saved runs, newest first."""
    return [InvestigationSummary.from_investigation(r) for r in list_investigations()]


@app.get("/api/investigations/{inv_id}", response_model=Investigation)
def get_investigation(inv_id: str):
    """The saved Investigation; 202 {"status": "running"} while an async run is still going."""
    store = _store(inv_id)
    status = store.status()
    if status == "running":
        return JSONResponse(status_code=202, content={"id": inv_id, "status": "running"})
    if status == "failed":
        detail = store.events()[-1].get("detail", "")
        raise HTTPException(500, f"Investigation {inv_id} failed: {detail}")
    return store.load_investigation()


def _store(inv_id: str) -> RunStore:
    """RunStore for an existing run folder, else 404."""
    try:
        store = RunStore(inv_id)
    except ValueError:
        raise HTTPException(404, "investigation not found")
    if store.status() is None:
        raise HTTPException(404, "investigation not found")
    return store


@app.get("/api/investigations/{inv_id}/events")
def investigation_events(inv_id: str, last_event_id: Optional[str] = Header(default=None)) -> StreamingResponse:
    """Server-sent events: the run's real pipeline events, in order, as they are written (§14)."""
    store = _store(inv_id)
    after = int(last_event_id) if last_event_id and last_event_id.isdigit() else -1
    return StreamingResponse(live_events.stream(store, after), media_type="text/event-stream", headers=SSE_HEADERS)


class RerunRequest(BaseModel):
    caption: str
    posted_at: Optional[str] = None     # "2026-11-19" or ISO datetime; default now


@app.post("/api/investigations/{inv_id}/rerun", response_model=Investigation)
def rerun_investigation(inv_id: str, body: RerunRequest = Body(...),
                        run_async: bool = Query(default=False, alias="async")):
    """Same stored files, new caption: re-run claims + caption-dependent witnesses, re-use the rest.
    Saved as a new investigation with parent_id = inv_id."""
    status = _store(inv_id).status()
    if status != "done":
        raise HTTPException(409, f"Investigation {inv_id} is {status}; re-run it once it has finished.")
    if not body.caption.strip():
        raise HTTPException(422, "The new caption is empty.")
    try:
        posted = parse_posted_at(body.posted_at)
    except ValueError:
        raise HTTPException(422, "The 'Shared on' date must be a date like 2026-11-19.")
    make = lambda: prepare_rerun(inv_id, body.caption, posted)  # noqa: E731
    if run_async:
        return _start_async(make)
    try:
        p = make()
    except ValueError as exc:   # e.g. a stored original no longer matches its hash
        raise HTTPException(409, f"Cannot re-run {inv_id}: {exc}")
    try:
        return execute(p)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"The re-run could not be completed: {type(exc).__name__}: {exc}")


@app.get("/api/live/feed")
def live_feed_stream(limit: Optional[int] = Query(default=None, ge=1)) -> StreamingResponse:
    """Server-sent events announcing every new investigation (investigation_started / _done / _failed).
    `limit` closes the stream after that many messages (for scripts and tests)."""
    return StreamingResponse(live_feed.stream(limit), media_type="text/event-stream", headers=SSE_HEADERS)


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
    """Printable report (browser -> Save as PDF), self-contained with embedded artifact images."""
    inv = _get(inv_id)
    return render_report(inv, RunStore(inv_id))


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
