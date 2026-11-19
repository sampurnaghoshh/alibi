"""The fixture, schemas.py and shared/contract.schema.json must agree exactly."""

import copy
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas import (
    SCHEMA_PATH,
    Finding,
    Investigation,
    InvestigationSummary,
    render_contract_json_schema,
)

FIXTURE = Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "sample_investigation.json"

# §2.7 / §9: words that must never appear in UI or report text.
FORBIDDEN = re.compile(r"\b(fake|real|authentic\w*|proven|definitely|AI-generated)\b", re.IGNORECASE)


@pytest.fixture
def raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_validates(raw):
    inv = Investigation.model_validate(raw)
    assert inv.assessment.headline == "context_not_supported"


def test_fixture_round_trips_exactly(raw):
    """No field silently dropped, renamed or defaulted: dump(validate(x)) == x."""
    assert Investigation.model_validate(raw).model_dump(mode="json") == raw


def test_json_schema_is_up_to_date():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render_contract_json_schema(), (
        "shared/contract.schema.json is stale: run `python -m app.schemas` from backend/"
    )


def test_summary_from_fixture(raw):
    s = InvestigationSummary.from_investigation(Investigation.model_validate(raw)).model_dump(mode="json")
    assert s == {
        "id": raw["id"],
        "created_at": raw["created_at"],
        "parent_id": None,
        "caption": raw["caption"],
        "inputs": ["chennai_flood.mp4"],
        "headline": raw["assessment"]["headline"],
        "headline_text": raw["assessment"]["headline_text"],
        "counts": raw["assessment"]["counts"],
        "human_review": "priority",
    }
    assert "InvestigationSummary" in json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]


def test_fixture_has_no_forbidden_words(raw):
    texts = [raw["caption"], raw["assessment"]["headline_text"]]
    for f in raw["findings"]:
        texts += [f["title"], f["summary"], *f["alternatives"], *f["limitations"]]
    for t in texts:
        assert not FORBIDDEN.search(t), f"forbidden word in: {t!r}"


# --- the contract rejects common mistakes ---------------------------------

def _finding(raw, fid):
    return next(f for f in raw["findings"] if f["id"] == fid)


@pytest.mark.parametrize("field", ["alternatives", "limitations"])
def test_empty_alternatives_or_limitations_rejected(raw, field):
    f = copy.deepcopy(_finding(raw, "f_weather_1"))
    f[field] = []
    with pytest.raises(ValidationError):
        Finding.model_validate(f)


def test_wrong_family_rejected(raw):
    f = copy.deepcopy(_finding(raw, "f_weather_1"))
    f["family"] = "signal"
    with pytest.raises(ValidationError, match="belongs to family"):
        Finding.model_validate(f)


def test_reliability_above_cap_rejected(raw):
    f = copy.deepcopy(_finding(raw, "f_ela_1"))
    f["reliability"] = "medium"  # ELA is always low
    with pytest.raises(ValidationError, match="capped"):
        Finding.model_validate(f)


def test_unknown_field_rejected(raw):
    f = copy.deepcopy(_finding(raw, "f_weather_1"))
    f["confidence"] = 0.9
    with pytest.raises(ValidationError):
        Finding.model_validate(f)


def test_bbox_outside_frame_rejected(raw):
    f = copy.deepcopy(_finding(raw, "f_ela_1"))
    f["locations"][0]["bbox"] = [0.5, 0.5, 0.6, 0.1]
    with pytest.raises(ValidationError, match="outside the frame"):
        Finding.model_validate(f)


def test_claim_span_must_match_caption(raw):
    raw["claims"][1]["source"].update(start=19, end=26)  # off by one: " Chenna"
    with pytest.raises(ValidationError, match="caption"):
        Investigation.model_validate(raw)


def test_bad_time_normalized_rejected(raw):
    raw["claims"][2]["normalized"]["precision"] = "evening"
    with pytest.raises(ValidationError):
        Investigation.model_validate(raw)


def test_dangling_claim_id_rejected(raw):
    raw["findings"][0]["claim_ids"].append("c99")
    with pytest.raises(ValidationError, match="unknown claim_ids"):
        Investigation.model_validate(raw)


def test_counts_must_match_findings(raw):
    raw["assessment"]["counts"]["consistent"] = 1
    with pytest.raises(ValidationError, match="counts"):
        Investigation.model_validate(raw)


def test_dangling_graph_edge_rejected(raw):
    raw["graph"]["edges"].append({"id": "e99", "source": "f_nope", "target": "c1", "relation": "supports"})
    with pytest.raises(ValidationError, match="missing node"):
        Investigation.model_validate(raw)
