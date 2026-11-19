"""Live Desk page (§14 step 3): GET /live, its static files, offline-only assets, wording, bot username."""

import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import config
from app.live import botinfo
from app.main import app

client = TestClient(app)
STATIC = config.LIVE_STATIC_DIR
OWN_FILES = ["index.html", "live.css", "live.js"]


def test_live_serves_the_page():
    r = client.get("/live")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "Human review required. This is an evidence report, not a verdict." in r.text
    assert "ALIBI" in r.text


def test_every_referenced_static_file_is_served():
    html = client.get("/live").text
    refs = re.findall(r'(?:src|href)="(/live/static/[^"]+)"', html)
    assert set(refs) == {"/live/static/live.css", "/live/static/vendor/cytoscape.min.js",
                         "/live/static/vendor/qrcode.js", "/live/static/live.js"}
    types = {"css": "text/css", "js": "javascript"}
    for ref in refs:
        r = client.get(ref)
        assert r.status_code == 200 and len(r.content) > 500, ref
        assert types[ref.rsplit(".", 1)[1]] in r.headers["content-type"], ref
    assert client.get("/live/static/nope.js").status_code == 404


def test_no_cdn_everything_is_vendored():
    for name in OWN_FILES:
        text = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"https?://", text), f"{name} must not load anything from the internet"
    readme = (STATIC / "README.md").read_text(encoding="utf-8")
    for lib, lic in (("cytoscape.min.js", "LICENSE-cytoscape.txt"), ("qrcode.js", "LICENSE-qrcode-generator.txt")):
        assert (STATIC / "vendor" / lib).stat().st_size > 10_000
        assert "MIT" in (STATIC / "vendor" / lic).read_text(encoding="utf-8")
        assert lib in readme and lic in readme
    assert "MIT" in readme


def test_no_forbidden_words_in_the_page():
    pattern = re.compile(config.FORBIDDEN_WORDS_RE, re.IGNORECASE)
    for name in OWN_FILES:
        text = (STATIC / name).read_text(encoding="utf-8")
        assert not pattern.search(text), f"{name}: {pattern.search(text)}"


def test_page_uses_section9_status_vocabulary():
    js = (STATIC / "live.js").read_text(encoding="utf-8")
    for label in ("Contradiction", "Consistent", "Inconclusive", "Insufficient signal", "Not applicable"):
        assert f'"{label}"' in js
    for icon in ("⚠️", "✅", "❔", "➖"):
        assert icon in js
    css = (STATIC / "live.css").read_text(encoding="utf-8")
    assert re.search(r"s-insufficient_signal\s*\{[^}]*dashed", css)            # grey dashed (§9)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_live_js_parses():
    r = subprocess.run(["node", "--check", str(STATIC / "live.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- bot username for the idle screen -----------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_cache():
    botinfo.reset_cache()
    yield
    botinfo.reset_cache()


def test_info_uses_env_override(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "alibi_desk_bot")
    assert client.get("/api/live/info").json() == {"bot_username": "alibi_desk_bot",
                                                   "bot_url": "https://t.me/alibi_desk_bot"}


def test_info_without_token_or_override_is_empty(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    assert client.get("/api/live/info").json() == {"bot_username": None, "bot_url": None}


def test_getme_result_is_cached_and_failures_back_off(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:fake")
    calls = []
    assert botinfo.bot_username(fetch=lambda: calls.append(1) or None) is None
    assert botinfo.bot_username(fetch=lambda: calls.append(1) or "late_bot") is None   # within the back-off
    assert len(calls) == 1
    botinfo.reset_cache()
    assert botinfo.bot_username(fetch=lambda: calls.append(1) or "alibi_desk_bot") == "alibi_desk_bot"
    assert botinfo.bot_username(fetch=lambda: calls.append(1) or "other") == "alibi_desk_bot"  # cached
    assert len(calls) == 2
