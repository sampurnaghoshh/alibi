"""The static Next.js export is served at "/" only after every API / Live Desk route (one-URL deploy)."""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


def test_frontend_mount_is_last_so_api_routes_win():
    names = [getattr(r, "name", "") for r in app.router.routes]
    if "frontend" not in names:
        pytest.skip("frontend/out not built here (it is in the Docker image)")
    assert names[-1] == "frontend"
    c = TestClient(app)
    assert c.get("/api/health").headers["content-type"].startswith("application/json")
    assert "Human review required" in c.get("/live").text
    r = c.get("/investigation?id=inv_x", follow_redirects=False)
    assert r.status_code in (301, 307, 308) and r.headers["location"].endswith("/investigation/?id=inv_x")


def test_frontend_dir_is_configurable():
    assert config.FRONTEND_DIR.name == "out" or "ALIBI_FRONTEND_DIR" in __import__("os").environ
