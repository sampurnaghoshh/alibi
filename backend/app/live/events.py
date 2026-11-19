"""
Per-investigation event stream (GET /api/investigations/{id}/events). Owner: Sampurna.

The run's history.jsonl is the source of truth: this follows the file as the pipeline appends
to it and sends each line as one SSE message, in order, as soon as it is written. Nothing is
invented or delayed; it only checks the file every config.LIVE_SSE_POLL_S seconds.

Each message:  id: <line number>   event: <history event>   data: {ts, event, detail, data?}
Events: file_received, claims_extracted, witness_started, witness_completed / witness_failed,
assessment_computed, graph_ready, done (or failed). The stream closes after done / failed.
A completed run replays its whole log. Reconnecting with Last-Event-ID resumes after that line.
"""

from __future__ import annotations

import json
import time
from typing import Iterator

from app import config
from app.live.sse import comment, sse
from app.storage import RunStore

TERMINAL = {"done", "failed"}


def stream(store: RunStore, after: int = -1) -> Iterator[str]:
    yield "retry: 2000\n\n"
    pos, buf, index = 0, b"", -1
    started = last_sent = time.monotonic()
    while time.monotonic() - started < config.LIVE_SSE_MAX_S:
        if store.history_path.exists():
            with open(store.history_path, "rb") as fh:
                fh.seek(pos)
                chunk = fh.read()
            pos += len(chunk)
            buf += chunk
            *lines, buf = buf.split(b"\n")          # only complete lines; keep a partial tail
            for raw in lines:
                if not raw.strip():
                    continue
                index += 1
                if index <= after:
                    continue
                line = json.loads(raw)
                last_sent = time.monotonic()
                yield sse(line["event"], line, index)
                if line["event"] in TERMINAL:
                    return
        # Runs saved before the live engine existed have no "done" line: finish from the saved file.
        # (New runs always log graph_ready before saving, so they wait for their real "done".)
        if not buf and store.exists() and _is_legacy(store):
            inv = store.load_investigation()
            index += 1
            yield sse("done", {"ts": inv.created_at.isoformat(), "event": "done", "detail": inv.assessment.headline,
                               "data": {"id": inv.id, "headline": inv.assessment.headline,
                                        "parent_id": inv.parent_id}}, index)
            return
        if time.monotonic() - last_sent >= config.LIVE_SSE_KEEPALIVE_S:
            last_sent = time.monotonic()
            yield comment()
        time.sleep(config.LIVE_SSE_POLL_S)


def _is_legacy(store: RunStore) -> bool:
    return not any(e["event"] in ("graph_ready", *TERMINAL) for e in store.events())
