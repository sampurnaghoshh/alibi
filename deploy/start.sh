#!/usr/bin/env bash
# Container entrypoint (Dockerfile CMD): the API on $PORT (7860 on Hugging Face), one worker, no reload.
# The Telegram bot runs next to it ONLY when TELEGRAM_BOT_TOKEN is set (a Space secret), and talks to
# the API inside the container. Nothing secret is stored in the image.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/runs data/cache
PORT="${PORT:-7860}"

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  export ALIBI_API_URL="${ALIBI_API_URL:-http://127.0.0.1:${PORT}}"
  echo "Telegram bot: starting (token from the environment)"
  (
    cd backend
    while true; do
      python -m app.bot || true
      echo "Telegram bot stopped; restarting in 5 s" >&2
      sleep 5
    done
  ) &
else
  echo "Telegram bot: off (set the TELEGRAM_BOT_TOKEN secret to turn it on)"
fi

cd backend
# One worker: the Live Desk feed and the background jobs live in this process (CLAUDE.md section 14).
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --workers 1 \
  --proxy-headers --forwarded-allow-ips='*'
