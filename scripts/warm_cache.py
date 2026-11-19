"""
Warm data/cache/ so the demo works offline.

For every case in demo/captions.md this runs the REAL code paths, so the cache ends up
in exactly the format the app reads:
    claim extraction (incl. geocoding, cached by app/claims)  ->  weather witness (cached API calls)

Steps that aren't implemented yet are skipped with a message; the script never fails
because of a stub. Run it again once claims + weather are done, then once more on the
venue network before the demo.

Usage (from the repo root, with the backend venv):
    backend\\.venv\\Scripts\\python.exe scripts\\warm_cache.py
    backend\\.venv\\Scripts\\python.exe scripts\\warm_cache.py --only case_a
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app import config  # noqa: E402
from app.claims.extract import extract_claims  # noqa: E402
from app.demo import parse_cases  # noqa: E402 - same parser as POST /api/demo/{case}
from app.runner import run_witnesses  # noqa: E402
from app.storage import parse_posted_at  # noqa: E402
from app.witnesses.base import InvestigationContext  # noqa: E402
from app.witnesses.weather import WeatherWitness  # noqa: E402

CAPTIONS = REPO / "demo" / "captions.md"


def cache_files() -> int:
    if not config.CACHE_DIR.exists():
        return 0
    return sum(1 for p in config.CACHE_DIR.rglob("*") if p.is_file() and p.name != ".gitkeep")


def warm(name: str, case: dict[str, str]) -> None:
    caption, posted = case.get("caption", ""), case.get("posted_at", "")
    print(f"\n[{name}]")
    if not caption or caption.upper() == "TODO":
        print("  skip: no caption yet (TODO in demo/captions.md)")
        return
    print(f"  caption:   {caption}")
    try:
        posted_at = parse_posted_at(None if posted.upper() in ("", "TODO") else posted)
    except ValueError:
        print(f"  skip: posted_at {posted!r} is not a date")
        return
    print(f"  posted_at: {posted_at.isoformat()}")

    # 1. Claims (+ geocoding, which the claims module caches).
    try:
        claims = extract_claims(caption, posted_at)
    except Exception as exc:  # noqa: BLE001 - keep warming the other cases
        print(f"  claims:    FAILED {type(exc).__name__}: {exc}")
        return
    if not claims:
        print("  claims:    none returned (claim extraction not implemented yet?); skipping geocoding + weather")
        return
    print("  claims:    " + "; ".join(f"{c.type}={c.value!r}" for c in claims))
    unlocated = [c.value for c in claims if c.type == "location" and not (c.normalized or {}).get("lat")]
    if unlocated:
        print(f"  geocoding: no coordinates for {unlocated}")

    # 2. Weather (the witness caches every API response under data/cache/).
    witness = WeatherWitness()
    if not witness.implemented:
        print("  weather:   skip, weather witness not implemented yet")
        return
    with tempfile.TemporaryDirectory() as tmp:
        ctx = InvestigationContext(
            inv_id="warm_cache", caption=caption, posted_at=posted_at,
            inputs=[], claims=claims, artifacts_dir=Path(tmp),
        )
        for f in run_witnesses(ctx, [witness]):
            print(f"  weather:   {f.status} ({f.reliability}) - {f.title}")
            if f.status == "inconclusive" and f.details.get("error"):
                print(f"             error: {f.details['error']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fill data/cache/ for the demo captions.")
    ap.add_argument("--only", help="warm a single case, e.g. case_a")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cases = parse_cases(CAPTIONS)
    if args.only:
        if args.only not in cases:
            sys.exit(f"unknown case {args.only!r}; known: {', '.join(cases)}")
        cases = {args.only: cases[args.only]}

    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    before = cache_files()
    print(f"Warming {config.CACHE_DIR} for {len(cases)} case(s) from {CAPTIONS.relative_to(REPO)}")
    for name, case in cases.items():
        warm(name, case)
    print(f"\nCache files: {before} -> {cache_files()}")


if __name__ == "__main__":
    main()
