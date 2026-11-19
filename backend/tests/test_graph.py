"""Graph builder (§4.5) and explanation templates/guard (§7.6)."""

import json

from app import config
from app.engine.explain import explain_findings, is_safe_rewrite
from app.engine.graph import build_graph
from app.schemas import Investigation

FIXTURE = json.loads(config.FIXTURE_PATH.read_text(encoding="utf-8"))


def _inv() -> Investigation:
    return Investigation.model_validate(FIXTURE)


def test_graph_from_fixture_matches_fixture_graph():
    inv = _inv()
    g = build_graph(inv.inputs, inv.claims, inv.findings)
    assert g.model_dump(mode="json") == FIXTURE["graph"]


def test_claim_status_is_worst_and_not_applicable_has_no_edges():
    inv = _inv()
    g = build_graph(inv.inputs, inv.claims, inv.findings)
    nodes = {n.id: n for n in g.nodes}
    assert nodes["c2"].status == "contradiction"      # contradiction beats inconclusive (metadata, enf)
    assert not [e for e in g.edges if e.source == "f_lipsync_1"]
    assert "f_lipsync_1" in nodes


def test_claim_without_findings_has_null_status_and_inconclusive_beats_consistent():
    inv = _inv()
    weather = next(f for f in inv.findings if f.id == "f_weather_1").model_copy(update={"status": "consistent"})
    meta = next(f for f in inv.findings if f.id == "f_metadata_1")
    g = build_graph(inv.inputs, inv.claims, [weather, meta])
    nodes = {n.id: n for n in g.nodes}
    assert nodes["c1"].status == "consistent"          # only weather points at c1
    assert nodes["c2"].status == "inconclusive"        # weather consistent + metadata inconclusive
    g2 = build_graph(inv.inputs, inv.claims, [])
    assert all(n.status is None for n in g2.nodes if n.type == "claim")


def test_claimless_finding_targets_input_from_locations():
    inv = _inv()
    ela = next(f for f in inv.findings if f.id == "f_ela_1")
    g = build_graph(inv.inputs, [], [ela])
    assert [(e.source, e.target, e.relation) for e in g.edges] == [("f_ela_1", "in_1", "inconclusive")]


# --- explanations ------------------------------------------------------------

def test_templates_fill_blank_text_without_touching_verdict():
    f = _inv().findings[1].model_copy(update={"title": " ", "summary": ""})
    [out] = explain_findings([f])
    assert out.title == "Weather evidence contradicts the stated context"
    assert out.summary and out.status == f.status and out.reliability == f.reliability
    assert out.details == f.details


def test_rewrite_guard():
    f = _inv().findings[1]   # weather summary numbers: 0.0, 17:00–21:00, 18, 0.0–0.4, 4, 1.2, 72
    ok_summary = ("Rainfall was 0.0 mm at the location during 17:00–21:00 on 18 Nov, "
                  "0.0–0.4 mm at 4 nearby points, and 1.2 mm in the prior 72 h.")
    assert is_safe_rewrite(f, "Rainfall records do not support flooding", ok_summary)
    assert not is_safe_rewrite(f, "Rainfall records do not support flooding", ok_summary.replace("1.2", "12"))
    assert not is_safe_rewrite(f, "This flood video is fake", ok_summary)
