"""Printable report: renders the fixture, orders findings, embeds artifacts, avoids forbidden words."""

import io
import json
import re

from PIL import Image

from app import config
from app.report import render_report
from app.schemas import Investigation
from app.storage import RunStore

FIXTURE = json.loads(config.FIXTURE_PATH.read_text(encoding="utf-8"))


def _inv() -> Investigation:
    return Investigation.model_validate(FIXTURE)


def test_fixture_report_renders_without_forbidden_words():
    html = render_report(_inv())
    assert html.startswith("<!doctype html>")
    assert "Human review required. This is an evidence report, not a verdict." in html
    assert FIXTURE["assessment"]["headline_text"] in html
    assert FIXTURE["inputs"][0]["sha256"] in html                      # full hash in chain of custody
    assert all(h["event"] in html for h in FIXTURE["history"])
    assert FIXTURE["assessment"]["experimental_score"]["label"] in html
    assert f"v{config.APP_VERSION}" in html
    assert not re.search(config.FORBIDDEN_WORDS_RE, html, re.IGNORECASE), \
        re.search(config.FORBIDDEN_WORDS_RE, html, re.IGNORECASE)


def test_claim_spans_highlighted():
    html = render_report(_inv())
    for c in FIXTURE["claims"]:
        assert f"{c['source']['text']}<sup>{c['id']}</sup></mark>" in html


def test_findings_ordered_contradictions_first_not_applicable_last():
    html = render_report(_inv())
    pos = {f["id"]: html.index(f"<code>{f['id']}</code> ·") for f in FIXTURE["findings"]}
    assert pos["f_prior_1"] < pos["f_weather_1"] < pos["f_metadata_1"] < pos["f_enf_1"] < pos["f_lipsync_1"]


def test_artifact_images_embedded_when_present(tmp_path):
    store = RunStore(FIXTURE["id"], root=tmp_path)
    store.artifacts_dir.mkdir(parents=True)
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
    (store.artifacts_dir / "ela_4.0s.png").write_bytes(buf.getvalue())

    html = render_report(_inv(), store)
    assert html.count("data:image/png;base64,") == 1                  # only the file that exists
    assert "data:image" not in render_report(_inv())                  # no store -> no embedding


def test_report_escapes_user_text():
    raw = json.loads(json.dumps(FIXTURE))
    raw["caption"] = raw["caption"] + " <script>alert(1)</script>"
    html = render_report(Investigation.model_validate(raw))
    assert "<script>" not in html and "&lt;script&gt;" in html
