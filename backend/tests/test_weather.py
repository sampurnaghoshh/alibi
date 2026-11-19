"""Weather witness (§6.3), offline against recorded Open-Meteo responses (tests/fixtures/weather/).

Covers the review issues: #1 forecast hours never count, #2 past_days sized to the claim,
#5 timezone handling, plus caching, one request for 5 points, evidence/details shape and wording.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from app import config
from app.claims.extract import extract_claims
from app.runner import run_witnesses
from app.storage import parse_posted_at
from app.witnesses import weather
from app.witnesses.base import InvestigationContext
from app.witnesses.weather import WeatherWitness, hourly_series, rain_between

FIX = Path(__file__).parent / "fixtures" / "weather"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


class FakeAPI:
    """Stands in for requests.get: serves a recorded response and records every call."""

    def __init__(self, response=None, fail=False):
        self.response, self.fail, self.calls = response, fail, []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.fail:
            raise requests.ConnectionError("network disabled in tests")
        body = self.response

        class R:
            content = b""
            def raise_for_status(self): pass
            def json(self): return body
        return R()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Temp cache + artifacts, frozen clock, and a fake API. Returns a helper to run the witness."""
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")

    def run(caption, posted, fixture=None, api=None, now=None):
        rec = load(fixture) if fixture else None
        clock = datetime.fromisoformat(now or rec["recorded_at"])
        monkeypatch.setattr(weather, "now", lambda: clock)
        fake = api or FakeAPI(rec["response"] if rec else None)
        monkeypatch.setattr(requests, "get", fake)
        posted_dt = parse_posted_at(posted) if isinstance(posted, str) else posted
        ctx = InvestigationContext("t", caption, posted_dt, [], extract_claims(caption, posted_dt),
                                   tmp_path / "artifacts")
        [f] = run_witnesses(ctx, [WeatherWitness()])
        return f, fake, ctx
    return run


CASE_A = "Massive flooding in Chennai yesterday evening, roads completely underwater"


# --- the main demo case, from recorded data -----------------------------------------

def test_case_a_from_recorded_data(env):
    f, api, ctx = env(CASE_A, "2026-11-19", "chennai_recent")
    assert (f.status, f.reliability) == ("contradiction", "medium")
    assert f.title == "Recorded rainfall does not support the stated flooding"
    # Fixture format: "0.0 mm at the claimed location during 17:00–21:00 on 18 Nov; 0.0–0.0 mm across 4 ...; 0.0 mm over the preceding 72 h."
    assert re.fullmatch(r"\d+\.\d mm at the claimed location during 17:00–21:00 on 18 Nov; "
                        r"\d+\.\d–\d+\.\d mm across 4 surrounding points; \d+\.\d mm over the preceding 72 h\.", f.summary)
    d = f.details
    assert d["api"] == "forecast_past_days"
    assert set(d["centre"]) == {"lat", "lon", "window_mm", "day_mm"}
    assert [g["label"] for g in d["grid"]] == ["N", "S", "E", "W"]
    assert d["grid"][0]["lat"] == pytest.approx(13.0827 + 0.15) and d["grid"][2]["lon"] == pytest.approx(80.2707 + 0.15)
    assert {"grid_max_mm", "prior_72h_mm", "hourly_centre", "thresholds"} <= set(d)
    assert [e.kind for e in f.evidence] == ["api", "artifact"]
    assert f.evidence[0].ref.startswith("https://api.open-meteo.com/v1/forecast?")
    assert (ctx.artifacts_dir / "weather_grid.json").is_file()
    assert any("upstream" in a.lower() for a in f.alternatives)


def test_one_request_for_all_five_points(env):
    _, api, _ = env(CASE_A, "2026-11-19", "chennai_recent")
    [call] = api.calls
    assert call["timeout"] == config.WEATHER_TIMEOUT_S == 8.0
    assert len(call["params"]["latitude"].split(",")) == 5 == len(call["params"]["longitude"].split(","))
    assert call["params"]["timezone"] == "UTC"


# --- issue 1: forecast hours are never evidence --------------------------------------

def test_future_window_is_inconclusive_not_contradiction(env):
    rec = load("chennai_recent")
    recorded = datetime.fromisoformat(rec["recorded_at"])
    # The recording does contain forecast values for the coming hours...
    series = hourly_series(rec["response"][0])
    assert any(t > recorded and v is not None for t, v in series)
    # ...but a claim about tonight, shared now, must not be judged from them.
    f, _, _ = env("Heavy rain in Chennai tonight", recorded, "chennai_recent")
    assert (f.status, f.reliability) == ("inconclusive", "low")
    assert f.title == "Recorded rainfall does not cover the claimed time"
    assert any("forecasts are never used" in lim for lim in f.limitations)
    assert all(datetime.fromisoformat(h["t"]) <= recorded for h in f.details["hourly_centre"])


def test_window_after_posting_time_is_incomplete(env):
    # Shared at 18:00 claiming "this evening" (17:00-21:00): the last 3 hours had not happened yet.
    rec = load("chennai_recent")
    f, _, _ = env("Heavy rain in Chennai this evening", "2026-11-18T18:00:00+05:30", "chennai_recent")
    assert f.status == "inconclusive" and f.details["coverage"]["window"] is False
    assert datetime.fromisoformat(f.details["observed_until"]) == datetime.fromisoformat("2026-11-18T18:00:00+05:30")


# --- issue 2: past_days sized to the claim ------------------------------------------

def test_thirty_days_ago_is_covered(env):
    f, api, _ = env("Massive flooding in Chennai 30 days ago", "2026-11-19", "chennai_recent")
    p = api.calls[0]["params"]
    assert 33 <= p["past_days"] <= config.WEATHER_MAX_PAST_DAYS          # not the old fixed 7
    assert f.details["api"] == "forecast_past_days"
    assert f.details["coverage"] == {"window": True, "grid": True, "prior_72h": True}


def test_old_event_uses_archive_and_supports_claim(env):
    f, api, _ = env("Flooding in Chennai on 2 Dec 2015", "2015-12-03", "chennai_2015_floods")
    p = api.calls[0]["params"]
    assert api.calls[0]["url"] == config.WEATHER_ARCHIVE_URL and "past_days" not in p
    assert p["start_date"] <= "2015-11-29" and p["end_date"] >= "2015-12-02"
    assert (f.status, f.reliability) == ("consistent", "medium")
    assert f.title == "Recorded rainfall is consistent with the stated flooding"
    assert f.details["centre"]["window_mm"] >= config.WEATHER_WET_POINT_MM
    assert f.summary.startswith(f"{f.details['centre']['window_mm']:.1f} mm at the claimed location on 2 Dec 2015;")


# --- issue 5: timezones ---------------------------------------------------------------

def test_london_sums_use_utc_times(env):
    rec = load("london_yesterday")
    f, api, _ = env("Flooding in London yesterday", "2026-11-19", "london_yesterday")
    assert api.calls[0]["params"]["timezone"] == "UTC"
    assert all(r["utc_offset_seconds"] == 0 for r in rec["response"])
    # "yesterday" in IST = 2026-11-17 18:30 UTC .. 2026-11-18 18:30 UTC; values at t in (start, end].
    start = datetime(2026, 11, 17, 18, 30, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    centre = rec["response"][0]["hourly"]
    expected = round(sum(v for t, v in zip(centre["time"], centre["precipitation"])
                         if v is not None and start < datetime.fromisoformat(t).replace(tzinfo=timezone.utc) <= end), 1)
    assert f.details["centre"]["window_mm"] == expected


def test_local_times_shifted_by_utc_offset():
    resp = {"utc_offset_seconds": 19800, "hourly": {"time": ["2026-11-18T17:00"], "precipitation": [2.5]}}
    [(t, v)] = hourly_series(resp)
    assert t == datetime(2026, 11, 18, 11, 30, tzinfo=timezone.utc) and v == 2.5


def test_hour_boundaries_start_exclusive_end_inclusive():
    ist = timezone(timedelta(hours=5, minutes=30))
    series = [(datetime(2026, 11, 18, h, tzinfo=ist).astimezone(timezone.utc), 1.0) for h in range(16, 23)]
    start, end = datetime(2026, 11, 18, 17, tzinfo=ist), datetime(2026, 11, 18, 21, tzinfo=ist)
    total, complete = rain_between(series, start, end, end)
    assert (total, complete) == (4.0, True)        # 18, 19, 20, 21 - not 17 (rain from 16-17)


# --- caching -----------------------------------------------------------------------------

def test_forecast_cache_fresh_then_refreshed(env):
    rec = load("chennai_recent")
    api = FakeAPI(rec["response"])
    env(CASE_A, "2026-11-19", "chennai_recent", api=api)
    env(CASE_A, "2026-11-19", "chennai_recent", api=api)
    assert len(api.calls) == 1                                  # second run served from cache
    for p in Path(config.CACHE_DIR).glob("openmeteo_*.json"):
        old = time.time() - config.WEATHER_FORECAST_CACHE_TTL_S - 60
        os.utime(p, (old, old))
    f, _, _ = env(CASE_A, "2026-11-19", "chennai_recent", api=api)
    assert len(api.calls) == 2 and f.details["data_source"] == "network"   # older than ~1 h -> refreshed


def test_offline_uses_stale_forecast_cache(env):
    env(CASE_A, "2026-11-19", "chennai_recent")
    for p in Path(config.CACHE_DIR).glob("openmeteo_*.json"):
        old = time.time() - 10 * 3600
        os.utime(p, (old, old))
    f, _, _ = env(CASE_A, "2026-11-19", "chennai_recent", api=FakeAPI(fail=True))
    assert f.status == "contradiction" and f.details["data_source"] == "stale_cache"
    assert any("older cached copy" in lim for lim in f.limitations)


def test_archive_cache_never_expires(env):
    env("Flooding in Chennai on 2 Dec 2015", "2015-12-03", "chennai_2015_floods")
    for p in Path(config.CACHE_DIR).glob("openmeteo_*.json"):
        old = time.time() - 30 * 24 * 3600
        os.utime(p, (old, old))
    offline = FakeAPI(fail=True)
    f, _, _ = env("Flooding in Chennai on 2 Dec 2015", "2015-12-03", "chennai_2015_floods", api=offline)
    assert offline.calls == [] and f.status == "consistent" and f.details["data_source"] == "cache"


def test_offline_without_cache_is_inconclusive(env):
    f, _, _ = env(CASE_A, "2026-11-19", "chennai_recent", api=FakeAPI(fail=True))
    assert (f.status, f.reliability, f.title) == ("inconclusive", "low", "Weather data unavailable")
    assert f.limitations[0].startswith("Error: ConnectionError")
    assert f.alternatives


# --- dry / storm claims and wording ----------------------------------------------------

def test_storm_claim_shows_numbers_for_reference(env):
    f, _, _ = env("Cyclone in Chennai yesterday", "2026-11-19", "chennai_recent")
    assert (f.status, f.reliability) == ("inconclusive", "low")
    assert f.title == "Rainfall shown for reference; storm claims are not judged"
    assert "mm at the claimed location on 18 Nov" in f.summary and "only judges rain and flood claims" in f.summary


def test_no_forbidden_words(env):
    texts = []
    for caption, posted, fx in [(CASE_A, "2026-11-19", "chennai_recent"),
                                ("Flooding in Chennai on 2 Dec 2015", "2015-12-03", "chennai_2015_floods"),
                                ("Heavy rain in Chennai tonight", None, "chennai_recent")]:
        posted = posted or datetime.fromisoformat(load(fx)["recorded_at"])
        f, _, _ = env(caption, posted, fx)
        texts += [f.title, f.summary, *f.alternatives, *f.limitations]
    for t in texts:
        assert not re.search(config.FORBIDDEN_WORDS_RE, t, re.IGNORECASE), t
