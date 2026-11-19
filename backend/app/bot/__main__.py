"""
Run the ALIBI Telegram bot (long polling; no public URL needed).

    cd backend
    python -m app.bot          # needs TELEGRAM_BOT_TOKEN in backend/.env and the API on ALIBI_API_URL

The token is never printed or logged: httpx request logging is silenced and every log line
passes through a formatter that replaces the token with <token>.
"""

from __future__ import annotations

import logging
import sys

from app import config
from app.bot.api import AlibiClient, AlibiError
from app.bot.handler import Bot
from app.bot.state import BotState
from app.bot.telegram import TelegramClient, TelegramError, redact


class RedactingFormatter(logging.Formatter):
    """Removes the bot token from the final log text, tracebacks included."""

    def __init__(self, token: str, fmt: str):
        super().__init__(fmt)
        self._token = token

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), self._token)


def setup_logging(token: str) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingFormatter(token, "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore"):          # these would log request URLs, which contain the token
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> int:
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set. Add it to backend/.env (see .env.example).", file=sys.stderr)
        return 1
    setup_logging(token)
    log = logging.getLogger("alibi.bot")
    if not config.TELEGRAM_ALLOWED_CHAT_IDS and not config.TELEGRAM_PASSCODE:
        log.warning("No TELEGRAM_ALLOWED_CHAT_IDS and no TELEGRAM_PASSCODE: the bot will ignore every chat.")

    tg, api = TelegramClient(token), AlibiClient()
    try:
        me = tg.get_me()
    except TelegramError as exc:
        log.error("Telegram rejected the bot token or is unreachable: %s", exc)
        return 1
    try:
        api.health()
        log.info("ALIBI API reachable at %s", api.base)
    except AlibiError:
        log.warning("ALIBI API not reachable at %s yet; users get a 'try again' message until it is", api.base)

    log.info("Bot @%s running (long polling). Ctrl+C to stop.", me.get("username"))
    try:
        Bot(tg, api, BotState()).run_forever()
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
