# ALIBI in one container, for a Hugging Face Space (Docker SDK, free CPU tier) or any Docker host.
# One URL serves everything on port 7860: "/" (upload page), "/investigation/?id=...", "/live" and "/api/*".
#
#   docker build -t alibi .
#   docker run --rm -p 7860:7860 alibi                         # http://localhost:7860
#   docker run --rm -p 7860:7860 -e TELEGRAM_BOT_TOKEN=... alibi   # also starts the Telegram bot
#
# No secrets are baked in: the Telegram token and passcode come from the environment (HF Space secrets).
# deploy/huggingface/Dockerfile is generated from this file (deploy/huggingface/make_space_dockerfile.py).

# --- 1. the Next.js frontend as a static export (frontend/out) -----------------------------------
FROM node:22-slim AS web
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci --no-audit --no-fund
COPY frontend/ frontend/
COPY shared/ shared/
# "" = same-origin: the frontend calls /api/... on the URL that served it.
ENV NEXT_PUBLIC_API_URL="" NEXT_TELEMETRY_DISABLED=1
RUN cd frontend && npm run build

# --- 2. the API, witnesses, Live Desk and bot -------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ALIBI_LLM_PROVIDER=none \
    ALIBI_LLM_EXPLAIN=0 \
    MPLCONFIGDIR=/tmp/matplotlib \
    NUMBA_CACHE_DIR=/tmp/numba \
    PORT=7860
# ffmpeg/ffprobe + exiftool for the witnesses; libgl1/libglib2.0 for OpenCV and MediaPipe.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libimage-exiftool-perl libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
# Hugging Face Spaces run the container as uid 1000; everything under the app folder belongs to it,
# so data/runs and data/cache are writable.
RUN useradd -m -u 1000 user
WORKDIR /home/user/app
COPY --chown=user backend/requirements.txt backend/requirements.txt
RUN pip install --timeout 120 --retries 10 -r backend/requirements.txt
COPY --chown=user backend/ backend/
COPY --chown=user shared/ shared/
COPY --chown=user scripts/ scripts/
# Verified archive (Case A originals), the face model, demo cases A/B and the warmed weather cache.
COPY --chown=user data/ data/
COPY --chown=user demo/ demo/
COPY --chown=user deploy/seed-cache/ data/cache/
COPY --chown=user deploy/start.sh deploy/start.sh
COPY --chown=user --from=web /src/frontend/out frontend/out
RUN sed -i 's/\r$//' deploy/start.sh && mkdir -p data/runs data/cache && chown -R user data
USER user
EXPOSE 7860
CMD ["bash", "deploy/start.sh"]
