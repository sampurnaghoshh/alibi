"""
Runs witnesses safely (principle 5: no module may crash an investigation). Owner: Sampurna.

- All witnesses run in parallel threads, each with its own time limit
  (config.WITNESS_TIMEOUT_S, 90 s for lipsync).
- Any exception, timeout, or output that breaks the contract becomes one
  inconclusive / low-reliability finding with the error in `limitations`.
- A witness whose applicable() is False gets a not_applicable finding.
- Python threads cannot be killed: a timed-out witness keeps running in the
  background, but its result is ignored.
- Events are logged in the order things really happen (witness_started when a witness
  begins, witness_completed / witness_failed the moment it finishes, with its findings),
  so the live event stream shows the real pipeline. The returned list keeps display order.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable, Optional

from app import config
from app.schemas import Finding
from app.witnesses.base import InvestigationContext, Witness

log_ = logging.getLogger("alibi.runner")

# (event, detail, data=None) -> None, e.g. RunStore.log. Called from worker threads too.
LogFn = Callable[..., Any]


def all_witnesses() -> list[Witness]:
    """The frozen witness list (§5), in display order."""
    from app.witnesses.ela import ElaWitness
    from app.witnesses.enf import EnfWitness
    from app.witnesses.lipsync import LipsyncWitness
    from app.witnesses.metadata import MetadataWitness
    from app.witnesses.prior_appearance import PriorAppearanceWitness
    from app.witnesses.synthetic_signals import SyntheticSignalsWitness
    from app.witnesses.weather import WeatherWitness

    return [
        MetadataWitness(),
        WeatherWitness(),
        PriorAppearanceWitness(),
        LipsyncWitness(),
        EnfWitness(),
        ElaWitness(),
        SyntheticSignalsWitness(),
    ]


def timeout_for(witness: Witness) -> float:
    return config.WITNESS_TIMEOUT_OVERRIDES_S.get(witness.name, config.WITNESS_TIMEOUT_S)


# --- findings the runner creates itself --------------------------------------

def failure_finding(witness: Witness, error: str) -> Finding:
    return witness.finding(
        status="inconclusive",
        reliability="low",
        title=f"{witness.label or witness.name} witness could not complete",
        summary="The analysis stopped because of a technical error, so this witness gives no evidence either way.",
        alternatives=["The failure is technical and says nothing about the media itself"],
        limitations=[f"Error: {error}"],
        details={"error": error},
    )


def not_applicable_finding(witness: Witness) -> Finding:
    return witness.finding(
        status="not_applicable",
        reliability="low",
        title=f"{witness.label or witness.name} witness not applicable",
        summary="The inputs or claims do not contain what this witness needs, so it was not run.",
        alternatives=["This witness needs other kinds of input or claims; it says nothing either way here"],
        limitations=["Not run for these inputs"],
    )


# --- running ----------------------------------------------------------------

def _run_one(witness: Witness, ctx: InvestigationContext, log: LogFn) -> tuple[object, Exception | None, int]:
    """Runs inside a worker thread. Returns (output, error, elapsed_ms) so each witness
    is timed on its own, not from the start of the whole batch."""
    log("witness_started", witness.name,
        data={"witness": witness.name, "family": witness.family, "label": witness.label})
    t0 = time.monotonic()
    try:
        output = [not_applicable_finding(witness)] if not witness.applicable(ctx) else witness.run(ctx)
        return output, None, int((time.monotonic() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001 - handed back to the collector, which logs it
        return None, exc, int((time.monotonic() - t0) * 1000)


def _check_output(witness: Witness, output: object, ctx: InvestigationContext) -> list[Finding]:
    """Enforce the contract on what a witness returned. Raises ValueError on violations."""
    if not isinstance(output, list) or not output:
        raise ValueError(f"run() must return a non-empty list of Finding, got {type(output).__name__}")
    known_claims = {c.id for c in ctx.claims}
    known_files = {i.file_id for i in ctx.inputs}
    findings = []
    for item in output:
        f = item if isinstance(item, Finding) else Finding.model_validate(item)
        if f.witness != witness.name:
            raise ValueError(f"finding {f.id} says witness={f.witness!r}, expected {witness.name!r}")
        if bad := set(f.claim_ids) - known_claims:
            raise ValueError(f"finding {f.id} refers to unknown claim ids {sorted(bad)}")
        if bad := {loc.file_id for loc in f.locations} - known_files:
            raise ValueError(f"finding {f.id} refers to unknown file ids {sorted(bad)}")
        findings.append(f)
    return findings


def run_witnesses(
    ctx: InvestigationContext,
    witnesses: Optional[list[Witness]] = None,
    log: Optional[LogFn] = None,
    timeouts: Optional[dict[str, float]] = None,
) -> list[Finding]:
    """Run witnesses in parallel; always returns findings (in display order), never raises."""
    witnesses = all_witnesses() if witnesses is None else witnesses
    log = log or (lambda event, detail, data=None: None)
    timeouts = timeouts or {}
    by_index: dict[int, list[Finding]] = {}

    def finish(i: int, w: Witness, findings: list[Finding], event: str, elapsed_ms: int) -> None:
        """Record one witness's result and log it right away (real completion order)."""
        for f in findings:
            f.elapsed_ms = elapsed_ms
        by_index[i] = findings
        statuses = ", ".join(f.status for f in findings)
        log(event, f"{w.name}: {statuses} ({elapsed_ms} ms)", data={
            "witness": w.name, "elapsed_ms": elapsed_ms,
            "findings": [f.model_dump(mode="json") for f in findings],
        })

    pool = ThreadPoolExecutor(max_workers=max(1, len(witnesses)), thread_name_prefix="witness")
    try:
        started = time.monotonic()
        futures: dict[Future, int] = {pool.submit(_run_one, w, ctx, log): i for i, w in enumerate(witnesses)}
        deadline = {fut: started + timeouts.get(witnesses[i].name, timeout_for(witnesses[i]))
                    for fut, i in futures.items()}
        pending = set(futures)
        while pending:
            nearest = min(deadline[f] for f in pending)
            done, pending = wait(pending, timeout=max(0.0, nearest - time.monotonic()), return_when=FIRST_COMPLETED)
            for fut in done:
                i = futures[fut]
                w = witnesses[i]
                output, error, elapsed_ms = fut.result()
                try:
                    if error is not None:
                        raise error
                    finish(i, w, _check_output(w, output, ctx), "witness_completed", elapsed_ms)
                except Exception as exc:  # noqa: BLE001 - principle 5
                    log_.error("witness %s failed", w.name, exc_info=exc)
                    finish(i, w, [failure_finding(w, f"{type(exc).__name__}: {exc}")], "witness_failed", elapsed_ms)
            now_ = time.monotonic()
            for fut in [f for f in pending if deadline[f] <= now_]:
                i = futures[fut]
                w = witnesses[i]
                limit = timeouts.get(w.name, timeout_for(w))
                finish(i, w, [failure_finding(w, f"did not finish within {limit:g} s")], "witness_failed",
                       int(limit * 1000))
                pending.discard(fut)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    results = [f for i in range(len(witnesses)) for f in by_index.get(i, [])]

    # Finding ids must be unique across the investigation.
    seen: set[str] = set()
    for f in results:
        base, k = f.id, 2
        while f.id in seen:
            f.id = f"{base}_{k}"
            k += 1
        seen.add(f.id)
    return results
