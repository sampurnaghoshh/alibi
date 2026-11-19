"""
ALIBI Evidence Contract (CLAUDE.md §4). Owner: Sampurna.

Every witness returns list[Finding]. The API returns Investigation.
The frontend reads the JSON Schema mirror in shared/contract.schema.json,
which is generated from this file:

    cd backend
    python -m app.schemas          # rewrites ../shared/contract.schema.json

Contract changes are made only by Sampurna and announced in the group.
tests/test_contract.py fails if the fixture or the JSON Schema drift from this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

# ---------------------------------------------------------------------------
# 4.1 Enums
# ---------------------------------------------------------------------------

Family = Literal["context", "signal", "pixels"]
Status = Literal["consistent", "contradiction", "inconclusive", "insufficient_signal", "not_applicable"]
Reliability = Literal["high", "medium", "low"]
ClaimType = Literal["location", "time", "event", "weather_condition", "speaker", "other"]
WitnessName = Literal["metadata", "weather", "prior_appearance", "lipsync", "enf", "ela"]

# Family status after the rule engine folds findings together (§7.2).
# insufficient_signal folds into inconclusive, so it never appears here.
FamilyStatus = Literal["consistent", "contradiction", "inconclusive", "not_applicable"]

Headline = Literal[
    "context_not_supported",
    "signals_inconsistent",
    "partially_supported",
    "consistent_so_far",
    "insufficient_evidence",
]
HumanReview = Literal["priority", "standard"]
Condition = Literal["flood", "heavy_rain", "rain", "dry", "storm", "other"]
TimePrecision = Literal["hour", "part_of_day", "day", "week", "month", "year"]
ExtractedBy = Literal["llm", "rules"]
EvidenceKind = Literal["api", "artifact", "metadata_field", "archive_match", "input"]
NodeType = Literal["input", "claim", "witness"]
EdgeRelation = Literal["supports", "contradicts", "inconclusive", "derived_from"]

# Witness -> family (fixed, §4.1).
WITNESS_FAMILY: dict[str, str] = {
    "metadata": "context",
    "weather": "context",
    "prior_appearance": "context",
    "lipsync": "signal",
    "enf": "signal",
    "ela": "pixels",
}

# Max reliability per witness (§5). A finding above its cap is a bug.
WITNESS_MAX_RELIABILITY: dict[str, str] = {
    "metadata": "medium",
    "weather": "medium",
    "prior_appearance": "high",
    "lipsync": "medium",
    "enf": "medium",
    "ela": "low",
}
RELIABILITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

EXPERIMENTAL_SCORE_LABEL = "Experimental consistency index (not calibrated, not a probability)"

SHA256_PATTERN = r"^[0-9a-f]{64}$"
INVESTIGATION_ID_PATTERN = r"^inv_\d{8}_\d{6}_[0-9a-z]{4}$"


class _Model(BaseModel):
    """Base: unknown fields are rejected so typos fail loudly instead of silently vanishing."""

    model_config = ConfigDict(
        extra="forbid",
        # In the generated JSON Schema, fields with defaults are still "required"
        # because the API always sends them. Keeps the frontend types honest.
        json_schema_serialization_defaults_required=True,
    )


# ---------------------------------------------------------------------------
# 4.2 Claim
# ---------------------------------------------------------------------------

class LocationNormalized(BaseModel):
    """normalized for type=location. Extra keys (e.g. admin1, geocoder name) are allowed."""

    model_config = ConfigDict(extra="allow")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    country: Optional[str] = Field(default=None, description="ISO 3166-1 alpha-2, e.g. 'IN'")


class TimeNormalized(_Model):
    """normalized for type=time. Relative dates resolve against posted_at.

    precision: 'part_of_day' is used for 'evening' (17-21), 'morning' (06-11), 'night' (20-04).
    """

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD (start date of the window)")
    start: AwareDatetime
    end: AwareDatetime
    precision: TimePrecision

    @model_validator(mode="after")
    def _window_order(self) -> "TimeNormalized":
        if self.end < self.start:
            raise ValueError("time window end is before start")
        return self


class ConditionNormalized(_Model):
    """normalized for type=weather_condition and type=event."""

    condition: Condition


# Which typed shape `normalized` must satisfy for each claim type.
_NORMALIZED_MODEL: dict[str, type[BaseModel]] = {
    "location": LocationNormalized,
    "time": TimeNormalized,
    "weather_condition": ConditionNormalized,
    "event": ConditionNormalized,
}


def _normalized_json_schema(schema: dict[str, Any]) -> None:
    """Document the per-type shapes of Claim.normalized in the JSON Schema."""
    schema.pop("anyOf", None)
    schema["anyOf"] = [
        LocationNormalized.model_json_schema(mode="serialization"),
        TimeNormalized.model_json_schema(mode="serialization"),
        ConditionNormalized.model_json_schema(mode="serialization"),
        {"type": "object", "description": "speaker / other: free-form"},
        {"type": "null"},
    ]


class ClaimSource(_Model):
    """Exact character span of the caption that produced the claim (principle 3)."""

    field: Literal["caption"] = "caption"
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _span_length(self) -> "ClaimSource":
        if self.end - self.start != len(self.text):
            raise ValueError(f"source span [{self.start}:{self.end}] length != len(text) ({len(self.text)})")
        return self


class Claim(_Model):
    id: str = Field(min_length=1)
    type: ClaimType
    value: str
    # Kept as a plain dict on the wire; validated against the typed shape for its claim type.
    normalized: Optional[dict[str, Any]] = Field(default=None, json_schema_extra=_normalized_json_schema)
    source: ClaimSource
    extracted_by: ExtractedBy

    @model_validator(mode="after")
    def _check_normalized(self) -> "Claim":
        shape = _NORMALIZED_MODEL.get(self.type)
        if shape is not None and self.normalized is not None:
            shape.model_validate(self.normalized)
        return self


# ---------------------------------------------------------------------------
# 4.3 Finding
# ---------------------------------------------------------------------------

class Evidence(_Model):
    kind: EvidenceKind
    ref: str = Field(min_length=1)
    label: str = Field(min_length=1)


class TimestampLocation(_Model):
    kind: Literal["timestamp"] = "timestamp"
    file_id: str
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)

    @model_validator(mode="after")
    def _order(self) -> "TimestampLocation":
        if self.end_s < self.start_s:
            raise ValueError("end_s is before start_s")
        return self


Unit = Annotated[float, Field(ge=0, le=1)]


class RegionLocation(_Model):
    kind: Literal["region"] = "region"
    file_id: str
    frame_s: Optional[float] = Field(default=None, ge=0, description="null for still images")
    bbox: list[Unit] = Field(min_length=4, max_length=4, description="normalized [x, y, w, h] in 0-1")

    @model_validator(mode="after")
    def _inside_frame(self) -> "RegionLocation":
        x, y, w, h = self.bbox
        if x + w > 1 + 1e-6 or y + h > 1 + 1e-6:
            raise ValueError("bbox extends outside the frame")
        return self


Location = Annotated[Union[TimestampLocation, RegionLocation], Field(discriminator="kind")]


class Finding(_Model):
    id: str = Field(min_length=1)
    witness: WitnessName
    family: Family
    claim_ids: list[str] = Field(default_factory=list)
    status: Status
    reliability: Reliability
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list, description="may be empty for whole-file findings")
    alternatives: list[str] = Field(min_length=1, description="never empty (principle 4)")
    limitations: list[str] = Field(min_length=1, description="never empty (principle 4)")
    elapsed_ms: int = Field(default=0, ge=0, description="filled in by the runner")

    @model_validator(mode="after")
    def _witness_rules(self) -> "Finding":
        expected = WITNESS_FAMILY[self.witness]
        if self.family != expected:
            raise ValueError(f"witness '{self.witness}' belongs to family '{expected}', not '{self.family}'")
        cap = WITNESS_MAX_RELIABILITY[self.witness]
        if RELIABILITY_RANK[self.reliability] > RELIABILITY_RANK[cap]:
            raise ValueError(f"witness '{self.witness}' reliability is capped at '{cap}' (§5)")
        return self


# ---------------------------------------------------------------------------
# 4.4 Investigation
# ---------------------------------------------------------------------------

class InputFile(_Model):
    file_id: str = Field(min_length=1)
    name: str
    mime: str
    size: int = Field(ge=0, description="bytes")
    sha256: str = Field(pattern=SHA256_PATTERN)
    duration_s: Optional[float] = Field(default=None, ge=0, description="null for images")
    stored_path: str = Field(
        min_length=1,
        description="Read-only original, relative to the run folder. "
        "Served at /api/files/{inv_id}/{stored_path}.",
    )


class StatusCounts(_Model):
    consistent: int = Field(default=0, ge=0)
    contradiction: int = Field(default=0, ge=0)
    inconclusive: int = Field(default=0, ge=0)
    insufficient_signal: int = Field(default=0, ge=0)
    not_applicable: int = Field(default=0, ge=0)


class FamilyStatuses(_Model):
    context: FamilyStatus = "not_applicable"
    signal: FamilyStatus = "not_applicable"
    pixels: FamilyStatus = "not_applicable"


class ExperimentalScore(_Model):
    value: int = Field(ge=0, le=100)
    label: str = EXPERIMENTAL_SCORE_LABEL


class Assessment(_Model):
    headline: Headline
    headline_text: str
    counts: StatusCounts
    families: FamilyStatuses
    weak_indicators: list[str] = Field(default_factory=list, description="finding ids")
    human_review: HumanReview
    experimental_score: Optional[ExperimentalScore] = None


class GraphNode(_Model):
    id: str
    type: NodeType
    label: str
    status: Optional[Status] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(_Model):
    id: str
    source: str
    target: str
    relation: EdgeRelation


class Graph(_Model):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class HistoryEvent(_Model):
    ts: AwareDatetime
    event: str = Field(
        description="e.g. file_received, claims_extracted, witness_completed, assessment_computed"
    )
    detail: str = ""


def _dupes(ids: list[str]) -> set[str]:
    seen: set[str] = set()
    return {i for i in ids if i in seen or seen.add(i)}


class Investigation(_Model):
    id: str = Field(pattern=INVESTIGATION_ID_PATTERN, description="inv_YYYYMMDD_HHMMSS_xxxx")
    created_at: AwareDatetime
    posted_at: AwareDatetime = Field(description="user-supplied 'when was this shared', default now")
    parent_id: Optional[str] = Field(
        default=None, pattern=INVESTIGATION_ID_PATTERN,
        description="Set on a caption re-run: the investigation whose stored files were re-used.",
    )
    caption: str = ""
    inputs: list[InputFile] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    assessment: Assessment
    graph: Graph = Field(default_factory=Graph)
    history: list[HistoryEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _integrity(self) -> "Investigation":
        """Cross-references must resolve. Catches engine/witness wiring bugs early."""
        errors: list[str] = []

        file_ids = [i.file_id for i in self.inputs]
        claim_ids = [c.id for c in self.claims]
        finding_ids = [f.id for f in self.findings]
        for label, ids in (("input file_id", file_ids), ("claim id", claim_ids), ("finding id", finding_ids)):
            if d := _dupes(ids):
                errors.append(f"duplicate {label}: {sorted(d)}")

        # Principle 3: every claim is traceable to an exact caption span.
        for c in self.claims:
            s = c.source
            if self.caption[s.start:s.end] != s.text:
                errors.append(f"claim {c.id}: caption[{s.start}:{s.end}] is not {s.text!r}")

        known_claims, known_files = set(claim_ids), set(file_ids)
        for f in self.findings:
            if missing := set(f.claim_ids) - known_claims:
                errors.append(f"finding {f.id}: unknown claim_ids {sorted(missing)}")
            for loc in f.locations:
                if loc.file_id not in known_files:
                    errors.append(f"finding {f.id}: location refers to unknown file_id {loc.file_id!r}")

        if missing := set(self.assessment.weak_indicators) - set(finding_ids):
            errors.append(f"weak_indicators refer to unknown findings {sorted(missing)}")

        tally = StatusCounts()
        for f in self.findings:
            setattr(tally, f.status, getattr(tally, f.status) + 1)
        if tally != self.assessment.counts:
            errors.append(f"assessment.counts {self.assessment.counts.model_dump()} != findings {tally.model_dump()}")

        node_ids = [n.id for n in self.graph.nodes]
        if d := _dupes(node_ids):
            errors.append(f"duplicate graph node ids: {sorted(d)}")
        if d := _dupes([e.id for e in self.graph.edges]):
            errors.append(f"duplicate graph edge ids: {sorted(d)}")
        known_nodes = set(node_ids)
        for e in self.graph.edges:
            if e.source not in known_nodes or e.target not in known_nodes:
                errors.append(f"edge {e.id}: {e.source} -> {e.target} refers to a missing node")

        if errors:
            raise ValueError("; ".join(errors))
        return self


# ---------------------------------------------------------------------------
# 4.7 InvestigationSummary (GET /api/investigations, history page)
# ---------------------------------------------------------------------------

class InvestigationSummary(_Model):
    """One row of the history list. Fetch the full Investigation by id for details."""

    id: str = Field(pattern=INVESTIGATION_ID_PATTERN)
    created_at: AwareDatetime
    parent_id: Optional[str] = Field(default=None, description="set on caption re-runs")
    caption: str
    inputs: list[str] = Field(description="input file names")
    headline: Headline
    headline_text: str
    counts: StatusCounts
    human_review: HumanReview

    @classmethod
    def from_investigation(cls, inv: Investigation) -> "InvestigationSummary":
        return cls(
            id=inv.id,
            created_at=inv.created_at,
            parent_id=inv.parent_id,
            caption=inv.caption,
            inputs=[i.name for i in inv.inputs],
            headline=inv.assessment.headline,
            headline_text=inv.assessment.headline_text,
            counts=inv.assessment.counts,
            human_review=inv.assessment.human_review,
        )


# ---------------------------------------------------------------------------
# JSON Schema export
# ---------------------------------------------------------------------------

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "shared" / "contract.schema.json"


def contract_json_schema() -> dict[str, Any]:
    """JSON Schema whose root is Investigation. Every other model (Finding, Claim,
    InvestigationSummary, ...) is under $defs."""
    schema = Investigation.model_json_schema(mode="serialization")
    summary = InvestigationSummary.model_json_schema(mode="serialization")
    schema["$defs"].update(summary.pop("$defs", {}))
    schema["$defs"]["InvestigationSummary"] = summary
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$comment": "GENERATED from backend/app/schemas.py by `python -m app.schemas`. Do not edit by hand.",
        **schema,
    }


def render_contract_json_schema() -> str:
    return json.dumps(contract_json_schema(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.write_text(render_contract_json_schema(), encoding="utf-8", newline="\n")
    print(f"wrote {SCHEMA_PATH}")
