"""
Weather witness (CLAUDE.md §6.3). Owner: Sampurna (taken over from Adithi; first version by Adithi).
Family: context. Max reliability: medium.

Question: does recorded rainfall at the claimed place and time support a rain / flood claim?

How it works
  - One Open-Meteo request for 5 points (centre + N/S/E/W at ±0.15°), times in UTC.
  - Recent claims use the forecast API with past_days sized to the claim (max 92); older ones
    use the archive API (ERA5).
  - Only hours that had already happened when the media was shared (and before now) count.
    Forecast hours are never treated as evidence; a window that runs past that point is
    incomplete, so the result is inconclusive.
  - Three numbers, as in the spec: (a) rain at the centre during the claimed window,
    (b) max rain at the 4 surrounding points over the claimed day(s), (c) centre rain over the
    72 h up to the end of the window. Each hourly value is the rain in the hour BEFORE its
    timestamp, so a window counts timestamps with start < t <= end.
  - Rules for flood / heavy_rain / rain claims (thresholds in config.py):
        (a) < 1 mm and (b) < 1 mm and (c) < 5 mm      -> contradiction (medium)
        (a) >= 10 mm or (c) >= 30 mm                  -> consistent (medium)
        otherwise / incomplete data                   -> inconclusive (low)
    dry / storm / other claims: numbers shown for reference only, always inconclusive.
  - Cache (data/cache/): archive answers forever; forecast answers are refreshed after ~1 h when
    online, and an older copy is used when offline.

Run alone (from backend/):
    python -m app.witnesses.weather --caption "Massive flooding in Chennai yesterday evening" --posted-at 2026-11-19
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import requests

from app import config
from app.schemas import Claim, Finding
from app.storage import now as _clock
from app.witnesses.base import InvestigationContext, Witness, run_cli

log = logging.getLogger("alibi.weather")

RAIN_CONDITIONS = {"flood": "flooding", "heavy_rain": "heavy rain", "rain": "rain"}
POINT_LABELS = ["centre", "N", "S", "E", "W"]
HOUR = timedelta(hours=1)

ALTERNATIVES = [
    "Rain upstream or in the catchment may not show up in a small local grid",
    "Flooding can come from drainage failure, blocked waterways or reservoir release",
    "Very localised rain can fall below the grid's resolution",
    "Gridded model and reanalysis rainfall can differ from what rain gauges recorded",
]
BASE_LIMITATIONS = [
    "Rainfall comes from Open-Meteo model and reanalysis grids (about 10-25 km), not from rain gauges",
    "Weather records cannot confirm or rule out non-rain causes of flooding",
]


def now() -> datetime:
    """Current time (patched in tests)."""
    return _clock()


# =============================================================================
# Claims and windows
# =============================================================================

@dataclass
class ClaimedWindow:
    start: datetime
    end: datetime
    precision: str

    @property
    def span(self) -> tuple[datetime, datetime]:
        """The claimed window widened to whole local days (used for the grid points)."""
        tz = self.start.tzinfo
        s = datetime(self.start.year, self.start.month, self.start.day, tzinfo=tz)
        last = self.end - timedelta(seconds=1)
        e = datetime(last.year, last.month, last.day, tzinfo=tz) + timedelta(days=1)
        return s, e


def _first(claims: list[Claim], key: str) -> Optional[Claim]:
    return next((c for c in claims if c.normalized and c.normalized.get(key) is not None), None)


def _window(claim: Claim) -> ClaimedWindow:
    n = claim.normalized or {}
    start, end = datetime.fromisoformat(n["start"]), datetime.fromisoformat(n["end"])
    if start.tzinfo is None:
        start = start.replace(tzinfo=config.DEFAULT_TZ)
    if end.tzinfo is None:
        end = end.replace(tzinfo=config.DEFAULT_TZ)
    if end <= start:
        raise ValueError("claimed end is not after start")
    return ClaimedWindow(start, end, n.get("precision", "day"))


def _date_label(d: datetime, ref_year: int) -> str:
    return f"{d.day} {d:%b}" + (f" {d.year}" if d.year != ref_year else "")


def describe_window(w: ClaimedWindow, ref_year: int) -> str:
    """'during 17:00–21:00 on 18 Nov' / 'on 18 Nov' / 'between 12 Nov and 18 Nov'."""
    if w.precision in ("part_of_day", "hour"):
        return f"during {w.start:%H:%M}–{w.end:%H:%M} on {_date_label(w.start, ref_year)}"
    last = w.end - timedelta(seconds=1)
    if w.start.date() == last.date():
        return f"on {_date_label(w.start, ref_year)}"
    return f"between {_date_label(w.start, ref_year)} and {_date_label(last, ref_year)}"


# =============================================================================
# Open-Meteo request, cache, parsing
# =============================================================================

def grid_points(lat: float, lon: float) -> list[tuple[str, float, float]]:
    d = config.WEATHER_GRID_OFFSET_DEG
    return [
        ("centre", lat, lon),
        ("N", round(lat + d, 4), lon),
        ("S", round(lat - d, 4), lon),
        ("E", lat, round(lon + d, 4)),
        ("W", lat, round(lon - d, 4)),
    ]


def build_request(points: list[tuple[str, float, float]], need_from: date, need_to: date,
                  today: date) -> tuple[str, str, dict[str, Any]]:
    """(api, url, params) for ONE request covering all points from need_from to need_to (UTC dates)."""
    lats = ",".join(f"{p[1]:.4f}" for p in points)
    lons = ",".join(f"{p[2]:.4f}" for p in points)
    common = {"latitude": lats, "longitude": lons, "hourly": "precipitation", "timezone": "UTC"}
    past_days = (today - need_from).days + 1
    if past_days <= config.WEATHER_MAX_PAST_DAYS:
        return "forecast_past_days", config.WEATHER_FORECAST_URL, {
            **common, "past_days": max(1, past_days), "forecast_days": 1,
        }
    return "archive", config.WEATHER_ARCHIVE_URL, {
        **common, "start_date": need_from.isoformat(), "end_date": need_to.isoformat(),
    }


def request_url(url: str, params: dict[str, Any]) -> str:
    return requests.Request("GET", url, params=params).prepare().url


def _cache_path(api: str, points: list, need_from: date, need_to: date) -> Path:
    """Keyed by what is needed (place + dates), not by past_days, so a warmed cache still works
    on a later day."""
    key = json.dumps([api, [p[1:] for p in points], need_from.isoformat(), need_to.isoformat()])
    return Path(config.CACHE_DIR) / f"openmeteo_{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"


def fetch(api: str, url: str, params: dict, cache_file: Path) -> tuple[list[dict], str]:
    """Return (one response per point, source) where source is 'network' | 'cache' | 'stale_cache'.
    Raises on failure when there is nothing usable in the cache."""
    cached = None
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
    if cached is not None:
        age = time.time() - cache_file.stat().st_mtime
        if api == "archive" or age < config.WEATHER_FORECAST_CACHE_TTL_S:
            return cached, "cache"
    try:
        r = requests.get(url, params=params, timeout=config.WEATHER_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
    except Exception:
        if cached is not None:
            return cached, "stale_cache"     # offline: an older copy beats nothing
        raise
    data = data if isinstance(data, list) else [data]
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data, "network"


def hourly_series(resp: dict) -> list[tuple[datetime, Optional[float]]]:
    """[(UTC end-of-hour time, mm in the preceding hour)]. Naive times are shifted by utc_offset_seconds."""
    offset = timedelta(seconds=resp.get("utc_offset_seconds") or 0)
    hourly = resp.get("hourly") or {}
    out = []
    for t, v in zip(hourly.get("time", []), hourly.get("precipitation", [])):
        ts = datetime.fromisoformat(t)
        ts = (ts - offset).replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
        out.append((ts, None if v is None else float(v)))
    return out


def rain_between(series, start: datetime, end: datetime, observed_until: datetime) -> tuple[float, bool]:
    """Sum of hourly values with start < t <= end and t <= observed_until.
    Complete only if the whole window had happened by observed_until and every hour has a value."""
    expected = math.ceil((end - start) / HOUR)
    total, n = 0.0, 0
    for t, v in series:
        if start < t <= end and t <= observed_until and v is not None:
            total += v
            n += 1
    return round(total, 1), (end <= observed_until and n >= expected)


# =============================================================================
# Witness
# =============================================================================

class WeatherWitness(Witness):
    name = "weather"
    label = "Weather"
    implemented = True

    def applicable(self, ctx: InvestigationContext) -> bool:
        has_condition = bool(ctx.claims_of("weather_condition") or ctx.claims_of("event"))
        return bool(ctx.claims_of("location") and ctx.claims_of("time") and has_condition)

    def _inconclusive(self, title: str, summary: str, claim_ids: list[str], limitations: list[str],
                      details: Optional[dict] = None) -> list[Finding]:
        return [self.finding(status="inconclusive", reliability="low", title=title, summary=summary,
                             claim_ids=claim_ids, details=details or {}, alternatives=ALTERNATIVES,
                             limitations=[*limitations, *BASE_LIMITATIONS])]

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        loc = _first(ctx.claims_of("location"), "lat")
        tclaim = _first(ctx.claims_of("time"), "start")
        cond_claim = _first(ctx.claims_of("weather_condition") + ctx.claims_of("event"), "condition")
        claim_ids = [c.id for c in (cond_claim, loc, tclaim) if c is not None]

        if loc is None or tclaim is None or cond_claim is None:
            return self._inconclusive(
                "Weather check not possible", "The caption needs a place with coordinates, a time and a weather "
                "or flood condition before rainfall records can be compared.", claim_ids,
                ["Weather verification requires a location, a time window and a condition"])
        try:
            w = _window(tclaim)
        except (KeyError, ValueError) as exc:
            return self._inconclusive("Weather check not possible", "The claimed time could not be interpreted.",
                                      claim_ids, [f"Error: {exc}"])

        condition = cond_claim.normalized["condition"]
        lat, lon = float(loc.normalized["lat"]), float(loc.normalized["lon"])
        points = grid_points(lat, lon)

        # Only rain that had already fallen when the media was shared (and before now) is evidence.
        current = now()
        posted = ctx.posted_at if ctx.posted_at.tzinfo else ctx.posted_at.replace(tzinfo=config.DEFAULT_TZ)
        observed_until = min(current, posted).astimezone(timezone.utc)

        span_start, span_end = w.span
        need_start = min(w.start - timedelta(hours=72), span_start).astimezone(timezone.utc)
        need_end = max(w.end, span_end).astimezone(timezone.utc)
        api, url, params = build_request(points, need_start.date(), need_end.date(),
                                         current.astimezone(timezone.utc).date())
        full_url = request_url(url, params)
        try:
            responses, source = fetch(api, url, params, _cache_path(api, points, need_start.date(), need_end.date()))
            if len(responses) != len(points):
                raise ValueError(f"expected {len(points)} locations in the response, got {len(responses)}")
        except Exception as exc:  # noqa: BLE001 - offline or API error -> inconclusive, never a crash
            return self._inconclusive(
                "Weather data unavailable",
                "Rainfall records could not be retrieved for the claimed place and time, so the weather "
                "gives no evidence either way.", claim_ids,
                [f"Error: {type(exc).__name__}: {exc}", "No cached copy of this weather data was available"],
                {"api": api, "request": full_url})

        series = [hourly_series(r) for r in responses]
        a, a_ok = rain_between(series[0], w.start, w.end, observed_until)
        c, c_ok = rain_between(series[0], w.end - timedelta(hours=72), w.end, observed_until)
        day = [rain_between(s, span_start, span_end, observed_until) for s in series]
        grid_vals = [mm for mm, _ in day[1:]]
        b, b_ok = max(grid_vals), all(ok for _, ok in day[1:])

        # Artifact + details for the frontend (weather map, chart) and the report.
        grid = [{"label": label, "lat": plat, "lon": plon, "day_mm": day[i][0],
                 "window_mm": rain_between(series[i], w.start, w.end, observed_until)[0]}
                for i, (label, plat, plon) in enumerate(points)][1:]
        tz = w.start.tzinfo
        hourly_centre = [{"t": t.astimezone(tz).isoformat(), "mm": v} for t, v in series[0]
                         if span_start < t <= span_end and t <= observed_until][:24 * 7]
        art = ctx.artifact_path("weather_grid.json")
        art.write_text(json.dumps({
            "request": full_url, "api": api, "source": source, "observed_until": observed_until.isoformat(),
            "points": [{"label": label, "lat": plat, "lon": plon,
                        "hourly": [[t.isoformat(), v] for t, v in series[i] if need_start < t <= need_end]}
                       for i, (label, plat, plon) in enumerate(points)],
        }, indent=1), encoding="utf-8")
        details = {
            "api": api,
            "claimed_condition": condition,
            "claimed_window": {"start": w.start.isoformat(), "end": w.end.isoformat()},
            "observed_until": observed_until.astimezone(tz).isoformat(),
            "centre": {"lat": lat, "lon": lon, "window_mm": a, "day_mm": day[0][0]},
            "grid": grid,
            "grid_max_mm": b,
            "prior_72h_mm": c,
            "hourly_centre": hourly_centre,
            "coverage": {"window": a_ok, "grid": b_ok, "prior_72h": c_ok},
            "data_source": source,
            "thresholds": {"dry_point_mm": config.WEATHER_DRY_POINT_MM, "dry_72h_mm": config.WEATHER_DRY_72H_MM,
                           "wet_point_mm": config.WEATHER_WET_POINT_MM, "wet_72h_mm": config.WEATHER_WET_72H_MM},
        }
        evidence = [
            {"kind": "api", "ref": full_url, "label": "Open-Meteo hourly precipitation (5 grid points)"},
            {"kind": "artifact", "ref": ctx.artifact_ref("weather_grid.json"), "label": "All 5 grid readings"},
        ]

        when = describe_window(w, current.year)   # show the year only when it isn't this year
        numbers = (f"{a:.1f} mm at the claimed location {when}; "
                   f"{min(grid_vals):.1f}–{b:.1f} mm across 4 surrounding points; "
                   f"{c:.1f} mm over the preceding 72 h.")

        limitations = list(BASE_LIMITATIONS)
        if w.end > observed_until:
            limitations.append("Part of the claimed time is after the media was shared (or still in the future), "
                               "so recorded weather cannot cover it; forecasts are never used as evidence")
        elif not (a_ok and c_ok and b_ok):
            limitations.append("Rainfall records are incomplete for part of the claimed time or the 72 h before it")
        if source == "stale_cache":
            limitations.append("Offline: an older cached copy of the weather data was used")

        # Rules (§6.3). Only rain / flood claims can be supported or contradicted.
        noun = RAIN_CONDITIONS.get(condition)
        if noun is None:
            status, reliability = "inconclusive", "low"
            title = f"Rainfall shown for reference; {condition.replace('_', ' ')} claims are not judged"
            summary = (f"{numbers} This witness only judges rain and flood claims, so for a "
                       f"{condition.replace('_', ' ')} claim these numbers are context, not evidence either way.")
        elif (a_ok and a >= config.WEATHER_WET_POINT_MM) or (c_ok and c >= config.WEATHER_WET_72H_MM):
            status, reliability = "consistent", "medium"
            title = f"Recorded rainfall is consistent with the stated {noun}"
            summary = numbers
        elif (a_ok and b_ok and c_ok and a < config.WEATHER_DRY_POINT_MM and b < config.WEATHER_DRY_POINT_MM
              and c < config.WEATHER_DRY_72H_MM):
            status, reliability = "contradiction", "medium"
            title = f"Recorded rainfall does not support the stated {noun}"
            summary = numbers
        else:
            status, reliability = "inconclusive", "low"
            if w.end > observed_until:
                title = "Recorded rainfall does not cover the claimed time"
                summary = (f"{numbers} The claimed time runs past when the media was shared, so only the hours "
                           f"already recorded are counted and no conclusion is drawn.")
            else:
                title = f"Recorded rainfall neither supports nor contradicts the stated {noun}"
                summary = numbers

        return [self.finding(status=status, reliability=reliability, title=title, summary=summary,
                             claim_ids=claim_ids, details=details, evidence=evidence,
                             alternatives=ALTERNATIVES, limitations=limitations)]


if __name__ == "__main__":
    run_cli(WeatherWitness())
