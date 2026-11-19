"""
Speech-to-text for spoken claims. Owner: Sampurna.

faster-whisper "tiny" (multilingual) on CPU with int8, loaded once and kept. Transcribes the first
config.WHISPER_MAX_S seconds of the first audio/video input. Never raises: a missing package or model,
no audio track, no speech or any error becomes a Transcript with status != "ok" and the reason in `note`
(principle 5). The transcript only feeds claim extraction; the LLM-free rules extract the claims, and each
claim keeps its exact character span in the transcript (principle 3).
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
from typing import Optional

import numpy as np

from app import config
from app.schemas import Transcript

log = logging.getLogger(__name__)
MODEL_NAME = "faster-whisper tiny (int8, CPU)"
_model = None
_lock = threading.Lock()


def _load():
    """The model, loaded once. Raises if the package or the downloaded weights are missing."""
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel  # noqa: PLC0415 - optional dependency

            if not (config.WHISPER_MODEL_DIR / "model.bin").is_file():
                raise FileNotFoundError(f"speech model not downloaded ({config.WHISPER_MODEL_DIR}); "
                                        "run scripts/download_models.py")
            _model = WhisperModel(str(config.WHISPER_MODEL_DIR), device="cpu", compute_type="int8")
    return _model


def audio_16k(path, max_s: float) -> np.ndarray:
    """Mono float32 16 kHz audio (what Whisper expects), first max_s seconds, via ffmpeg."""
    fd, tmp = tempfile.mkstemp(suffix=".f32")
    os.close(fd)
    try:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vn", "-t", f"{max_s:g}", "-ac", "1", "-ar", "16000",
               "-f", "f32le", tmp]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {res.stderr.strip()[:200] or 'no audio track?'}")
        return np.fromfile(tmp, dtype=np.float32)
    finally:
        os.remove(tmp)


def transcribe(inputs) -> Optional[Transcript]:
    """Transcript of the first audio/video input, or None when there is no audio/video input at all."""
    target = next((i for i in inputs if i.kind in ("video", "audio")), None)
    if target is None:
        return None
    base = {"file_id": target.file_id, "model": MODEL_NAME}
    try:
        model = _load()
    except Exception as exc:  # noqa: BLE001
        return Transcript(status="unavailable", note=f"Speech-to-text not available: {exc}", **base)
    try:
        audio = audio_16k(target.path, config.WHISPER_MAX_S)
        if audio.size < 16000 // 2:
            return Transcript(status="no_speech", span_s=round(audio.size / 16000, 1),
                              note="No audio track or less than 0.5 s of audio.", **base)
        segments, info = model.transcribe(audio, beam_size=1, vad_filter=True, condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        span = round(audio.size / 16000, 1)
        if len(text) < config.WHISPER_MIN_CHARS:
            return Transcript(status="no_speech", span_s=span, language=getattr(info, "language", None),
                              note="No speech detected in the audio.", **base)
        return Transcript(status="ok", text=text, span_s=span, language=getattr(info, "language", None),
                          note=f"Machine transcript of the first {span:g} s; it can mishear words.", **base)
    except Exception as exc:  # noqa: BLE001
        log.exception("transcription failed")
        return Transcript(status="failed", note=f"Speech-to-text failed: {type(exc).__name__}: {exc}", **base)
