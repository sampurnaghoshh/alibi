"""
Witness interface (CLAUDE.md §6). Owner: Sampurna.

A witness is a pure function of InvestigationContext -> list[Finding].
Subclasses set `name` (and `label`), implement `applicable()` and `run()`,
and use `self.finding(...)` to build findings with witness/family/id filled in.

Every witness file ends with:

    if __name__ == "__main__":
        run_cli(MyWitness())

so it can run alone, e.g. (from backend/):

    python -m app.witnesses.weather --caption "Massive flooding in Chennai yesterday evening" --posted-at 2026-11-19
    python -m app.witnesses.weather --claims-json ../shared/fixtures/sample_investigation.json
    python -m app.witnesses.metadata --file ../demo/case_a/clip.mp4
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from app.schemas import (
    WITNESS_FAMILY,
    Claim,
    ClaimType,
    Evidence,
    Family,
    Finding,
    Reliability,
    Status,
    WitnessName,
)


@dataclass
class InputRef:
    """One uploaded file: contract metadata + where the read-only original lives on disk."""

    file_id: str
    name: str
    mime: str
    size: int
    sha256: str
    path: Path                         # absolute path to the read-only original
    duration_s: Optional[float] = None

    @property
    def kind(self) -> str:
        """'video' | 'image' | 'audio' | 'other', from the mime type."""
        top = self.mime.split("/", 1)[0]
        return top if top in ("video", "image", "audio") else "other"


@dataclass
class InvestigationContext:
    """Everything a witness may read. Witnesses must not modify inputs."""

    inv_id: str
    caption: str
    posted_at: datetime
    inputs: list[InputRef]
    claims: list[Claim]
    artifacts_dir: Path                # write derived files here (frames, heatmaps, ...)
    extra: dict[str, Any] = field(default_factory=dict)

    def claims_of(self, type_: ClaimType) -> list[Claim]:
        return [c for c in self.claims if c.type == type_]

    def inputs_of(self, kind: str) -> list[InputRef]:
        """kind: 'video' | 'image' | 'audio'."""
        return [i for i in self.inputs if i.kind == kind]

    def artifact_path(self, name: str) -> Path:
        """Absolute path for a new artifact. Use artifact_ref(name) in Finding.evidence."""
        p = self.artifacts_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def artifact_ref(name: str) -> str:
        """Relative ref used in findings, served at /api/files/{inv_id}/artifacts/{name}."""
        return f"artifacts/{name}"


class Witness(ABC):
    name: WitnessName
    label: str = ""                    # human-readable, e.g. "Prior appearance"
    implemented: bool = True           # stubs set False; shown by /api/health

    @property
    def family(self) -> Family:
        return WITNESS_FAMILY[self.name]  # type: ignore[return-value]

    @abstractmethod
    def applicable(self, ctx: InvestigationContext) -> bool:
        """Cheap check: can this witness say anything about these inputs/claims?"""

    @abstractmethod
    def run(self, ctx: InvestigationContext) -> list[Finding]:
        """Do the analysis. Catch what you can; the runner catches the rest."""

    def finding(
        self,
        *,
        status: Status,
        reliability: Reliability,
        alternatives: Iterable[str],
        limitations: Iterable[str],
        title: Optional[str] = None,
        summary: Optional[str] = None,
        claim_ids: Iterable[str] = (),
        details: Optional[dict[str, Any]] = None,
        evidence: Iterable[Evidence | dict] = (),
        locations: Iterable[Any] = (),
        n: int = 1,
    ) -> Finding:
        """Build a Finding with id/witness/family filled in. Use n=2, 3... for extra findings.
        Omit title/summary to get the template text from app/engine/explain.py."""
        from app.engine.explain import template_summary, template_title

        title = title or template_title(self.name, status)
        summary = summary or template_summary(self.name, status, reliability)
        return Finding(
            id=f"f_{self.name}_{n}",
            witness=self.name,
            family=self.family,
            claim_ids=list(claim_ids),
            status=status,
            reliability=reliability,
            title=title,
            summary=summary,
            details=details or {},
            evidence=list(evidence),
            locations=list(locations),
            alternatives=list(alternatives),
            limitations=list(limitations),
        )

    def placeholder(self) -> list[Finding]:
        """What the stubs return until the real witness is written."""
        return [
            self.finding(
                status="inconclusive",
                reliability="low",
                title=f"{self.label or self.name} witness not implemented yet",
                summary="Placeholder finding: no analysis was performed.",
                alternatives=["No analysis was performed, so this says nothing about the media"],
                limitations=["Stub implementation (see CLAUDE.md §6)"],
            )
        ]


# ---------------------------------------------------------------------------
# Standalone runner used by every witness's __main__
# ---------------------------------------------------------------------------

def _input_from_path(file_id: str, path: Path) -> InputRef:
    from app.storage import probe_duration, sha256_file

    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return InputRef(
        file_id=file_id,
        name=path.name,
        mime=mime,
        size=path.stat().st_size,
        sha256=sha256_file(path),
        path=path.resolve(),
        duration_s=probe_duration(path, mime),
    )


def _load_claims(path: Path) -> list[Claim]:
    """Accepts a JSON list of claims, or any object with a "claims" key (e.g. the fixture)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("claims", [])
    return [Claim.model_validate(c) for c in data]


def run_cli(witness: Witness, argv: Optional[list[str]] = None) -> list[Finding]:
    """Parse CLI args, build a context, run the witness through the real runner, print JSON."""
    from app import config
    from app.runner import run_witnesses
    from app.storage import parse_posted_at

    ap = argparse.ArgumentParser(description=f"Run the {witness.name} witness on its own.")
    ap.add_argument("--caption", default="", help="caption text to extract claims from")
    ap.add_argument("--posted-at", default=None, help="when it was shared, e.g. 2026-11-19 (default now)")
    ap.add_argument("--file", action="append", default=[], help="input file (repeatable)")
    ap.add_argument("--claims-json", default=None, help="use claims from this JSON instead of extracting")
    args = ap.parse_args(argv)

    posted_at = parse_posted_at(args.posted_at)
    if args.claims_json:
        claims = _load_claims(Path(args.claims_json))
        caption = args.caption or json.loads(Path(args.claims_json).read_text(encoding="utf-8")).get("caption", "")
    else:
        from app.claims.extract import extract_claims

        caption = args.caption
        claims = extract_claims(caption, posted_at)

    artifacts = config.RUNS_DIR / "_cli" / witness.name / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    ctx = InvestigationContext(
        inv_id="cli",
        caption=caption,
        posted_at=posted_at,
        inputs=[_input_from_path(f"in_{i}", Path(p)) for i, p in enumerate(args.file, 1)],
        claims=claims,
        artifacts_dir=artifacts,
    )

    findings = run_witnesses(ctx, [witness], log=lambda ev, d: print(f"[{ev}] {d}", file=sys.stderr))
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console: allow "–", "°" etc.
    print(json.dumps([f.model_dump(mode="json") for f in findings], indent=2, ensure_ascii=False))
    print(f"artifacts: {artifacts}", file=sys.stderr)
    return findings
