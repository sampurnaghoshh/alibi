"""
Client for our own ALIBI API (default http://localhost:8000). Owner: Sampurna.

Every failure becomes AlibiError with a message that can be shown to the user as is.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional

import httpx

from app import config

DOWN = "The ALIBI server isn't reachable right now. Please try again in a minute."


class AlibiError(Exception):
    """Something went wrong talking to the ALIBI API; str() is safe to show the user."""


def _detail(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail") or f"HTTP {r.status_code}")
    except ValueError:
        return f"HTTP {r.status_code}"


class AlibiClient:
    def __init__(self, base: Optional[str] = None, http: Optional[httpx.Client] = None):
        self.base = (base or config.ALIBI_API_URL).rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(30.0, read=120.0))

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self._http.request(method, f"{self.base}{path}", **kw)
        except httpx.TransportError:
            raise AlibiError(DOWN) from None
        if r.status_code >= 400:
            raise AlibiError(_detail(r))
        return r

    def health(self) -> dict:
        return self._request("GET", "/api/health").json()

    def start(self, filename: str, content: bytes, mime: str, caption: str, posted_at: str) -> str:
        """POST /api/investigations?async=true -> investigation id."""
        r = self._request("POST", "/api/investigations", params={"async": "true"},
                          data={"caption": caption, "posted_at": posted_at},
                          files=[("files", (filename, content, mime or "application/octet-stream"))])
        return r.json()["id"]

    def rerun(self, inv_id: str, caption: str, posted_at: str) -> str:
        """POST /api/investigations/{id}/rerun?async=true -> new investigation id."""
        r = self._request("POST", f"/api/investigations/{inv_id}/rerun", params={"async": "true"},
                          json={"caption": caption, "posted_at": posted_at})
        return r.json()["id"]

    def events(self, inv_id: str) -> Iterator[tuple[str, dict]]:
        """Follow GET /api/investigations/{id}/events: yields (event, payload) until the stream closes."""
        try:
            with self._http.stream("GET", f"{self.base}/api/investigations/{inv_id}/events",
                                   timeout=httpx.Timeout(30.0, read=config.LIVE_SSE_MAX_S)) as r:
                if r.status_code >= 400:
                    r.read()
                    raise AlibiError(_detail(r))
                event, data = None, None
                for line in r.iter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                    elif line == "" and event is not None:
                        yield event, data or {}
                        event, data = None, None
        except httpx.TransportError:
            raise AlibiError(DOWN) from None

    def investigation(self, inv_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/investigations/{inv_id}").json()

    def report(self, inv_id: str) -> bytes:
        return self._request("GET", f"/api/investigations/{inv_id}/report").content
