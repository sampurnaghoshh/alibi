"""
Runs witnesses safely (principle 5: no module may crash an investigation). Owner: Sampurna.

- All witnesses run in parallel threads, each with its own time limit
  (config.WITNESS_TIMEOUT_S, 90 s for lipsync).
- Any exception, timeout, or output that breaks the contract becomes one
  inconclusive / low-reliability finding with the error in `limitations`.
- A witness whose applicable() is False gets a not_applicable finding.
- Python threads cannot be killed: a timed-out witness keeps running in the
  background, but its result is ignored.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Callable, Optional

from app import config
from app.schemas import Finding
from app.witnesses.base import InvestigationContext, Witness

log_ = logging.getLogger("alibi.runner")

LogFn = Callable[[str, str], None]   # (event, detail) -> None, e.g. RunStore.log


def all_witnesses() -> list[Witness]:
    """The frozen witness list (§5), in display order."""
    from app.witnesses.ela import ElaWitness
    from app.witnesses.enf import EnfWitness
    from app.witnesses.lipsync import LipsyncWitness
    from app.witnesses.metadata import MetadataWitness
    from app.witnesses.prior_appearance import PriorAppearanceWitness
    from app.witnesses.weather import WeatherWitness

    return [
        MetadataWitness(),
        WeatherWitness(),
        PriorAppearanceWitness(),
        LipsyncWitness(),
        EnfWitness(),
        ElaWitness(),
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

def _run_one(witness: Witness, ctx: InvestigationContext) -> tuple[object, Exception | None, int]:
    """Runs inside a worker thread. Returns (output, error, elapsed_ms) so each witness
    is timed on its own, not from the start of the whole batch."""
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
    """Run witnesses in parallel; always returns findings, never raises."""
    witnesses = all_witnesses() if witnesses is None else witnesses
    log = log or (lambda event, detail: None)
    timeouts = timeouts or {}
    results: list[Finding] = []

    pool = ThreadPoolExecutor(max_workers=max(1, len(witnesses)), thread_name_prefix="witness")
    try:
        started = time.monotonic()
        futures = [(w, pool.submit(_run_one, w, ctx)) for w in witnesses]

        for w, fut in futures:  # collect in fixed display order
            limit = timeouts.get(w.name, timeout_for(w))
            remaining = max(0.0, limit - (time.monotonic() - started))
            elapsed_ms = None
            try:
                output, error, elapsed_ms = fut.result(timeout=remaining)
                if error is not None:
                    raise error
                findings = _check_output(w, output, ctx)
                event = "witness_completed"
            except FutureTimeout:
                findings = [failure_finding(w, f"did not finish within {limit:g} s")]
                event = "witness_failed"
                elapsed_ms = int(limit * 1000)
            except Exception as exc:  # noqa: BLE001 - principle 5
                log_.error("witness %s failed", w.name, exc_info=exc)
                findings = [failure_finding(w, f"{type(exc).__name__}: {exc}")]
                event = "witness_failed"

            if elapsed_ms is None:
                elapsed_ms = int((time.monotonic() - started) * 1000)
            for f in findings:
                f.elapsed_ms = elapsed_ms
            statuses = ", ".join(f.status for f in findings)
            log(event, f"{w.name}: {statuses} ({elapsed_ms} ms)")
            results.extend(findings)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # Finding ids must be unique across the investigation.
    seen: set[str] = set()
    for f in results:
        base, k = f.id, 2
        while f.id in seen:
            f.id = f"{base}_{k}"
            k += 1
        seen.add(f.id)
    return results
