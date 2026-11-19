"""CORS for the Vercel frontend and the Cloudflare tunnel (Vercel + laptop deployment)."""

from fastapi.testclient import TestClient

from app import config
from app.main import app

client = TestClient(app)


def _allowed(origin: str) -> str | None:
    r = client.options("/api/demo", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
    return r.headers.get("access-control-allow-origin")


def test_vercel_and_tunnel_origins_are_allowed():
    for origin in ("https://alibi.vercel.app", "https://alibi-git-main-team.vercel.app",
                   "https://quiet-river-1234.trycloudflare.com", "http://localhost:3000"):
        assert _allowed(origin) == origin, origin


def test_other_origins_are_not():
    for origin in ("https://evil.example", "http://alibi.vercel.app",            # http, not https
                   "https://alibi.vercel.app.evil.example", "https://vercel.app.evil.example",
                   "https://notvercel.app"):
        assert _allowed(origin) is None, origin


def test_simple_get_from_vercel_gets_the_header():
    r = client.get("/api/health", headers={"Origin": "https://alibi.vercel.app"})
    assert r.headers.get("access-control-allow-origin") == "https://alibi.vercel.app"


def test_extra_origins_from_env_list():
    assert config.parse_origins(" https://a.example, https://b.example/ ,,") == ["https://a.example", "https://b.example"]
    assert config.parse_origins("") == []
