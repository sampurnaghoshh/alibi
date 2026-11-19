"""
Re-record the Open-Meteo responses used by tests/test_weather.py (free API, needs network).

    cd backend
    python tests/fixtures/weather/record.py

Each fixture stores the request and the moment it was recorded; the tests freeze the
witness's clock at `recorded_at`, so the recorded data covers exactly what the witness asks for.
"""

import json
import sys
from datetime import timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

import requests  # noqa: E402

from app.claims.extract import extract_claims  # noqa: E402
from app.storage import now, parse_posted_at  # noqa: E402
from app.witnesses import weather  # noqa: E402

CASES = {
    # name: (caption, posted_at as ISO or None for "now")
    "chennai_recent": ("Massive flooding in Chennai 30 days ago", "2026-11-19"),
    "chennai_2015_floods": ("Flooding in Chennai on 2 Dec 2015", "2015-12-03"),
    "london_yesterday": ("Flooding in London yesterday", "2026-11-19"),
}


def request_for(caption: str, posted_iso: str, recorded):
    posted = parse_posted_at(posted_iso)
    claims = extract_claims(caption, posted)
    loc = next(c for c in claims if c.type == "location")
    w = weather._window(next(c for c in claims if c.type == "time"))
    points = weather.grid_points(loc.normalized["lat"], loc.normalized["lon"])
    span_start, span_end = w.span
    need_start = min(w.start - timedelta(hours=72), span_start).astimezone(timezone.utc)
    need_end = max(w.end, span_end).astimezone(timezone.utc)
    return weather.build_request(points, need_start.date(), need_end.date(), recorded.astimezone(timezone.utc).date())


if __name__ == "__main__":
    recorded = now()
    for name, (caption, posted) in CASES.items():
        api, url, params = request_for(caption, posted, recorded)
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        out = {"recorded_at": recorded.isoformat(), "caption": caption, "posted_at": posted,
               "api": api, "request": weather.request_url(url, params), "response": r.json()}
        (HERE / f"{name}.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
        n = len(out["response"]) if isinstance(out["response"], list) else 1
        print(f"{name}: {api}, {n} points, {len(r.content) / 1024:.0f} KB")
