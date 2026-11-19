"""
The ALIBI Telegram bot logic. Owner: Sampurna.

One update at a time per worker, never raising: any problem becomes a friendly reply or a log line.

Flow for a case (document / video / photo with a caption):
    "📥 Received" -> claims (extracted locally from the caption, no download needed)
    -> [warning if Telegram compressed it] -> [size check: Bot API downloads stop at 20 MB]
    -> download -> POST /api/investigations?async=true -> follow the event stream, editing ONE
       progress message as witnesses finish -> final result message -> HTML report document.
Replying to a result message with new text re-runs that investigation with the new caption.
Only allowed chats are served: TELEGRAM_ALLOWED_CHAT_IDS, or "/start <TELEGRAM_PASSCODE>".
"""

from __future__ import annotations

import hmac
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
from typing import Optional

from app import config
from app.bot import texts
from app.bot.api import AlibiClient, AlibiError
from app.bot.state import BotState
from app.bot.telegram import TelegramClient, TelegramError
from app.claims.extract import extract_claims

log = logging.getLogger("alibi.bot")


class Bot:
    def __init__(self, tg: TelegramClient, api: AlibiClient, state: BotState, workers: int = config.BOT_WORKERS):
        self.tg, self.api, self.state = tg, api, state
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bot") if workers else None

    # --- polling ---------------------------------------------------------------------

    def run_forever(self, stop: Optional[Event] = None, sleep=time.sleep) -> None:
        offset, backoff = None, 1.0
        while not (stop and stop.is_set()):
            try:
                updates = self.tg.get_updates(offset)
            except TelegramError as exc:
                log.warning("getUpdates failed: %s (retrying in %.0f s)", exc, backoff)
                sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            backoff = 1.0
            for update in updates:
                offset = update["update_id"] + 1     # acknowledge before handling: one bad update can't loop
                self.dispatch(update)

    def dispatch(self, update: dict) -> None:
        if self._pool:
            self._pool.submit(self.handle_update, update)
        else:
            self.handle_update(update)

    def handle_update(self, update: dict) -> None:
        """Never raises: one bad message must not stop the bot."""
        try:
            self._handle(update)
        except Exception as exc:  # noqa: BLE001
            log.error("update %s failed: %s: %s", update.get("update_id"), type(exc).__name__, exc)

    # --- routing ---------------------------------------------------------------------

    def _handle(self, update: dict) -> None:
        msg = update.get("message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()

        if text.startswith("/start"):
            return self._start(chat_id, msg, text)
        if not self.state.is_allowed(chat_id):
            log.info("ignored a message from chat %s (not approved)", chat_id)
            return
        if text.startswith("/help"):
            return self._say(chat_id, texts.HELP)

        media = self._media(msg)
        if media:
            return self._case(msg, media)

        reply = msg.get("reply_to_message")
        if text and reply:
            parent = self.state.result_for(chat_id, reply["message_id"])
            if parent:
                return self._rerun(msg, parent, text)
            return self._say(chat_id, texts.NOT_A_RESULT, msg["message_id"])
        self._say(chat_id, texts.NEED_MEDIA, msg["message_id"])

    def _start(self, chat_id: int, msg: dict, text: str) -> None:
        if self.state.is_allowed(chat_id):
            return self._say(chat_id, texts.HELP)
        code = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        if config.TELEGRAM_PASSCODE and code and hmac.compare_digest(code, config.TELEGRAM_PASSCODE):
            self.state.approve(chat_id)
            log.info("chat %s approved with the passcode", chat_id)
            return self._say(chat_id, "✅ This chat is now approved.\n\n" + texts.HELP)
        log.info("ignored /start from chat %s (no or wrong passcode)", chat_id)

    @staticmethod
    def _media(msg: dict) -> Optional[dict]:
        if doc := msg.get("document"):
            return {"kind": "file", "file_id": doc["file_id"], "name": doc.get("file_name") or "upload",
                    "mime": doc.get("mime_type") or "application/octet-stream", "size": doc.get("file_size")}
        if video := msg.get("video"):
            return {"kind": "video", "file_id": video["file_id"],
                    "name": video.get("file_name") or f"telegram_video_{video.get('file_unique_id', 'clip')}.mp4",
                    "mime": video.get("mime_type") or "video/mp4", "size": video.get("file_size")}
        if photos := msg.get("photo"):
            p = max(photos, key=lambda x: x.get("file_size") or x.get("width", 0) * x.get("height", 0))
            return {"kind": "photo", "file_id": p["file_id"],
                    "name": f"telegram_photo_{p.get('file_unique_id', 'photo')}.jpg", "mime": "image/jpeg",
                    "size": p.get("file_size")}
        return None

    # --- cases -------------------------------------------------------------------------

    @staticmethod
    def _posted_at(msg: dict) -> datetime:
        return datetime.fromtimestamp(msg["date"], tz=config.DEFAULT_TZ)

    def _say(self, chat_id: int, text: str, reply_to: Optional[int] = None) -> Optional[dict]:
        try:
            return self.tg.send_message(chat_id, text, reply_to)
        except TelegramError as exc:
            log.warning("sendMessage to %s failed: %s", chat_id, exc)
            return None

    def _claims_or_example(self, chat_id: int, mid: int, caption: str, posted: datetime) -> bool:
        """Instant feedback from the caption alone. False (and an example) if there is nothing to check."""
        self._say(chat_id, texts.RECEIVED, mid)
        claims = extract_claims(caption, posted) if caption.strip() else []
        if not claims:
            self._say(chat_id, texts.NO_CLAIMS, mid)
            return False
        self._say(chat_id, texts.claims_line(claims), mid)
        return True

    def _case(self, msg: dict, media: dict) -> None:
        chat_id, mid = msg["chat"]["id"], msg["message_id"]
        caption, posted = msg.get("caption") or "", self._posted_at(msg)
        if not self._claims_or_example(chat_id, mid, caption, posted):
            return
        if media["kind"] == "video":
            self._say(chat_id, texts.COMPRESSED_VIDEO, mid)
        elif media["kind"] == "photo":
            self._say(chat_id, texts.COMPRESSED_PHOTO, mid)

        size = media.get("size") or 0
        if size > config.TELEGRAM_MAX_DOWNLOAD_BYTES:
            return self._say(chat_id, texts.TOO_BIG.format(size=f"{size / 1024 / 1024:.0f}"), mid)
        try:
            content = self.tg.download(media["file_id"])
        except TelegramError as exc:
            if "too big" in exc.description.lower():
                return self._say(chat_id, texts.TOO_BIG.format(size="over 20"), mid)
            log.warning("download failed: %s", exc)
            return self._say(chat_id, texts.DOWNLOAD_FAILED, mid)
        try:
            inv_id = self.api.start(media["name"], content, media["mime"], caption, posted.isoformat())
        except AlibiError as exc:
            return self._say(chat_id, texts.API_ERROR.format(error=exc), mid)
        log.info("chat %s: started %s", chat_id, inv_id)
        self._follow(chat_id, mid, inv_id, rerun=False)

    def _rerun(self, msg: dict, parent_id: str, caption: str) -> None:
        chat_id, mid, posted = msg["chat"]["id"], msg["message_id"], self._posted_at(msg)
        if not self._claims_or_example(chat_id, mid, caption, posted):
            return
        try:
            inv_id = self.api.rerun(parent_id, caption, posted.isoformat())
        except AlibiError as exc:
            return self._say(chat_id, texts.API_ERROR.format(error=exc), mid)
        log.info("chat %s: re-run of %s started as %s", chat_id, parent_id, inv_id)
        self._follow(chat_id, mid, inv_id, rerun=True)

    def _follow(self, chat_id: int, reply_to: int, inv_id: str, rerun: bool) -> None:
        """Edit one progress message from the real event stream, then send the result + report."""
        marks: dict[str, str] = {}
        progress = self._say(chat_id, texts.progress(marks), reply_to)

        def update_progress(done: bool = False) -> None:
            if progress:
                try:
                    self.tg.edit_message(chat_id, progress["message_id"], texts.progress(marks, done))
                except TelegramError as exc:
                    log.warning("progress edit failed: %s", exc)

        outcome, error = None, ""
        stream = self.api.events(inv_id)
        try:
            for event, payload in stream:
                data = payload.get("data") or {}
                if event in ("witness_completed", "witness_failed"):
                    marks[data.get("witness", "?")] = (texts.mark(data.get("findings") or [])
                                                       if event == "witness_completed" else "?")
                    update_progress()
                elif event in ("done", "failed"):
                    outcome, error = event, data.get("error") or payload.get("detail", "")
                    break
        except AlibiError as exc:
            return self._say(chat_id, texts.API_ERROR.format(error=exc), reply_to)
        finally:
            stream.close()       # release the connection now, so the next request can reuse it

        if outcome == "failed":
            return self._say(chat_id, texts.FAILED.format(error=error), reply_to)
        if outcome != "done":
            return self._say(chat_id, texts.STREAM_LOST.format(url=f"{self.api.base}/api/investigations/{inv_id}"),
                             reply_to)
        update_progress(done=True)

        try:
            inv = self.api.investigation(inv_id)
        except AlibiError as exc:
            return self._say(chat_id, texts.API_ERROR.format(error=exc), reply_to)
        result = self._say(chat_id, texts.final(inv, rerun), reply_to)
        if result:
            self.state.remember_result(chat_id, result["message_id"], inv_id)
        try:
            report = self.api.report(inv_id)
            doc = self.tg.send_document(chat_id, f"alibi_report_{inv_id}.html", report, texts.REPORT_CAPTION,
                                        reply_to=result["message_id"] if result else reply_to)
            self.state.remember_result(chat_id, doc["message_id"], inv_id)   # replying to the report works too
        except (AlibiError, TelegramError) as exc:
            log.warning("report for %s not sent: %s", inv_id, exc)
