"""
Lip-sync synchrony indicator (CLAUDE.md §6.5). Owner: Nooman.   Family: signal. Max reliability: medium.
Runner time limit: 90 s.

Analysis (shared with scripts/calibrate_lipsync.py, so calibration and witness see the same numbers):
  1. probe_media():    rotation, start times, audio presence (ffprobe).
  2. mouth_series():   ffmpeg decodes frames (auto-rotated upright, resampled to a constant
                       LIPSYNC_ANALYSIS_FPS, scaled to LIPSYNC_FRAME_WIDTH while decoding) ->
                       MediaPipe FaceLandmarker (VIDEO mode, tracks between frames) ->
                       mouth aperture = inner-lip gap (13-14) / face height (10-152), median-smoothed.
                       mouth_features(): aperture, or velocity = |frame-to-frame change| (config picks one).
  3. audio_loudness(): ffmpeg mono 16 kHz -> band-pass 300-3000 Hz -> RMS in dB (numpy), at the frame times.
  4. speech_mask():    adaptive threshold between the dB noise floor and peak.
  5. clip_sync():      ONE lag per clip over all speech frames, +/- 0.3 s: best lag, corr, and peak
                       prominence (best corr - median corr over lags). lag > 0 = audio after the mouth.
                       window_sync(): 3 s windows (1 s step), corr at the clip lag: only shows where sync drops.
  6. summarize():      status from the clip-level numbers (thresholds in config).

Calibration (2026-11-19, 5 genuine iPhone clips + 5 audio-swapped controls): no threshold separated
genuine from swapped cleanly, so findings are capped at config.LIPSYNC_MAX_RELIABILITY = "low" (§6.5
fallback): lip-sync is only ever a weak indicator.

Witness run(): one finding per video (up to LIPSYNC_MAX_INPUTS), reliability never above
LIPSYNC_MAX_RELIABILITY. While LIPSYNC_ALLOW_CONTRADICTION is False the indicator is ALWAYS inconclusive/low
("weak or unclear"), with the rule outcome + raw numbers in details and the worst windows as timestamps.
Face-size guard: median face height < LIPSYNC_MIN_FACE_HEIGHT -> inconclusive/low "face too small".
Artifact: artifacts/lipsync_series.json { t[], mouth[], audio[], window_lags[], window_corrs[] } (+ extras).
Missing face model or any analysis error -> inconclusive/low, never a crash.

The FaceLandmarker model is not part of the pip package: download face_landmarker.task to
config.LIPSYNC_FACE_MODEL (data/models/face_landmarker.task), from
https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task

Run alone (from backend/):
    python -m app.witnesses.lipsync --file ../demo/case_b/talking.mp4
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from app import config
from app.schemas import Finding
from app.witnesses.base import InvestigationContext, Witness, run_cli

# MediaPipe face-mesh landmark indices.
UPPER_INNER_LIP, LOWER_INNER_LIP = 13, 14
FOREHEAD_TOP, CHIN = 10, 152


# ---------------------------------------------------------------------------
# 1. Probe
# ---------------------------------------------------------------------------

@dataclass
class MediaProbe:
    width: int                 # stored (encoded) size, before rotation
    height: int
    rotation: int              # display rotation in degrees (iPhone portrait clips: 90 / -90)
    video_start_s: float
    audio_start_s: Optional[float]   # None = no audio track
    duration_s: float
    native_fps: float

    @property
    def has_audio(self) -> bool:
        return self.audio_start_s is not None

    @property
    def upright_size(self) -> tuple[int, int]:
        """(w, h) after ffmpeg's autorotate, i.e. how the frame is meant to be viewed."""
        if abs(self.rotation) % 180 == 90:
            return self.height, self.width
        return self.width, self.height


def _rotation_of(stream: dict) -> int:
    """Rotation from the display-matrix side data (new ffmpeg) or the legacy 'rotate' tag."""
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"])))
    rot = (stream.get("tags") or {}).get("rotate")
    return int(rot) if rot not in (None, "") else 0


def probe_media(path: Path) -> MediaProbe:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, timeout=20, check=True,
    ).stdout
    info = json.loads(out)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError(f"{path.name}: no video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    num, _, den = (video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0.0)
    return MediaProbe(
        width=int(video["width"]),
        height=int(video["height"]),
        rotation=_rotation_of(video),
        video_start_s=float(video.get("start_time") or 0.0),
        audio_start_s=float(audio.get("start_time") or 0.0) if audio else None,
        duration_s=duration,
        native_fps=fps,
    )


# ---------------------------------------------------------------------------
# 2. Mouth aperture
# ---------------------------------------------------------------------------

def _analysis_size(probe: MediaProbe, width: int) -> tuple[int, int]:
    """Upright size at `width` px wide (never upscaled), height keeping the aspect ratio, both even.
    Same result as ffmpeg's scale=<width>:-2; computed here because the pipe reader needs the frame size."""
    w, h = probe.upright_size
    scale = min(1.0, width / w)
    return max(2, int(round(w * scale / 2)) * 2), max(2, int(round(h * scale / 2)) * 2)


def iter_frames(path: Path, probe: MediaProbe, fps: float, width: int):
    """Yield upright RGB frames (H, W, 3) at a constant `fps`, `width` px wide.

    ffmpeg resamples to `fps` and scales while decoding, so Python never sees full-size 4K frames, and
    it decodes in its own process while MediaPipe consumes the pipe (the two overlap).
    ffmpeg applies the container's display rotation itself (autorotate is on by default and
    runs before our filters), so portrait iPhone clips come out upright, not sideways.
    """
    w, h = _analysis_size(probe, width)
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path), "-an",
        "-vf", f"fps={fps},scale={w}:{h}",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def _landmarker(model_path: Path):
    """MediaPipe Tasks FaceLandmarker in VIDEO mode (mediapipe >= 0.10 has no mp.solutions)."""
    if not Path(model_path).is_file():
        raise FileNotFoundError(
            f"MediaPipe model missing: {model_path}. Download face_landmarker.task from "
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
        )
    from mediapipe.tasks.python import BaseOptions, vision  # noqa: PLC0415

    opts = vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
    )
    return vision.FaceLandmarker.create_from_options(opts)


@dataclass
class MouthSeries:
    t: np.ndarray              # frame times in seconds, from the first video frame
    raw: np.ndarray            # aperture per frame, NaN where no face was found
    mouth: np.ndarray          # gap-filled + median-smoothed; NaN where the face was missing too long
    face_found: np.ndarray     # bool per frame
    frame_size: tuple[int, int]
    first_face_frame: Optional[np.ndarray] = None    # one upright frame with its mouth landmarks,
    first_face_points: Optional[np.ndarray] = None   # kept for the calibration plot (orientation check)
    face_height: Optional[np.ndarray] = None   # forehead-chin distance / frame height per frame, NaN = no face

    def median_face_height(self) -> Optional[float]:
        """Median face height (fraction of frame height) over frames where a face was found."""
        if self.face_height is None or np.all(np.isnan(self.face_height)):
            return None
        return float(np.nanmedian(self.face_height))


def mouth_aperture(landmarks, w: int, h: int) -> float:
    """Inner-lip vertical gap / face height, in pixel space (landmarks are normalized 0-1)."""
    def px(i):
        p = landmarks[i]
        return np.array([p.x * w, p.y * h])

    face_h = np.linalg.norm(px(FOREHEAD_TOP) - px(CHIN))
    if face_h <= 1e-6:
        return float("nan")
    return float(np.linalg.norm(px(UPPER_INNER_LIP) - px(LOWER_INNER_LIP)) / face_h)


def face_height_fraction(landmarks) -> float:
    """Forehead-top (10) to chin (152) distance as a fraction of the frame height (y only: upright frames)."""
    return float(abs(landmarks[CHIN].y - landmarks[FOREHEAD_TOP].y))


def _fill_short_gaps(x: np.ndarray, max_gap: int) -> np.ndarray:
    """Linearly interpolate NaN runs of length <= max_gap; longer runs stay NaN."""
    x = x.copy()
    isnan = np.isnan(x)
    if isnan.all() or not isnan.any():
        return x
    idx = np.arange(len(x))
    filled = np.interp(idx, idx[~isnan], x[~isnan])
    i = 0
    while i < len(x):
        if isnan[i]:
            j = i
            while j < len(x) and isnan[j]:
                j += 1
            inner = i > 0 and j < len(x)          # don't extrapolate at the clip edges
            if inner and (j - i) <= max_gap:
                x[i:j] = filled[i:j]
            i = j
        else:
            i += 1
    return x


def _nan_medfilt(x: np.ndarray, k: int) -> np.ndarray:
    """Centered median filter of width k that ignores NaN (NaN stays NaN)."""
    if k <= 1:
        return x.copy()
    half = k // 2
    out = np.full_like(x, np.nan)
    for i in range(len(x)):
        if np.isnan(x[i]):
            continue
        seg = x[max(0, i - half): i + half + 1]
        out[i] = np.nanmedian(seg)
    return out


def mouth_series(path: Path, probe: MediaProbe, fps: Optional[float] = None,
                 width: Optional[int] = None, model_path: Optional[Path] = None) -> MouthSeries:
    """Mouth aperture per frame. Defaults are read from config at call time (tests can monkeypatch)."""
    import mediapipe as mp  # noqa: PLC0415

    fps = fps or config.LIPSYNC_ANALYSIS_FPS
    width = width or config.LIPSYNC_FRAME_WIDTH
    model_path = model_path or config.LIPSYNC_FACE_MODEL
    size = _analysis_size(probe, width)
    raw: list[float] = []
    heights: list[float] = []
    first_frame, first_pts = None, None
    with _landmarker(model_path) as lm:
        for i, frame in enumerate(iter_frames(path, probe, fps, width)):
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame))
            res = lm.detect_for_video(image, int(round(i * 1000.0 / fps)))
            if res.face_landmarks:
                pts = res.face_landmarks[0]
                raw.append(mouth_aperture(pts, size[0], size[1]))
                heights.append(face_height_fraction(pts))
                if first_frame is None and i >= fps:   # ~1 s in: past any fade-in
                    first_frame = frame.copy()
                    idx = (UPPER_INNER_LIP, LOWER_INNER_LIP, FOREHEAD_TOP, CHIN)
                    first_pts = np.array([[pts[k].x * size[0], pts[k].y * size[1]] for k in idx])
            else:
                raw.append(float("nan"))
                heights.append(float("nan"))
    raw_arr = np.array(raw, dtype=float)
    smooth = _nan_medfilt(_fill_short_gaps(raw_arr, config.LIPSYNC_MAX_GAP_FRAMES), config.LIPSYNC_SMOOTH_FRAMES)
    return MouthSeries(
        t=np.arange(len(raw_arr)) / fps,
        raw=raw_arr,
        mouth=smooth,
        face_found=~np.isnan(raw_arr),
        frame_size=size,
        first_face_frame=first_frame,
        first_face_points=first_pts,
        face_height=np.array(heights, dtype=float),
    )


def mouth_features(mouth: np.ndarray) -> dict[str, np.ndarray]:
    """The two mouth signals compared during calibration.

    aperture: how open the mouth is.  velocity: how fast it is opening or closing
    (|frame-to-frame change|), i.e. articulation, which tends to track syllable energy.
    """
    return {"aperture": mouth, "velocity": np.abs(np.diff(mouth, prepend=np.nan))}


# ---------------------------------------------------------------------------
# 3-4. Audio loudness + speech mask
# ---------------------------------------------------------------------------

def load_audio(path: Path, sr: Optional[int] = None) -> np.ndarray:
    """Mono float32 audio at `sr` via ffmpeg (handles AAC in .MOV/.mp4, no temp file)."""
    sr = sr or config.LIPSYNC_AUDIO_SR
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True, timeout=60, check=True,
    ).stdout
    return np.frombuffer(out, np.int16).astype(np.float32) / 32768.0


def loudness_db(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Speech-band loudness: band-pass LIPSYNC_BAND_HZ, RMS, dB. Returns (times_s, dB) at the RMS hop."""
    from scipy.signal import butter, sosfiltfilt  # noqa: PLC0415

    lo, hi = config.LIPSYNC_BAND_HZ
    sos = butter(4, [lo, hi], btype="bandpass", fs=sr, output="sos")
    if len(y) > 50:  # sosfiltfilt needs a few samples of padding
        y = sosfiltfilt(sos, y).astype(np.float32)
    hop = int(round(config.LIPSYNC_RMS_HOP_S * sr))
    frame = int(round(config.LIPSYNC_RMS_FRAME_S * sr))
    rms = frame_rms(y, frame, hop)
    db = 20.0 * np.log10(np.maximum(rms, 10 ** (config.LIPSYNC_DB_FLOOR / 20.0)))
    return np.arange(len(db)) * hop / sr, db


def frame_rms(y: np.ndarray, frame: int, hop: int) -> np.ndarray:
    """Centered frame RMS, same as librosa.feature.rms(center=True, pad_mode="constant").

    Plain numpy (running sum of squares): librosa's first call costs ~3 s of JIT warm-up per process.
    """
    pad = frame // 2
    ypad = np.pad(y.astype(np.float64), pad)
    n = 1 + max(0, (len(ypad) - frame) // hop)
    csum = np.concatenate(([0.0], np.cumsum(ypad * ypad)))
    starts = np.arange(n) * hop
    energy = np.maximum(csum[starts + frame] - csum[starts], 0.0)
    return np.sqrt(energy / frame)


def audio_loudness(path: Path, probe: MediaProbe, t: np.ndarray) -> np.ndarray:
    """Speech-band loudness (dB) sampled at the video frame times `t` (seconds from the first video frame).

    The audio track's start offset relative to the video track is applied, so a container-level
    offset doesn't show up as a lag.
    """
    sr = config.LIPSYNC_AUDIO_SR
    db_t, db = loudness_db(load_audio(path, sr), sr)
    db_t = db_t + (probe.audio_start_s or 0.0) - probe.video_start_s
    return np.interp(t, db_t, db, left=config.LIPSYNC_DB_FLOOR, right=config.LIPSYNC_DB_FLOOR)


def speech_mask(db: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """Adaptive speech mask in dB: loudness above floor + frac * (peak - floor).

    If the audio has almost no dynamic range (peak - floor < LIPSYNC_SPEECH_MIN_RANGE_DB), nothing is
    speech: steady noise or music would otherwise be half "speech" by construction.
    """
    floor = float(np.percentile(db, config.LIPSYNC_SPEECH_FLOOR_PCT))
    peak = float(np.percentile(db, config.LIPSYNC_SPEECH_PEAK_PCT))
    thr = floor + config.LIPSYNC_SPEECH_THRESH_FRAC * (peak - floor)
    info = {"floor_db": round(floor, 1), "peak_db": round(peak, 1), "threshold_db": round(thr, 1)}
    if peak - floor < config.LIPSYNC_SPEECH_MIN_RANGE_DB:
        return np.zeros(len(db), dtype=bool), info
    return db > thr, info


# ---------------------------------------------------------------------------
# 5. One lag per clip (+ windows that only show where sync drops)
# ---------------------------------------------------------------------------

def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 0 else float("nan")


def lag_curve(mouth: np.ndarray, audio: np.ndarray, sel: np.ndarray, max_lag: int) -> np.ndarray:
    """corr(mouth[i], audio[i + L]) over the selected frames i, for L = -max_lag..max_lag (frames).

    L > 0: the audio comes after the mouth. A lag is scored only if >= 80% of the selected frames
    stay inside the clip after shifting; otherwise NaN.
    """
    n = len(mouth)
    idx_all = np.flatnonzero(sel & ~np.isnan(mouth))
    curve = np.full(2 * max_lag + 1, np.nan)
    if len(idx_all) < 3:
        return curve
    for k, lag in enumerate(range(-max_lag, max_lag + 1)):
        idx = idx_all[(idx_all + lag >= 0) & (idx_all + lag < n)]
        if len(idx) >= 0.8 * len(idx_all):
            curve[k] = _pearson(mouth[idx], audio[idx + lag])
    return curve


def clip_sync(mouth: np.ndarray, audio: np.ndarray, speech: np.ndarray, fps: float) -> Optional[dict[str, Any]]:
    """ONE lag for the whole clip, from all speech frames where the face was found.

    prominence = corr at the best lag - median corr over all lags. Genuine sync should give a clear
    peak; unrelated audio gives a flat curve whose maximum is just the luckiest lag.
    Returns None if there is not enough usable speech.
    """
    max_lag = int(round(config.LIPSYNC_MAX_LAG_S * fps))
    sel = speech & ~np.isnan(mouth)
    if sel.sum() < config.LIPSYNC_MIN_SPEECH_S * fps:
        return None
    curve = lag_curve(mouth, audio, sel, max_lag)
    if np.all(np.isnan(curve)):
        return None
    k = int(np.nanargmax(curve))
    best = float(curve[k])
    return {
        "lag_s": round((k - max_lag) / fps, 3),
        "corr": round(best, 3),
        "prominence": round(best - float(np.nanmedian(curve)), 3),
        "corr_at_zero": round(float(curve[max_lag]), 3),
        "speech_frames": int(sel.sum()),
        "lags_s": [round((j - max_lag) / fps, 3) for j in range(len(curve))],
        "curve": [None if np.isnan(c) else round(float(c), 4) for c in curve],
    }


def window_sync(mouth: np.ndarray, audio: np.ndarray, speech: np.ndarray, fps: float,
                lag_s: Optional[float]) -> list[dict[str, Any]]:
    """3 s windows, 1 s step: corr at the CLIP's lag inside each window (+ the window's own best lag, for the
    chart). Display only (where sync drops); the status never comes from a single window."""
    n = len(mouth)
    win = int(round(config.LIPSYNC_WINDOW_S * fps))
    step = max(1, int(round(config.LIPSYNC_STEP_S * fps)))
    lag = int(round((lag_s or 0.0) * fps))
    max_lag = int(round(config.LIPSYNC_MAX_LAG_S * fps))
    starts = list(range(0, n - win + 1, step)) if n >= win else [0]
    out = []
    for i0 in starts:
        i1 = min(n, i0 + win)
        face_frac = float(np.mean(~np.isnan(mouth[i0:i1])))
        speech_frac = float(np.mean(speech[i0:i1]))
        w = {"start_s": round(i0 / fps, 3), "end_s": round(i1 / fps, 3),
             "face_fraction": round(face_frac, 3), "speech_fraction": round(speech_frac, 3),
             "is_speech": speech_frac >= config.LIPSYNC_WINDOW_MIN_SPEECH, "corr": None, "best_lag_s": None,
             "skip": None}
        if not w["is_speech"]:
            w["skip"] = "no speech"
        elif face_frac < config.LIPSYNC_WINDOW_MIN_FACE:
            w["skip"] = "face missing"
        else:
            idx = np.arange(max(i0, -lag), min(i1, n - lag))
            m, a = mouth[idx], audio[idx + lag]
            ok = ~np.isnan(m)
            if ok.sum() >= 0.8 * (i1 - i0):
                c = _pearson(m[ok], a[ok])
                w["corr"] = None if np.isnan(c) else round(c, 3)
            in_win = np.zeros(n, dtype=bool)
            in_win[i0:i1] = True
            curve = lag_curve(mouth, audio, in_win, max_lag)
            if not np.all(np.isnan(curve)):
                w["best_lag_s"] = round((int(np.nanargmax(curve)) - max_lag) / fps, 3)
        out.append(w)
    return out


# ---------------------------------------------------------------------------
# 6. Summary + rules (calibration only for now; the witness will wrap this in a Finding)
# ---------------------------------------------------------------------------

def summarize(face_fraction: float, sync: Optional[dict], windows: list[dict], has_audio: bool,
              face_height: Optional[float] = None) -> dict[str, Any]:
    """Status from the clip-level lag / corr / prominence. Thresholds are in config (LIPSYNC_*).

    face_height (median, fraction of frame height) is reported alongside; the face-size guard
    (LIPSYNC_MIN_FACE_HEIGHT) is applied by the witness, so calibration still sees the rule outcome."""
    speech_frac = float(np.mean([w["is_speech"] for w in windows])) if windows else 0.0
    s: dict[str, Any] = {
        "face_fraction": round(face_fraction, 3),
        "speech_window_fraction": round(speech_frac, 3),
        "lag_s": sync["lag_s"] if sync else None,
        "corr": sync["corr"] if sync else None,
        "prominence": sync["prominence"] if sync else None,
        "face_height": None if face_height is None else round(face_height, 3),
    }
    if not has_audio:
        return {**s, "status": "not_applicable", "reason": "no audio track"}
    if face_fraction < config.LIPSYNC_MIN_FACE_FRACTION:
        return {**s, "status": "not_applicable",
                "reason": f"face in {face_fraction:.0%} of frames (< {config.LIPSYNC_MIN_FACE_FRACTION:.0%})"}
    if sync is None:
        # Absence of evidence: too little usable speech says nothing about synchrony.
        return {**s, "status": "insufficient_signal", "reason": "not enough speech with a visible face"}

    abs_lag, corr, prom = abs(sync["lag_s"]), sync["corr"], sync["prominence"]
    if (abs_lag <= config.LIPSYNC_CONSISTENT_MAX_LAG_S and corr >= config.LIPSYNC_CONSISTENT_MIN_CORR
            and prom >= config.LIPSYNC_CONSISTENT_MIN_PROMINENCE):
        return {**s, "status": "consistent", "reason": "clear correlation peak near zero lag"}
    off = (abs_lag >= config.LIPSYNC_CONTRA_MIN_LAG_S or corr < config.LIPSYNC_CONTRA_MAX_CORR
           or prom < config.LIPSYNC_CONTRA_MAX_PROMINENCE)
    if off and speech_frac >= config.LIPSYNC_MIN_SPEECH_FRACTION:
        return {**s, "status": "contradiction", "reason": "no clear audio-mouth correlation peak near zero lag"}
    return {**s, "status": "inconclusive", "reason": "between the consistent and contradiction thresholds"}


@dataclass
class ClipAnalysis:
    probe: MediaProbe
    mouth: Optional[MouthSeries]
    audio: Optional[np.ndarray]                 # speech-band loudness, dB, per frame
    speech: Optional[np.ndarray]
    speech_info: dict[str, float] = field(default_factory=dict)
    syncs: dict[str, Optional[dict]] = field(default_factory=dict)   # per mouth feature
    windows: list[dict] = field(default_factory=list)                # for config.LIPSYNC_MOUTH_FEATURE
    summary: dict[str, Any] = field(default_factory=dict)            # for config.LIPSYNC_MOUTH_FEATURE
    fps: float = 0.0


def analyze_clip(path: Path, fps: Optional[float] = None) -> ClipAnalysis:
    """Full §6.5 analysis of one video. Raises on missing tools/model; the witness catches."""
    fps = fps or config.LIPSYNC_ANALYSIS_FPS
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg/ffprobe not found on PATH")
    probe = probe_media(path)
    if not probe.has_audio:
        return ClipAnalysis(probe, None, None, None, fps=fps, summary=summarize(0.0, None, [], False))
    ms = mouth_series(path, probe, fps)
    audio = audio_loudness(path, probe, ms.t)
    speech, info = speech_mask(audio)
    face_fraction = float(ms.face_found.mean()) if len(ms.face_found) else 0.0
    feats = mouth_features(ms.mouth)
    ok = face_fraction >= config.LIPSYNC_MIN_FACE_FRACTION
    syncs = {name: (clip_sync(f, audio, speech, fps) if ok else None) for name, f in feats.items()}
    chosen = config.LIPSYNC_MOUTH_FEATURE
    sync = syncs[chosen]
    windows = window_sync(feats[chosen], audio, speech, fps, sync["lag_s"] if sync else None) if ok else []
    return ClipAnalysis(probe, ms, audio, speech, info, syncs, windows,
                        summarize(face_fraction, sync, windows, True, ms.median_face_height()), fps)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------

ALTERNATIVES = [
    "Dubbing or translation can replace the original audio track",
    "Bluetooth or other playback/recording audio delay can shift sound against the picture",
    "A low or variable frame rate blurs the timing of mouth movement",
    "A small face in frame gives a noisy mouth measurement",
    "The person speaking may be off-screen, or another voice may be louder than the visible speaker",
]
BASE_LIMITATIONS = [
    "Calibrated on only 5 genuine clips and 5 audio-swapped controls; the two groups did not separate cleanly",
    "Mouth movement and speech loudness are only loosely related; this is a synchrony indicator, not a verdict",
]
WEAK_TITLE = "Lip-sync synchrony indicator: weak or unclear"
_RANK = {"low": 0, "medium": 1, "high": 2}


def _cap(reliability: str) -> str:
    """Never above config.LIPSYNC_MAX_RELIABILITY (calibration fallback) or the §5 max (medium)."""
    return min(reliability, config.LIPSYNC_MAX_RELIABILITY, "medium", key=_RANK.__getitem__)


def _num(x: Any, nd: int = 4) -> Optional[float]:
    """JSON-safe rounded float (NaN -> None)."""
    if x is None:
        return None
    x = float(x)
    return None if np.isnan(x) else round(x, nd)


def worst_windows(windows: list[dict], k: int) -> list[tuple[float, float]]:
    """The k speech windows with the lowest corr at the clip lag, overlapping ones merged, in time order."""
    scored = sorted((w for w in windows if w["corr"] is not None), key=lambda w: w["corr"])[:k]
    spans = sorted((w["start_s"], w["end_s"]) for w in scored)
    merged: list[list[float]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def write_series(path: Path, a: ClipAnalysis) -> None:
    """artifacts/lipsync_series.json for the frontend chart: { t[], mouth[], audio[], window_lags[], window_corrs[] }.

    mouth = the feature used (config.LIPSYNC_MOUTH_FEATURE), audio = speech-band loudness in dB.
    window_lags = each window's own best lag; window_corrs = corr at the clip's lag (null = not a speech window).
    """
    feat = mouth_features(a.mouth.mouth)[config.LIPSYNC_MOUTH_FEATURE]
    sync = a.syncs.get(config.LIPSYNC_MOUTH_FEATURE)
    data = {
        "t": [round(float(x), 3) for x in a.mouth.t],
        "mouth": [_num(x) for x in feat],
        "audio": [_num(x, 2) for x in a.audio],
        "window_lags": [w["best_lag_s"] for w in a.windows],
        "window_corrs": [w["corr"] for w in a.windows],
        # extra context for the chart (not required by the frontend)
        "mouth_feature": config.LIPSYNC_MOUTH_FEATURE,
        "audio_unit": "dB (300-3000 Hz band)",
        "speech": [int(x) for x in a.speech],
        "window_start_s": [w["start_s"] for w in a.windows],
        "window_end_s": [w["end_s"] for w in a.windows],
        "clip_lag_s": sync["lag_s"] if sync else None,
        "lag_curve": {"lags_s": sync["lags_s"], "corr": sync["curve"]} if sync else None,
    }
    path.write_text(json.dumps(data), encoding="utf-8")


class LipsyncWitness(Witness):
    name = "lipsync"
    label = "Lip-sync"
    implemented = True

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(ctx.inputs_of("video"))

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        model = Path(config.LIPSYNC_FACE_MODEL)   # read at call time, never hard-coded
        if not model.is_file():
            return [self.model_missing(model)]
        videos = ctx.inputs_of("video")[: config.LIPSYNC_MAX_INPUTS]
        findings = []
        for n, inp in enumerate(videos, 1):
            try:
                findings.append(self._analyse(ctx, inp, n, len(videos)))
            except Exception as exc:  # noqa: BLE001 - principle 5: never crash an investigation
                findings.append(self._failed(inp, n, exc))
        return findings

    # --- one video -------------------------------------------------------------

    def _analyse(self, ctx: InvestigationContext, inp, n: int, total: int) -> Finding:
        a = analyze_clip(inp.path)
        s = a.summary
        feat = config.LIPSYNC_MOUTH_FEATURE
        sync = a.syncs.get(feat)
        details: dict[str, Any] = {
            "file": inp.name,
            "rule_status": s["status"],
            "rule_reason": s["reason"],
            "mouth_feature": feat,
            "lag_s": s["lag_s"],
            "corr": s["corr"],
            "prominence": s["prominence"],
            "corr_at_zero_lag": sync["corr_at_zero"] if sync else None,
            "face_fraction": s["face_fraction"],
            "speech_window_fraction": s["speech_window_fraction"],
            "speech_windows": sum(w["is_speech"] for w in a.windows),
            "analysis_fps": a.fps,
            "rotation_deg": a.probe.rotation,
            "lag_search_s": config.LIPSYNC_MAX_LAG_S,
            "contradiction_allowed": config.LIPSYNC_ALLOW_CONTRADICTION,
            "thresholds": {
                "consistent": {"max_abs_lag_s": config.LIPSYNC_CONSISTENT_MAX_LAG_S,
                               "min_corr": config.LIPSYNC_CONSISTENT_MIN_CORR,
                               "min_prominence": config.LIPSYNC_CONSISTENT_MIN_PROMINENCE},
                "contradiction": {"min_abs_lag_s": config.LIPSYNC_CONTRA_MIN_LAG_S,
                                  "max_corr": config.LIPSYNC_CONTRA_MAX_CORR,
                                  "max_prominence": config.LIPSYNC_CONTRA_MAX_PROMINENCE},
            },
        }
        where = f" in {inp.name}" if total > 1 else ""

        if s["status"] == "not_applicable":
            return self.finding(
                n=n, status="not_applicable", reliability="low", details=details,
                title=f"Lip-sync synchrony indicator not applicable{where}",
                summary=f"Not analysed: {s['reason']}.",
                alternatives=["The indicator needs a visible face and an audio track; it says nothing either way here"],
                limitations=["Needs a face in at least half of the frames and an audio track"],
            )
        # Face-size guard: a small face gives a few-pixel lip gap, too noisy to time against the audio.
        face_h = s.get("face_height")
        details["face_height"] = face_h
        details["min_face_height"] = config.LIPSYNC_MIN_FACE_HEIGHT
        if face_h is not None and face_h < config.LIPSYNC_MIN_FACE_HEIGHT:
            evidence = self._series(ctx, inp, n, a)
            return self.finding(
                n=n, status="inconclusive", reliability="low", details=details, evidence=evidence,
                title=f"Lip-sync synchrony indicator: face too small{where}",
                summary=f"The face fills about {face_h:.0%} of the frame height (needs at least "
                        f"{config.LIPSYNC_MIN_FACE_HEIGHT:.0%}), so mouth movement cannot be timed reliably "
                        "against the audio. No synchrony conclusion is drawn.",
                alternatives=ALTERNATIVES,
                limitations=["face too small for a reliable synchrony measurement"] + BASE_LIMITATIONS,
            )
        if s["status"] == "insufficient_signal":
            return self.finding(
                n=n, status="insufficient_signal", reliability="low", details=details,
                title=f"Lip-sync synchrony indicator: not enough speech{where}",
                summary=f"Not enough speech with a visible face to measure synchrony "
                        f"(face in {s['face_fraction']:.0%} of frames).",
                alternatives=["Short, quiet or music-only audio gives no usable speech; this says nothing about the media"],
                limitations=BASE_LIMITATIONS,
            )

        # From here we have numbers: series artifact + evidence.
        evidence = self._series(ctx, inp, n, a)
        nums = (f"best lag {s['lag_s']:+.2f} s, correlation {s['corr']:.2f}, "
                f"peak prominence {s['prominence']:.2f}")
        spans = worst_windows(a.windows, config.LIPSYNC_MAX_LOCATIONS)
        locations = [{"kind": "timestamp", "file_id": inp.file_id, "start_s": s0, "end_s": s1} for s0, s1 in spans]
        calibrated = config.LIPSYNC_ALLOW_CONTRADICTION   # False = uncalibrated: ALWAYS inconclusive

        if s["status"] == "consistent" and calibrated:
            return self.finding(
                n=n, status="consistent", reliability=_cap("medium"), details=details, evidence=evidence,
                title=f"Mouth movement and speech loudness move together{where}",
                summary=f"Across the speech, mouth movement tracks speech loudness ({nums}). "
                        "Synchrony indicator only; it requires human review.",
                alternatives=ALTERNATIVES,
                limitations=BASE_LIMITATIONS,
            )
        if s["status"] == "contradiction" and calibrated:
            return self.finding(
                n=n, status="contradiction", reliability=_cap("medium"), details=details,
                evidence=evidence, locations=locations,
                title=f"Mouth movement does not track the speech audio{where}",
                summary=f"No clear match between mouth movement and speech loudness near zero lag ({nums}). "
                        "Synchrony indicator only; it requires human review.",
                alternatives=ALTERNATIVES,
                limitations=BASE_LIMITATIONS,
            )
        # Rule says inconclusive, or any rule result while the indicator is uncalibrated: weak / unclear.
        held_back = s["status"] in ("consistent", "contradiction")
        summary = f"Mouth movement and speech loudness show only a weak or unclear relationship ({nums})."
        if held_back:
            summary += (" The indicator is not yet calibrated: its thresholds did not separate genuine from "
                        "swapped-audio clips, so every result is reported as inconclusive.")
        summary += " Lowest-agreement segments are marked for human review."
        return self.finding(
            n=n, status="inconclusive", reliability="low", details=details, evidence=evidence, locations=locations,
            title=WEAK_TITLE + (f" ({inp.name})" if total > 1 else ""),
            summary=summary,
            alternatives=ALTERNATIVES,
            limitations=BASE_LIMITATIONS + (
                ["Lip-sync is reported as inconclusive until recalibration (LIPSYNC_ALLOW_CONTRADICTION is off)"]
                if held_back else []),
        )

    def _series(self, ctx: InvestigationContext, inp, n: int, a: ClipAnalysis) -> list[dict]:
        """Write the chart series artifact; return the evidence list pointing at it."""
        name = "lipsync_series.json" if n == 1 else f"lipsync_series_{inp.file_id}.json"
        write_series(ctx.artifact_path(name), a)
        return [{"kind": "artifact", "ref": ctx.artifact_ref(name), "label": "Mouth movement vs speech loudness series"},
                {"kind": "input", "ref": inp.file_id, "label": inp.name}]

    def _failed(self, inp, n: int, exc: Exception) -> Finding:
        return self.finding(
            n=n, status="inconclusive", reliability="low",
            title="Lip-sync synchrony indicator could not be computed",
            summary=f"The analysis of {inp.name} failed, so no synchrony measurement is available.",
            details={"file": inp.name, "error": f"{type(exc).__name__}: {exc}"},
            alternatives=["No synchrony analysis was completed, so this finding says nothing about the media"],
            limitations=[f"Analysis error: {type(exc).__name__}: {exc}"],
        )

    def model_missing(self, model: Path) -> Finding:
        """No face model -> no analysis. Inconclusive + low: absence of a tool says nothing about the media."""
        return self.finding(
            status="inconclusive",
            reliability="low",
            title="Lip-sync synchrony indicator not run: face model not found",
            summary="The face landmark model needed for the lip-sync synchrony indicator is not installed, "
                    "so no audio-visual synchrony analysis was performed.",
            details={"model_path": str(model)},
            alternatives=["No synchrony analysis was performed, so this finding says nothing about the media"],
            limitations=[
                "face model not found",
                f"Expected the MediaPipe Face Landmarker model at {model} (see data/models/README.md)",
            ],
        )


if __name__ == "__main__":
    run_cli(LipsyncWitness())
