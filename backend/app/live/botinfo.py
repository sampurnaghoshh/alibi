"""
The Telegram bot's @username for the Live Desk idle screen ("Send a clip to @…" + QR). Owner: Sampurna.

TELEGRAM_BOT_USERNAME in backend/.env wins. Otherwise the API asks Telegram once (getMe) at
startup, in the background, and caches the answer. Offline or no token: the page simply shows
no username. A failed lookup is retried at most once a minute. The token is never logged.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import httpx

from app import config

log = logging.getLogger("alibi.live.botinfo")

RETRY_AFTER_S = 60.0

_lock = threading.Lock()
_cached: Optional[str] = None
_last_failure = 0.0


def _ask_telegram() -> Optional[str]:
    from app.bot.telegram import TelegramClient, TelegramError  # noqa: PLC0415

    try:
        client = TelegramClient(config.TELEGRAM_BOT_TOKEN, http=httpx.Client(timeout=5.0), retries=1)
        return (client.get_me() or {}).get("username")
    except (TelegramError, ValueError) as exc:
        log.warning("could not read the bot username from Telegram: %s", exc)
        return None


def bot_username(fetch: Callable[[], Optional[str]] = _ask_telegram) -> Optional[str]:
    """@username without the @, or None. Never raises, never blocks for more than one quick lookup."""
    global _cached, _last_failure
    if config.TELEGRAM_BOT_USERNAME:
        return config.TELEGRAM_BOT_USERNAME
    with _lock:
        if _cached:
            return _cached
        if not config.TELEGRAM_BOT_TOKEN or time.monotonic() - _last_failure < RETRY_AFTER_S and _last_failure:
            return None
        name = fetch()
        if name:
            _cached = name
        else:
            _last_failure = time.monotonic()
        return name


def warm_up() -> None:
    """Called at API startup: look the username up in the background so /live never waits."""
    threading.Thread(target=bot_username, name="botinfo", daemon=True).start()


def reset_cache() -> None:   # tests
    global _cached, _last_failure
    with _lock:
        _cached, _last_failure = None, 0.0
