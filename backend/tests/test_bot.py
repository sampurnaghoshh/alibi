"""Telegram bot (§14 step 2) against a mocked Telegram Bot API and a mocked ALIBI API (httpx.MockTransport)."""

import json
import logging
import re
import threading
from datetime import datetime
from urllib.parse import parse_qs

import httpx
import pytest

from app import config
from app.bot import gps, texts
from app.bot.__main__ import RedactingFormatter
from app.bot.api import DOWN, AlibiClient
from app.bot.handler import Bot
from app.bot.state import BotState
from app.bot.telegram import TelegramClient, TelegramError

TOKEN = "987654321:AAF-very-secret-token_value"
FIXTURE = json.loads(config.FIXTURE_PATH.read_text(encoding="utf-8"))
INV, INV2 = "inv_20261119_120000_abcd", "inv_20261119_120100_efgh"
CHAT, STRANGER = 111, 999
CASE_A = "Massive flooding in Chennai yesterday evening, roads completely underwater"
DATE = 1763532000   # message time (unix)


class World:
    """Fake Telegram + fake ALIBI API on one MockTransport. Records every call."""

    def __init__(self):
        self.tg, self.api = [], []
        self.next_id = 500
        self.api_down = False
        self.tg_failures = 0              # next N Telegram calls fail with a network error
        self.update_batches = []          # getUpdates answers: list of lists, or "error"
        self.stop = threading.Event()

    def handle(self, req: httpx.Request) -> httpx.Response:
        return self.telegram(req) if req.url.host == "tg.test" else self.alibi(req)

    # --- Telegram ---------------------------------------------------------------------
    def telegram(self, req):
        if self.tg_failures:
            self.tg_failures -= 1
            raise httpx.ConnectError(f"connection refused for {req.url}")      # URL contains the token
        if req.url.path.startswith("/file/"):
            return httpx.Response(200, content=b"\x00\x00\x00\x18ftypmp42 fake video bytes")
        method = req.url.path.rsplit("/", 1)[1]
        body = json.loads(req.content) if req.headers.get("content-type", "").startswith("application/json") else {}
        self.tg.append((method, body, req.content))
        if method == "getUpdates":
            if not self.update_batches:
                self.stop.set()
                return httpx.Response(200, json={"ok": True, "result": []})
            batch = self.update_batches.pop(0)
            if batch == "error":
                return httpx.Response(502, json={"ok": False, "description": "Bad Gateway"})
            return httpx.Response(200, json={"ok": True, "result": batch})
        result = {"getMe": {"username": "alibi_test_bot"}, "editMessageText": True,
                  "getFile": {"file_path": "documents/file_7.mp4"}}.get(method)
        if method in ("sendMessage", "sendDocument"):
            self.next_id += 1
            result = {"message_id": self.next_id, "chat": {"id": body.get("chat_id")}, "text": body.get("text")}
        return httpx.Response(200, json={"ok": True, "result": result})

    def sent(self):
        return [b["text"] for m, b, _ in self.tg if m == "sendMessage"]

    def edits(self):
        return [b["text"] for m, b, _ in self.tg if m == "editMessageText"]

    def methods(self):
        return [m for m, _, _ in self.tg]

    # --- ALIBI --------------------------------------------------------------------------
    def alibi(self, req):
        if self.api_down:
            raise httpx.ConnectError("connection refused")
        path = req.url.path
        self.api.append((req.method, path, dict(req.url.params), req.content))
        if req.method == "POST" and path == "/api/investigations":
            return httpx.Response(202, json={"id": INV, "status": "running"})
        if req.method == "POST" and path.endswith("/rerun"):
            return httpx.Response(202, json={"id": INV2, "status": "running", "parent_id": INV})
        inv_id = path.split("/")[3]
        if path.endswith("/events"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body(inv_id))
        if path.endswith("/report"):
            return httpx.Response(200, content=b"<!doctype html><title>report</title>")
        return httpx.Response(200, json={**FIXTURE, "id": inv_id, "parent_id": INV if inv_id == INV2 else None})


def sse_body(inv_id: str) -> bytes:
    events = [("file_received", {}), ("claims_extracted", {"claims": FIXTURE["claims"]})]
    for f in FIXTURE["findings"]:
        events.append(("witness_started", {"witness": f["witness"]}))
    for f in FIXTURE["findings"]:
        events.append(("witness_completed", {"witness": f["witness"], "findings": [f]}))
    events += [("assessment_computed", {"assessment": FIXTURE["assessment"]}), ("graph_ready", {}),
               ("done", {"id": inv_id, "headline": FIXTURE["assessment"]["headline"]})]
    out = ": keepalive\n\n"
    for i, (e, data) in enumerate(events):
        out += f"id: {i}\nevent: {e}\ndata: {json.dumps({'event': e, 'detail': '', 'data': data})}\n\n"
    return out.encode()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_CHAT_IDS", {CHAT})
    monkeypatch.setattr(config, "TELEGRAM_PASSCODE", "open-sesame")
    monkeypatch.setattr(gps, "has_gps", lambda content, name="": None)   # tests set True/False when it matters
    w = World()
    http = httpx.Client(transport=httpx.MockTransport(w.handle))
    w.bot = Bot(TelegramClient(TOKEN, http=http, base="https://tg.test", sleep=lambda s: None),
                AlibiClient(base="https://alibi.test", http=http),
                BotState(tmp_path / "bot_chats.json"), workers=0)
    w.state_file = tmp_path / "bot_chats.json"
    return w


def update(chat=CHAT, mid=1, text=None, caption=None, document=None, video=None, photo=None, reply_to=None):
    msg = {"message_id": mid, "date": DATE, "chat": {"id": chat, "type": "private"}}
    for key, value in (("text", text), ("caption", caption), ("document", document), ("video", video),
                       ("photo", photo)):
        if value is not None:
            msg[key] = value
    if reply_to:
        msg["reply_to_message"] = {"message_id": reply_to, "chat": {"id": chat}}
    return {"update_id": mid, "message": msg}


DOC = {"file_id": "F1", "file_unique_id": "U1", "file_name": "clip.mp4", "mime_type": "video/mp4", "file_size": 3_000_000}


# --- the main flow ---------------------------------------------------------------------------

def test_file_with_caption_full_flow(world):
    world.bot.handle_update(update(caption=CASE_A, document=DOC))
    sent = world.sent()
    assert sent[0] == texts.RECEIVED
    assert sent[1] == "🔎 Claims found: Event: flooding · Location: Chennai · Time: yesterday evening"
    assert sent[2].startswith("🔍 Checking: Metadata …")
    assert texts.COMPRESSED_VIDEO not in sent

    # download, then the async API call with the caption and the message time
    assert world.methods().index("getFile") < len(world.methods())
    post = next(c for c in world.api if c[0] == "POST")
    assert post[1] == "/api/investigations" and post[2] == {"async": "true"}
    posted = datetime.fromtimestamp(DATE, tz=config.DEFAULT_TZ).isoformat()
    assert CASE_A.encode() in post[3] and posted.encode() in post[3] and b'filename="clip.mp4"' in post[3]

    # one progress message, edited as witnesses finish; icons by STATUS
    edits = world.edits()
    assert len(edits) == 7
    assert edits[0].startswith("🔍 Checking:") and "…" in edits[0]
    line = ("Metadata ❔ · Weather ⚠️ · Prior appearance ⚠️ · Lip-sync ➖ · Power-grid hum ❔ · ELA ❔")
    assert edits[-1] == "🔍 Analysis complete: " + line

    final = sent[3]
    assert final.startswith("🧾 " + FIXTURE["assessment"]["headline_text"])
    assert "2 contradictions · 2 inconclusive · 1 insufficient signal · 1 not applicable" in final
    assert line in final and texts.LEGEND in final
    assert "• Frames match a clip verified as recorded in Pune on 25 Jul 2024: 9 of 14" in final
    assert "• Recorded rainfall does not support the stated flooding: 0.0 mm" in final
    assert texts.HUMAN_REVIEW in final
    assert "🔎 Is this media AI-generated or manipulated? No manipulation indicators found" in final

    doc = next(c for c in world.tg if c[0] == "sendDocument")
    assert f'filename="alibi_report_{INV}.html"'.encode() in doc[2]
    # every bot reply answers the user's message
    assert all(b.get("reply_parameters", {}).get("message_id") in (1, None) for m, b, _ in world.tg if m == "sendMessage")
    saved = json.loads(world.state_file.read_text())
    assert INV in saved["results"].values()


@pytest.mark.parametrize("statuses,icon", [
    (["contradiction", "consistent"], "⚠️"),
    (["consistent", "inconclusive"], "✅"),        # metadata: date consistent + GPS inconclusive
    (["inconclusive"], "❔"),
    (["insufficient_signal"], "❔"),
    (["not_applicable"], "➖"),
    ([], "❔"),
])
def test_icon_by_status(statuses, icon):
    assert texts.mark([{"status": s} for s in statuses]) == icon


def test_gps_tip_when_file_has_no_location(world, monkeypatch):
    monkeypatch.setattr(gps, "has_gps", lambda content, name="": False)
    world.bot.handle_update(update(caption=CASE_A, document=DOC))
    received_id = next(r for r in world.tg if r[0] == "sendMessage")   # first message the bot sent
    tip = [b for m, b, _ in world.tg if m == "editMessageText" and texts.GPS_TIP in b["text"]]
    assert len(tip) == 1 and tip[0]["text"] == texts.RECEIVED + "\n\n" + texts.GPS_TIP
    assert tip[0]["message_id"] == 501 and received_id                 # the "Received" message is edited
    assert "Options → turn on Location" in texts.HELP


def test_no_gps_tip_when_location_present_or_compressed(world, monkeypatch):
    monkeypatch.setattr(gps, "has_gps", lambda content, name="": True)
    world.bot.handle_update(update(caption=CASE_A, document=DOC))
    monkeypatch.setattr(gps, "has_gps", lambda content, name="": False)
    video = {"file_id": "V1", "file_unique_id": "UV", "mime_type": "video/mp4", "file_size": 2_000_000}
    world.bot.handle_update(update(mid=2, caption=CASE_A, video=video))      # already warned to send as File
    assert not any(texts.GPS_TIP in t for t in world.edits())


@pytest.mark.skipif(not __import__("shutil").which("exiftool"), reason="exiftool not installed")
def test_has_gps_reads_real_files(tmp_path):
    import io
    import subprocess

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (90, 120, 150)).save(buf, format="JPEG")
    plain = buf.getvalue()
    tagged = tmp_path / "gps.jpg"
    tagged.write_bytes(plain)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-GPSLatitude=12.97", "-GPSLatitudeRef=N",
                    "-GPSLongitude=77.59", "-GPSLongitudeRef=E", str(tagged)], check=True)
    assert gps.has_gps(plain, "plain.jpg") is False
    assert gps.has_gps(tagged.read_bytes(), "gps.jpg") is True
    assert gps.has_gps(b"", "empty.jpg") is None


def test_compressed_video_gets_a_warning(world):
    video = {"file_id": "V1", "file_unique_id": "UV", "mime_type": "video/mp4", "file_size": 2_000_000}
    world.bot.handle_update(update(caption=CASE_A, video=video))
    assert texts.COMPRESSED_VIDEO in world.sent()
    assert b'filename="telegram_video_UV.mp4"' in next(c for c in world.api if c[0] == "POST")[3]


def test_file_over_20_mb_is_declined_politely(world):
    world.bot.handle_update(update(caption=CASE_A, document={**DOC, "file_size": 25 * 1024 * 1024}))
    assert any("25 MB" in t and "20 MB" in t for t in world.sent())
    assert "getFile" not in world.methods() and not world.api


@pytest.mark.parametrize("caption", [None, "", "look at this!!"])
def test_no_caption_runs_the_file_only_analysis(world, caption):
    world.bot.handle_update(update(caption=caption, document=DOC))
    sent = world.sent()
    assert sent[:2] == [texts.RECEIVED, texts.FILE_ONLY]
    assert "getFile" in world.methods()
    post = next(c for c in world.api if c[0] == "POST")
    assert post[1] == "/api/investigations"                       # analysed anyway, file only
    final = next(t for t in sent if t.startswith("🧾"))
    assert "Is this media AI-generated or manipulated?" in final


def test_reply_without_a_claim_still_asks_for_one(world):
    world.bot.handle_update(update(caption=CASE_A, document=DOC))
    result_msg_id = world.next_id - 1
    world.bot.handle_update(update(mid=2, text="hmm", reply_to=result_msg_id))
    assert world.sent()[-1] == texts.NO_CLAIMS                      # a re-run needs a new story


# --- re-run ----------------------------------------------------------------------------------

def test_reply_to_result_reruns_with_new_caption(world):
    world.bot.handle_update(update(caption=CASE_A, document=DOC))
    result_id = next(c for c in world.tg if c[0] == "sendMessage" and c[1]["text"].startswith("🧾"))
    result_msg_id = world.next_id - 1                 # the report document came right after it
    assert world.bot.state.result_for(CHAT, result_msg_id) == INV

    world.bot.handle_update(update(mid=2, text="Heavy rain in Pune on 25 Jul 2024", reply_to=result_msg_id))
    rerun = next(c for c in world.api if c[0] == "POST" and c[1].endswith("/rerun"))
    assert rerun[1] == f"/api/investigations/{INV}/rerun" and rerun[2] == {"async": "true"}
    assert json.loads(rerun[3])["caption"] == "Heavy rain in Pune on 25 Jul 2024"
    assert world.sent()[-1].startswith(texts.RERUN_PREFIX + FIXTURE["assessment"]["headline_text"])
    assert result_id


def test_reply_to_other_message_explains(world):
    world.bot.handle_update(update(mid=3, text="Flooding in Chennai today", reply_to=42))
    assert world.sent() == [texts.NOT_A_RESULT] and not world.api


# --- access control ----------------------------------------------------------------------------

def test_unknown_chat_is_ignored(world):
    world.bot.handle_update(update(chat=STRANGER, caption=CASE_A, document=DOC))
    world.bot.handle_update(update(chat=STRANGER, mid=2, text="/help"))
    world.bot.handle_update(update(chat=STRANGER, mid=3, text="/start wrong-code"))
    world.bot.handle_update(update(chat=STRANGER, mid=4, text="/start"))
    assert world.tg == [] and world.api == []


def test_passcode_approves_and_persists(world, tmp_path):
    world.bot.handle_update(update(chat=STRANGER, text="/start open-sesame"))
    assert world.sent()[0].startswith("✅ This chat is now approved.")
    assert json.loads(world.state_file.read_text())["approved"] == [STRANGER]
    assert BotState(world.state_file).is_allowed(STRANGER)          # survives a restart
    world.bot.handle_update(update(chat=STRANGER, mid=2, caption=CASE_A, document=DOC))
    assert any(c[0] == "POST" for c in world.api)


# --- robustness -------------------------------------------------------------------------------

def test_api_down_gives_a_clear_message(world):
    world.api_down = True
    world.bot.handle_update(update(caption=CASE_A, document=DOC))
    assert world.sent()[-1] == texts.API_ERROR.format(error=DOWN)


def test_bad_update_does_not_raise(world):
    world.bot.handle_update({"update_id": 9, "message": {"message_id": 1}})     # no chat
    world.bot.handle_update({"update_id": 10})                                   # no message
    world.bot.handle_update(update(mid=11, text="/help"))
    assert world.sent() == [texts.HELP]


def test_network_errors_are_retried(world):
    world.tg_failures = 2
    world.bot.handle_update(update(text="/help"))
    assert world.sent() == [texts.HELP]


def test_poll_loop_survives_errors_and_keeps_going(world):
    world.update_batches = ["error", "error", "error", [update(text="/help")], [{"update_id": 99}]]
    world.bot.run_forever(stop=world.stop, sleep=lambda s: None)
    assert world.sent() == [texts.HELP]
    offsets = [b.get("offset") for m, b, _ in world.tg if m == "getUpdates"]
    assert offsets[-1] == 100                         # acknowledged past the last update


# --- secrets and wording ----------------------------------------------------------------------

def test_token_never_in_logs_or_errors(world, caplog, monkeypatch):
    logging.getLogger("httpx").setLevel(logging.WARNING)    # what app.bot.__main__ does
    caplog.set_level(logging.DEBUG)
    world.tg_failures = 10                                   # every Telegram call fails, URL in the error
    world.bot.handle_update(update(caption=CASE_A, document=DOC))
    world.tg_failures = 3                                    # all 3 attempts of the next call fail
    with pytest.raises(TelegramError) as exc:
        world.bot.tg.send_message(CHAT, "hi")
    assert TOKEN not in str(exc.value) and exc.value.__cause__ is None
    assert TOKEN not in caplog.text
    rec = logging.LogRecord("x", logging.ERROR, __file__, 1, f"boom at /bot{TOKEN}/sendMessage", None, None)
    assert TOKEN not in RedactingFormatter(TOKEN, "%(message)s").format(rec)


FORBIDDEN = re.compile(config.FORBIDDEN_WORDS_RE, re.IGNORECASE)


def test_no_forbidden_words_in_bot_text(world):
    strings = [v for k, v in vars(texts).items() if k.isupper() and isinstance(v, str)]
    world.bot.handle_update(update(caption=CASE_A, document=DOC))
    strings += world.sent() + world.edits()
    for s in strings:
        assert not config.forbidden_word(s), s          # the judges' question is the one exemption
