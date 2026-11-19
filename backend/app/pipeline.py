"""
The investigation pipeline. Owner: Sampurna.

    prepare:  storage (originals + sha256 + history), announce on the live feed
    execute:  claims -> witnesses (parallel) -> explanations -> rules -> graph
              -> Investigation (validated) -> investigation.json -> "done"

prepare() runs inside the request (it reads the uploaded files); execute() can run in the
background (POST ...?async=true). Every step is written to the run's history.jsonl, which is
both the chain-of-custody log and the live event stream (§14):

    file_received, claims_extracted, witness_started, witness_completed / witness_failed,
    assessment_computed, graph_ready, done (or failed)

Caption re-runs (prepare_rerun) copy the parent's originals (hash-checked), re-run the
caption-dependent witnesses and re-use the caption-independent findings (config.RERUN_*).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, BinaryIO, Optional

from app import config
from app.claims.extract import extract_claims
from app.engine.explain import explain_findings
from app.engine.graph import build_graph
from app.engine.summary import summarize
from app.engine.rules import assess
from app.live import feed
from app.runner import all_witnesses, run_witnesses
from app.schemas import Claim, Finding, InputFile, Investigation
from app.storage import RunStore, now
from app.witnesses.base import InputRef, InvestigationContext, Witness

log = logging.getLogger("alibi.pipeline")


@dataclass
class Upload:
    filename: str
    stream: BinaryIO
    mime: Optional[str] = None              # detected from content (app/uploads.py)
    declared_mime: Optional[str] = None     # what the client claimed; logged if different


@dataclass
class Prepared:
    """A run whose originals are stored and logged; ready for execute()."""

    store: RunStore
    created_at: datetime
    caption: str
    posted_at: datetime
    inputs: list[InputFile] = field(default_factory=list)
    refs: list[InputRef] = field(default_factory=list)
    parent: Optional[Investigation] = None
    reused: list[Finding] = field(default_factory=list)   # findings carried over from the parent

    @property
    def inv_id(self) -> str:
        return self.store.inv_id


def _ref(info: InputFile, path) -> InputRef:
    return InputRef(file_id=info.file_id, name=info.name, mime=info.mime, size=info.size,
                    sha256=info.sha256, path=path, duration_s=info.duration_s)


def _announce(p: Prepared) -> None:
    feed.publish("investigation_started", {
        "id": p.inv_id, "created_at": p.created_at.isoformat(), "caption": p.caption,
        "posted_at": p.posted_at.isoformat(), "inputs": [i.name for i in p.inputs],
        "parent_id": p.parent.id if p.parent else None,
    })


# --- stage 1: store originals --------------------------------------------------------

def prepare(uploads: list[Upload], caption: str, posted_at: datetime) -> Prepared:
    p = Prepared(store=RunStore.create(), created_at=now(), caption=caption, posted_at=posted_at)
    for n, up in enumerate(uploads, 1):
        info, path = p.store.save_original(f"in_{n}", up.filename, up.stream, up.mime, up.declared_mime)
        p.inputs.append(info)
        p.refs.append(_ref(info, path))
    _announce(p)
    return p


def _artifact_refs(f: Finding) -> set[str]:
    """Every artifacts/... path a finding mentions (evidence and details)."""
    refs = {e.ref for e in f.evidence if e.kind == "artifact"}

    def walk(v: Any) -> None:
        if isinstance(v, str) and v.startswith("artifacts/"):
            refs.add(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(f.details)
    return refs


def prepare_rerun(parent_id: str, caption: str, posted_at: datetime) -> Prepared:
    """New run on the same stored files with a new caption. Raises FileNotFoundError / ValueError."""
    parent_store = RunStore(parent_id)
    if not parent_store.exists():
        raise FileNotFoundError(parent_id)
    parent = parent_store.load_investigation()

    p = Prepared(store=RunStore.create(), created_at=now(), caption=caption, posted_at=posted_at, parent=parent)
    for info in parent.inputs:
        _, path = p.store.copy_original_from(parent_store, info)
        p.inputs.append(info)
        p.refs.append(_ref(info, path))

    # Caption-independent findings are carried over, with the artifacts they point to.
    p.reused = [f.model_copy(deep=True) for f in parent.findings if f.witness in config.RERUN_REUSED]
    for f in p.reused:
        for ref in _artifact_refs(f):
            src, dest = parent_store.run_dir / ref, p.store.run_dir / ref
            if src.is_file() and not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
    _announce(p)
    return p


# --- stage 2: analyse -----------------------------------------------------------------

def _transcript(p: "Prepared", store: RunStore):
    """Speech-to-text of the first audio/video input (app/transcribe.py never raises). None = no such input."""
    from app.transcribe import transcribe  # noqa: PLC0415

    try:
        tr = transcribe(p.refs)
    except Exception as exc:  # noqa: BLE001 - belt and braces: transcription must never stop a run
        log.exception("transcription failed")
        from app.schemas import Transcript  # noqa: PLC0415
        tr = Transcript(status="failed", note=f"Speech-to-text failed: {type(exc).__name__}: {exc}")
    if tr is not None:
        store.log("transcribed", f"{tr.status}: {tr.text[:80] if tr.text else tr.note}",
                  data={"transcript": tr.model_dump(mode="json")})
    return tr


def _spoken_claims(text: str, posted_at: datetime, existing: list[Claim]) -> list[Claim]:
    """Claims from the transcript, with spans in the transcript text; ones the caption already makes are dropped."""
    seen = {(c.type, c.value.lower()) for c in existing}
    out = []
    for c in extract_claims(text, posted_at):
        if (c.type, c.value.lower()) in seen:
            continue
        seen.add((c.type, c.value.lower()))
        out.append(c.model_copy(update={"id": f"c{len(existing) + len(out) + 1}",
                                        "source": c.source.model_copy(update={"field": "transcript"})}))
    return out


def _claims(caption: str, posted_at: datetime, store: RunStore, transcript=None) -> list[Claim]:
    """Claim extraction must never stop an investigation (principle 5)."""
    try:
        claims = extract_claims(caption, posted_at)
        if transcript is not None and transcript.status == "ok":
            claims += _spoken_claims(transcript.text, posted_at, claims)
    except Exception as exc:  # noqa: BLE001
        log.exception("claim extraction failed")
        store.log("claims_failed", f"{type(exc).__name__}: {exc}", data={"claims": []})
        return []
    methods = ", ".join(sorted({c.extracted_by for c in claims})) or "none"
    types = ", ".join(c.type for c in claims)
    store.log("claims_extracted", f"{len(claims)} claims via {methods}" + (f" ({types})" if types else ""),
              data={"claims": [c.model_dump(mode="json") for c in claims]})
    return claims


def remap_claim_ids(f: Finding, old_claims: list[Claim], new_claims: list[Claim]) -> Finding:
    """Point a re-used finding at the new run's claims: keep a link only where the new caption
    makes the same claim (same type and normalized value); drop it otherwise."""
    old = {c.id: c for c in old_claims}
    mapped = []
    for cid in f.claim_ids:
        o = old.get(cid)
        match = next((n for n in new_claims if o and n.type == o.type and n.normalized == o.normalized), None)
        if match and match.id not in mapped:
            mapped.append(match.id)
    if len(mapped) < len(f.claim_ids):
        f.limitations.append("Links to claims that the new caption no longer makes were removed")
    f.claim_ids = mapped
    return f


def _display_order(findings: list[Finding]) -> list[Finding]:
    order = {w.name: i for i, w in enumerate(all_witnesses())}
    return sorted(findings, key=lambda f: order.get(f.witness, len(order)))   # stable within a witness


def _unique_ids(findings: list[Finding]) -> list[Finding]:
    seen: set[str] = set()
    for f in findings:
        base, k = f.id, 2
        while f.id in seen:
            f.id = f"{base}_{k}"
            k += 1
        seen.add(f.id)
    return findings


def execute(p: Prepared, witnesses: Optional[list[Witness]] = None) -> Investigation:
    store = p.store
    parent_id = p.parent.id if p.parent else None
    try:
        transcript = _transcript(p, store)
        claims = _claims(p.caption, p.posted_at, store, transcript)

        # Re-used findings (caption re-runs) are available immediately.
        reused = [remap_claim_ids(f, p.parent.claims, claims) for f in p.reused] if p.parent else []
        for f in reused:
            f.limitations = [x for x in f.limitations if not x.startswith("Re-used unchanged from investigation")]
            f.limitations.append(f"Re-used unchanged from investigation {parent_id}; "
                                 f"this analysis does not depend on the caption")
        for name in dict.fromkeys(f.witness for f in reused):
            mine = [f for f in reused if f.witness == name]
            store.log("witness_completed", f"{name}: {', '.join(f.status for f in mine)} (re-used from {parent_id})",
                      data={"witness": name, "elapsed_ms": 0, "reused_from": parent_id,
                            "findings": [f.model_dump(mode="json") for f in mine]})

        if witnesses is None and reused:
            done = {f.witness for f in reused}
            witnesses = [w for w in all_witnesses() if w.name not in done]
        ctx = InvestigationContext(
            inv_id=store.inv_id, caption=p.caption, posted_at=p.posted_at,
            inputs=p.refs, claims=claims, artifacts_dir=store.artifacts_dir,
        )
        findings = run_witnesses(ctx, witnesses, log=store.log)
        findings = _unique_ids(_display_order(reused + findings) if reused else findings)

        findings = explain_findings(findings)
        assessment = assess(findings)
        store.log("assessment_computed", f"{assessment.headline}; human_review={assessment.human_review}",
                  data={"assessment": assessment.model_dump(mode="json"),
                        "summary": summarize(findings, claims)})          # the Live Desk shows this sentence
        graph = build_graph(p.inputs, claims, findings)
        store.log("graph_ready", f"{len(graph.nodes)} nodes, {len(graph.edges)} edges",
                  data={"graph": graph.model_dump(mode="json")})

        inv = Investigation(
            id=store.inv_id, created_at=p.created_at, posted_at=p.posted_at, parent_id=parent_id,
            caption=p.caption, inputs=p.inputs, claims=claims, findings=findings,
            assessment=assessment, graph=graph, history=store.history(), transcript=transcript,
        )
        inv = Investigation.model_validate(inv.model_dump())  # full re-validation incl. cross-references
        store.save_investigation(inv)
    except Exception as exc:
        log.exception("investigation %s failed", store.inv_id)
        store.log("failed", f"{type(exc).__name__}: {exc}", data={"error": f"{type(exc).__name__}: {exc}"})
        feed.publish("investigation_failed", {"id": store.inv_id, "error": f"{type(exc).__name__}: {exc}",
                                              "parent_id": parent_id})
        raise

    summary = {"id": inv.id, "headline": assessment.headline, "headline_text": assessment.headline_text,
               "human_review": assessment.human_review, "parent_id": parent_id}
    store.log("done", assessment.headline, data=summary)
    feed.publish("investigation_done", summary)
    return inv


def run_investigation(uploads: list[Upload], caption: str, posted_at: datetime,
                      witnesses: Optional[list[Witness]] = None) -> Investigation:
    """Synchronous: prepare + execute."""
    return execute(prepare(uploads, caption, posted_at), witnesses)
