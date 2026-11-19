"""
Live feed: announces every new investigation (GET /api/live/feed). Owner: Sampurna.

In-process publish/subscribe: the pipeline calls publish(); every open feed stream gets the
message. This works with a single uvicorn process (how we run the demo); with several
workers each process would only see its own investigations.

Messages:
    event "investigation_started": {id, created_at, caption, posted_at, inputs[], parent_id}
    event "investigation_done":    {id, headline, headline_text, human_review, parent_id}
    event "investigation_failed":  {id, error, parent_id}
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Iterator, Optional

from app import config
from app.live.sse import comment, sse

_subscribers: set[queue.Queue] = set()
_lock = threading.Lock()
_counter = 0


def publish(event: str, payload: dict[str, Any]) -> None:
    """Send to every open feed. Never blocks the pipeline; a full queue drops the message."""
    global _counter
    with _lock:
        _counter += 1
        msg = (_counter, event, payload)
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)


def stream(limit: Optional[int] = None, keepalive_s: Optional[float] = None) -> Iterator[str]:
    """SSE generator for one client. Subscribes when first iterated; `limit` closes the stream
    after that many messages (handy for tests and scripts)."""
    keepalive_s = keepalive_s or config.LIVE_SSE_KEEPALIVE_S
    q: queue.Queue = queue.Queue(maxsize=200)
    with _lock:
        _subscribers.add(q)
    sent = 0
    try:
        yield "retry: 2000\n\n"
        last = time.monotonic()
        while limit is None or sent < limit:
            try:
                n, event, payload = q.get(timeout=1.0)
            except queue.Empty:
                if time.monotonic() - last >= keepalive_s:
                    last = time.monotonic()
                    yield comment()
                continue
            sent += 1
            last = time.monotonic()
            yield sse(event, payload, n)
    finally:
        with _lock:
            _subscribers.discard(q)
