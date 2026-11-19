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
    assert not config.forbidden_word(html), config.forbidden_word(html)   # the judges' question is the one exemption
    assert "Is this media AI-generated or manipulated?" in html and "No manipulation indicators found" in html


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


def test_count_pills_are_pluralised():
    from app.report import COUNT_WORDS
    html = render_report(_inv())
    assert ">2 contradictions<" in html and ">2 inconclusive<" in html
    assert ">1 with insufficient signal<" in html and ">1 not applicable<" in html
    assert COUNT_WORDS["contradiction"] == ("contradiction", "contradictions")


def test_normalized_values_are_human_readable_with_raw_json_appendix():
    html = render_report(_inv())
    assert "13.0827° N, 80.2707° E (IN)" in html
    assert "18 Nov 2026, 17:00–21:00 IST · part of day" in html
    assert "<td>flood</td>" in html
    from html import unescape
    appendix = unescape(html[html.index("Appendix: raw claim data"):])
    assert '"lat": 13.0827' in appendix and '"precision": "part_of_day"' in appendix


def test_time_windows_read_naturally():
    from app.report import _time_window
    ist = "+05:30"
    assert _time_window({"start": f"2026-11-18T00:00:00{ist}", "end": f"2026-11-19T00:00:00{ist}",
                         "precision": "day"}) == "18 Nov 2026 · day"
    assert _time_window({"start": f"2026-11-09T00:00:00{ist}", "end": f"2026-11-16T00:00:00{ist}",
                         "precision": "week"}) == "9–15 Nov 2026 · week"
    assert _time_window({"start": f"2026-11-01T00:00:00{ist}", "end": f"2026-12-01T00:00:00{ist}",
                         "precision": "month"}) == "Nov 2026 · month"
    assert _time_window({"start": f"2026-11-18T20:00:00{ist}", "end": f"2026-11-19T04:00:00{ist}",
                         "precision": "part_of_day"}) == "18 Nov 2026, 20:00 – 19 Nov 2026, 04:00 IST · part of day"


def test_summary_sentence_is_the_shared_one():
    from app.engine.summary import summarize
    inv = _inv()
    sentence = summarize(inv.findings, inv.claims)["sentence"]
    html = render_report(inv)
    assert f'<p class="summary">{sentence}</p>' in html
    assert html.index("class=\"headline\"") < html.index('class="summary"') < html.index('class="pills"')


def test_time_machine_pairs_embedded_side_by_side(tmp_path):
    raw = json.loads(json.dumps(FIXTURE))
    prior = next(f for f in raw["findings"] if f["witness"] == "prior_appearance")
    prior["details"].update({
        "verified_date": "2026-10-05", "verified_location": "Jalahalli, Bengaluru Urban, Karnataka",
        "verified_by": "Adithi (team member, own recording)",
        "frame_matches": [{"upload_s": float(i), "archive_s": float(i), "hamming": 0,
                           "upload_frame": f"artifacts/frames/up_{i:03d}.jpg",
                           "archive_frame": f"artifacts/frames/ar_{i:03d}.jpg"} for i in range(3)]})
    inv = Investigation.model_validate(raw)
    store = RunStore(inv.id, root=tmp_path)
    (store.artifacts_dir / "frames").mkdir(parents=True)
    for i in range(3):
        for side in ("up", "ar"):
            Image.new("RGB", (36, 64), (10 * i, 80, 120)).save(store.artifacts_dir / "frames" / f"{side}_{i:03d}.jpg")
    html = render_report(inv, store)
    assert html.count('<div class="pair">') == 3
    assert html.count("data:image/jpeg;base64,") == 6
    assert "Verified record: 2026-10-05 · Jalahalli, Bengaluru Urban, Karnataka · Adithi" in html
    assert "<th>frame_matches</th>" not in html                       # not dumped as JSON in Key details


def test_report_escapes_user_text():
    raw = json.loads(json.dumps(FIXTURE))
    raw["caption"] = raw["caption"] + " <script>alert(1)</script>"
    html = render_report(Investigation.model_validate(raw))
    assert "<script>" not in html and "&lt;script&gt;" in html
