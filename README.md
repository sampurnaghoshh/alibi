# ALIBI: Explainable Multimedia Verification

> Every piece of media has an alibi. We check whether it holds up.

ALIBI treats each kind of evidence (caption, metadata, weather records, earlier appearances, audio, lip movement) as a **witness**, cross-examines their statements against the claims the media makes, and produces an explainable investigation report. It is an evidence report, not a verdict: human review is always required.

Full spec, contract and ownership: [CLAUDE.md](CLAUDE.md).

## Prerequisites (Windows)

- Python 3.12 (`py -3.12 --version`)
- Node 24 (`node --version`)
- `ffmpeg` / `ffprobe` and `exiftool` on PATH:
  ```powershell
  winget install Gyan.FFmpeg
  winget install OliverBetz.ExifTool
  ```
  Open a new terminal afterwards, then check with `ffprobe -version` and `exiftool -ver`.

## Backend

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env      # keep ALIBI_LLM_PROVIDER=none for the demo
uvicorn app.main:app --reload --port 8000
```

### Offline install (venue Wi-Fi is slow or down)

A pre-downloaded set of wheels for every package in `requirements.txt` lives in `C:\Dev\wheels` on Sampurna's laptop (85 files, about 222 MB). It was built with:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pip download -d C:\Dev\wheels -r requirements.txt --timeout 120 --retries 10 --resume-retries 20
```

To install on another laptop without internet:

1. Copy the whole `wheels` folder to that laptop (USB stick or local file share), e.g. to `C:\Dev\wheels`.
2. Create and activate the venv:
   ```powershell
   cd backend
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install from the folder only (`--no-index` means pip never goes online):
   ```powershell
   pip install --no-index --find-links C:\Dev\wheels -r requirements.txt
   ```

The wheels are for **Windows 64-bit + Python 3.12** only. A different OS or Python version needs its own `pip download`. `ffmpeg` and `exiftool` are not pip packages; copy their installers separately.

### LLM: optional, and free only

**Budget rule: we spend no money on APIs.** The default is `ALIBI_LLM_PROVIDER=none`, and **the demo runs with `none`**: claims come from the offline rules path (gazetteer + date rules) and explanations from templates. The LLM only ever extracts claims (and, if enabled, rewords explanations); it never decides a verdict. On any error, refusal or rate limit, ALIBI falls back to rules. No other model is ever called automatically.

If you want to try the LLM path, use **Gemini with a free Google AI Studio key**:

1. Go to <https://aistudio.google.com/apikey> and sign in with a Google account.
2. Click **Create API key**. Let it create a new project, and **do not enable billing** on that project. Without billing the key can only use the free tier, so it can never be charged.
3. In `backend/.env` set:
   ```
   ALIBI_LLM_PROVIDER=gemini
   GEMINI_API_KEY=<your key>
   ```
   The default model is `gemini-2.5-flash-lite` (a free-tier Flash-Lite model). If AI Studio no longer lists it, set `ALIBI_LLM_MODEL` to a current free-tier Flash or Flash-Lite model from AI Studio's model list.
4. Restart the backend. `GET /api/health` shows `"llm": {"provider": "gemini", "configured": true}`.

Notes:
- Free-tier daily limits are small. Claim extraction uses one call per investigation. Rewording findings (`ALIBI_LLM_EXPLAIN=1`) adds about 6 calls per investigation, so it is off by default.
- On the free tier, Google may use prompts to improve its products. Captions are sent to Google, so don't use real case material with the LLM on.
- `anthropic` is supported in code but is **paid: do not use it**.

If activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Tests:
```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest -q
```

Run a single witness on its own (example):
```powershell
python -m app.witnesses.weather --caption "Massive flooding in Chennai yesterday evening" --posted-at 2026-11-19
```

## Frontend

```powershell
cd frontend
npm install
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000"
npm run dev                      # http://localhost:3000
```

(Or put `NEXT_PUBLIC_API_URL=http://localhost:8000` in `frontend/.env.local`.)

## Layout

```
backend/app/        FastAPI app, contract (schemas.py), engine/, claims/, witnesses/
backend/tests/      pytest, one file per module
frontend/           Next.js + TypeScript + Tailwind
shared/             contract.schema.json + fixtures/sample_investigation.json
scripts/            build_archive, calibration, warm_cache
data/archive/       verified clips + archive.json (committed)
data/cache/         API cache (gitignored; warm before demo)
data/runs/          investigations (gitignored)
demo/               case_a/, case_b/, controls/
```

## Git

- `main` must always run. Branches: `<name>/<module>`. Sampurna merges.
- Never commit `.env`, `data/runs/`, `data/cache/`, or large videos.
