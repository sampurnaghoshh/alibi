"""
Minimal Telegram Bot API client over httpx (long polling, no webhook). Owner: Sampurna.

The bot token is part of every request URL, so:
  - nothing here logs a URL, and app.bot.__main__ silences httpx's own request logging;
  - every error message passes through redact() before it is raised or logged.
Network errors, 429 (rate limit) and 5xx are retried with backoff.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

import httpx

from app import config

log = logging.getLogger("alibi.bot.telegram")


class TelegramError(Exception):
    """A Bot API call failed (after retries). The message never contains the token."""

    def __init__(self, message: str, status: Optional[int] = None, description: str = ""):
        super().__init__(message)
        self.status = status
        self.description = description


def redact(text: str, token: str) -> str:
    return text.replace(token, "<token>") if token else text


class TelegramClient:
    def __init__(self, token: str, http: Optional[httpx.Client] = None, base: Optional[str] = None,
                 retries: int = 3, sleep: Callable[[float], None] = time.sleep):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set (backend/.env)")
        self._token = token
        self._base = (base or config.TELEGRAM_API_BASE).rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(15.0, read=config.TELEGRAM_POLL_TIMEOUT_S + 15))
        self._retries = retries
        self._sleep = sleep

    def _redact(self, text: str) -> str:
        return redact(text, self._token)

    # --- core call ------------------------------------------------------------------

    def call(self, method: str, params: Optional[dict] = None, files: Optional[dict] = None,
             timeout: Optional[float] = None) -> Any:
        url = f"{self._base}/bot{self._token}/{method}"
        delay = 1.0
        for attempt in range(1, self._retries + 1):
            try:
                if files:
                    r = self._http.post(url, data=params or {}, files=files, timeout=timeout)
                else:
                    r = self._http.post(url, json=params or {}, timeout=timeout)
            except httpx.TransportError as exc:
                if attempt == self._retries:
                    raise TelegramError(self._redact(f"{method}: network error: {type(exc).__name__}")) from None
                log.warning("telegram %s: network error (%s), retry %d", method, type(exc).__name__, attempt)
                self._sleep(delay)
                delay *= 2
                continue
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.status_code == 429 or r.status_code >= 500:
                wait = float((body.get("parameters") or {}).get("retry_after", delay))
                if attempt == self._retries:
                    raise TelegramError(f"{method}: HTTP {r.status_code}", r.status_code, body.get("description", ""))
                log.warning("telegram %s: HTTP %d, retry %d in %.0f s", method, r.status_code, attempt, wait)
                self._sleep(wait)
                delay *= 2
                continue
            if not body.get("ok"):
                desc = self._redact(str(body.get("description", "")))
                raise TelegramError(f"{method}: {desc or f'HTTP {r.status_code}'}", r.status_code, desc)
            return body.get("result")
        raise TelegramError(f"{method}: failed")  # pragma: no cover

    # --- methods used by the bot -----------------------------------------------------

    def get_me(self) -> dict:
        return self.call("getMe")

    def get_updates(self, offset: Optional[int], timeout: int = config.TELEGRAM_POLL_TIMEOUT_S) -> list[dict]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", params, timeout=timeout + 15) or []

    def send_message(self, chat_id: int, text: str, reply_to: Optional[int] = None) -> dict:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return self.call("sendMessage", params)

    def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        try:
            self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text,
                                          "disable_web_page_preview": True})
        except TelegramError as exc:
            if "not modified" not in exc.description:   # same text again is harmless
                raise

    def send_document(self, chat_id: int, filename: str, content: bytes, caption: str = "",
                      reply_to: Optional[int] = None) -> dict:
        params: dict[str, Any] = {"chat_id": str(chat_id), "caption": caption}
        if reply_to:
            params["reply_to_message_id"] = str(reply_to)
            params["allow_sending_without_reply"] = "true"
        return self.call("sendDocument", params, files={"document": (filename, content, "text/html")}, timeout=60)

    def download(self, file_id: str) -> bytes:
        """getFile + download. Raises TelegramError (e.g. 'file is too big' above 20 MB)."""
        info = self.call("getFile", {"file_id": file_id})
        url = f"{self._base}/file/bot{self._token}/{info['file_path']}"
        try:
            r = self._http.get(url, timeout=120)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise TelegramError(self._redact(f"download failed: {type(exc).__name__}")) from None
        return r.content
