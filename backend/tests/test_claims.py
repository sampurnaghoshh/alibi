"""Claim extraction (§6.1): rules path offline, LLM path guarded, spans always traceable."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.claims import extract, llm
from app.claims.extract import extract_claims
from app.engine.explain import explain_findings
from app.schemas import Investigation

FIXTURE = json.loads(config.FIXTURE_PATH.read_text(encoding="utf-8"))
IST = timezone(timedelta(hours=5, minutes=30))
POSTED = datetime(2026, 11, 19, 9, 0, tzinfo=IST)


def _summary(claims):
    return [(c.type, c.source.text, c.normalized) for c in claims]


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "none")


@pytest.fixture
def fake_llm(monkeypatch):
    """Pretend the free Gemini provider is configured (with LLM rewording on);
    tests set what complete_json returns. Nothing touches the network."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_EXPLAIN", True)

    def install(fn):
        monkeypatch.setattr(llm, "complete_json", fn)
    return install


# --- rules path ------------------------------------------------------------------

def test_case_a_matches_fixture_exactly(no_llm):
    posted = datetime.fromisoformat(FIXTURE["posted_at"])
    claims = extract_claims(FIXTURE["caption"], posted)
    assert [c.model_dump(mode="json") for c in claims] == FIXTURE["claims"]


def test_bombay_resolves_to_mumbai(no_llm):
    [loc] = [c for c in extract_claims("Waterlogged streets in Bombay", POSTED) if c.type == "location"]
    assert loc.value == "Bombay"                          # value stays as written (traceable)
    assert loc.normalized == {"lat": 19.0760, "lon": 72.8777, "country": "IN"}


def test_last_night_is_relative_to_posted_at(no_llm):
    [t] = [c for c in extract_claims("Heavy rain last night", POSTED) if c.type == "time"]
    assert t.normalized == {
        "date": "2026-11-18",
        "start": "2026-11-18T20:00:00+05:30",
        "end": "2026-11-19T04:00:00+05:30",
        "precision": "part_of_day",
    }


@pytest.mark.parametrize("caption", ["", "   ", "Just a nice day out", "Eating puri with friends this weekend"])
def test_no_claims(no_llm, caption):
    assert extract_claims(caption, POSTED) == []


def test_explicit_dates_and_precisions(no_llm):
    claims = extract_claims("Cyclone hits Puri on 18 Nov 2024; flash floods in Bhubaneswar, video from November 2023", POSTED)
    got = {c.source.text: c.normalized for c in claims}
    assert got["18 Nov 2024"]["precision"] == "day" and got["18 Nov 2024"]["date"] == "2024-11-18"
    assert got["November 2023"]["precision"] == "month"
    assert got["Cyclone"] == {"condition": "storm"}
    assert got["flash floods"] == {"condition": "flood"}   # longest match, not just "floods"
    assert got["Puri"]["country"] == "IN"


def test_longest_match_dedupe_and_spans(no_llm):
    caption = "Flooding in New Delhi today; flooded roads across New Delhi"
    claims = extract_claims(caption, POSTED)
    assert _summary(claims)[:2] == [
        ("weather_condition", "Flooding", {"condition": "flood"}),
        ("location", "New Delhi", {"lat": 28.6139, "lon": 77.2090, "country": "IN"}),   # not "Delhi"
    ]
    assert len(claims) == 3                                 # repeated flood + repeated place deduped
    assert [c.id for c in claims] == ["c1", "c2", "c3"]
    for c in claims:
        assert caption[c.source.start:c.source.end] == c.source.text


def test_speaker_and_part_of_day(no_llm):
    claims = extract_claims("Speech by Rahul Gandhi in Kochi yesterday morning", POSTED)
    got = {c.type: c for c in claims}
    assert got["speaker"].value == "Rahul Gandhi"
    assert got["time"].normalized["start"] == "2026-11-18T06:00:00+05:30"
    assert got["time"].normalized["end"] == "2026-11-18T11:00:00+05:30"


def test_naive_posted_at_uses_ist(no_llm):
    [t] = [c for c in extract_claims("rain today", datetime(2026, 11, 19, 9, 0)) if c.type == "time"]
    assert t.normalized["start"] == "2026-11-19T00:00:00+05:30"


def test_no_llm_call_when_provider_none(no_llm, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("LLM must not be called")
    monkeypatch.setattr(llm, "complete_json", boom)
    assert len(extract_claims(FIXTURE["caption"], POSTED)) == 3


# --- LLM path ----------------------------------------------------------------------

def test_llm_hallucinated_span_dropped_and_new_claims_added(fake_llm, monkeypatch):
    caption = "Massive flooding in Chennai yesterday evening near Marina Beach"
    fake_llm(lambda system, prompt, max_tokens=0: [
        {"type": "location", "value": "Mumbai", "source_text": "Mumbai"},             # not in caption -> dropped
        {"type": "location", "value": "Marina Beach, Chennai", "source_text": "Marina Beach"},
        {"type": "event", "value": "flood", "source_text": "flooding"},                # overlaps rules -> rules win
        {"type": "nonsense", "value": "x", "source_text": "near"},                     # bad type -> dropped
    ])
    monkeypatch.setattr(extract, "geocode", lambda name: {"lat": 13.05, "lon": 80.28, "country": "IN"})

    claims = extract_claims(caption, POSTED)
    by_text = {c.source.text: c for c in claims}
    assert "Mumbai" not in by_text
    assert by_text["Marina Beach"].extracted_by == "llm"
    assert by_text["Marina Beach"].normalized == {"lat": 13.05, "lon": 80.28, "country": "IN"}
    assert by_text["flooding"].extracted_by == "rules" and by_text["flooding"].type == "weather_condition"
    assert [c.extracted_by for c in claims].count("rules") == 3
    for c in claims:
        assert caption[c.source.start:c.source.end] == c.source.text


def test_llm_error_falls_back_to_rules(fake_llm):
    posted = datetime.fromisoformat(FIXTURE["posted_at"])

    def fail(*a, **k):
        raise llm.LLMError("timeout")
    fake_llm(fail)
    assert [c.model_dump(mode="json") for c in extract_claims(FIXTURE["caption"], posted)] == FIXTURE["claims"]

    fake_llm(lambda *a, **k: {"not": "a list"})            # malformed reply -> also rules only
    assert [c.model_dump(mode="json") for c in extract_claims(FIXTURE["caption"], posted)] == FIXTURE["claims"]


class _FakeAnthropic:
    """Stands in for anthropic.Anthropic (never really called): records calls, can refuse."""

    calls: list = []
    refuse = False

    def __init__(self, **kwargs):
        self.messages = self

    def create(self, *, model, **kwargs):
        from types import SimpleNamespace as NS
        _FakeAnthropic.calls.append((model, sorted(kwargs)))
        if _FakeAnthropic.refuse:
            return NS(stop_reason="refusal", content=[])
        return NS(stop_reason="end_turn", content=[NS(type="text", text='[{"ok": true}]')])


@pytest.fixture
def fake_anthropic(monkeypatch):
    import anthropic
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)
    _FakeAnthropic.calls, _FakeAnthropic.refuse = [], False
    return _FakeAnthropic


def test_gemini_is_the_free_default_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    assert llm.model_name() == "gemini-2.5-flash-lite"


def test_refusal_is_one_call_then_rules_no_fallback_model(fake_anthropic):
    fake_anthropic.refuse = True
    with pytest.raises(llm.LLMError):
        llm.complete_json("s", "p")
    [(model, params)] = fake_anthropic.calls                 # exactly one call, to the configured model
    assert model == "claude-haiku-5-5"
    assert "fallbacks" not in params and "betas" not in params

    posted = datetime.fromisoformat(FIXTURE["posted_at"])
    assert [c.model_dump(mode="json") for c in extract_claims(FIXTURE["caption"], posted)] == FIXTURE["claims"]


def test_llm_rewording_off_by_default(monkeypatch):
    """Even with a provider configured, findings are not sent to the LLM unless ALIBI_LLM_EXPLAIN=1."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_EXPLAIN", config.os.getenv("ALIBI_LLM_EXPLAIN", "0") == "1")

    def boom(*a, **k):
        raise AssertionError("must not call the LLM")
    monkeypatch.setattr(llm, "complete_json", boom)
    f = Investigation.model_validate(FIXTURE).findings[0]
    assert explain_findings([f])[0].title == f.title


def test_env_example_is_free_first():
    from dotenv import dotenv_values
    values = dotenv_values(config.BACKEND_DIR / ".env.example")
    assert values["ALIBI_LLM_PROVIDER"] == "none"
    assert values["ALIBI_LLM_MODEL"] == ""               # inline comment is not part of the value
    assert values["ALIBI_LLM_EXPLAIN"] == "0"
    assert "anthropic = paid, do not use" in (config.BACKEND_DIR / ".env.example").read_text(encoding="utf-8")


def test_llm_json_parsing():
    assert llm.parse_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert llm.parse_json('Here you go: [1, 2]') == [1, 2]
    with pytest.raises(llm.LLMError):
        llm.parse_json("no json here")


# --- explanation rewrite hook --------------------------------------------------------

def test_explain_uses_llm_but_guard_keeps_numbers(fake_llm):
    findings = Investigation.model_validate(FIXTURE).findings
    weather = next(f for f in findings if f.id == "f_weather_1")
    prior = next(f for f in findings if f.id == "f_prior_1")

    def reword(system, prompt, max_tokens=0):
        if '"f_weather_1"' in prompt:   # changes a number -> must be rejected
            return {"title": "Rain records do not support flooding", "summary": "About 2 mm of rain fell."}
        # Same numbers (25, 2024 in the title; summary unchanged) -> accepted
        return {"title": "Frames match an earlier clip verified in Pune on 25 Jul 2024", "summary": prior.summary}
    fake_llm(reword)

    out = {f.id: f for f in explain_findings([weather, prior])}
    assert out["f_weather_1"].title == weather.title and out["f_weather_1"].summary == weather.summary
    assert out["f_prior_1"].title == "Frames match an earlier clip verified in Pune on 25 Jul 2024"
    assert out["f_prior_1"].status == prior.status and out["f_prior_1"].reliability == prior.reliability


def test_explain_llm_failure_keeps_text(fake_llm):
    f = Investigation.model_validate(FIXTURE).findings[0]

    def fail(*a, **k):
        raise llm.LLMError("down")
    fake_llm(fail)
    [out] = explain_findings([f])
    assert (out.title, out.summary) == (f.title, f.summary)
