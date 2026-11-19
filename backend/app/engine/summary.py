"""
The plain-language evidence summary, shared by the Live Desk and the printable report. Owner: Sampurna.

One function, so the wording can never drift between the two: the pipeline sends its result with the
`assessment_computed` event (the Live Desk shows it), GET /api/investigations/{id}/summary serves it for
older runs, and app/report.py calls it directly.

Template only, no LLM. Each witness is merged to its most significant status, and each sentence says what
the witness actually checked (its findings' claim scope: time / location / event), e.g.

    Recorded rainfall does not support the claimed flood. The capture time matches the stated time,
    which does not confirm the location or the event. 3 other checks gave no clear answer.

Witnesses this build doesn't implement are counted apart ("... is not available in this build"), never
as an investigation result.
"""

from __future__ import annotations

from typing import Iterable, Optional

from app import config
from app.schemas import Claim, Finding

WITNESS_ORDER = ("metadata", "weather", "prior_appearance", "lipsync", "enf", "ela")
RANK = {"contradiction": 4, "consistent": 3, "inconclusive": 2, "insufficient_signal": 1, "not_applicable": 0}
SCOPE = {"time": "time", "location": "location", "weather_condition": "event", "event": "event",
         "speaker": "speaker", "other": "claim"}
SCOPE_ORDER = ("time", "location", "event", "speaker", "claim")
CONDITION = {"flood": "flood", "heavy_rain": "heavy rain", "rain": "rain", "storm": "storm", "dry": "dry weather"}


def label(witness: str) -> str:
    return config.WITNESS_LABELS.get(witness, witness)


def join_with(names: list[str], word: str) -> str:
    """["a"] -> "a"; ["a", "b", "c"] -> "a, b and c"."""
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + f" {word} " + names[-1]


def worst_status(findings: Iterable[Finding]) -> str:
    statuses = [f.status for f in findings]
    return max(statuses, key=lambda s: RANK[s]) if statuses else "inconclusive"


def _sort_scopes(scopes: Iterable[str]) -> list[str]:
    return sorted(set(scopes), key=SCOPE_ORDER.index)


def scopes_of(findings: Iterable[Finding], claims: list[Claim]) -> list[str]:
    """["time", "location", ...] covered by these findings' claim links."""
    by_id = {c.id: c for c in claims}
    return _sort_scopes(SCOPE.get(by_id[cid].type, "claim")
                        for f in findings for cid in f.claim_ids if cid in by_id)


def caption_scopes(claims: list[Claim]) -> list[str]:
    return _sort_scopes(SCOPE.get(c.type, "claim") for c in claims)


def the_scopes(scopes: list[str], word: str = "and") -> str:
    return join_with([f"the {s}" for s in scopes], word)


def event_word(claims: list[Claim]) -> str:
    c = next((x for x in claims if x.type in ("weather_condition", "event")), None)
    if c is None:
        return "conditions"
    cond = (c.normalized or {}).get("condition")
    return CONDITION.get(cond, c.value)


def _meta_what(scopes: list[str]) -> str:
    t, loc = "time" in scopes, "location" in scopes
    return "date and GPS position" if t and loc else ("GPS position" if loc else "time")


def witness_clause(witness: str, status: str, scopes: list[str], claims: list[Claim]) -> str:
    """One clause per witness, from its findings' scope."""
    stated = "the stated " + join_with(scopes, "and") if scopes else "the caption"
    which = join_with(scopes, "and") if scopes else "context"
    if status == "contradiction":
        return {
            "weather": f"Recorded rainfall does not support the claimed {event_word(claims)}",
            "metadata": f"The file's capture {_meta_what(scopes)} contradicts {stated}",
            "prior_appearance": f"Frames match an archive clip verified with a different {which}",
            "lipsync": "Lip movement and speech audio are out of step",
            "enf": "The power-grid hum does not match the grid of the stated location",
        }.get(witness, f"{label(witness)} contradicts {stated}")
    return {
        "weather": f"Recorded rainfall is consistent with the claimed {event_word(claims)}",
        "metadata": f"The capture {_meta_what(scopes)} matches {stated}",
        "prior_appearance": f"Frames match an archive clip verified with the same {which}",
        "lipsync": "Lip movement and speech audio move together",
        "enf": "The power-grid hum matches the grid of the stated location",
    }.get(witness, f"{label(witness)} is consistent with {stated}")


def unavailable_witnesses() -> set[str]:
    """Witnesses this build doesn't implement (the same source /api/health reads)."""
    from app.runner import all_witnesses  # noqa: PLC0415 - imports every witness; only load when needed

    return {w.name for w in all_witnesses() if not w.implemented}


def summarize(findings: list[Finding], claims: list[Claim], unavailable: Optional[set[str]] = None) -> dict:
    """{"sentence", "counts": {contradiction, consistent, other, unavailable}, "witnesses": [{witness, status}]}.
    Counts are per witness (a witness with several findings counts once, by its most significant status)."""
    unavailable = unavailable_witnesses() if unavailable is None else unavailable
    by_witness: dict[str, list[Finding]] = {}
    for f in findings:
        by_witness.setdefault(f.witness, []).append(f)

    contra, cons, other, unav, rows = [], [], [], [], []
    for w in WITNESS_ORDER:
        fs = by_witness.get(w)
        if not fs:
            continue
        if w in unavailable:
            unav.append(w)
            rows.append({"witness": w, "status": "unavailable"})
            continue
        status = worst_status(fs)
        entry = (w, status, scopes_of([f for f in fs if f.status == status], claims))
        rows.append({"witness": w, "status": status})
        (contra if status == "contradiction" else cons if status == "consistent" else other).append(entry)

    parts, everything = [], caption_scopes(claims)
    for w, status, scopes in contra:
        parts.append(witness_clause(w, status, scopes, claims) + ".")
    for w, status, scopes in cons:
        others = [s for s in everything if s not in scopes]
        if not everything:
            tail = "."
        elif not scopes:
            tail = ", which says nothing about the caption's claims."
        elif others:
            tail = f", which does not confirm {the_scopes(others, 'or')}."
        else:
            tail = "."
        parts.append(witness_clause(w, status, scopes, claims) + tail)
    if other:
        if not contra and not cons:
            parts.append("None of the checks could confirm or contradict the caption with this media.")
        else:
            parts.append(f"{len(other)} other check{'s' if len(other) > 1 else ''} gave no clear answer.")
    if unav:
        parts.append(f"{join_with([label(w) for w in unav], 'and')} {'are' if len(unav) > 1 else 'is'} "
                     "not available in this build.")

    return {
        "sentence": " ".join(parts),
        "counts": {"contradiction": len(contra), "consistent": len(cons), "other": len(other),
                   "unavailable": len(unav)},
        "witnesses": rows,
    }
