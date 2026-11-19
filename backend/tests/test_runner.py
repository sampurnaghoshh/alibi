"""Runner: crashes, timeouts and contract violations become inconclusive findings, never exceptions."""

import time
from datetime import datetime, timezone

import pytest

from app.runner import all_witnesses, run_witnesses
from app.schemas import Claim
from app.witnesses.base import InvestigationContext, Witness


@pytest.fixture
def ctx(tmp_path):
    return InvestigationContext(
        inv_id="test",
        caption="Flooding in Chennai",
        posted_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
        inputs=[],
        claims=[Claim.model_validate({
            "id": "c1", "type": "location", "value": "Chennai",
            "normalized": {"lat": 13.08, "lon": 80.27, "country": "IN"},
            "source": {"field": "caption", "start": 12, "end": 19, "text": "Chennai"},
            "extracted_by": "rules",
        })],
        artifacts_dir=tmp_path,
    )


class Crashing(Witness):
    name = "weather"
    def applicable(self, ctx): return True
    def run(self, ctx): raise RuntimeError("boom")


class Slow(Witness):
    name = "lipsync"
    def applicable(self, ctx): return True
    def run(self, ctx):
        time.sleep(2)
        return self.placeholder()


class NotApplicable(Witness):
    name = "enf"
    def applicable(self, ctx): return False
    def run(self, ctx): raise AssertionError("must not run")


class BadClaimRef(Witness):
    name = "metadata"
    def applicable(self, ctx): return True
    def run(self, ctx):
        f = self.placeholder()[0]
        f.claim_ids = ["c99"]
        return [f]


class ReturnsNothing(Witness):
    name = "ela"
    def applicable(self, ctx): return True
    def run(self, ctx): return None


def test_crash_becomes_inconclusive(ctx):
    events = []
    [f] = run_witnesses(ctx, [Crashing()], log=lambda e, d: events.append((e, d)))
    assert (f.status, f.reliability) == ("inconclusive", "low")
    assert "RuntimeError: boom" in f.limitations[0]
    assert f.alternatives
    assert events[0][0] == "witness_failed"


def test_timeout_becomes_inconclusive(ctx):
    t0 = time.monotonic()
    [f] = run_witnesses(ctx, [Slow()], timeouts={"lipsync": 0.2})
    assert time.monotonic() - t0 < 1.5
    assert f.status == "inconclusive" and "did not finish" in f.limitations[0]


def test_not_applicable(ctx):
    [f] = run_witnesses(ctx, [NotApplicable()])
    assert f.status == "not_applicable"


@pytest.mark.parametrize("witness", [BadClaimRef(), ReturnsNothing()])
def test_contract_violation_becomes_inconclusive(ctx, witness):
    [f] = run_witnesses(ctx, [witness])
    assert f.status == "inconclusive" and f.limitations[0].startswith("Error:")


def test_all_stubs_run_cleanly(ctx):
    findings = run_witnesses(ctx)
    assert {f.witness for f in findings} == {w.name for w in all_witnesses()}
    assert len({f.id for f in findings}) == len(findings)
    assert all(f.status in ("inconclusive", "not_applicable") for f in findings)
