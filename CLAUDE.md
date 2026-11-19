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
| **Sampurna** (lead) | Claude Code (Opus) in VS Code | Repo skeleton, **evidence contract**, **rule engine**, graph builder, FastAPI app, storage, report, integration, final merges | `backend/app/main.py`, `schemas.py`, `engine/*`, `storage.py`, `report.py`, `shared/*` |
| **Nooman** | Claude Code (Opus) in VS Code | **Claim extraction** (LLM + rule fallback), **lip-sync witness**, **ENF audio witness** (conditional), calibration scripts | `backend/app/claims/*`, `witnesses/lipsync.py`, `witnesses/enf.py`, `scripts/calibrate_*.py`, `scripts/test_enf.py` |
| **Adithi** | Free LLMs | **Metadata witness**, **weather witness**, **prior-appearance witness**, ELA (only if time), archive builder | `witnesses/metadata.py`, `witnesses/weather.py`, `witnesses/prior_appearance.py`, `witnesses/ela.py`, `scripts/build_archive.py` |
| **Dhruva** | Free LLMs | **Entire frontend**: upload page, investigation dashboard, contradiction graph, witness cards, evidence drawer, report view, demo polish | `frontend/*` |

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
WitnessName = "metadata" | "weather" | "prior_appearance" | "lipsync" | "enf" | "ela"
```

Witness → family (fixed):
| Witness | Family |
|---|---|
| metadata, weather, prior_appearance | context |
| lipsync, enf | signal |
| ela | pixels |

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

---

## 5. Final witness list (FROZEN)

| # | Witness | Owner | Status | Max reliability |
|---|---|---|---|---|
| 1 | Claim extraction (not a witness, it feeds everything) | Nooman | **Must** | n/a |
| 2 | Metadata integrity | Adithi | **Must** | medium |
| 3 | Weather (grid + 72 h) | Adithi | **Must** | medium |
| 4 | Prior appearance (perceptual-hash archive) | Adithi | **Must** | high |
| 5 | Lip-sync synchrony indicator | Nooman | **Must** | medium |
| 6 | ENF power-grid hum | Nooman | **Conditional** on the hour-1 test | medium |
| 7 | ELA heatmap | Adithi | Only if time | low (always) |
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

### 6.1 Claim extraction (Nooman): `app/claims/extract.py`
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
  - Capture date present **and** differs from the claimed date by > 2 days → `contradiction` (medium).
  - GPS present **and** > 50 km from the claimed location → `contradiction` (medium).
  - Present and matching → `consistent` (low; metadata is easy to edit).
  - Missing → `inconclusive` with alternative "metadata is commonly stripped by messaging apps and social platforms".
  - Editing software found → separate finding, `inconclusive` (low), title "Editing software tag present".
- Always list: "Metadata can be edited or removed; it is supporting evidence, not ground truth."

### 6.3 Weather (Adithi): `witnesses/weather.py`
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

### 6.4 Prior appearance (Adithi): `witnesses/prior_appearance.py` + `scripts/build_archive.py`
- Archive: `data/archive/archive.json` plus the source clips. Each entry: `{ "clip_id", "title", "verified_date", "verified_location", "verified_by", "source_note", "frame_hashes": [hex...] }`.
- `build_archive.py`: for each clip, sample 1 frame/s (OpenCV), compute `imagehash.phash` (64-bit), store the hashes.
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

### 6.7 ELA (Adithi, only if time): `witnesses/ela.py`
- Images, or a video keyframe. Re-save as JPEG q=90, take the absolute difference ×15 → heatmap PNG artifact.
- **Always `inconclusive`, reliability `low`**, listed under `weak_indicators`. ELA is a visual aid, not a verdict.

---

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
3. **Headline:**
   - `context_not_supported` / `signals_inconsistent`: ≥ 1 high-reliability contradiction, **or** contradictions in ≥ 2 families. Pick the text by which family contradicts (context → "The stated context is not supported by the available evidence"; signal → "Audio-visual signals show inconsistencies").
   - `partially_supported`: exactly 1 family with a medium contradiction and nothing high.
   - `consistent_so_far`: ≥ 2 families consistent and no contradictions. Text: "No contradictions found in the available evidence". Never "authentic".
   - `insufficient_evidence`: everything else.
4. **human_review:** always present. `priority` if there's any contradiction, else `standard`. The UI always shows "Human review required. This is an evidence report, not a verdict."
5. **experimental_score** (optional, small, never the headline): start at 100, −30 per high contradiction, −15 per medium, −5 per low, −3 per inconclusive, floor 0. Always shown with its label.
6. **Explanations:** after the rules run, the LLM may rewrite `title`/`summary` into plain English, **given only the finding JSON**, and it must not change status or numbers. If the LLM is off, use template strings.

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
| POST | `/api/investigations` | multipart: `files[]`, `caption`, `posted_at` → runs synchronously, returns `Investigation` |
| GET | `/api/investigations/{id}` | fetch a saved investigation |
| GET | `/api/investigations` | list past runs (history page) |
| GET | `/api/files/{inv_id}/{path}` | serve originals (read-only) and artifacts |
| GET | `/api/investigations/{id}/report` | printable HTML report (browser → Save as PDF) |
| GET | `/api/health` | witness availability (ffmpeg/exiftool found? LLM configured?) |

Synchronous is fine for the demo (target < 20 s for a 15 s clip). The frontend **reveals findings one by one** with a staggered animation for the demo effect.

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
ALIBI_LLM_PROVIDER=none        # anthropic | gemini | none
ALIBI_LLM_MODEL=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
```
Note: a Claude Code subscription is not an API key. The app's runtime LLM needs its own key, or falls back to rules.

---

## 9. UI and language guide (Dhruva + everyone)

### Screens
1. **Upload** (`/`): drag-drop files (image/video/audio), caption textarea, "Shared on" date (default today), big "Investigate" button. Also show two demo-sample buttons (Case A, Case B) that load the preset files.
2. **Investigation** (`/investigation/[id]`):
   - **Header:** headline text, counts as pills (`2 contradictions · 1 consistent · 3 inconclusive`), the human-review banner, and a small experimental score with its label.
   - **Caption with highlighted claim spans:** hover a claim to light up its graph node and linked findings.
   - **Contradiction graph:** inputs → claims ← witnesses. Edge colours: green supports, red contradicts, grey inconclusive. Nodes appear one by one.
   - **Witness cards:** one per finding with status chip, reliability, title, summary, "Why?" expand (details, evidence links, alternatives, limitations), and "Where?" (jump to timestamp / show region).
   - **Evidence drawer:** video player with flagged timestamps on the scrubber, lip-sync chart, weather map with 5 points and mm values, archive match side-by-side frames, ENF spectrogram.
   - **History tab:** chain-of-custody log with SHA-256 values.
   - **Export:** "Open report" → printable page.
3. **History** (`/history`): past investigations.

Build everything against `shared/fixtures/sample_investigation.json` first. Switch to the live API when the backend is ready.

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

---

## 10. Demo plan

### Assets (`demo/`)
- **Case A: genuine footage, false caption (the killer moment).** A teammate's own older rain/flood video from their phone gallery with a known date and place. They shot it, so it's genuinely verified and we own it. Add the original to the archive with its true date/place. Forward it through WhatsApp to strip metadata (realistic). Caption: "Massive flooding in Chennai yesterday evening" for a date when Chennai was dry (check with the weather module first!).
  - Expected: prior appearance → **contradiction (high)**; weather → **contradiction (medium)**; metadata → inconclusive (stripped); lip-sync → not applicable.
  - Pitch line: *"A deepfake detector would call this authentic, and it is. The footage is genuine. The story isn't."*
- **Case B: manipulated audio/video.** A teammate records a talking-head clip. Replace the audio with a voice clone of **the same teammate, with their consent**, saying something different.
  - Expected: lip-sync → contradiction (medium) with flagged timestamps; ENF → consistent on the original vs insufficient signal on the swap (if ENF passed the hour-1 test); metadata → editing/encoder hints.
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
