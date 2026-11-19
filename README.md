# ALIBI: Explainable Multimedia Verification

> Every piece of media has an alibi. We check whether it holds up.

ALIBI treats each kind of evidence (caption, metadata, weather records, earlier appearances, audio, lip movement) as a **witness**, cross-examines their statements against the claims the media makes, and produces an explainable investigation report. It is an evidence report, not a verdict: human review is always required.

Full spec, contract and ownership: [CLAUDE.md](CLAUDE.md).

## Prerequisites (Windows)

- Python 3.11 (`py -3.11 --version`)
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
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env      # then edit if you have an LLM key
uvicorn app.main:app --reload --port 8000
```

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
