"""
The investigation pipeline. Owner: Sampurna.

    storage (originals + sha256 + history) -> claims -> witnesses (parallel)
    -> rules -> graph -> explanations -> Investigation (validated) -> investigation.json

Every step is written to the run's history.jsonl (chain of custody).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Optional

from app.claims.extract import extract_claims
from app.engine.explain import explain_findings
from app.engine.graph import build_graph
from app.engine.rules import assess
from app.runner import run_witnesses
from app.schemas import Claim, Investigation
from app.storage import RunStore, now
from app.witnesses.base import InputRef, InvestigationContext, Witness

log = logging.getLogger("alibi.pipeline")


@dataclass
class Upload:
    filename: str
    stream: BinaryIO
    mime: Optional[str] = None


def _claims(caption: str, posted_at: datetime, store: RunStore) -> list[Claim]:
    """Claim extraction must never stop an investigation (principle 5)."""
    try:
        claims = extract_claims(caption, posted_at)
    except Exception as exc:  # noqa: BLE001
        log.exception("claim extraction failed")
        store.log("claims_failed", f"{type(exc).__name__}: {exc}")
        return []
    methods = ", ".join(sorted({c.extracted_by for c in claims})) or "none"
    types = ", ".join(c.type for c in claims)
    store.log("claims_extracted", f"{len(claims)} claims via {methods}" + (f" ({types})" if types else ""))
    return claims


def run_investigation(
    uploads: list[Upload],
    caption: str,
    posted_at: datetime,
    witnesses: Optional[list[Witness]] = None,
) -> Investigation:
    created_at = now()
    store = RunStore.create()

    # 1. Preserve originals (read-only, hashed, logged as file_received).
    inputs, refs = [], []
    for n, up in enumerate(uploads, 1):
        info, path = store.save_original(f"in_{n}", up.filename, up.stream, up.mime)
        inputs.append(info)
        refs.append(InputRef(
            file_id=info.file_id, name=info.name, mime=info.mime, size=info.size,
            sha256=info.sha256, path=path, duration_s=info.duration_s,
        ))

    # 2. Claims from the caption.
    claims = _claims(caption, posted_at, store)

    # 3. Witnesses (each logs witness_completed / witness_failed).
    ctx = InvestigationContext(
        inv_id=store.inv_id, caption=caption, posted_at=posted_at,
        inputs=refs, claims=claims, artifacts_dir=store.artifacts_dir,
    )
    findings = run_witnesses(ctx, witnesses, log=store.log)

    # 4. Explanations (templates, optional guarded LLM rewording), rules, graph.
    findings = explain_findings(findings)
    assessment = assess(findings)
    graph = build_graph(inputs, claims, findings)
    store.log("assessment_computed", f"{assessment.headline}; human_review={assessment.human_review}")

    # 5. Assemble, validate against the contract, save.
    inv = Investigation(
        id=store.inv_id,
        created_at=created_at,
        posted_at=posted_at,
        caption=caption,
        inputs=inputs,
        claims=claims,
        findings=findings,
        assessment=assessment,
        graph=graph,
        history=store.history(),
    )
    inv = Investigation.model_validate(inv.model_dump())  # full re-validation incl. cross-references
    store.save_investigation(inv)
    return inv
