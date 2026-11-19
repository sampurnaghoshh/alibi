"""
Claim extraction (CLAUDE.md §6.1). Owner: Nooman. Not a witness: it feeds every witness.

TODO (Nooman):
- LLM path (app/claims/llm.py, chosen by config.LLM_PROVIDER): strict JSON claims with type, value, source_text.
  Compute start/end yourself with caption.find(source_text); DROP the claim if not found (principle 3).
- Rules fallback (must work offline; used when provider = none or the LLM fails):
  gazetteer (app/claims/gazetteer.json), dateparser with RELATIVE_BASE=posted_at,
  config.PART_OF_DAY_HOURS for evening/morning/night (precision "part_of_day"), condition keyword map.
- Geocode locations (Open-Meteo, config.GEOCODE_URL), cached under config.CACHE_DIR.
- The demo must never depend on the LLM: test demo captions with ALIBI_LLM_PROVIDER=none.

Run alone (from backend/):
    python -m app.claims.extract --caption "Massive flooding in Chennai yesterday evening" --posted-at 2026-11-19
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from app.schemas import Claim


def extract_claims(caption: str, posted_at: datetime) -> list[Claim]:
    """caption + posted_at -> claims, each traceable to an exact caption span. Never raises."""
    return []  # placeholder: no claims until the rules/LLM paths are written


if __name__ == "__main__":
    from app.storage import parse_posted_at

    ap = argparse.ArgumentParser(description="Extract claims from a caption.")
    ap.add_argument("--caption", required=True)
    ap.add_argument("--posted-at", default=None, help="e.g. 2026-11-19 (default now)")
    args = ap.parse_args()

    claims = extract_claims(args.caption, parse_posted_at(args.posted_at))
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps([c.model_dump(mode="json") for c in claims], indent=2, ensure_ascii=False))
