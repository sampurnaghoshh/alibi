"""
ALIBI configuration: paths, environment, and every threshold.

Thresholds are STARTING VALUES from CLAUDE.md §6. Calibrate them on genuine
clips and update the values here, noting "calibrated on <what>" in the comment.
Each owner edits only their own section.
"""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

try:  # backend/.env (never committed). Missing python-dotenv is not fatal.
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass


# =============================================================================
# --- Sampurna: engine ---
# =============================================================================

APP_VERSION = "0.3.0"

# Paths
BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = Path(os.getenv("ALIBI_DATA_DIR", REPO_ROOT / "data"))
RUNS_DIR = DATA_DIR / "runs"          # one folder per investigation (gitignored)
CACHE_DIR = DATA_DIR / "cache"        # API response cache (gitignored, warm before demo)
ARCHIVE_DIR = DATA_DIR / "archive"    # verified clips + archive.json (committed)
FIXTURE_PATH = REPO_ROOT / "shared" / "fixtures" / "sample_investigation.json"

# Timezone used when the user gives a date without one (venue is in India).
DEFAULT_TZ = timezone(timedelta(hours=5, minutes=30))

# API
CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

# Runner: per-witness time limit in seconds (§6). Witnesses run in parallel.
WITNESS_TIMEOUT_S = 30.0
WITNESS_TIMEOUT_OVERRIDES_S = {"lipsync": 90.0}

# Experimental score (§7.5). Never the headline, always shown with its label.
SCORE_START = 100
SCORE_PENALTY = {"high": 30, "medium": 15, "low": 5}   # per contradiction, by reliability
SCORE_PENALTY_INCONCLUSIVE = 3                          # per inconclusive finding
# insufficient_signal and not_applicable cost 0 (absence of evidence is not evidence).

# Rule engine (§7). Headline texts: fixed templates, never the forbidden words of §9.
HEADLINE_TEXT = {
    "context_not_supported": "The stated context is not supported by the available evidence",
    "signals_inconsistent": "Audio-visual signals show inconsistencies",
    "consistent_so_far": "No contradictions found in the available evidence",
    "insufficient_evidence": "Not enough evidence to support or contradict the stated context",
}
# partially_supported: one family contradicts at medium reliability; text depends on which.
PARTIAL_HEADLINE_TEXT = {
    "context": "Part of the available evidence does not support the stated context",
    "signal": "One audio-visual signal shows a possible inconsistency",
    "pixels": "One pixel-level indicator shows a possible inconsistency",
}
# Witnesses whose findings are always weak indicators, whatever their status (§6.7: ELA).
WEAK_INDICATOR_WITNESSES = ("ela",)

# Readable witness names (graph labels, template explanations).
WITNESS_LABELS = {
    "metadata": "Metadata",
    "weather": "Weather",
    "prior_appearance": "Prior appearance",
    "lipsync": "Lip-sync",
    "enf": "Power-grid hum",
    "ela": "ELA",
}

# §2.7 / §9: never in UI, report or explanation text. "authentic" covers "authenticity".
FORBIDDEN_WORDS_RE = r"\b(fake|real|authentic\w*|proven|definitely|AI-generated)\b"

# Explanations (§7.6): let the LLM reword titles/summaries. Off unless a provider is configured.
LLM_EXPLAIN = os.getenv("ALIBI_LLM_EXPLAIN", "1") == "1"

# Runtime LLM (claim extraction + explanations only; never decides truth)
LLM_PROVIDER = os.getenv("ALIBI_LLM_PROVIDER", "none").strip().lower()   # anthropic | gemini | none
LLM_MODEL = os.getenv("ALIBI_LLM_MODEL", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()


def llm_configured() -> bool:
    """True when a provider is chosen and its key is present."""
    if LLM_PROVIDER == "anthropic":
        return bool(ANTHROPIC_API_KEY)
    if LLM_PROVIDER == "gemini":
        return bool(GEMINI_API_KEY)
    return False


# =============================================================================
# --- Nooman: claims + lipsync + enf ---
# =============================================================================

# Claims (§6.1): part-of-day windows as (start_hour, end_hour); night wraps past midnight.
PART_OF_DAY_HOURS = {
    "morning": (6, 11),
    "evening": (17, 21),
    "night": (20, 4),
}
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Lip-sync synchrony indicator (§6.5). Calibrate on >= 5 genuine phone clips.
LIPSYNC_MIN_FACE_FRACTION = 0.50     # face detected in >= 50% of sampled frames
LIPSYNC_SMOOTH_FRAMES = 3            # median filter on mouth aperture
LIPSYNC_WINDOW_S = 3.0
LIPSYNC_STEP_S = 1.0
LIPSYNC_MAX_LAG_S = 0.5              # cross-correlation search range +/-
LIPSYNC_CONSISTENT_MAX_LAG_S = 0.12  # median |lag| <= this ...
LIPSYNC_CONSISTENT_MIN_CORR = 0.30   # ... and median corr >= this -> consistent
LIPSYNC_CONTRA_MIN_LAG_S = 0.25      # median |lag| >= this ...
LIPSYNC_CONTRA_MAX_CORR = 0.10       # ... or median corr < this -> contradiction
LIPSYNC_MIN_SPEECH_FRACTION = 0.50   # contradiction needs speech in >= 50% of windows

# ENF power-grid hum (§6.6). Conditional on the hour-1 go/no-go test.
ENF_GO = False                       # set True ONLY if 50/100 Hz was found in >= 3 of 5 venue clips
ENF_STFT_WINDOW_S = (4.0, 8.0)       # shorter end for short clips
ENF_PEAK_HALF_WIDTH_HZ = 0.5         # signal band: f +/- 0.5 Hz
ENF_NOISE_BAND_HZ = 5.0              # compared against a 5 Hz band around f
ENF_MIN_SNR_DB = 6.0
ENF_MIN_FRAME_FRACTION = 0.60        # detected if SNR >= 6 dB in >= 60% of frames
ENF_FREQS_50HZ = (50.0, 100.0)
ENF_FREQS_60HZ = (60.0, 120.0)


# =============================================================================
# --- Adithi: metadata + weather + prior_appearance + ela ---
# =============================================================================

# Metadata integrity (§6.2)
METADATA_MAX_DATE_DIFF_DAYS = 2      # capture date vs claimed date
METADATA_MAX_GPS_DIST_KM = 50.0      # GPS vs claimed location
METADATA_EDITING_SOFTWARE = [
    "photoshop", "lightroom", "snapseed", "capcut", "picsart", "facetune",
    "premiere", "after effects", "final cut", "davinci", "inshot", "kinemaster",
]

# Weather (§6.3)
WEATHER_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_RECENT_DAYS = 90             # newer than this -> forecast API with past_days
WEATHER_PAST_DAYS = 7
WEATHER_GRID_OFFSET_DEG = 0.15       # N/S/E/W points, about 15 km
WEATHER_DRY_POINT_MM = 1.0           # (a) centre < 1 mm and (b) grid max < 1 mm ...
WEATHER_DRY_72H_MM = 5.0             # ... and (c) 72 h < 5 mm -> contradiction
WEATHER_WET_POINT_MM = 10.0          # (a) >= 10 mm ...
WEATHER_WET_72H_MM = 30.0            # ... or (c) >= 30 mm -> consistent

# Prior appearance (§6.4)
PRIOR_SAMPLE_FPS = 1.0
PRIOR_MAX_HAMMING = 8                # frame match if phash distance <= 8
PRIOR_MIN_MATCHED_FRAMES = 3         # clip match if >= 3 frames ...
PRIOR_MIN_MATCHED_FRACTION = 0.30    # ... or >= 30% of sampled frames
ARCHIVE_JSON = ARCHIVE_DIR / "archive.json"

# ELA (§6.7): always inconclusive, reliability low
ELA_JPEG_QUALITY = 90
ELA_AMPLIFICATION = 15
