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

### Telegram bot (Live Desk)

Judges send a clip to a Telegram bot. They get the claims at once, live progress, then the result and a printable report. Replying to the result with a new caption re-checks the same file. It's free: long polling, no public URL or tunnel needed.

1. **Create the bot:** in Telegram, talk to **@BotFather** → `/newbot` → copy the token.
2. **Configure** `backend/.env` (see `.env.example`):
   ```
   TELEGRAM_BOT_TOKEN=<token from BotFather>
   TELEGRAM_ALLOWED_CHAT_IDS=<your chat id>      # optional, comma-separated
   TELEGRAM_PASSCODE=<a word only the team knows>
   ```
   Only allowed chats are served. Any other chat can join by sending `/start <passcode>` (stored in `data/bot_chats.json`, gitignored). Everything else is ignored.
3. **Run it next to the API**, as a second process, in two terminals:
   ```powershell
   cd backend; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --port 8000
   cd backend; .\.venv\Scripts\Activate.ps1; python -m app.bot
   ```
   The bot talks to `ALIBI_API_URL` (default `http://127.0.0.1:8000`; `localhost` adds ~2 s per connection on Windows). It never prints or logs the token.

**How judges should send a clip:** record 5–8 s in 1080p, then tap 📎 → **File** (not Gallery/Video) and put the claim in the caption, e.g. "Heavy rain in Bengaluru this afternoon".
- A normal video upload makes Telegram strip the capture date and GPS. The bot warns about that, and metadata comes out inconclusive.
- Bots can't download files over 20 MB, so a long 4K clip gets a polite "send a shorter clip".

### Live Desk page (big screen)

With the API running, open **<http://127.0.0.1:8000/live>** on the projector and press **F11**.
- **Idle screen:** shows "Send a clip to @your_bot" and a QR code. The username comes from `TELEGRAM_BOT_USERNAME`, or from Telegram when the API starts.
- **New cases open automatically,** whether from the Telegram bot, an upload, a demo button or a re-run. The caption, witness tiles, graph and headline fill in as the investigation's events arrive.
- **Shortcuts:** `/live?id=inv_…` reopens a case; **I** or **Esc** toggles the idle screen.
- **Offline:** it needs no internet connection. The graph and QR libraries are local files (see `backend/app/live/static/README.md`).

## Frontend

```powershell
cd frontend
npm install
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000"
npm run dev                      # http://localhost:3000
```

(Or put `NEXT_PUBLIC_API_URL=http://localhost:8000` in `frontend/.env.local`.)

The frontend is a static export (`npm run build` writes `frontend/out`). The investigation page is
`/investigation/?id=inv_…`. When `frontend/out` exists, the API serves it at `/`, so one URL serves the
upload page, the investigation page, `/live` and `/api/*`.

## Deploy (one container, e.g. a Hugging Face Space)

```powershell
docker build -t alibi .
docker run --rm -p 7860:7860 alibi                                   # http://localhost:7860
docker run --rm -p 7860:7860 -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_PASSCODE=... alibi   # + the bot
```

- `Dockerfile`: builds the frontend export, then runs uvicorn with one worker on port 7860
  (`deploy/start.sh`). The image includes the verified archive, demo Cases A and B and a warmed
  weather cache (`deploy/seed-cache/`). `ALIBI_LLM_PROVIDER=none`. No secrets go into the image.
- The Telegram bot starts in the same container only when `TELEGRAM_BOT_TOKEN` is set.
- Hugging Face Space: `deploy/huggingface/` holds the Space's `README.md` (its config) and a
  Dockerfile generated by `make_space_dockerfile.py` that builds this repo from GitHub at one commit.

## Licence

Code: MIT (`LICENSE`). Third-party components, Open-Meteo weather data (CC BY 4.0) and the team-recorded
demo media: see `THIRD_PARTY_NOTICES.md`.

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
