# ALIBI: Explainable Multimedia Verification

> **Every piece of media has an alibi. We check whether it holds up.**
> Hackathon: SapthaHacks / InnovEdge Hackathon 2026, 24 hours, ~155 teams.
> Problem Statement 04: *TruthLens, Multimodal Digital Forensics and Deepfake Detection System*.

This file is the single source of truth for the project. If you are an AI assistant starting a new chat, read all of it before writing any code. If something here conflicts with a request, ask Sampurna (team lead) before going ahead.

---

## 1. What we are building (read this first)

Most teams will build "upload a file, run a classifier, show 87% FAKE". **We are not doing that.**

ALIBI treats each kind of evidence (caption text, metadata, weather records, earlier appearances of the same footage, audio, lip movement) as a **witness**. Each witness gives a structured statement. A **rule-based contradiction engine** cross-examines those statements against the claims the media makes ("Chennai, yesterday, flood") and produces an **explainable investigation report**: what is consistent, what contradicts, what is inconclusive, why, from which evidence, and what else could explain it.

**Core insight for the pitch:** genuine footage with a false caption (an old flood video reshared as "today") fools every deepfake detector, because the pixels are real. ALIBI catches it, because it checks the *story*, not just the pixels.

**Positioning:** "explainable multimedia verification and digital evidence intelligence platform", **not** "AI deepfake detector".

**Users:** fact-checkers, newsrooms, police cyber cells, investigators.

### The 4 problem-statement rules we must visibly satisfy
1. Analyse image, video, audio and text, with **cross-modal correlation**.
2. Flag **where** (region / frame / timestamp / segment) and **why**.
3. **Calibrated confidence + limitations + alternative explanations**, with no unsupported certainty.
4. **Preserve original evidence**, keep traceability and analysis history, and keep a **human in the loop**.

---

## 2. Non-negotiable principles (every module must obey these)

1. **The LLM never decides truth.** The LLM does exactly two jobs: (a) extract claims from caption text, (b) turn structured findings into readable sentences. All verdicts come from deterministic Python modules plus the rule engine.
2. **Absence of evidence is not evidence of fabrication.**
   - Missing EXIF → `inconclusive`, never `contradiction`.
   - No 50 Hz hum → `insufficient_signal`, never "synthetic voice".
   - Not found in our archive → `inconclusive`, never "new footage".
   - 0 mm rain → "weather evidence does not support the stated conditions", never "fake".
3. **Every claim is traceable to source text.** Each claim stores the exact character span of the caption that produced it. If the LLM returns a span that is not literally in the caption, drop the claim.
4. **Every finding lists alternative explanations and limitations.** An empty `alternatives` list is a bug.
5. **No module may crash an investigation.** On any exception, return a finding with `status: "inconclusive"`, `reliability: "low"`, and the error in `limitations`.
6. **Originals are never modified.** Store uploads read-only with a SHA-256 hash. All derived files (frames, heatmaps, spectrograms) go in a separate `artifacts/` folder.
7. **Forbidden words in UI and report:** "fake", "real", "proven", "definitely", "AI-generated" (as a verdict). Use the vocabulary in §9.
8. **FEATURE FREEZE.** The witness list in §5 is final. Do not add witnesses, classifiers or features unless Sampurna approves.

---

## 3. Team and ownership

| Person | AI tools | Owns | Files |
|---|---|---|---|
| **Sampurna** (lead) | Claude Code (Opus) in VS Code | Repo skeleton, **evidence contract**, **rule engine**, graph builder, FastAPI app, storage, report, integration, final merges, **claim extraction** + LLM client (taken over from Nooman), **weather witness**, **ELA witness** and **archive builder** (taken over from Adithi), **Live Desk** (§14), **frontend** (taken over from Dhruva after his upload page and investigation view were merged) | `frontend/*`, `backend/app/live/*` (incl. the standalone page in `backend/app/live/static/`), `backend/app/bot/*`, `backend/app/main.py`, `schemas.py`, `engine/*`, `storage.py`, `report.py`, `pipeline.py`, `media.py`, `uploads.py`, `demo.py`, `claims/*`, `shared/*`, `witnesses/weather.py`, `witnesses/ela.py`, `scripts/build_archive.py`, `scripts/warm_cache.py` |
| **Nooman** | Claude Code (Opus) in VS Code | **Lip-sync witness**, **ENF audio witness** (conditional), calibration scripts | `witnesses/lipsync.py`, `witnesses/enf.py`, `scripts/calibrate_*.py`, `scripts/test_enf.py` |
| **Adithi** | Free LLMs | **Metadata witness**, **prior-appearance witness** | `witnesses/metadata.py`, `witnesses/prior_appearance.py` |
| **Dhruva** | Free LLMs | Built the frontend (upload page, investigation view, evidence graph); **handed over to Sampurna** on 20 Nov. Message Sampurna for frontend changes. | none |

**Rule:** don't edit files you don't own. Need a change elsewhere? Message the owner. Contract changes (`schemas.py`, `shared/contract.schema.json`, `shared/fixtures/*`) are made **only by Sampurna** and announced in the group.

**For Adithi and Dhruva (free LLMs):** your LLM can't read this repo. Paste these into your chat: §2 (principles), §4 (contract), §6 (your module brief), and the relevant file. Ask it to follow the contract exactly.

---

## 4. The Evidence Contract (THE most important section)

Every witness returns a list of `Finding` objects. The engine only understands this format. The frontend only reads the `Investigation` format. If you follow this, integration just works.

Pydantic models live in `backend/app/schemas.py`. A JSON Schema mirror is in `shared/contract.schema.json`. A full example is in `shared/fixtures/sample_investigation.json` (Dhruva builds the UI against this from hour 1).

### 4.1 Enums
```
Family      = "context" | "signal" | "pixels"
Status      = "consistent" | "contradiction" | "inconclusive" | "insufficient_signal" | "not_applicable"
Reliability = "high" | "medium" | "low"
ClaimType   = "location" | "time" | "event" | "weather_condition" | "speaker" | "other"
WitnessName = "metadata" | "weather" | "prior_appearance" | "lipsync" | "enf" | "ela" | "synthetic_signals"
```

Witness → family (fixed):
| Witness | Family |
|---|---|
| metadata, weather, prior_appearance | context |
| lipsync, enf | signal |
| ela, synthetic_signals | pixels |

### 4.2 Claim
```json
{
  "id": "c1",
  "type": "location",
  "value": "Chennai",
  "normalized": { "lat": 13.0827, "lon": 80.2707, "country": "IN" },
  "source": { "field": "caption", "start": 32, "end": 39, "text": "Chennai" },
  "extracted_by": "llm"
}
```
- `normalized` for `time`: `{ "date": "2026-11-18", "start": "...ISO...", "end": "...ISO...", "precision": "day" }`. Relative dates ("yesterday") resolve against `posted_at`. `start`/`end` must include a timezone offset.
  - `precision` is one of `hour | part_of_day | day | week | month | year`. Use `part_of_day` for "evening" / "morning" / "night" windows (e.g. "yesterday evening" → `start` 17:00, `end` 21:00, `precision: "part_of_day"`).
- `normalized` for `weather_condition`/`event`: `{ "condition": "flood" }` from the set `flood | heavy_rain | rain | dry | storm | other`.
- `extracted_by`: `"llm" | "rules"`.

### 4.3 Finding
```json
{
  "id": "f_weather_1",
  "witness": "weather",
  "family": "context",
  "claim_ids": ["c1", "c2", "c3"],
  "status": "contradiction",
  "reliability": "medium",
  "title": "Recorded rainfall does not support the stated flooding",
  "summary": "0.0 mm at the claimed location on 18 Nov; 0.0–0.4 mm at 4 surrounding points; 1.2 mm over the preceding 72 h.",
  "details": { "...module-specific numbers..." },
  "evidence": [
    { "kind": "api", "ref": "https://api.open-meteo.com/v1/forecast?...", "label": "Open-Meteo hourly precipitation" },
    { "kind": "artifact", "ref": "artifacts/weather_grid.json", "label": "Grid readings" }
  ],
  "locations": [
    { "kind": "timestamp", "file_id": "in_1", "start_s": 3.2, "end_s": 5.0 },
    { "kind": "region", "file_id": "in_1", "frame_s": 4.0, "bbox": [0.31, 0.22, 0.18, 0.12] }
  ],
  "alternatives": [
    "Rain upstream or in the catchment may not show up in a small local grid",
    "Flooding can come from drainage failure or reservoir release"
  ],
  "limitations": ["Reanalysis grid resolution is about 10 km"],
  "elapsed_ms": 812
}
```
- `bbox` is normalized `[x, y, w, h]` in 0–1.
- `evidence.kind`: `"api" | "artifact" | "metadata_field" | "archive_match" | "input"`.
- `locations` may be empty for whole-file findings.

### 4.4 Investigation (what the API returns and the frontend renders)
```json
{
  "id": "inv_20261119_101500_ab12",
  "created_at": "ISO",
  "posted_at": "ISO (user-supplied 'when was this shared', default now)",
  "caption": "Massive flooding in Chennai yesterday evening, roads completely underwater",
  "inputs": [
    { "file_id": "in_1", "name": "clip.mp4", "mime": "video/mp4", "size": 4821000, "sha256": "…", "duration_s": 14.2, "stored_path": "originals/in_1_clip.mp4" }
  ],
  "claims": [ "...Claim[]..." ],
  "findings": [ "...Finding[]..." ],
  "assessment": {
    "headline": "context_not_supported",
    "headline_text": "The stated context is not supported by the available evidence",
    "counts": { "contradiction": 2, "consistent": 1, "inconclusive": 2, "insufficient_signal": 1 },
    "families": { "context": "contradiction", "signal": "inconclusive", "pixels": "not_applicable" },
    "weak_indicators": ["f_ela_1"],
    "human_review": "priority",
    "experimental_score": { "value": 38, "label": "Experimental consistency index (not calibrated, not a probability)" }
  },
  "graph": { "nodes": [], "edges": [] },
  "history": [
    { "ts": "ISO", "event": "file_received", "detail": "clip.mp4 sha256=…" },
    { "ts": "ISO", "event": "witness_completed", "detail": "weather: contradiction (812 ms)" }
  ]
}
```

### 4.5 Graph format (for React Flow)
- Node: `{ "id": "c1", "type": "claim" | "witness" | "input", "label": "Chennai", "status": "consistent|contradiction|…|null", "meta": {} }`
- Edge: `{ "id": "e1", "source": "f_weather_1", "target": "c1", "relation": "supports" | "contradicts" | "inconclusive" | "derived_from" }`
- Inputs → claims use `derived_from`. Findings → claims use the relation that matches the finding status.

### 4.6 Input fields and validation rules (enforced by `schemas.py`)
- `inputs[].stored_path`: path of the read-only original, relative to the run folder. The frontend plays/shows files from `/api/files/{inv_id}/{stored_path}`. `duration_s` is `null` for images.
- Unknown fields are rejected everywhere except inside `details`, graph `meta`, and location `normalized`.
- `alternatives` and `limitations` must be non-empty.
- `family` must match the witness (§4.1), and `reliability` may not exceed the witness's max in §5.
- Each claim's `source.text` must equal `caption[start:end]`.
- `claim_ids`, location `file_id`s, `weak_indicators` and graph edges must point at things that exist, and `assessment.counts` must match the findings.
- After changing `schemas.py`, regenerate the JSON Schema: `cd backend; python -m app.schemas`. `pytest` fails if it is stale.

- `parent_id` (Investigation and InvestigationSummary): `null`, or on a caption re-run (§14) the id of the investigation whose stored files were re-used.
- `history` stays `{ts, event, detail}`. The run's `history.jsonl` additionally stores a `data` payload per line for the live event stream (§14).

### 4.7 InvestigationSummary (what `GET /api/investigations` returns, for the history page)
A list, newest first. Fetch the full `Investigation` by `id` for anything else. In `contract.schema.json` it is under `$defs/InvestigationSummary`.
```json
[
  {
    "id": "inv_20261119_101500_ab12",
    "created_at": "ISO",
    "caption": "Massive flooding in Chennai yesterday evening, roads completely underwater",
    "inputs": ["chennai_flood.mp4"],
    "headline": "context_not_supported",
    "headline_text": "The stated context is not supported by the available evidence",
    "counts": { "consistent": 0, "contradiction": 2, "inconclusive": 2, "insufficient_signal": 1, "not_applicable": 1 },
    "human_review": "priority"
  }
]
```
- `inputs` is the list of input file **names** (not full `InputFile` objects).

---

## 5. Final witness list (FROZEN)

| # | Witness | Owner | Status | Max reliability |
|---|---|---|---|---|
| 1 | Claim extraction (not a witness, it feeds everything) | Sampurna (from Nooman) | Done | n/a |
| 2 | Metadata integrity | Adithi | **Must** | medium |
| 3 | Weather (grid + 72 h) | Sampurna (from Adithi) | Done | medium |
| 4 | Prior appearance (perceptual-hash archive) | Adithi | **Must** | high |
| 5 | Lip-sync synchrony indicator | Nooman | **Must** | medium |
| 6 | ENF power-grid hum | Nooman | **Conditional** on the hour-1 test | medium |
| 7 | ELA heatmap | Sampurna (from Adithi) | Done | low (always) |
| 8 | Synthetic signals (PS04 synthetic image / video / speech indicators; approved by Sampurna, §6.8) | Sampurna | Done | medium |
| — | Solar/shadow analysis | — | **Roadmap slide only** | — |
| — | Extra deepfake classifiers | — | **Skip** | — |

---

## 6. Module briefs

All witnesses implement the interface in `backend/app/witnesses/base.py`:

```python
class Witness(ABC):
    name: WitnessName
    family: Family
    def applicable(self, ctx: InvestigationContext) -> bool: ...
    def run(self, ctx: InvestigationContext) -> list[Finding]: ...
```

`InvestigationContext` gives you: `inputs` (paths + metadata), `claims`, `caption`, `posted_at`, `artifacts_dir`, and a helper `ctx.claims_of(type)`. The runner wraps `run()` in try/except plus a timeout (30 s default, 90 s for lipsync) and turns failures into an inconclusive finding. **Still, never let exceptions escape on purpose.**

Every module ships with a `__main__` block so it can run alone:
`python -m app.witnesses.weather --caption "..." --posted-at 2026-11-19` prints the findings JSON.

All thresholds below are **starting values. Calibrate them on genuine clips** and record the final values in `backend/app/config.py`.

### 6.1 Claim extraction (Sampurna, taken over from Nooman; done): `app/claims/extract.py`
- **Done.** Rules path is fully offline: `gazetteer.json` (~250 places with coordinates and alternate spellings) means no geocoding call for known places. Also handles afternoon (12:00–17:00), "N days ago", "N hours ago" (precision `hour`), weekdays, "last week" (`week`), "Month YYYY" (`month`), "in YYYY" (`year`), and simple speaker patterns. The same place/condition/time mentioned twice is kept once (first mention). Times use `posted_at`'s timezone, IST if none.
- LLM claims are added only where the rules found nothing; overlapping spans keep the rules version.
- **Budget rule: we spend no money on APIs.**
  - Default `ALIBI_LLM_PROVIDER=none` (rules + templates, free, offline). **The demo runs with `none`.**
  - Recommended optional provider: `gemini` with a free Google AI Studio key from a project without billing (setup steps in README). Default model `gemini-2.5-flash-lite` (free tier), override with `ALIBI_LLM_MODEL`.
  - `anthropic` is **paid: do not use**. The code still supports it, but it is never the default and never called automatically.
  - One call to the configured model, 15 s timeout, no retries, **no fallback models**. On any refusal, error or rate limit: rules only.
  - LLM rewording of findings (§7.6) is off by default (`ALIBI_LLM_EXPLAIN=0`) to save free-tier quota.
- Input: caption, posted_at. Output: `list[Claim]`.
- **LLM path:** provider-agnostic client in `app/claims/llm.py`, chosen by env vars `ALIBI_LLM_PROVIDER` (`anthropic` | `gemini` | `none`), `ALIBI_LLM_MODEL`, and the API key. Ask for strict JSON: claims with `type`, `value`, `source_text`. **Compute `start/end` yourself** with `caption.find(source_text)` and drop the claim if it isn't found.
- **Rules fallback (must work offline, used when provider = none or the LLM fails):**
  - Locations: gazetteer of ~200 Indian cities plus major world cities (`app/claims/gazetteer.json`), case-insensitive whole-word match.
  - Times: `dateparser` with `RELATIVE_BASE=posted_at` for "yesterday", "last night", "today", explicit dates. Map "evening" → 17:00–21:00, "morning" → 06:00–11:00, "night" → 20:00–04:00.
  - Conditions: keyword map (flood/flooding/waterlogged/underwater → flood; heavy rain/downpour → heavy_rain; …).
- Geocoding: Open-Meteo geocoding `https://geocoding-api.open-meteo.com/v1/search?name=Chennai&count=1`, with results cached to disk.
- **The demo must never depend on the LLM.** Test the demo captions with `provider=none`.

### 6.2 Metadata integrity (Adithi): `witnesses/metadata.py`
- Tools: `exiftool -json -G` (subprocess) for images and video; `ffprobe -v quiet -print_format json -show_format -show_streams` for video/audio.
- Report: fields present/missing (CreateDate, GPS, Make/Model, Software, encoder), editing-software tags (Photoshop, Snapseed, CapCut, etc.), re-encode hints (WhatsApp-style names, missing camera fields, `encoder=Lavf`).
- Rules:
  - **Capture time** (done; finished by Sampurna with Adithi's agreement, Adithi stays the owner). It's compared as a full datetime against the claim's `[start, end]` (week/month claims use the whole window):
    - Source, best first: `Keys:CreationDate` (local time + offset) → image `DateTimeOriginal` + `OffsetTimeOriginal` → `QuickTime:CreateDate` (UTC by the QuickTime spec) → ffprobe `creation_time` (UTC) → any date without an offset.
    - Time zone known: inside the window ±`METADATA_TIME_MARGIN_HOURS` (12) → `consistent` (low); further outside → `contradiction` (medium).
    - Time zone unknown: inside the window → `consistent` (low); outside but within `METADATA_MAX_DATE_DIFF_DAYS` (2) → `inconclusive`; further → `contradiction` (medium).
    - `details.files[]` has each file's `date_status` / `gps_status`, `capture_time`, `capture_source`, `fields_present`, `fields_missing`, `make`, `model`, `reencode_hints`. exiftool runs as `-n -G1` (signed GPS, Composite tags preferred); `"exiftool not found"` is a limitation whenever it's missing.
  - GPS present **and** > 50 km from the claimed location → `contradiction` (medium).
  - Present and matching → `consistent` (low; metadata is easy to edit).
  - Missing → `inconclusive` with alternative "metadata is commonly stripped by messaging apps and social platforms".
  - Editing software found → separate finding, `inconclusive` (low), title "Editing software tag present".
- Always list: "Metadata can be edited or removed; it is supporting evidence, not ground truth."

### 6.3 Weather (Sampurna, taken over from Adithi; done): `witnesses/weather.py`
- **Done**, with these refinements of the brief below:
  - **Only rain that had already fallen counts.** Hours after `min(now, posted_at)` are never used, and forecast values are never evidence. A claimed window that runs past that point is incomplete, so the result is `inconclusive`.
  - **One request for all 5 points**, with times in UTC and an 8 s timeout. Recent claims use the forecast API with `past_days` sized to the claim (up to 92); older claims use the archive API.
  - **The three numbers:** (a) centre rain in the claimed window, (b) max of the 4 surrounding points over the claimed day(s), (c) centre rain over the 72 h up to the window end. Hourly values cover the preceding hour, so the count is `start < t ≤ end`.
  - **Cache:** `data/cache/openmeteo_*.json`, keyed by place and dates, so a warmed cache works on a later day. Archive data is kept forever. Forecast data is refreshed after about 1 h when online, and the older copy is used when offline.
  - **Output for the frontend:** `details` has `centre {lat, lon, window_mm, day_mm}`, `grid [{label, lat, lon, day_mm, window_mm}]`, `grid_max_mm`, `prior_72h_mm`, `hourly_centre [{t, mm}]`, `coverage`, `thresholds` and `api`. Evidence is the Open-Meteo request URL plus `artifacts/weather_grid.json`.
  - **Dry, storm and other claims:** the numbers are shown for reference only, always `inconclusive`.
  - **Tests:** `tests/test_weather.py` replays recorded responses from `tests/fixtures/weather/` offline. Re-record them with `python tests/fixtures/weather/record.py`.
- Needs location + time claims, plus a weather/event claim (flood, rain, storm, dry). Otherwise `not_applicable`.
- **API choice matters:**
  - Claimed date within the last ~90 days → **forecast API with `past_days`**: `https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&hourly=precipitation&daily=precipitation_sum&past_days=7&timezone=auto`
  - Older → **archive API**: `https://archive-api.open-meteo.com/v1/archive?...&start_date=..&end_date=..`. The archive (ERA5) lags by about 5 days, so never use it for recent dates.
- Query **5 points**: the centre plus N/S/E/W at ±0.15° (~15 km).
- Report **three separate numbers**: (a) rain at the claimed coordinates on the claimed day/window, (b) max rain across the surrounding points, (c) cumulative rain at the centre over the preceding 72 h.
- Rules for flood/heavy_rain/rain claims:
  - (a) < 1 mm **and** (b) < 1 mm **and** (c) < 5 mm → `contradiction` (medium).
  - (a) ≥ 10 mm or (c) ≥ 30 mm → `consistent` (medium).
  - Anything in between → `inconclusive`.
- Always list as alternatives: upstream/catchment rain, drainage failure, reservoir release, and localised rain below grid resolution.
- Cache responses to `data/cache/` so the demo works offline once warmed up.

### 6.4 Prior appearance (witness: Adithi, `witnesses/prior_appearance.py`; archive builder: Sampurna, `scripts/build_archive.py`)
- Archive: `data/archive/archive.json` plus the source clips. Each entry: `{ "clip_id", "title", "verified_date", "verified_location", "verified_by", "source_note", "frame_hashes": [hex...] }`.
- `build_archive.py` (done): reads the hand-edited `data/archive/manifest.json` (format in the script's docstring; clip paths are relative to `data/archive/`), samples 1 frame/s, computes `imagehash.phash` (64-bit, 16 hex chars), and writes `archive.json` as `{ "built_at", "sample_fps", "hash", "clips": [entry, ...] }`. Each entry also has `frame_times_s` (same order as `frame_hashes`), `sha256`, `duration_s`, `sample_fps`, and optional `verified_lat`/`verified_lon` for distance checks. Run: `backend\.venv\Scripts\python.exe scripts\build_archive.py`.
- **Use `app/media.py` in the witness:** `sample_frames(path, config.PRIOR_SAMPLE_FPS)` → `[(t, image)]`, `phash_hex(image)`, `hamming(a, b)`. The archive was built with the same functions, so hashes are comparable.
  - Frames come back shrunk to `config.FRAME_SAMPLE_MAX_WIDTH` (640 px). Call `sample_frames` with its defaults, don't pass your own `max_width`.
  - `archive.json` records the width as `frame_max_width`; if you change it, rebuild the archive.
  - A test proves the archive and an uploaded copy hash identically.
- Witness: sample the uploaded video the same way (images = 1 frame). A frame matches when Hamming distance ≤ 8. A clip matches when ≥ 3 frames, or ≥ 30% of sampled frames, match the same archive entry.
- Rules:
  - Match **and** the verified date or location disagrees with the claim → `contradiction` (**high**). Title: "Frames match a clip verified as recorded in {place} on {date}".
  - Match and agrees → `consistent` (high).
  - No match → `inconclusive`: "Not found in our archive of N verified clips. This does not mean the footage is new."
- `locations`: the matched timestamps in the upload.
- Limitations: "Archive is small (N clips). In production this connects to reverse-image search and fact-checker databases."

### 6.5 Lip-sync synchrony indicator (Nooman): `witnesses/lipsync.py`
- Applicable: video with an audio track and a detectable face in ≥ 50% of sampled frames. Otherwise `not_applicable`.
- Visual: MediaPipe face landmarks per frame → mouth aperture = inner-lip vertical distance ÷ face height. Smooth it (median, 3 frames).
- Audio: extract with ffmpeg → `librosa` RMS envelope resampled to the video fps. Speech mask = RMS above an adaptive threshold.
- Windows of 3 s, step 1 s. In each speech window, normalized cross-correlation over lags ±0.5 s → best lag + peak correlation.
- Rules (calibrate on ≥ 5 genuine clips from the team's phones first):
  - Median |lag| ≤ 0.12 s **and** median corr ≥ 0.30 → `consistent` (medium).
  - Median |lag| ≥ 0.25 s **or** median corr < 0.10, with speech in ≥ 50% of windows → `contradiction` (medium). Flag the worst windows as `locations` timestamps.
  - Otherwise `inconclusive`.
- **If genuine clips trip a contradiction during calibration, raise the thresholds or cap reliability at low. Never force a contradiction.**
- Artifact: `artifacts/lipsync_series.json` with `{ t[], mouth[], audio[], window_lags[], window_corrs[] }` for the frontend chart.
- Always call it a "synchrony indicator". Alternatives: dubbing/translation, Bluetooth audio delay, a low frame rate, the speaker being off-screen.
- Optional upgrade after everything else works: pretrained SyncNet. It's an upgrade, never a dependency.

### 6.6 ENF power-grid hum (Nooman, conditional): `witnesses/enf.py`
- **Hour-1 go/no-go test:** record 5 clips on team phones indoors in the venue near lights and laptops. Run the detector. If 50/100 Hz isn't detected in at least 3 of 5, ENF ships as a display-only spectrogram with `insufficient_signal` and never contributes a contradiction.
- Method: load audio at its native sample rate. STFT with a 4–8 s window (shorter for short clips). For each of 50, 100, 60, 120 Hz: peak SNR = power in ±0.5 Hz vs a 5 Hz band around it.
- Detected if SNR ≥ 6 dB for ≥ 60% of frames.
- Rules: 50/100 detected, location claim in India → `consistent` (medium). 60/120 detected, claim in a 50 Hz country → `contradiction` (medium). Nothing → `insufficient_signal`. Never more than that.
- Artifact: spectrogram PNG zoomed to 40–130 Hz with the hum line marked.
- Alternatives: phone noise suppression removes hum; outdoor or battery-only environments have none; imported equipment.

### 6.7 ELA (Sampurna, taken over from Adithi; done): `witnesses/ela.py`
- Images, or a video keyframe. Re-save as JPEG q=90, take the absolute difference ×15 → heatmap PNG artifact.
- **Always `inconclusive`, reliability `low`**, listed under `weak_indicators`. ELA is a visual aid, not a verdict.

---

### 6.8 Synthetic signals (Sampurna; added after the freeze with team-lead approval): `witnesses/synthetic_signals.py`
- Images: noise-residual spectrum peaks (up-sampling grids), missing sensor noise in flat blocks, camera signature (JPEG tables, make/model/lens; context only). Contradiction (medium) only when both pixel signals are strong and no camera fields exist; any pixel signal -> inconclusive (low) "weak synthetic signal"; none -> consistent (low).
- Video: frame-change spikes / flicker -> timestamp locations; never a contradiction. Audio (files and video soundtracks): digital-silence floor, hard bandwidth limit, spectral flatness; one indicator alone is not counted; never a contradiction.
- Artifacts: spectrum, noise map, frame-change graph (+ JSON), audio spectrum. Badge: marker > AI SIGNALS: STRONG > EDITED > AI SIGNALS: WEAK > AI: NOT DETECTED (`app/engine/summary.py` `ai_check`). Calibration: `scripts/calibrate_synthetic.py`; thresholds `SYNTH_*` in `config.py`. Re-used on caption re-runs.

## 7. Rule engine (Sampurna): `app/engine/rules.py`

Transparent, deterministic, explainable. **No averaging.**

1. **Per-finding:** use as-is.
2. **Per-family status:**
   - Any finding in the family with `contradiction` and reliability ≥ medium → family = `contradiction`.
   - Else any `consistent` → `consistent`.
   - Else any `inconclusive`/`insufficient_signal` → `inconclusive`.
   - Else `not_applicable`.
   - Low-reliability contradictions go to `weak_indicators` and never set a family status.
   - Several contradictions in one family count **once** for the headline (they aren't independent), but every finding is still shown on its own.
   - A family whose only finding is a low-reliability contradiction is `inconclusive` (a witness spoke, but too weakly to count), never `contradiction`.
   - `weak_indicators` = all low-reliability contradictions **plus** every ELA finding (§6.7).
3. **Headline:**
   - `context_not_supported` / `signals_inconsistent`: ≥ 1 high-reliability contradiction, **or** contradictions in ≥ 2 families. Pick the text by which family contradicts (context → "The stated context is not supported by the available evidence"; signal → "Audio-visual signals show inconsistencies").
   - `partially_supported`: exactly 1 family with a medium contradiction and nothing high. Text by family: context → "Part of the available evidence does not support the stated context"; signal → "One audio-visual signal shows a possible inconsistency".
   - `consistent_so_far`: ≥ 2 families consistent and no contradictions (not even low ones). Text: "No contradictions found in the available evidence". Never "authentic".
   - `insufficient_evidence`: everything else. Text: "Not enough evidence to support or contradict the stated context".
   - All texts live in `config.py` (`HEADLINE_TEXT`, `PARTIAL_HEADLINE_TEXT`).
4. **human_review:** always present. `priority` if there's any contradiction, else `standard`. The UI always shows "Human review required. This is an evidence report, not a verdict."
5. **experimental_score** (optional, small, never the headline): start at 100, −30 per high contradiction, −15 per medium, −5 per low, −3 per inconclusive, floor 0. `insufficient_signal` and `not_applicable` cost 0 (absence of evidence is not evidence). Always shown with its label.
6. **Explanations:** after the rules run, the LLM may rewrite `title`/`summary` into plain English, **given only the finding JSON**, and it must not change status or numbers. If the LLM is off, use template strings. (`app/engine/explain.py`: a rewrite is rejected unless it keeps exactly the same numbers and has no forbidden words. Witnesses may omit `title`/`summary` in `self.finding(...)` to get the template.)

`app/engine/graph.py` builds nodes and edges from claims and findings (§4.5).

---

## 8. Architecture and stack

```
alibi/
├── CLAUDE.md
├── README.md                    # setup + run commands
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py              # FastAPI app + routes
│   │   ├── schemas.py           # THE CONTRACT (pydantic)
│   │   ├── config.py            # thresholds, paths, env
│   │   ├── storage.py           # runs dir, sha256, read-only originals, history log
│   │   ├── runner.py            # runs witnesses with try/except + timeout
│   │   ├── report.py            # printable HTML report
│   │   ├── claims/{extract.py, llm.py, gazetteer.json}
│   │   ├── engine/{rules.py, graph.py, explain.py}
│   │   └── witnesses/{base.py, metadata.py, weather.py, prior_appearance.py, lipsync.py, enf.py, ela.py}
│   └── tests/                   # pytest, one file per module, uses tests/fixtures/
├── frontend/                    # Next.js (App Router) + TypeScript + Tailwind
├── shared/
│   ├── contract.schema.json     # generated from schemas.py
│   └── fixtures/sample_investigation.json
├── scripts/{build_archive.py, calibrate_lipsync.py, test_enf.py, warm_cache.py}
├── data/
│   ├── archive/                 # verified clips + archive.json (committed, small)
│   ├── cache/                   # API cache (gitignored, warm before demo)
│   └── runs/                    # investigations (gitignored)
└── demo/                        # case_a/, case_b/, controls/ + captions.md
```

**Backend:** Python 3.12 (`py -3.12` on Windows), FastAPI, uvicorn, pydantic v2, opencv-python, imagehash, Pillow, librosa, soundfile, scipy, numpy, mediapipe, requests, dateparser, python-multipart. System tools: `ffmpeg`, `exiftool`.

**Frontend:** Next.js + TypeScript + Tailwind, `@xyflow/react` (graph), `recharts` (lip-sync and weather charts), `wavesurfer.js` (audio + spectrogram), `react-leaflet` (weather grid map), `framer-motion` (staggered reveal).

### API
| Method | Route | Purpose |
|---|---|---|
| POST | `/api/investigations` | multipart: one `files` part per file (`files[]` is accepted too), `caption`, `posted_at` → runs synchronously, returns `Investigation` |
| GET | `/api/investigations/{id}` | fetch a saved investigation |
| GET | `/api/fixture` | the Case A sample investigation (`shared/fixtures/sample_investigation.json`) for frontend work |
| GET | `/api/demo` | preset cases from `demo/captions.md` (for the Case A / Case B buttons) |
| POST | `/api/demo/{case_name}` | run a preset case (its files, caption, posted_at) with no file picking → `Investigation` |
| POST | `/api/investigations?async=true` | same upload, returns **202** `{id, status, events, investigation}` at once and analyses in the background (§14) |
| GET | `/api/investigations/{id}/events` | server-sent events: the real pipeline events of that run, in order (§14) |
| POST | `/api/investigations/{id}/rerun` | JSON `{caption, posted_at}` → new `Investigation` on the same files, `parent_id` = id (also `?async=true`) |
| GET | `/api/live/feed` | server-sent events announcing every new investigation (§14) |
| GET | `/api/investigations` | list past runs (history page) |
| GET | `/api/files/{inv_id}/{path}` | serve originals (read-only) and artifacts |
| GET | `/api/investigations/{id}/report` | printable HTML report (browser → Save as PDF) |
| GET | `/api/investigations/{id}/summary` | the plain-language evidence summary `{sentence, counts, witnesses}` (`app/engine/summary.py`); the same text the report prints and the Live Desk shows |
| GET | `/api/health` | witness availability (ffmpeg/exiftool found? LLM configured?) |

**Frontend: demo buttons.** `GET /api/demo` returns `[{ "name", "caption", "posted_at", "files", "expect", "ready", "problem" }]`, e.g. `{"name": "case_a", ..., "ready": false, "problem": "The demo file 'demo/case_a/clip.mp4' does not exist yet. ..."}`. Enable a button only when `ready` is true, and show `problem` as its tooltip otherwise. Clicking runs `POST /api/demo/case_a` (no body) and returns a normal `Investigation`, so navigate to `/investigation/?id={id}` as after an upload. A case that isn't ready returns **404** with the reason in `detail`.

**Upload rules** (`POST /api/investigations`; errors come back as `{"detail": "<friendly message>"}`, so show `detail` to the user):
- At most **5 files**, each at most **200 MB**, none empty → otherwise **422** (count / empty) or **413** (too large).
- Only images, video and audio, checked from the file's actual bytes, not its extension → otherwise **415**. Supported: JPEG, PNG, WebP, GIF, BMP, TIFF, HEIC/AVIF, MP4/MOV/3GP, WebM/MKV, AVI, MP3, WAV, M4A/AAC, OGG/Opus, FLAC, AMR. The stored `inputs[].mime` is the detected type.
- If any file is rejected, nothing is stored. A file that passes but is corrupt still returns **200**, with inconclusive findings that explain the error.
- A bad "Shared on" date, or no file and no caption → **422**.

Synchronous is fine for the demo (target < 20 s for a 15 s clip). The frontend **reveals findings one by one** with a staggered animation for the demo effect.

### Deployment (Vercel + laptop tunnel; current)
- **Frontend:** Vercel, root directory `frontend`, static export; `NEXT_PUBLIC_API_URL` = the laptop's Cloudflare quick-tunnel URL, fixed at build time (new tunnel URL -> update the variable and redeploy). When the API is unreachable the pages show the calm offline banner (`frontend/app/OfflineBanner.tsx`), never raw errors.
- **Backend:** `scripts\serve_prod.ps1 [-Tunnel]`: uvicorn on 127.0.0.1:8000, one worker, no reload; `-Tunnel` starts `cloudflared tunnel --url http://localhost:8000`.
- **CORS:** `config.CORS_ORIGIN_REGEX` allows `https://*.vercel.app` and `https://*.trycloudflare.com`; `ALIBI_CORS_ORIGINS` adds exact origins (`tests/test_cors.py`).
- Hugging Face free Docker Spaces are no longer available, so the one-container setup below is kept for any Docker host only.

### Deployment (one container)
- `Dockerfile` (repo root): Node stage builds the frontend as a static export with `NEXT_PUBLIC_API_URL=""` (same-origin), then `python:3.12-slim` + ffmpeg + exiftool runs `deploy/start.sh`: uvicorn, **one worker, no reload**, port 7860. The API serves `frontend/out` at `/` after every API route (`config.FRONTEND_DIR`).
- Baked in: `data/archive`, the face model, demo Cases A/B, the warmed weather cache (`deploy/seed-cache/` -> `data/cache/`). `ALIBI_LLM_PROVIDER=none`. Never secrets (`.dockerignore` drops `.env`, runs and the bot's chat list).
- The Telegram bot runs in the same container only when `TELEGRAM_BOT_TOKEN` is set (HF Space secret).
- Hugging Face Space: `deploy/huggingface/README.md` (Space config) + the Dockerfile generated by `deploy/huggingface/make_space_dockerfile.py --commit <sha>` (fetches this repo from GitHub at that commit). `tests/test_deploy.py` keeps it in sync.

### Run commands
Windows / PowerShell (see README.md for full setup). macOS/Linux: use `python3.12 -m venv .venv` and `source .venv/bin/activate`.
```powershell
# backend
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
pytest -q

# frontend
cd frontend && npm install && npm run dev   # http://localhost:3000
# NEXT_PUBLIC_API_URL=http://localhost:8000
```

### Environment (`backend/.env`, never commit)
```
ALIBI_LLM_PROVIDER=none        # none (default, demo) | gemini (free AI Studio key) | anthropic (paid, do not use)
ALIBI_LLM_MODEL=               # empty = gemini-2.5-flash-lite
GEMINI_API_KEY=
ALIBI_LLM_EXPLAIN=0
ANTHROPIC_API_KEY=             # paid, do not use
```
Budget rule: no money is spent on APIs (§6.1). A Claude Code subscription is not an API key; the app's runtime LLM is optional, and without one it uses rules.

---

## 9. UI and language guide (Dhruva + everyone)

### Screens
1. **Upload** (`/`): drag-drop files (image/video/audio), caption textarea, "Shared on" date (default today), big "Investigate" button. Also show two demo-sample buttons (Case A, Case B) that load the preset files.
2. **Investigation** (`/investigation/?id=inv_…`; the frontend is a static export served by the API, so the id is a query parameter):
   - **Header:** headline text, counts as pills (`2 contradictions · 1 consistent · 3 inconclusive`), the human-review banner, and a small experimental score with its label.
   - **Caption with highlighted claim spans:** hover a claim to light up its graph node and linked findings.
   - **Contradiction graph:** inputs → claims ← witnesses. Edge colours: green supports, red contradicts, grey inconclusive. Nodes appear one by one.
   - **Witness cards:** one per finding with status chip, reliability, title, summary, "Why?" expand (details, evidence links, alternatives, limitations), and "Where?" (jump to timestamp / show region).
   - **Evidence drawer:** video player with flagged timestamps on the scrubber, lip-sync chart, weather map with 5 points and mm values, archive match side-by-side frames, ENF spectrogram.
   - **History tab:** chain-of-custody log with SHA-256 values.
   - **Export:** "Open report" → printable page.
3. **History** (`/history`): past investigations.

Build everything against `shared/fixtures/sample_investigation.json` first (also served at `GET /api/fixture`). Switch to the live API when the backend is ready. The live pipeline is wired: `POST /api/investigations` returns a real `Investigation`; until the witnesses are filled in, their findings are placeholders.

### Status vocabulary (use exactly these)
| Status | Label in UI | Colour |
|---|---|---|
| consistent | Consistent | green |
| contradiction | Contradiction | red |
| inconclusive | Inconclusive | grey |
| insufficient_signal | Insufficient signal | grey (dashed) |
| not_applicable | Not applicable | hidden or muted |

Say **"does not support"**, **"contradicts the stated…"**, **"no contradictions found"**, **"evidence indicator"**, **"requires human review"**.
Never say **fake / real / authentic / proven / definitely / AI-generated**.

Visual style: dark investigation-room theme, monospace for numbers and hashes, clean cards, one accent colour per status. It should feel like a forensic tool, not a toy.

### ALIBI design system (shared by the Live Desk and the frontend)
The look is a premium forensic case file, not a generic dark dashboard: calm, editorial, never alarming. The Live Desk (`backend/app/live/static/live.css`, `:root`) is the reference implementation.

| Token | Value | Use |
|---|---|---|
| `bg` | `#0D1012` | page background |
| `surface` | `#15191D` | cards, panels |
| `elevated` | `#1C2227` | hover, selected, overlays |
| `border` | `#303840` | 1px, subtle |
| `text` | `#E8ECEF` | primary text |
| `text-secondary` | `#929DA7` | labels, metadata |
| `contradiction` | `#D96B68` | contradiction (coral red) |
| `supports` | `#69B88A` | consistent / supports (green) |
| `inconclusive` | `#929CAA` | inconclusive; insufficient signal is the same grey, dashed |
| `accent` | `#83B6D1` | selection, links, re-run |

- **Shape and space:** 12px radius everywhere, an 8px spacing scale, 1px borders. Almost no shadows, and no glows or gradients.
- **Type:** Inter for all text. IBM Plex Mono **only** for IDs, timestamps, hashes and numbers. Both are vendored locally, with no font CDN.
- **Status:** a small coloured badge with an icon (Lucide: `triangle-alert`, `circle-check`, `circle-help`, `circle-minus`). Cards keep thin neutral borders; only the selected card is highlighted. Inconclusive must never look disabled.
- **Text:** never truncate a title. Summaries may be previewed (2 lines) as long as the full text is one click away.
- **Headline:** a small uppercase "Evidence summary" label, the headline in primary text with a thin 3px bar in its status colour, and one plain sentence from a template (no LLM). Counts are plain numbers ("01 contradiction · 01 consistent · 04 inconclusive/other").
- **Human-review banner:** always visible, but calm: thin border, regular weight.

---

## 10. Demo plan

### Assets (`demo/`)
- **Case A: genuine footage, false caption (the killer moment).** A teammate's own older rain/flood video from their phone gallery with a known date and place. They shot it, so it's genuinely verified and we own it. Add the original to the archive with its true date/place. Forward it through WhatsApp to strip metadata (realistic). Caption: "Massive flooding in Chennai yesterday evening" for a date when Chennai was dry (check with the weather module first!).
  - Expected: prior appearance → **contradiction (high)**; weather → **contradiction (medium)**; metadata → inconclusive (stripped); lip-sync → not applicable.
  - Pitch line: *"A deepfake detector would call this authentic, and it is. The footage is genuine. The story isn't."*
- **Case B: manipulated audio/video.** A teammate records a talking-head clip. Replace the audio with a voice clone of **the same teammate, with their consent**, saying something different.
  - Built (`demo/case_b/caseb_final/caseb_swapped.mp4`): Nooman's own talking-head video with the audio of another of his own recordings, with consent. Expected: lip-sync → **inconclusive (low), never a contradiction**. Calibration showed genuine and swapped clips overlap (the genuine original scores *less* in sync), so contradictions are switched off; ENF → insufficient signal (ENF failed the hour-1 test). In the pitch this is the honesty beat: "we tell you when we can't decide".
- **Controls:** 3 genuine clips with true captions → must come out `consistent_so_far` or `insufficient_evidence`, **never** a contradiction. Run them before every demo.
- **Live moment:** a judge records a short clip on their phone and we run it live. Expect mostly consistent or inconclusive results, and say so honestly.
- **Backup:** a screen recording of the full Case A + B flow, saved offline. Warm `data/cache/` with `scripts/warm_cache.py`.
- In the pitch, say plainly that the demo media were staged by the team.

### 3-minute demo flow
1. Problem (20 s): "Seeing is no longer believing. Most viral misinformation in India is *real* footage with a false story."
2. Case A live (60 s): the graph builds, the archive match and weather contradiction appear, open "Why?".
3. Case B live (45 s): the lip-sync chart drifts apart, flagged timestamps.
4. Honesty beat (20 s): show an inconclusive finding and its alternative explanations. "We tell you when we can't decide."
5. Architecture (20 s): witnesses → contract → rule engine → LLM only explains.
6. Roadmap + close (15 s): solar-shadow analysis, reverse-image search, fact-checker integrations. *"In a world where seeing is no longer believing, evidence needs an alibi."*

### Judge Q&A answers
| Question | Answer |
|---|---|
| What if metadata is manipulated? | Metadata is supporting evidence with low/medium reliability, never ground truth. |
| No hum in genuine audio? | Absence returns "insufficient signal", never a contradiction. |
| It rained nearby / upstream? | We check 5 grid points and 72 h of history, and list upstream rain as an alternative. |
| What if the LLM hallucinates claims? | Every claim must match an exact caption span or it's dropped. The LLM never decides verdicts. |
| How is the score calculated? | Rule-based, by evidence family. Correlated witnesses count once. The numeric index is experimental and labelled. |
| Different from deepfake detectors? | They inspect pixels. We cross-examine context across modalities. Case A proves the gap. |
| Can it be gamed? | Yes, partly. Metadata can be forged and the archive is limited. That's why we show provenance and reliability per witness and require human review. |

---

## 11. 24-hour timeline (H0 = hackathon start)

| Hours | Sampurna | Nooman | Adithi | Dhruva |
|---|---|---|---|---|
| H0–1 | Repo skeleton, `schemas.py`, fixture JSON, push | ENF + lip-sync test recordings, run quick scripts | Test Open-Meteo forecast/archive/geocoding calls | Scaffold Next.js, render fixture JSON |
| H1–3 | Runner, storage, API stubs returning fixture | **ENF go/no-go**; claim extraction rules path | Metadata witness; pick Case A clip | Upload page + header + witness cards |
| H3–10 | Rule engine + graph + tests | Lip-sync witness + calibration; LLM path | Weather witness; archive builder + prior appearance | Graph view, evidence drawer, charts |
| H10–14 | Integrate all witnesses, end-to-end | ENF (if go) / support integration | ELA (if time); warm cache | Switch to live API |
| H14–18 | Report page, history log, explanations | Record Case B; tune thresholds on controls | Run controls; fix false contradictions | Polish, animations, report view |
| H18–21 | Bug bash on Case A, Case B, controls | Same | Same | Same |
| H21–24 | **FEATURE FREEZE.** Pitch deck, rehearse ×3, backup recording, Q&A prep | | | |

Stand-up every 3 hours (5 min): done / next / blocked.

---

## 12. Git workflow
- One GitHub repo. `main` must always run.
- Branches: `<name>/<module>` (e.g. `adithi/weather`). Small PRs. Sampurna merges.
- Commit at least hourly. Never commit `.env`, `data/runs/`, `data/cache/`, or large videos (except `data/archive/` and `demo/` clips under ~20 MB).
- Before a PR: `pytest -q` passes, and your module's `__main__` works on a fixture.

---

## 13. Instructions for AI coding assistants (Claude Code)
- Read §2 and §4 before every task. **Never change the contract** unless the user is Sampurna and explicitly asks.
- Work only inside the files the current user owns (§3). Ask before touching anything else.
- Prefer simple, readable, well-commented code. This is a 24 h build, so no over-engineering, no new frameworks, no databases.
- Every witness: pure function of `InvestigationContext` → `list[Finding]`, a `__main__` runner, a pytest with a small fixture, and non-empty `alternatives` and `limitations`.
- Put all thresholds in `config.py`, with a comment saying they're calibrated values.
- Never write UI text or report text using the forbidden words in §9.
- Don't add features outside §5 (feature freeze). If something seems missing, propose it to the user and don't build it.
- When unsure about the forensic meaning of a result, choose the more cautious status (`inconclusive`).

---

## 14. Live Desk (wow layer; approved by Sampurna, owned entirely by Sampurna)

A live, end-to-end show of the whole system during the pitch, built on the same pipeline, contract and rules. It is an exception to the feature freeze in §2.8, approved by the team lead. **Owner:** Sampurna. **Files:** `backend/app/live/*` (the engine, and the standalone page in `backend/app/live/static/`), `backend/app/bot/*`. The Live Desk page is served by the backend at `GET /live` and does not use or touch `frontend/` (the Next.js app), so the two never block each other.

### The flow
1. **A judge sends a video to the ALIBI Telegram bot as a FILE** (document), with a caption. Sending it as a file matters: Telegram re-compresses videos sent as "video", which would change the original. The bot stores what it received (SHA-256, read-only, §2.6).
2. **Instant claims reply.** Within seconds the bot answers with the claims it found ("Place: Chennai · Time: yesterday evening · Condition: flooding"), each traceable to the caption text.
3. **Live investigation on the big screen.** The Live Desk page (`/live`) picks up the new case from the live feed and opens it automatically. Witnesses appear as they start and finish, the graph builds, and the headline lands. All of it comes from the run's real events, with no fake delays.
4. **Verdict + report back in Telegram.** The bot sends the headline in §9 vocabulary, always with "Human review required. This is an evidence report, not a verdict." It also sends the printable report as a document.
5. **Phone briefing to the human reviewer.** A Sarvam voice call reads a short briefing to the on-duty reviewer: headline, contradictions, what needs checking. This is the human-in-the-loop beat.
6. **Caption re-run turns it green.** The reviewer (or judge) sends a corrected caption. `POST /api/investigations/{id}/rerun` re-checks the same stored files against the new story. It turns green only if the evidence supports it; the system never forces a result, and §2 applies unchanged.

**Budget rule (§6.1) applies:** the Telegram Bot API is free. Use Sarvam only within free credits. If a call would cost money, skip it and send a text briefing instead.

### Step 1: live engine (done)
- **`POST /api/investigations?async=true`:** validates and stores the originals during the request, returns **202** `{id, status: "running", parent_id, events, investigation}` at once, and analyses in a background worker (`config.LIVE_BACKGROUND_WORKERS`). Meanwhile `GET /api/investigations/{id}` returns **202** `{status: "running"}`, and a failed run returns 500 with the error.
- **`GET /api/investigations/{id}/events`** (server-sent events): the run's `history.jsonl` is the single source of truth. The stream follows the file as the pipeline writes it and sends each line once, in order. It never invents an event or adds a delay. Each message has these fields:
  - `id:` the line number (send `Last-Event-ID` to resume after it)
  - `event:` the event name
  - `data:` `{ts, event, detail, data}`

  | event | `data` payload |
  |---|---|
  | `file_received` | `{input}` (an `InputFile`; plus `copied_from` on re-runs) |
  | `claims_extracted` | `{claims: Claim[]}` |
  | `witness_started` | `{witness, family, label}` |
  | `witness_completed` / `witness_failed` | `{witness, elapsed_ms, findings: Finding[]}` (plus `reused_from` on re-runs), sent the moment that witness really finishes |
  | `assessment_computed` | `{assessment, summary}`: `summary` is `app/engine/summary.py`'s `{sentence, counts, witnesses}` (template, no LLM), shared with the report |
  | `graph_ready` | `{graph}` |
  | `done` | `{id, headline, headline_text, human_review, parent_id}`, then the stream closes. On error, `failed` with `{error}`. |

  A finished run replays its whole log. The saved `Investigation.history` keeps only `{ts, event, detail}` and ends at `graph_ready`, because `done` is logged after saving.
- **`POST /api/investigations/{id}/rerun`**, JSON `{caption, posted_at?}` (also `?async=true`). It creates a **new** investigation with `parent_id` = id:
  - The originals are copied, and each SHA-256 is checked first; a mismatch returns 409.
  - Claims are re-extracted, and the caption-dependent witnesses run again (`config.RERUN_CAPTION_DEPENDENT`: metadata, weather, prior_appearance, enf). ENF runs again because its verdict uses the claimed country (50 vs 60 Hz).
  - The caption-independent findings are re-used (`config.RERUN_REUSED`: ela, lipsync), together with their artifacts. Each gets a limitation saying it was re-used. Its claim links are kept only where the new caption makes the same claim.
  - Unknown id → 404. A parent that's still running or failed → 409. An empty caption → 422.
- **`GET /api/live/feed`** (server-sent events) announces every new investigation, including re-runs and demo runs:
  - `investigation_started` `{id, created_at, caption, posted_at, inputs[], parent_id}`
  - `investigation_done` `{id, headline, headline_text, human_review, parent_id}`
  - `investigation_failed` `{id, error, parent_id}`

  Keepalive comments are sent every 15 s; `?limit=N` closes the stream after N messages. The feed is in-process, so **run uvicorn with one worker**.

### Step 2: Telegram bot (done): `backend/app/bot/`, run `python -m app.bot`
- **Setup and connection:** long polling over `httpx` (no new dependency, no public URL). The bot talks to `ALIBI_API_URL` (default `http://127.0.0.1:8000`). Setup is in README "Telegram bot".
- **Files:**
  - `telegram.py`: Bot API client, with retries for network errors, 429 and 5xx.
  - `api.py`: ALIBI client, with friendly errors and SSE reading.
  - `handler.py`: the flow.
  - `texts.py`: every user-facing string, in §9 vocabulary.
  - `state.py`: `data/bot_chats.json`.
- **Flow:**
  1. A document/video/photo with a caption gets "📥 Received", then the claims, extracted locally from the caption.
  2. If Telegram compressed the media (sent as video or photo), the bot warns that the capture date/GPS was removed.
  3. Files over 20 MB get a polite request for a shorter clip.
  4. The bot downloads the file, sends `POST ?async=true` with `posted_at` set to the message time, and edits **one** progress message from the real event stream, with one icon per witness by status: ⚠️ contradiction, ✅ consistent, ❔ inconclusive / insufficient signal, ➖ not applicable (… still running). Files without GPS get a tip to turn on Location in the iPhone share sheet.
  5. It sends the result (headline, counts, each contradiction's title and summary, the human-review line), then `alibi_report_<id>.html`.
  6. **Replying to the result (or the report) with new text** calls `/rerun` with that caption, and the result is marked "🔁 Same file, new story: …".
- **Access:** only chats in `TELEGRAM_ALLOWED_CHAT_IDS`, or chats approved with `/start <TELEGRAM_PASSCODE>`. Everything else is silently ignored.
- **Safety:** one bad update never stops the loop. The token is never logged: httpx request logging is off, every log line passes through a redacting formatter, and errors are re-raised without the URL.
- **Tests:** `tests/test_bot.py`, with Telegram and ALIBI mocked via `httpx.MockTransport`.

### Step 3: Live Desk page (done): `GET /live`, files in `backend/app/live/static/`
- **What it is:** a standalone page served by the backend, in plain HTML, CSS and vanilla JS with no build step. It doesn't use or touch `frontend/`.
- **Works offline:** the libraries are vendored as files in `static/vendor/`, with no CDN: Cytoscape.js 3.30.2 for the graph and qrcode-generator 1.4.4 for the QR code (both MIT), the Inter and IBM Plex Mono fonts (OFL) and Lucide icons (ISC; `vendor/lucide/icons.js` is generated from the vendored SVGs). Licences and SHA-256s are in `static/README.md`. A test fails if any page file loads something from the internet.
- **Look:** a full-screen case file for a projector, built on the §9 design system (tokens, Inter + IBM Plex Mono, Lucide icons), with large type and the §9 colours and wording.
- **Idle screen:** the ALIBI logo, "Send a clip to @<bot>" and a QR code to `https://t.me/<bot>`, with sending instructions. The username comes from `TELEGRAM_BOT_USERNAME` (optional override) or Telegram's `getMe`, looked up in the background at API start-up and cached (`app/live/botinfo.py`, `GET /api/live/info`).
- **Auto-open:** the page listens to `/api/live/feed`. Any new investigation (Telegram, upload, demo, re-run) opens automatically. `/live?id=inv_…` reopens a specific case; **I** toggles the idle screen, **Esc** closes a panel and then returns to it.
- **Demo triggers (keyboard only, nothing on screen):** **1** runs Case A and **2** runs Case B (`POST /api/demo/case_a|case_b`); the case opens through the live feed as usual. **?** shows the shortcut help, including the last trigger's outcome (e.g. a case that isn't ready).
- **Live view, drawn from `/api/investigations/{id}/events` as each event arrives** (no timers or staged delays; only a short CSS fade when something appears):
  - The caption gets its claim spans underlined on `claims_extracted`. They turn red, green or grey with the worst status pointing at them.
  - The header shows the file name and shared time on one line, the case ID small and muted, the caption, and compact claim tags (`LOCATION · DELHI`).
  - Six witness tiles go from queued to investigating (`witness_started`, with a subtle progress bar), then on `witness_completed` to a status badge (Lucide icon + §9 label and colour; insufficient signal is grey dashed), the full title and a 2-line summary preview, in the order they actually finish.
  - The graph has three fixed columns (Media → Claims → Witnesses) at preset positions, so it never jumps. There is **one node per witness**: its findings are merged and the most significant status wins. Edges come only from the findings' `claim_ids` (strongest relation per claim; a finding without claims has no edges), are thin and coloured by relation, fade in, and show their label on hover. `graph_ready` redraws it the same way from the collected findings.
  - Witness nodes are ordered by the average height of the claims they link to (fewest crossings). Identical node widths, even spacing, subtle arrowheads (media → claims, witnesses → claims). The layout is about 55:45, tiles to graph.
  - **Linked selection:** clicking a tile or witness node highlights both, lights up its claims (graph and caption) and edges, dims the rest, and opens the detail panel. The tiles become a compact list beside the panel. The panel has these sections: What was checked · Data retrieved · What was found · Why it supports/contradicts the claim (template, from the finding's claim scope) · Alternatives & limitations. Clicking a claim node, caption span or claim tag highlights every witness edge touching it. **Esc** or a click on empty space clears it.
  - A thin **execution timeline** under the tiles shows when each witness started and finished, from the `witness_started` / `witness_completed` timestamps.
  - Witnesses that `/api/health` reports as `implemented: false` are shown muted as "Not available in this build". They get no graph edges and are counted apart, never as an investigation result.
  - **Case details** drawer (button or **D**): each input's SHA-256, size, duration and read-only stored path, plus the chain-of-custody log of the run's events.
  - Then the editorial evidence summary: the headline with a thin status bar, plain sentences built from what each witness checked (time / location / event scope; template, no LLM), e.g. "Recorded rainfall does not support the claimed flood. The capture time matches the stated time, which does not confirm the location or the event.", and per-witness counts. The calm "Human review required. This is an evidence report, not a verdict." banner is always visible.
- **Evidence views (key V, `static/views.js`):** a second view in the same screen area as the witnesses, built only from the case's own data; each card appears when its data exists, never as an empty box:
  - map: India outline (Natural Earth, India point of view, vendored), claimed place, verified archive place, file GPS, and the witnesses' own distances in km;
  - time: the claimed window against the verified recording date and the file capture time, with the witnesses' gaps;
  - frame filmstrip: `prior_appearance` `details.frame_strip`, one thumbnail per sampled frame plus its best Hamming distance;
  - rain by hour, the 5 weather grid points, lip-sync curves (`lipsync_series.json`), the ENF and ELA images;
  - provenance: SHA-256 plus custody events as they arrive.
- **Re-run (`parent_id`):** a "Same file, new story" tag, and the old and new captions and headlines side by side.
- **Time Machine:** if a prior_appearance finding has `details.frame_matches`, it shows the best-matching uploaded frame and archive frame side by side, with `verified_date`, `verified_location` and `verified_by`. Frame paths are run artifacts (`artifacts/...`) served from `/api/files/{id}/...`.
- **Tests:** `tests/test_live_page.py` covers the page and its static files, no external URLs, licences, wording, a `node --check` of `live.js`, and the bot-username cache.

### Next steps
- Step 4: Sarvam briefing.
