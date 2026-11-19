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
# Browsers may call the API from these origins (CORS):
# - the local Next.js dev server;
# - any https://*.vercel.app deployment and any https://*.trycloudflare.com quick-tunnel host
#   (CORS_ORIGIN_REGEX); the API has no login, so CORS here is about browsers, not access control;
# - extra exact origins from ALIBI_CORS_ORIGINS (comma-separated, e.g. a custom domain).
def parse_origins(value: str) -> list[str]:
    """ALIBI_CORS_ORIGINS="https://a.example, https://b.example/" -> ["https://a.example", "https://b.example"]."""
    return [o.strip().rstrip("/") for o in (value or "").split(",") if o.strip()]


CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"] + parse_origins(os.getenv("ALIBI_CORS_ORIGINS", ""))
CORS_ORIGIN_REGEX = r"https://([a-z0-9-]+\.)*(vercel\.app|trycloudflare\.com)$"
# The Next.js frontend as a static export (`npm run build` -> frontend/out). When the folder exists,
# the API serves it at "/" (one URL for the frontend, /live and /api/*: Docker / Hugging Face Space).
FRONTEND_DIR = Path(os.getenv("ALIBI_FRONTEND_DIR", REPO_ROOT / "frontend" / "out"))

# Upload limits (checked before anything is stored)
MAX_UPLOAD_FILES = 5
MAX_UPLOAD_BYTES = 200 * 1024 * 1024        # per file

# Live Desk (§14). Caption re-runs (POST /api/investigations/{id}/rerun) repeat the witnesses whose
# result depends on the caption's claims and re-use the rest from the original run.
RERUN_CAPTION_DEPENDENT = ("metadata", "weather", "prior_appearance", "enf")   # ENF: verdict uses the claimed country
RERUN_REUSED = ("ela", "lipsync", "synthetic_signals")
LIVE_BACKGROUND_WORKERS = 2          # async investigations running at once
LIVE_SSE_POLL_S = 0.2                # how often the event stream checks history.jsonl for new lines
LIVE_SSE_KEEPALIVE_S = 15.0
LIVE_SSE_MAX_S = 600.0               # an event stream closes after this long

# Telegram bot (§14 step 2, app/bot/). All secrets come from backend/.env; never log the token.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ALLOWED_CHAT_IDS = {
    int(x) for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x.lstrip("-").isdigit()
}
TELEGRAM_PASSCODE = os.getenv("TELEGRAM_PASSCODE", "").strip()      # "/start <passcode>" approves a chat
TELEGRAM_API_BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
TELEGRAM_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024    # Bot API getFile limit
TELEGRAM_POLL_TIMEOUT_S = 30                       # long polling
BOT_WORKERS = 4                                    # messages handled at once
BOT_STATE_FILE = DATA_DIR / "bot_chats.json"       # approved chats + which message belongs to which run (gitignored)
# 127.0.0.1 rather than "localhost": on Windows, "localhost" tries IPv6 ::1 first and every new
# connection to uvicorn (which listens on 127.0.0.1) waits ~2 s before falling back.
ALIBI_API_URL = os.getenv("ALIBI_API_URL", "http://127.0.0.1:8000").rstrip("/")
# Optional: the bot's @username for the Live Desk idle screen. Empty = ask Telegram (getMe) once and cache it.
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")

# Live Desk page (§14 step 3): standalone HTML/CSS/JS served at GET /live, libraries vendored (offline).
LIVE_STATIC_DIR = BACKEND_DIR / "app" / "live" / "static"

# Demo presets (POST /api/demo/{case}): cases are read from this file; file paths in it are
# relative to the repo root and must stay inside demo/.
DEMO_CAPTIONS = REPO_ROOT / "demo" / "captions.md"

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
    "synthetic_signals": "Synthetic signals",
}

# §2.7 / §9: never in UI, report or explanation text. "authentic" covers "authenticity".
FORBIDDEN_WORDS_RE = r"\b(fake|real|authentic\w*|proven|definitely|AI-generated)\b"
# The one sentence that may contain a forbidden word: the question judges ask, shown with its answer
# ("Is this media AI-generated or manipulated?" -> "AI-generation marker found (...)"). It is a question,
# never a verdict; every answer and all other UI/report text still obey FORBIDDEN_WORDS_RE.
FORBIDDEN_WORDS_ALLOWED_PHRASES = (
    "Is this media AI-generated or manipulated?",
    "No AI marker or editing trace found. Most AI-generated media carries no marker, so this is not proof of authenticity.",
)


def forbidden_word(text: str):
    """re.Match of the first forbidden word in text (after removing the allowed question), or None."""
    import re
    for phrase in FORBIDDEN_WORDS_ALLOWED_PHRASES:
        text = text.replace(phrase, "")
    return re.search(FORBIDDEN_WORDS_RE, text, re.IGNORECASE)

# Explanations (§7.6): let the LLM reword titles/summaries. OFF by default: it costs one call per
# finding (~6 per investigation), which would eat a free-tier daily quota. Set ALIBI_LLM_EXPLAIN=1 to try it.
LLM_EXPLAIN = os.getenv("ALIBI_LLM_EXPLAIN", "0") == "1"

# ELA (§6.7, taken over from Adithi): ALWAYS inconclusive, reliability low, a weak indicator.
# The hotspot values only choose the wording and the highlighted region; they never change the status.
ELA_JPEG_QUALITY = 90
ELA_AMPLIFICATION = 15
ELA_BLOCK_PX = 16                    # error levels are compared per 16x16 block
ELA_HOTSPOT_RATIO = 3.0              # block mean >= 3x the median block -> "hotspot" block
ELA_HOTSPOT_MIN_LEVEL = 2.0          # ... and >= 2 (of 255, before amplification), ignores near-zero noise
ELA_HOTSPOT_MAX_FRACTION = 0.5       # hotspots covering > 50% of the frame are not "localised"
ELA_MAX_INPUTS = 3                   # analyse at most this many images/videos per investigation

# Weather (§6.3, taken over from Adithi). Starting values from the spec; the rules are unchanged.
WEATHER_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_MAX_PAST_DAYS = 92           # forecast API's past_days limit; anything older -> archive API (ERA5)
WEATHER_GRID_OFFSET_DEG = 0.15       # N/S/E/W points, about 15 km
WEATHER_DRY_POINT_MM = 1.0           # (a) centre < 1 mm and (b) grid max < 1 mm ...
WEATHER_DRY_72H_MM = 5.0             # ... and (c) 72 h < 5 mm -> contradiction
WEATHER_WET_POINT_MM = 10.0          # (a) >= 10 mm ...
WEATHER_WET_72H_MM = 30.0            # ... or (c) >= 30 mm -> consistent
WEATHER_TIMEOUT_S = 8.0              # one request for all 5 points
WEATHER_FORECAST_CACHE_TTL_S = 3600  # refresh cached forecast data after ~1 h when online; archive is cached forever

# Archive builder (§6.4, scripts/build_archive.py, taken over from Adithi).
# Hand-edited list of verified clips; the builder adds frame hashes and writes ARCHIVE_JSON.
# Sampling must match the prior_appearance witness: both use app/media.py with sample_fps_for(duration).
ARCHIVE_MANIFEST = DATA_DIR / "archive" / "manifest.json"
# Sampled frames are shrunk to this width before hashing (pHash only looks at 32x32 anyway).
# Changing it changes every hash: rebuild data/archive/archive.json afterwards.
FRAME_SAMPLE_MAX_WIDTH = 640
# Short clips are sampled more densely (media.sample_fps_for, used by BOTH build_archive.py and prior_appearance):
# a 2.9 s clip gives 3 frames at 1 fps (one frame decides a match) but 12 at 4 fps. Approved by Sampurna.
# Changing these changes which frames are hashed: rebuild data/archive/archive.json afterwards.
SHORT_CLIP_S = 10.0                  # clips shorter than this ...
SHORT_CLIP_FPS = 4.0                 # ... are sampled at 4 frames/s instead of PRIOR_SAMPLE_FPS

# Runtime LLM (claim extraction + explanations only; never decides truth)
LLM_PROVIDER = os.getenv("ALIBI_LLM_PROVIDER", "none").strip().lower()   # anthropic | gemini | none
LLM_MODEL = os.getenv("ALIBI_LLM_MODEL", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()


def llm_configured() -> bool:
    """True when a provider is chosen and its key is present.

    Reads module attributes at call time so tests can monkeypatch them.
    """
    if LLM_PROVIDER == "anthropic":
        return bool(ANTHROPIC_API_KEY)
    if LLM_PROVIDER == "gemini":
        return bool(GEMINI_API_KEY)
    return False


# =============================================================================
# --- Sampurna: claims (taken over from Nooman) ---
# =============================================================================

# Claims (§6.1): part-of-day windows as (start_hour, end_hour); night wraps past midnight.
PART_OF_DAY_HOURS = {
    "morning": (6, 11),
    "afternoon": (12, 17),
    "evening": (17, 21),
    "night": (20, 4),
}
GAZETTEER_PATH = BACKEND_DIR / "app" / "claims" / "gazetteer.json"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODE_CACHE_DIR = CACHE_DIR / "geocode"
GEOCODE_TIMEOUT_S = 5.0

# Runtime LLM client (app/claims/llm.py): claim extraction + explanation wording only.
LLM_TIMEOUT_S = 15.0
# BUDGET RULE: we spend no money on APIs. Default provider is "none" (rules + templates, free, offline).
# Recommended optional provider: "gemini" with a free Google AI Studio key (free-tier Flash-Lite model).
# "anthropic" is PAID: supported in code but not to be used. No model is ever called automatically
# as a fallback; on any refusal or error we fall back to rules/templates.
LLM_DEFAULT_MODEL = {"gemini": "gemini-2.5-flash-lite", "anthropic": "claude-haiku-5-5"}  # override with ALIBI_LLM_MODEL
LLM_EFFORT = "low"                   # Anthropic effort: short extraction/rewording tasks, keep latency down


# =============================================================================
# --- Nooman: lipsync + enf ---
# =============================================================================

# Lip-sync synchrony indicator (§6.5). Calibrate on >= 5 genuine phone clips + swapped-audio controls
# (scripts/calibrate_lipsync.py). ONE lag per clip from all speech frames; 3 s windows only show where sync drops.
LIPSYNC_FACE_MODEL = DATA_DIR / "models" / "face_landmarker.task"   # MediaPipe Tasks model, see data/models/README.md
LIPSYNC_MOUTH_FEATURE = "velocity"   # "aperture" | "velocity" (|d aperture / frame|). Calibrated 2026-11-19 on
                                     # 5 genuine iPhone clips + 5 audio-swapped controls: velocity corr overlapped
                                     # least (genuine min 0.115 vs swapped max 0.147); aperture overlapped more.
# FALLBACK (§6.5), calibrated 2026-11-19: no metric (corr, prominence, |lag|; either feature) separated genuine
# from swapped clips cleanly, so every lip-sync finding is capped at LOW reliability: it can only ever be a
# weak indicator, never set the signal family or the headline.
LIPSYNC_MAX_RELIABILITY = "low"
# Genuine/swapped didn't separate in calibration on 5+5 clips, so pending Sampurna's decision. While False,
# lip-sync is ALWAYS inconclusive (low) until recalibration: any rule result (contradiction OR consistent) is
# reported as inconclusive, with the raw lag/corr/prominence and the rule outcome in details.
# Recalibration 2026-11-19 (1080p close-ups, faceH 28-38%; 5 calibration + 5 held-out test clips, 5 swaps each,
# scripts/calibrate_lipsync_thresholds.py): velocity corr < 0.1485 / prominence < 0.0945 was 0 genuine / 3 of 5
# swaps on calibration but FAILED the held-out test (1 genuine contradiction). Aperture prominence < 0.12 held
# (0 genuine on both sets) but caught only 1 of 5 swaps on each. |lag| unusable (genuine reached the 0.3 s edge).
# Decision: flag stays False, reliability stays capped at low, no threshold changes.
LIPSYNC_ALLOW_CONTRADICTION = False
LIPSYNC_MIN_FACE_HEIGHT = 0.25       # STARTING VALUE: median face height (forehead-chin) / frame height below
                                     # this -> inconclusive/low "face too small for a reliable synchrony measurement"
LIPSYNC_FACE_PROBE_S = 3.0           # early exit: no face in any frame of the first 3 s -> stop decoding,
                                     # not_applicable "no face detected in the opening seconds" (live-demo speed)
LIPSYNC_MAX_SPAN_S = 12.0            # analyse only the first 12 s (video + audio); measured ~9 s on 4K IMG_5383.
                                     # A limitation is added whenever this actually cuts the clip. 0/None = whole clip
LIPSYNC_MAX_INPUTS = 2               # analyse at most 2 videos per investigation (runner limit is 90 s; ~10-35 s each)
LIPSYNC_MAX_LOCATIONS = 3            # worst windows (lowest corr at the clip lag) reported as timestamps
LIPSYNC_MIN_FACE_FRACTION = 0.50     # face detected in >= 50% of sampled frames, else not_applicable
LIPSYNC_SMOOTH_FRAMES = 3            # median filter on mouth aperture
LIPSYNC_MAX_GAP_FRAMES = 5           # interpolate face dropouts up to 5 frames; longer stay missing
LIPSYNC_ANALYSIS_FPS = 30.0          # constant analysis rate (iPhone ~29.83 fps VFR); +/-0.3 s = +/-9 frames
LIPSYNC_FRAME_WIDTH = 640            # ffmpeg scales to 640 px wide while decoding (scale=640:-2, never upscaled);
                                     # 960 and 1920 gave the same numbers, only slower
LIPSYNC_MAX_LAG_S = 0.30             # clip-level cross-correlation search range +/-
LIPSYNC_MIN_SPEECH_S = 2.0           # need >= 2 s of speech frames with a face, else insufficient_signal
# Audio: speech-band loudness in dB.
LIPSYNC_AUDIO_SR = 16000
LIPSYNC_BAND_HZ = (300.0, 3000.0)    # band-pass before RMS (drops hum, rumble and hiss)
LIPSYNC_RMS_HOP_S = 0.010            # loudness at 100 Hz, then interpolated onto video frame times
LIPSYNC_RMS_FRAME_S = 0.040
LIPSYNC_DB_FLOOR = -90.0             # dB floor for digital silence
LIPSYNC_SPEECH_FLOOR_PCT = 10        # adaptive speech mask (dB): noise floor = 10th percentile ...
LIPSYNC_SPEECH_PEAK_PCT = 95         # ... peak = 95th percentile ...
LIPSYNC_SPEECH_THRESH_FRAC = 0.50    # ... speech if dB > floor + 0.50 * (peak - floor)
LIPSYNC_SPEECH_MIN_RANGE_DB = 6.0    # peak - floor < 6 dB -> no speech anywhere (flat audio)
# Display windows.
LIPSYNC_WINDOW_S = 3.0
LIPSYNC_STEP_S = 1.0
LIPSYNC_WINDOW_MIN_SPEECH = 0.30     # a window is a "speech window" if >= 30% of its frames are speech
LIPSYNC_WINDOW_MIN_FACE = 0.80       # ... and the face was found in >= 80% of its frames
LIPSYNC_MIN_SPEECH_FRACTION = 0.50   # contradiction needs speech in >= 50% of windows
# Status thresholds on the clip-level numbers. STARTING VALUES (§6.5), prominence = best corr - median corr over lags.
LIPSYNC_CONSISTENT_MAX_LAG_S = 0.12  # |lag| <= this ...
LIPSYNC_CONSISTENT_MIN_CORR = 0.30   # ... and corr >= this ...
LIPSYNC_CONSISTENT_MIN_PROMINENCE = 0.0   # ... and prominence >= this -> consistent (placeholder until calibrated)
LIPSYNC_CONTRA_MIN_LAG_S = 0.25      # |lag| >= this ...
LIPSYNC_CONTRA_MAX_CORR = 0.10       # ... or corr < this ...
LIPSYNC_CONTRA_MAX_PROMINENCE = -1.0 # ... or prominence < this -> contradiction (placeholder: off until calibrated)

# ENF power-grid hum (§6.6). Conditional on the hour-1 go/no-go test.
ENF_GO = False                       # set True ONLY if 50/100 Hz was found in >= 3 of 5 venue clips.
                                     # Hour-1 test (scripts/test_enf.py): NO-GO, hum in 0 of 5 iPhone clips ->
                                     # ENF is display-only: always insufficient_signal (low), never a verdict
ENF_STFT_WINDOW_S = (4.0, 8.0)       # shorter end for short clips
ENF_PEAK_HALF_WIDTH_HZ = 0.5         # signal band: f +/- 0.5 Hz
ENF_NOISE_BAND_HZ = 5.0              # compared against a 5 Hz band around f
ENF_MIN_SNR_DB = 6.0
ENF_MIN_FRAME_FRACTION = 0.60        # detected if SNR >= 6 dB in >= 60% of frames
ENF_FREQS_50HZ = (50.0, 100.0)
ENF_FREQS_60HZ = (60.0, 120.0)
ENF_SHORT_CLIP_MIN_WIN_S = 2.0       # STFT window never below 2 s (0.5 Hz bins); shorter audio -> insufficient_signal
ENF_MAX_SPAN_S = 12.0                # analyse only the first 12 s of audio (speed); limitation added when it cuts
ENF_MAX_INPUTS = 2                   # analyse at most 2 audio/video inputs per investigation
# Used only if ENF_GO is True (§6.6): 50/100 Hz + claim in these countries -> consistent; 60/120 Hz + claim in a
# country NOT on a 60 Hz or mixed grid -> contradiction. ISO 3166-1 alpha-2.
ENF_CONSISTENT_COUNTRIES = ("IN",)
ENF_60HZ_COUNTRIES = ("US", "CA", "MX", "KR", "TW", "PH", "SA", "CO", "VE", "PE", "EC", "CR", "PA", "GT",
                      "HN", "NI", "SV", "DO", "CU", "PR", "BS", "BZ", "LR", "FM", "GU", "AS")
ENF_MIXED_GRID_COUNTRIES = ("JP", "BR")   # both 50 and 60 Hz in use: never a contradiction

# Prior appearance (§6.4), witness handed over from Adithi. The base settings (PRIOR_SAMPLE_FPS, PRIOR_MAX_HAMMING,
# PRIOR_MIN_MATCHED_FRAMES, PRIOR_MIN_MATCHED_FRACTION, ARCHIVE_JSON) stay in Adithi's section below.
PRIOR_SHORT_MAX_HAMMING = 4          # photos / clips with < PRIOR_MIN_MATCHED_FRAMES sampled frames: ONE frame decides,
                                     # so it must be near-identical (re-saved copies ~0-4 bits; unrelated images ~32)
PRIOR_DATE_TOLERANCE_DAYS = 2        # verified_date outside the claimed time window +/- 2 days -> disagrees
PRIOR_LOCATION_TOLERANCE_KM = 50.0   # verified lat/lon > 50 km from the claimed lat/lon -> disagrees. Entries without
                                     # coordinates: a place-name match can agree, a mismatch is never a contradiction
PRIOR_MAX_INPUTS = 2                 # check at most 2 images/videos per investigation (speed)
PRIOR_HIGH_MAX_MEDIAN_HAMMING = 1.5  # consistent/contradiction (high) only if the matched frames' median Hamming <= this
                                     # (near-identical copy); else inconclusive / medium. Measured 2026-11-19 on 10 close-up
                                     # takes (4K original archived): true copies (1080p, WhatsApp-style 848/640 px CRF 28)
                                     # median 0-1.0; OTHER takes of the same scene that pass the frame rule median 2-8
                                     # (13 pairs at 2). 1.5 = widest margin; the gap is clean but only 1 bit wide.
# Live Desk "Time Machine" (§14): matched upload/archive frame pairs saved as JPGs in artifacts/frames/.
PRIOR_TIME_MACHINE_MAX_PAIRS = 3     # Sam's decision: 3 pairs = best match from the start / middle / end
                                     # thirds of the matched frames (fewer matches: all of them)
PRIOR_TIME_MACHINE_WIDTH = 640       # px, JPG width (display only; hashing is unaffected)
PRIOR_TIME_MACHINE_JPEG_QUALITY = 80
# Live Desk "frame filmstrip" (details.frame_strip): one small JPG per sampled upload frame, display only.
PRIOR_STRIP_MAX_FRAMES = 48          # longer clips: evenly spread over the clip
PRIOR_STRIP_THUMB_WIDTH = 160        # px


# =============================================================================
# --- Adithi: metadata + weather + prior_appearance + ela ---
# =============================================================================

# Metadata integrity (§6.2)
# Capture time vs the claim's [start, end] window (calibrated on the team's genuine clips):
#   time zone known (offset in the file, or QuickTime UTC): within the window +/- this many hours
#   is consistent, further outside is a contradiction (medium).
METADATA_TIME_MARGIN_HOURS = 12
#   time zone unknown (no offset): outside the window but within this many days is inconclusive,
#   further outside is a contradiction (medium).
METADATA_MAX_DATE_DIFF_DAYS = 2
METADATA_MAX_GPS_DIST_KM = 50.0      # GPS vs claimed location
METADATA_EDITING_SOFTWARE = [
    "photoshop", "lightroom", "snapseed", "capcut", "picsart", "facetune",
    "premiere", "after effects", "final cut", "davinci", "inshot", "kinemaster",
]

# Weather moved to the Sampurna section (handed over by Adithi).

# Prior appearance (§6.4)
PRIOR_SAMPLE_FPS = 1.0
PRIOR_MAX_HAMMING = 8                # frame match if phash distance <= 8
PRIOR_MIN_MATCHED_FRAMES = 3         # clip match if >= 3 frames ...
PRIOR_MIN_MATCHED_FRACTION = 0.30    # ... or >= 30% of sampled frames
ARCHIVE_JSON = ARCHIVE_DIR / "archive.json"

# ELA moved to the Sampurna section (handed over by Adithi).


# Synthetic-generation signals (PS04 synthetic images / video / speech; Sampurna). Signal-level indicators,
# no trained classifier. Starting values, calibrated on demo/controls (genuine, must never contradict) and
# demo/ai_test/ (scripts/calibrate_synthetic.py prints the scores).
SYNTH_MAX_INPUTS = 3
SYNTH_FFT_MAX_SIDE = 1024            # centre crop (cropping keeps periodic traces, resizing would not)
SYNTH_FFT_MIN_RADIUS = 0.08          # ignore the lowest frequencies (picture content)
SYNTH_FFT_PEAK_Z = 6.0               # a peak in the noise-residual spectrum: >= 6 robust sd above the radial level
SYNTH_FFT_MIN_PEAKS = 4              # periodic grids give several symmetric peaks
SYNTH_FFT_WEAK_Z = 6.0               # strongest peak z for a weak / strong spectral signal (genuine and screenshots: <= 4.1;
SYNTH_FFT_STRONG_Z = 9.0             # a 4x nearest-neighbour upsample: 6.2)
SYNTH_NOISE_BLOCK_PX = 32
SYNTH_NOISE_FLAT_QUANTILE = 0.3      # the flattest 30% of blocks
SYNTH_NOISE_ZERO_STD = 0.5
SYNTH_NOISE_WEAK_STD = 0.9           # residual sd in flat blocks (0-255 scale). demo/ai_test: 0.38-0.87 for 4 of 5 AI images
                                     # (2.06 for the 5th); the one camera-style JPEG: 0.99. Few genuine photos: weak only.
SYNTH_NOISE_STRONG_STD = 0.3
SYNTH_MIN_STRONG_SIGNALS = 2         # contradiction only when both pixel signals are strong (and no camera fields)
SYNTH_VIDEO_FPS = 10.0
SYNTH_VIDEO_MAX_S = 60.0
SYNTH_SPIKE_MIN = 30.0               # mean abs frame difference (0-255) for an abrupt change ...
SYNTH_SPIKE_RATIO = 8.0              # ... and at least 8x the clip's median change
SYNTH_FLICKER_MIN = 4.0              # brightness step for a flicker reversal
SYNTH_FLICKER_RATE = 0.15            # share of frames with reversals
SYNTH_AUDIO_MAX_S = 60.0
SYNTH_DIGITAL_SILENCE_DBFS = -80.0   # noise floor at or below: near-digital silence
SYNTH_CUTOFF_DROP_DB = 30.0          # a hard bandwidth limit: >= 30 dB drop within 500 Hz ...
SYNTH_CUTOFF_MAX_HZ = 12500          # ... at or below 12.5 kHz (8 / 11 / 12 kHz = 16 / 22 / 24 kHz generators)
SYNTH_FLATNESS_MAX = 0.002           # median spectral flatness of the louder frames at or below: very tonal
SYNTH_AUDIO_WEAK_COUNT = 2           # 2 audio indicators = weak (one alone is common: WhatsApp cuts at ~5.5 kHz)
SYNTH_AUDIO_STRONG_COUNT = 3         # all 3 = strong (still never a contradiction)

# Splice / compositing localisation (part of synthetic_signals; indicator only, never a contradiction)
SPLICE_BLOCK_PX = 32
SPLICE_FLAT_QUANTILE = 0.7           # only the flatter 70% of blocks (edges and texture dominate the rest)
SPLICE_Z = 3.5                       # a block is inconsistent at |robust z| >= 3.5 of log residual sd
SPLICE_MIN_BLOCKS = 4                # region size: at least 4 blocks ...
SPLICE_MAX_FRACTION = 0.25           # ... and at most a quarter of the picture
SPLICE_ELA_AGREE_RATIO = 1.5         # error level in the region >= 1.5x the rest -> agrees with ELA (reliability medium)

# Speech-to-text (spoken claims): faster-whisper "tiny" multilingual, CPU int8, first 30 s of the first
# audio/video input. Weights: scripts/download_models.py -> data/models/faster-whisper-tiny (MIT licence).
WHISPER_MODEL_DIR = Path(os.getenv("ALIBI_WHISPER_DIR", DATA_DIR / "models" / "faster-whisper-tiny"))
WHISPER_MAX_S = 30.0
WHISPER_MIN_CHARS = 3                # shorter transcripts count as no speech
