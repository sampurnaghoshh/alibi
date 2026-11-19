"""One-container deploy (Dockerfile, Hugging Face Space): in sync, no secrets, single worker, same-origin."""

import importlib.util
import json
import re

from app import config

REPO = config.REPO_ROOT
DOCKERFILE = (REPO / "Dockerfile").read_text(encoding="utf-8")
START = (REPO / "deploy" / "start.sh").read_text(encoding="utf-8")


def _space_module():
    spec = importlib.util.spec_from_file_location("make_space", REPO / "deploy" / "huggingface" / "make_space_dockerfile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_space_dockerfile_is_generated_from_the_root_one():
    mod = _space_module()
    committed = (REPO / "deploy" / "huggingface" / "Dockerfile").read_text(encoding="utf-8")
    assert committed == mod.convert(DOCKERFILE, "main", mod.DEFAULT_REPO_URL), \
        "run: python deploy/huggingface/make_space_dockerfile.py"
    # every build-context COPY reads from the pinned source checkout instead
    for line in committed.splitlines():
        if line.startswith("COPY") and "--from=web" not in line:
            assert "--from=source /src/" in line, line


def test_space_readme_frontmatter():
    readme = (REPO / "deploy" / "huggingface" / "README.md").read_text(encoding="utf-8")
    front = readme.split("---")[1]
    for key in ("title: ALIBI", "sdk: docker", "app_port: 7860"):
        assert key in front, key
    # the repo's own README is not a Space config
    assert not (REPO / "README.md").read_text(encoding="utf-8").startswith("---")


def test_no_secrets_baked_into_the_image():
    instructions = "\n".join(l for l in DOCKERFILE.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"(TOKEN|API_KEY|PASSCODE|SECRET)\s*=", instructions)
    ignore = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for must in ("**/.env", "data/runs/*", "data/cache/*", "data/bot_chats.json", "backend/.venv", "frontend/node_modules"):
        assert must in ignore, must
    assert "ALIBI_LLM_PROVIDER=none" in DOCKERFILE


def test_single_worker_no_reload_and_bot_only_with_a_token():
    assert "--workers 1" in START and "--reload" not in START
    assert 'if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]' in START
    assert "python -m app.bot" in START.split('if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]')[1].split("fi")[0]
    assert "7860" in START and "EXPOSE 7860" in DOCKERFILE
    assert "\r" not in START                       # runs on Linux


def test_frontend_is_built_same_origin_as_a_static_export():
    assert 'NEXT_PUBLIC_API_URL=""' in DOCKERFILE
    next_config = (REPO / "frontend" / "next.config.ts").read_text(encoding="utf-8")
    assert 'output: "export"' in next_config and "trailingSlash: true" in next_config
    assert (REPO / "frontend" / "app" / "investigation" / "page.tsx").is_file()
    assert not (REPO / "frontend" / "app" / "investigation" / "[id]").exists()


def test_seed_cache_is_plain_weather_json():
    files = sorted((REPO / "deploy" / "seed-cache").glob("*.json"))
    assert files
    for f in files:
        json.loads(f.read_text(encoding="utf-8"))
        assert re.fullmatch(r"(openmeteo|weather)_[0-9a-f]+\.json", f.name), f.name


def test_licence_and_notices_present():
    assert (REPO / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    notices = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for item in ("Cytoscape", "qrcode-generator", "Inter", "IBM Plex Mono", "Lucide", "MediaPipe", "Open-Meteo", "CC BY 4.0"):
        assert item in notices, item
