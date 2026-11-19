"""Server-sent events formatting. Owner: Sampurna."""

from __future__ import annotations

import json
from typing import Any, Optional

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",     # don't let a proxy buffer the stream
    "Connection": "keep-alive",
}


def sse(event: str, data: Any, event_id: Optional[int | str] = None) -> str:
    """One SSE message. `data` is sent as a single line of JSON."""
    out = ""
    if event_id is not None:
        out += f"id: {event_id}\n"
    out += f"event: {event}\n"
    out += f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    return out


def comment(text: str = "keepalive") -> str:
    """An SSE comment line: keeps the connection open, ignored by EventSource."""
    return f": {text}\n\n"
