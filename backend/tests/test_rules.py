"""Rule engine (§7): family statuses, headline, weak indicators, counts, score."""

import json

import pytest

from app import config
from app.engine.rules import assess, family_status
from app.schemas import Finding, Investigation

FIXTURE = json.loads(config.FIXTURE_PATH.read_text(encoding="utf-8"))


def mk(fid: str, witness: str, status: str, reliability: str) -> Finding:
    """Minimal valid finding for rule tests."""
    family = {"metadata": "context", "weather": "context", "prior_appearance": "context",
              "lipsync": "signal", "enf": "signal", "ela": "pixels"}[witness]
    return Finding(
        id=fid, witness=witness, family=family, status=status, reliability=reliability,
        title="t", summary="s", alternatives=["a"], limitations=["l"],
    )


def test_case_a_fixture_reproduces_its_assessment():
    inv = Investigation.model_validate(FIXTURE)
    a = assess(inv.findings)
    assert a.headline == "context_not_supported"
    assert a.experimental_score.value == 49
    assert a.experimental_score.label == "Experimental consistency index (not calibrated, not a probability)"
    assert a.families.model_dump() == FIXTURE["assessment"]["families"]
    assert a.model_dump(mode="json") == FIXTURE["assessment"]   # counts, weak indicators, texts: all of it


def test_all_consistent_two_families():
    a = assess([
        mk("f1", "weather", "consistent", "medium"),
        mk("f2", "prior_appearance", "consistent", "high"),
        mk("f3", "lipsync", "consistent", "medium"),
    ])
    assert a.headline == "consistent_so_far"
    assert a.headline_text == "No contradictions found in the available evidence"
    assert a.human_review == "standard"
    assert a.experimental_score.value == 100


def test_only_inconclusive_is_insufficient_evidence():
    a = assess([
        mk("f1", "metadata", "inconclusive", "low"),
        mk("f2", "enf", "insufficient_signal", "low"),
        mk("f3", "lipsync", "not_applicable", "low"),
    ])
    assert a.headline == "insufficient_evidence"
    assert a.families.model_dump() == {"context": "inconclusive", "signal": "inconclusive", "pixels": "not_applicable"}
    assert a.human_review == "standard"
    assert a.experimental_score.value == 97          # only the one inconclusive costs 3


def test_low_contradiction_is_weak_indicator_only():
    findings = [mk("f1", "metadata", "contradiction", "low")]
    a = assess(findings)
    assert a.weak_indicators == ["f1"]
    assert family_status(findings, "context") != "contradiction"
    assert a.families.context == "inconclusive"
    assert a.headline == "insufficient_evidence"
    assert a.human_review == "priority"             # any contradiction still asks for priority review
    assert a.experimental_score.value == 95


def test_low_contradiction_does_not_override_consistent():
    findings = [mk("f1", "metadata", "contradiction", "low"), mk("f2", "weather", "consistent", "medium")]
    assert family_status(findings, "context") == "consistent"


def test_two_medium_contradictions_same_family_count_once():
    a = assess([
        mk("f1", "weather", "contradiction", "medium"),
        mk("f2", "metadata", "contradiction", "medium"),
        mk("f3", "lipsync", "consistent", "medium"),
    ])
    assert a.headline == "partially_supported"
    assert a.headline_text == config.PARTIAL_HEADLINE_TEXT["context"]
    assert a.counts.contradiction == 2              # both still listed and counted
    assert a.experimental_score.value == 70         # and both still cost points


def test_medium_contradictions_in_two_families():
    a = assess([mk("f1", "weather", "contradiction", "medium"), mk("f2", "lipsync", "contradiction", "medium")])
    assert a.headline == "context_not_supported"
    assert a.families.context == a.families.signal == "contradiction"


def test_signal_only_high_bar():
    """Case B shape: one medium lip-sync contradiction -> partially_supported, signal text."""
    a = assess([mk("f1", "lipsync", "contradiction", "medium"), mk("f2", "metadata", "inconclusive", "low")])
    assert a.headline == "partially_supported"
    assert a.headline_text == config.PARTIAL_HEADLINE_TEXT["signal"]


def test_high_contradiction_alone_is_enough():
    a = assess([mk("f1", "prior_appearance", "contradiction", "high")])
    assert a.headline == "context_not_supported"


def test_counts_include_not_applicable_and_score_floor():
    findings = [mk(f"f{i}", "prior_appearance", "contradiction", "high") for i in range(5)]
    findings.append(mk("f9", "lipsync", "not_applicable", "low"))
    a = assess(findings)
    assert a.counts.not_applicable == 1 and a.counts.contradiction == 5
    assert a.experimental_score.value == 0


@pytest.mark.parametrize("key", list(config.HEADLINE_TEXT) + ["partial"])
def test_headline_texts_avoid_forbidden_words(key):
    import re
    texts = config.PARTIAL_HEADLINE_TEXT.values() if key == "partial" else [config.HEADLINE_TEXT[key]]
    for t in texts:
        assert not re.search(config.FORBIDDEN_WORDS_RE, t, re.IGNORECASE)
