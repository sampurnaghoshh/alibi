"""
Rule engine (CLAUDE.md §7). Owner: Sampurna.

Transparent and deterministic: pure functions of the findings, no averaging.
assess(findings) -> Assessment is the entry point; the other functions are the
individual rules so each can be tested and explained on its own.
"""

from __future__ import annotations

from app import config
from app.schemas import (
    RELIABILITY_RANK,
    Assessment,
    ExperimentalScore,
    Family,
    FamilyStatus,
    FamilyStatuses,
    Finding,
    Headline,
    StatusCounts,
)

FAMILIES: tuple[Family, ...] = ("context", "signal", "pixels")


def is_strong_contradiction(f: Finding) -> bool:
    """A contradiction that can set a family status: reliability medium or high."""
    return f.status == "contradiction" and RELIABILITY_RANK[f.reliability] >= RELIABILITY_RANK["medium"]


def is_weak_indicator(f: Finding) -> bool:
    """Low-reliability contradictions, plus witnesses that are always weak (ELA)."""
    return (f.status == "contradiction" and f.reliability == "low") or f.witness in config.WEAK_INDICATOR_WITNESSES


def family_status(findings: list[Finding], family: Family) -> FamilyStatus:
    """§7.2. Low-reliability contradictions never make a family 'contradiction'.

    They still mean a witness said something, so on their own they leave the
    family 'inconclusive' rather than 'not_applicable' (the cautious choice).
    """
    fs = [f for f in findings if f.family == family]
    if any(is_strong_contradiction(f) for f in fs):
        return "contradiction"
    if any(f.status == "consistent" for f in fs):
        return "consistent"
    if any(f.status in ("inconclusive", "insufficient_signal", "contradiction") for f in fs):
        return "inconclusive"
    return "not_applicable"


def family_statuses(findings: list[Finding]) -> FamilyStatuses:
    return FamilyStatuses(**{fam: family_status(findings, fam) for fam in FAMILIES})


def count_statuses(findings: list[Finding]) -> StatusCounts:
    counts = StatusCounts()
    for f in findings:
        setattr(counts, f.status, getattr(counts, f.status) + 1)
    return counts


def headline(findings: list[Finding], families: FamilyStatuses) -> tuple[Headline, str]:
    """§7.3. Families, not findings, are counted: several contradictions in one family count once."""
    contra_families = [fam for fam in FAMILIES if getattr(families, fam) == "contradiction"]
    consistent_families = [fam for fam in FAMILIES if getattr(families, fam) == "consistent"]
    any_high = any(f.status == "contradiction" and f.reliability == "high" for f in findings)
    any_contradiction = any(f.status == "contradiction" for f in findings)

    if any_high or len(contra_families) >= 2:
        key: Headline = "context_not_supported" if "context" in contra_families else "signals_inconsistent"
        return key, config.HEADLINE_TEXT[key]
    if len(contra_families) == 1:
        return "partially_supported", config.PARTIAL_HEADLINE_TEXT[contra_families[0]]
    if not any_contradiction and len(consistent_families) >= 2:
        return "consistent_so_far", config.HEADLINE_TEXT["consistent_so_far"]
    return "insufficient_evidence", config.HEADLINE_TEXT["insufficient_evidence"]


def experimental_score(findings: list[Finding]) -> ExperimentalScore:
    """§7.5. Labelled, never the headline. insufficient_signal / not_applicable cost 0."""
    score = config.SCORE_START
    for f in findings:
        if f.status == "contradiction":
            score -= config.SCORE_PENALTY[f.reliability]
        elif f.status == "inconclusive":
            score -= config.SCORE_PENALTY_INCONCLUSIVE
    return ExperimentalScore(value=max(0, score))


def assess(findings: list[Finding]) -> Assessment:
    families = family_statuses(findings)
    key, text = headline(findings, families)
    return Assessment(
        headline=key,
        headline_text=text,
        counts=count_statuses(findings),
        families=families,
        weak_indicators=[f.id for f in findings if is_weak_indicator(f)],
        human_review="priority" if any(f.status == "contradiction" for f in findings) else "standard",
        experimental_score=experimental_score(findings),
    )
