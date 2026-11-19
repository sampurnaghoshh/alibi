"""
Run every control case in demo/captions.md through the real pipeline and print PASS/FAIL.

Controls are genuine clips with TRUE captions (CLAUDE.md §10). The rule:
    PASS = no contradiction at all (any reliability) -> headline consistent_so_far / insufficient_evidence
    FAIL = any contradiction; the table names the witness(es) that caused it
    SKIP = the case is not ready (file missing, caption TODO)
Run it before every demo. Exit code 1 if anything FAILs (or nothing could run).

Usage (from the repo root, with the backend venv):
    backend\\.venv\\Scripts\\python.exe scripts\\run_controls.py            # temp runs folder
    backend\\.venv\\Scripts\\python.exe scripts\\run_controls.py --keep     # save runs to data/runs/ (open reports)
    backend\\.venv\\Scripts\\python.exe scripts\\run_controls.py --only control_2
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app import config  # noqa: E402
from app.demo import load_cases  # noqa: E402
from app.storage import parse_posted_at  # noqa: E402
from app.uploads import UploadError, validate_upload  # noqa: E402

ALLOWED = {"consistent_so_far", "insufficient_evidence"}


def run_case(case) -> dict:
    from app.pipeline import Upload, run_investigation  # after RUNS_DIR is set

    posted = parse_posted_at(None if case.posted_at.upper() in ("", "TODO") else case.posted_at)
    handles = [open(p, "rb") for p in case.paths]
    try:
        checked = [validate_upload(p.name, fh) for p, fh in zip(case.paths, handles)]
        t0 = time.monotonic()
        inv = run_investigation([Upload(c.filename, c.stream, c.mime) for c in checked], case.caption, posted)
        elapsed = time.monotonic() - t0
    finally:
        for fh in handles:
            fh.close()
    contradictions = [f for f in inv.findings if f.status == "contradiction"]
    passed = not contradictions and inv.assessment.headline in ALLOWED
    return {
        "inv": inv, "elapsed": elapsed, "passed": passed,
        "why": ", ".join(f"{f.witness} ({f.reliability}): {f.title}" for f in contradictions),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the control clips and check for contradictions.")
    ap.add_argument("--keep", action="store_true", help="save runs to data/runs/ instead of a temp folder")
    ap.add_argument("--only", help="run a single control, e.g. control_2")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if not args.keep:
        config.RUNS_DIR = Path(tempfile.mkdtemp(prefix="alibi_controls_")) / "runs"
    cases = [c for n, c in load_cases().items() if n.startswith("control") and (not args.only or n == args.only)]
    if not cases:
        print("No control cases found in demo/captions.md" + (f" named {args.only}" if args.only else ""))
        return 1

    rows, details = [], []
    for case in cases:
        if not case.ready:
            rows.append((case.name, "-", "-", "-", "SKIP"))
            details.append(f"{case.name}: SKIP, {case.problem}")
            continue
        print(f"running {case.name} ({', '.join(p.name for p in case.paths)}) ...", flush=True)
        try:
            r = run_case(case)
        except UploadError as exc:
            rows.append((case.name, case.paths[0].name, "-", "-", "FAIL"))
            details.append(f"{case.name}: FAIL, upload rejected: {exc}")
            continue
        inv = r["inv"]
        rows.append((case.name, case.paths[0].name, inv.assessment.headline, f"{r['elapsed']:.1f}s",
                     "PASS" if r["passed"] else "FAIL"))
        statuses = "  ".join(f"{f.witness}={f.status}" for f in inv.findings)
        details.append(f"{case.name}: {inv.id}\n    caption: {case.caption}\n    claims:  "
                       + ("; ".join(f"{c.type}={c.source.text!r}" for c in inv.claims) or "none")
                       + f"\n    witnesses: {statuses}"
                       + (f"\n    CONTRADICTION from {r['why']}" if r["why"] else ""))

    widths = [max(len(str(x)) for x in col) for col in zip(("case", "file", "headline", "time", "result"), *rows)]
    line = "  ".join("{:<%d}" % w for w in widths)
    print()
    print(line.format("case", "file", "headline", "time", "result"))
    print(line.format(*("-" * w for w in widths)))
    for row in rows:
        print(line.format(*row))
    print()
    print("\n".join(details))
    print(f"\nruns saved in {config.RUNS_DIR}")

    failed = sum(r[-1] == "FAIL" for r in rows)
    ran = sum(r[-1] in ("PASS", "FAIL") for r in rows)
    print(f"\n{ran - failed} PASS, {failed} FAIL, {len(rows) - ran} SKIP")
    return 1 if failed or ran == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
