# ALIBI: setup and operations

How to install, run, test and deploy ALIBI. For what it does and why, see the [README](../README.md); the full spec and evidence contract are in [CLAUDE.md](../CLAUDE.md). Licence: [LICENSE](../LICENSE), third-party notices: [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

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

You can pre-download wheels for every package in `requirements.txt` (about 85 files, roughly 220 MB) into a folder such as `C:\Dev\wheels` while you still have a good connection:

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
- **Shortcuts:** `/live?id=inv_…` reopens a case; **I** toggles the idle screen; **Esc** closes a panel, then returns to it; **V** switches to the evidence views; **D** opens the case details; **1** / **2** run demo Case A / Case B; **?** shows the shortcut help.
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

## Deployment

**Frontend on Vercel (free) + backend on a workstation through a Cloudflare quick tunnel.** Hugging Face no longer offers free Docker Spaces, so the earlier one-container Space
plan is not used (the `Dockerfile` still works on any Docker host).

1. **Backend + tunnel (workstation):**
   ```powershell
   winget install --id Cloudflare.cloudflared            # once
   powershell -ExecutionPolicy Bypass -File scripts\serve_prod.ps1 -Tunnel
   ```
   `serve_prod.ps1` runs uvicorn on `127.0.0.1:8000` with **one worker and no `--reload`** (the Live
   Desk feed and background jobs live in that process). `-Tunnel` opens `cloudflared tunnel --url
   http://localhost:8000` in a second window, which prints the public `https://....trycloudflare.com` URL.
   A quick tunnel gets a **new URL every time** it starts.
2. **Frontend (Vercel):** project root directory `frontend`, environment variable
   `NEXT_PUBLIC_API_URL` = the tunnel URL (no trailing slash). The URL is fixed at build time, so after
   a new tunnel URL: update the variable and **Redeploy**. `frontend/vercel.json` pins the Next.js
   preset; the build is a static export.
3. **CORS:** the API accepts browser calls from `https://*.vercel.app`, `https://*.trycloudflare.com`,
   the local dev server, and any extra origins in `ALIBI_CORS_ORIGINS` (comma-separated, in
   `backend/.env`), e.g. a custom domain.
4. **Offline:** when the API can't be reached (workstation off, tunnel down), the site shows "The analysis
   server is offline right now. The live demo runs on our forensic workstation." instead of errors.
5. **Live Desk:** `<tunnel URL>/live` (the frontend's "Live Desk" link points there). The Telegram bot
   runs on the same workstation (`python -m app.bot`).

**One container (any Docker host):** `docker build -t alibi .` then `docker run --rm -p 7860:7860 alibi`
serves the frontend, `/live` and `/api/*` on one URL; the Telegram bot starts inside it only when
`TELEGRAM_BOT_TOKEN` is set. `deploy/huggingface/` (the old Space config) is kept for reference.

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

- `main` must always run. Branches: `<name>/<module>`. The team lead merges.
- Never commit `.env`, `data/runs/`, `data/cache/`, or large videos.
