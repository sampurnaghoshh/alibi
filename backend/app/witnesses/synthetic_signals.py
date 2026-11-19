"""
Synthetic-generation signals (PS04: synthetic images, synthetic elements / temporal consistency in
video, synthetic or cloned speech). Owner: Sampurna. Family: pixels. Max reliability: medium.

Signal-level indicators only, computed with numpy / scipy / OpenCV. No trained classifier, so they are
evidence indicators for a human, never a verdict. Thresholds live in config.py (SYNTH_*).

IMAGES (one finding per image)
  1. Frequency spectrum: 2D FFT of the luminance. Periodic peaks off the natural 1/f falloff are typical
     of the upsampling layers in diffusion / GAN generators. JPEG's own 8x8 block grid is excluded for
     JPEG files. Artifact: the log-spectrum with the peaks circled.
  2. Noise residual: high-pass residual (picture minus 3x3 median) in the flattest blocks. Camera sensors
     leave noise everywhere; generated pictures are often noise-free in flat areas. Artifact: noise map.
  3. Camera signature: JPEG quantization tables (camera-specific vs standard software tables vs none for
     PNG/WebP) and camera make/model/lens fields. Context only: it never counts as a pixel signal alone.
  Status: contradiction (medium) only when BOTH pixel signals are strong and no camera fields exist;
  any pixel signal -> inconclusive (low) "weak synthetic signal"; none -> consistent (low).

VIDEO: temporal consistency of sampled frames (frame-difference spikes = abrupt changes / cuts,
  brightness flicker). Artifact: frame-change graph; spikes become timestamp locations. Never a
  contradiction (edits and cuts are normal in genuine footage). The soundtrack gets the audio check.

AUDIO: cloned-speech indicators: noise floor (near-digital silence), a hard bandwidth limit (audio made
  at a low sample rate and upsampled, as many speech generators do), spectral flatness. Artifact:
  spectrum plot. Never a contradiction (phone calls, noise suppression and codecs do the same).

Run alone (from backend/):
    python -m app.witnesses.synthetic_signals --file ../demo/ai_test/image.png
"""

from __future__ import annotations

import json
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

from app import config
from app.media import load_image
from app.schemas import Finding
from app.witnesses.base import InputRef, InvestigationContext, Witness, run_cli

IMAGE_ALTERNATIVES = [
    "Screenshots, screen recordings and graphics have no sensor noise and no camera fields",
    "Heavy compression (messaging apps, social platforms) removes noise and can add periodic patterns",
    "Editing apps, filters, beauty modes and AI upscalers smooth noise and resample the picture",
    "Resizing or re-encoding by a platform replaces the camera's JPEG tables with standard ones",
]
VIDEO_ALTERNATIVES = [
    "Cuts, transitions and stabilisation in normal editing cause abrupt frame changes",
    "Fast camera movement, flashes, flickering lights and auto-exposure change brightness quickly",
    "Dropped frames from a slow phone or a screen recording look like jumps",
]
AUDIO_ALTERNATIVES = [
    "Phone calls, voice notes and Bluetooth headsets limit the bandwidth of genuine speech",
    "Noise suppression in phones and meeting apps removes the background noise floor",
    "Re-encoding by messaging apps and platforms changes the spectrum",
]
SPLICE_ALTERNATIVES = [
    "Different lighting, shadows or exposure across the scene change the local noise level",
    "Focus blur and depth of field make parts of a genuine photo smoother than the rest",
    "Uneven compression, denoising or beauty filters change noise in some regions only",
]
LIMITATIONS = [
    "Signal-level indicators without a trained classifier; not calibrated on a large dataset",
    "Many generators, and any re-encoding, leave none of these signals, so their absence is not proof of anything",
]
CORAL = (217, 107, 104)


# ---------------------------------------------------------------------------------------- images

def _luma(img: Image.Image) -> np.ndarray:
    """Float luminance (0-255), centre-cropped to at most SYNTH_FFT_MAX_SIDE (cropping keeps periodic
    traces; resizing would destroy them)."""
    y = np.asarray(img.convert("L"), dtype=np.float32)
    s = config.SYNTH_FFT_MAX_SIDE
    h, w = y.shape
    top, left = max(0, (h - s) // 2), max(0, (w - s) // 2)
    return y[top: top + s, left: left + s]


def spectrum_analysis(y: np.ndarray, jpeg: bool) -> tuple[dict, Image.Image]:
    """Periodic peaks in the 2D spectrum. Returns (numbers, log-spectrum image with peaks circled)."""
    import cv2  # noqa: PLC0415
    from scipy.ndimage import maximum_filter  # noqa: PLC0415

    n = min(y.shape) // 2 * 2
    y = y[:n, :n]
    # Spectrum of the noise residual (picture minus 3x3 median): generator up-sampling grids show up as
    # periodic peaks there, while the picture content (which dominates the plain spectrum) is removed.
    resid0 = y - cv2.medianBlur(np.clip(y, 0, 255).astype(np.uint8), 3).astype(np.float32)
    win = np.outer(np.hanning(n), np.hanning(n))
    mag = np.log(np.abs(np.fft.fftshift(np.fft.fft2(resid0 * win))) ** 2 + 1e-6)
    c = n // 2
    vv, uu = np.mgrid[-c:c, -c:c]
    r = np.hypot(uu, vv).astype(int)
    radial = np.bincount(r.ravel(), mag.ravel()) / np.maximum(np.bincount(r.ravel()), 1)
    resid = mag - radial[r]                                   # what's left after the natural 1/f falloff
    mask = (r > config.SYNTH_FFT_MIN_RADIUS * c) & (r < c) & (np.abs(uu) > 2) & (np.abs(vv) > 2)
    if jpeg:                                                  # JPEG's own 8x8 block grid is not a generator trace
        step = n / 8
        near = lambda a: np.abs(a - np.round(a / step) * step) <= 3   # noqa: E731
        mask &= ~(near(uu) & near(vv))
    v = resid[mask]
    centre = float(np.median(v))
    sd = 1.4826 * float(np.median(np.abs(v - centre))) or 1.0     # robust spread (MAD)
    z = (resid - centre) / sd
    peaks = mask & (z >= config.SYNTH_FFT_PEAK_Z) & (z == maximum_filter(z, size=5))
    py, px = np.nonzero(peaks)
    order = np.argsort(-z[py, px])[:24]
    py, px = py[order], px[order]
    periods = sorted({round(n / max(float(np.hypot(px[i] - c, py[i] - c)), 1e-6), 1) for i in range(len(px))})

    disp = mag - mag.min()
    disp = (255 * disp / (disp.max() or 1)).astype(np.uint8)
    im = Image.fromarray(disp).convert("RGB")
    draw = ImageDraw.Draw(im)
    rad = max(4, n // 80)
    for x, yy in zip(px, py):
        draw.ellipse([x - rad, yy - rad, x + rad, yy + rad], outline=CORAL, width=max(1, n // 400))
    if n > 768:
        im = im.resize((768, 768))
    return ({"fft_size": int(n), "peak_count": int(peaks.sum()),
             "max_peak_z": round(float(z[mask].max()), 1) if mask.any() else 0.0,
             "periods_px": periods[:8]}, im)


def noise_analysis(y: np.ndarray) -> tuple[dict, Image.Image]:
    """High-pass residual in the flattest blocks: genuine sensors leave noise even in flat areas."""
    import cv2  # noqa: PLC0415

    u8 = np.clip(y, 0, 255).astype(np.uint8)
    resid = y - cv2.medianBlur(u8, 3).astype(np.float32)
    b = config.SYNTH_NOISE_BLOCK_PX
    h, w = y.shape
    bh, bw = h // b, w // b
    if not (bh and bw):
        raise ValueError("picture too small for the noise analysis")
    blocks = lambda a: a[: bh * b, : bw * b].reshape(bh, b, bw, b)   # noqa: E731
    std = blocks(resid).std(axis=(1, 3))
    mean = blocks(y).mean(axis=(1, 3))
    gy, gx = np.gradient(cv2.GaussianBlur(y, (5, 5), 0))
    grad = blocks(np.hypot(gx, gy)).mean(axis=(1, 3))
    valid = (mean > 16) & (mean < 240)                          # skip clipped black / white areas
    if valid.sum() < 8:
        valid = np.ones_like(valid, dtype=bool)
    flat = grad[valid] <= np.quantile(grad[valid], config.SYNTH_NOISE_FLAT_QUANTILE)
    flat_std = std[valid][flat]
    flat_noise = float(np.median(flat_std))
    near_zero = float((flat_std < config.SYNTH_NOISE_ZERO_STD).mean())

    m = std / (np.quantile(std, 0.98) or 1)
    im = Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8)).resize((bw * 8, bh * 8), Image.NEAREST)
    return {"flat_noise_std": round(flat_noise, 3), "flat_blocks_near_zero": round(near_zero, 3),
            "median_noise_std": round(float(np.median(std)), 3), "blocks": int(bh * bw)}, im.convert("RGB")


# Standard IJG / libjpeg luminance table (quality 50); software encoders scale it.
IJG_LUMA = np.array([16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
                     14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
                     49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99])
ZIGZAG = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14,
          21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53,
          60, 61, 54, 47, 55, 62, 63]


def _ijg_quality(table) -> Optional[int]:
    """The IJG quality whose scaled standard table equals this one (zigzag or natural order), else None."""
    t = np.array(list(table), dtype=int)
    if t.size != 64:
        return None
    candidates = (t, t[np.argsort(ZIGZAG)])
    for q in range(1, 101):
        scale = 5000 / q if q < 50 else 200 - 2 * q
        std = np.clip((IJG_LUMA * scale + 50) // 100, 1, 255)
        if any(np.array_equal(c, std) for c in candidates):
            return q
    return None


def camera_signature(path) -> dict:
    with Image.open(path) as im:
        fmt = im.format
        q = getattr(im, "quantization", None) or {}
        exif = im.getexif()
        sub = exif.get_ifd(0x8769) if exif else {}
        make, model = exif.get(271), exif.get(272)
        lens = sub.get(42036) if sub else None
    ijg = _ijg_quality(q[0]) if 0 in q else None
    tables = "none" if not q else ("standard software table (IJG quality %d)" % ijg if ijg else "custom (camera-style)")
    return {"format": fmt, "jpeg_tables": tables,
            "make": str(make).strip("\x00 ") if make else None, "model": str(model).strip("\x00 ") if model else None,
            "lens": str(lens).strip("\x00 ") if lens else None,
            "camera_fields": bool(make or model or lens)}


def _grade(value: float, weak: float, strong: float, lower_is_worse: bool = False) -> str:
    if lower_is_worse:
        return "strong" if value <= strong else "weak" if value <= weak else "none"
    return "strong" if value >= strong else "weak" if value >= weak else "none"


def image_signals(spec: dict, noise: dict, cam: dict) -> list[dict]:
    peaks_ok = spec["peak_count"] >= config.SYNTH_FFT_MIN_PEAKS
    s_spec = _grade(spec["max_peak_z"], config.SYNTH_FFT_WEAK_Z, config.SYNTH_FFT_STRONG_Z) if peaks_ok else "none"
    s_noise = _grade(noise["flat_noise_std"], config.SYNTH_NOISE_WEAK_STD, config.SYNTH_NOISE_STRONG_STD,
                     lower_is_worse=True)
    return [
        {"name": "spectrum", "strength": s_spec,
         "reason": f"periodic spectral peaks ({spec['peak_count']}, strongest z={spec['max_peak_z']})"},
        {"name": "noise", "strength": s_noise,
         "reason": f"almost no sensor noise in flat areas (residual {noise['flat_noise_std']:.2f})"},
        {"name": "camera", "strength": "none" if cam["camera_fields"] else "context",
         "reason": "no camera make/model/lens fields" + ("" if cam["jpeg_tables"].startswith("custom")
                                                        else f", JPEG tables: {cam['jpeg_tables']}")},
    ]


def combine(signals: list[dict]) -> tuple[str, str]:
    """(strength, status). Contradiction only when BOTH pixel signals are strong and no camera fields."""
    pixel = [s for s in signals if s["name"] != "camera"]
    strong = sum(s["strength"] == "strong" for s in pixel)
    any_px = any(s["strength"] != "none" for s in pixel)
    no_camera = any(s["name"] == "camera" and s["strength"] == "context" for s in signals)
    if strong >= config.SYNTH_MIN_STRONG_SIGNALS and no_camera:
        return "strong", "contradiction"
    if any_px:
        return "weak", "inconclusive"
    return "none", "consistent"


def splice_analysis(img: Image.Image) -> tuple[dict, Image.Image]:
    """Block-wise noise-residual inconsistency: blocks whose noise level differs strongly from the rest
    (robust z of log residual sd, flatter blocks only, so edges and texture do not dominate). The largest
    connected group of inconsistent blocks is the candidate region; its error level (JPEG re-save, as ELA)
    is compared with the rest of the picture."""
    import io  # noqa: PLC0415

    import cv2  # noqa: PLC0415
    from scipy.ndimage import label  # noqa: PLC0415

    y = np.asarray(img.convert("L"), dtype=np.float32)
    b = config.SPLICE_BLOCK_PX
    h, w = y.shape
    bh, bw = h // b, w // b
    if bh < 4 or bw < 4:
        raise ValueError("picture too small for the splice check")
    blocks = lambda a: a[: bh * b, : bw * b].reshape(bh, b, bw, b)   # noqa: E731
    resid = y - cv2.medianBlur(np.clip(y, 0, 255).astype(np.uint8), 3).astype(np.float32)
    lstd = np.log(blocks(resid).std(axis=(1, 3)) + 0.05)
    gy, gx = np.gradient(cv2.GaussianBlur(y, (5, 5), 0))
    grad = blocks(np.hypot(gx, gy)).mean(axis=(1, 3))
    mean = blocks(y).mean(axis=(1, 3))
    usable = (grad <= np.quantile(grad, config.SPLICE_FLAT_QUANTILE)) & (mean > 12) & (mean < 243)
    if usable.sum() < 8:
        usable = np.ones_like(usable, dtype=bool)
    med = float(np.median(lstd[usable]))
    mad = 1.4826 * float(np.median(np.abs(lstd[usable] - med))) or 0.05
    z = (lstd - med) / max(mad, 0.05)
    odd = usable & (np.abs(z) >= config.SPLICE_Z)
    lab, k = label(odd)
    best = None
    for i in range(1, k + 1):
        comp = lab == i
        size = int(comp.sum())
        if size < config.SPLICE_MIN_BLOCKS or size > config.SPLICE_MAX_FRACTION * bh * bw:
            continue
        score = float(np.abs(z[comp]).mean()) * np.sqrt(size)
        if best is None or score > best[0]:
            best = (score, comp, size)
    bbox, ela_ratio, region_z, size = None, None, 0.0, 0
    if best:
        _, comp, size = best
        rows, cols = np.where(comp)
        x0, y0 = cols.min() * b / w, rows.min() * b / h
        x1, y1 = min((cols.max() + 1) * b / w, 1.0), min((rows.max() + 1) * b / h, 1.0)
        bbox = [round(float(x0), 4), round(float(y0), 4),
                float(np.floor((x1 - x0) * 1e4) / 1e4), float(np.floor((y1 - y0) * 1e4) / 1e4)]
        region_z = round(float(np.abs(z[comp]).mean()), 1)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=config.ELA_JPEG_QUALITY)
        buf.seek(0)
        err = np.abs(np.asarray(img.convert("RGB"), np.float32)
                     - np.asarray(Image.open(buf).convert("RGB"), np.float32)).mean(axis=2)
        e = blocks(err).mean(axis=(1, 3))
        ela_ratio = round(float(e[comp].mean() / (e[~comp].mean() or 1e-6)), 2)

    heat = np.clip(np.abs(z) / (2 * config.SPLICE_Z), 0, 1)
    rgb = np.stack([heat * 217 + (1 - heat) * 21, heat * 107 + (1 - heat) * 25, heat * 104 + (1 - heat) * 29], axis=-1)
    im = Image.fromarray(rgb.astype(np.uint8)).resize((bw * 12, bh * 12), Image.NEAREST)
    if bbox:
        d = ImageDraw.Draw(im)
        iw, ih = im.size
        d.rectangle([bbox[0] * iw, bbox[1] * ih, (bbox[0] + bbox[2]) * iw, (bbox[1] + bbox[3]) * ih],
                    outline=(232, 236, 239), width=3)
    return {"bbox": bbox, "region_blocks": size, "region_z": region_z, "ela_ratio": ela_ratio,
            "blocks": int(bh * bw), "block_px": b}, im


# ---------------------------------------------------------------------------------------- video

def temporal_analysis(path) -> dict:
    import cv2  # noqa: PLC0415

    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(fps / config.SYNTH_VIDEO_FPS)))
        t, diff, bright, prev, i = [], [], [], None, 0
        while i / fps <= config.SYNTH_VIDEO_MAX_S:
            ok = cap.grab()
            if not ok:
                break
            if i % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                g = cv2.resize(g, (160, max(1, int(160 * g.shape[0] / g.shape[1])))).astype(np.float32)
                bright.append(float(g.mean()))
                if prev is not None:
                    t.append(round(i / fps, 2))
                    diff.append(float(np.abs(g - prev).mean()))
                prev = g
            i += 1
    finally:
        cap.release()
    if len(diff) < 3:
        raise ValueError("too few frames for a temporal check")
    d = np.array(diff)
    med = float(np.median(d))
    thr = max(config.SYNTH_SPIKE_MIN, med * config.SYNTH_SPIKE_RATIO)
    spikes = [t[k] for k in range(len(d)) if d[k] >= thr]
    db = np.diff(np.array(bright))
    flips = (db[:-1] * db[1:] < 0) & (np.abs(db[:-1]) > config.SYNTH_FLICKER_MIN) & (np.abs(db[1:]) > config.SYNTH_FLICKER_MIN)
    flicker = float(flips.mean()) if flips.size else 0.0
    return {"t": t, "diff": [round(x, 2) for x in diff], "median_diff": round(med, 2), "spike_threshold": round(thr, 2),
            "spikes_s": spikes, "flicker_rate": round(flicker, 3), "sample_fps": round(fps / step, 2)}


def line_chart(xs, ys, marks, title: str, xlabel: str, hline: Optional[float] = None, mark_label: str = "") -> Image.Image:
    """Small dark line chart (PIL, no plotting library)."""
    W, H, L, R, T, B = 960, 300, 56, 16, 34, 40
    im = Image.new("RGB", (W, H), (21, 25, 29))
    dr = ImageDraw.Draw(im)
    dr.text((L, 10), title, fill=(232, 236, 239))
    if not len(xs):
        return im
    x0, x1 = float(min(xs)), float(max(xs)) or 1.0
    y0, y1 = float(min(min(ys), hline if hline is not None else min(ys))), float(max(max(ys), hline or 0)) or 1.0
    if y1 == y0:
        y1 = y0 + 1
    px = lambda x: L + (x - x0) / ((x1 - x0) or 1) * (W - L - R)   # noqa: E731
    py = lambda y: H - B - (y - y0) / (y1 - y0) * (H - T - B)      # noqa: E731
    dr.rectangle([L, T, W - R, H - B], outline=(48, 56, 64))
    if hline is not None:
        dr.line([(L, py(hline)), (W - R, py(hline))], fill=(146, 157, 167), width=1)
    dr.line([(px(x), py(y)) for x, y in zip(xs, ys)], fill=(131, 182, 209), width=2)
    for m in marks:
        dr.line([(px(m), T), (px(m), H - B)], fill=CORAL, width=2)
    dr.text((L, H - B + 8), f"{x0:g}", fill=(146, 157, 167))
    dr.text((W - R - 60, H - B + 8), f"{x1:g} {xlabel}", fill=(146, 157, 167))
    dr.text((4, T), f"{y1:.0f}", fill=(146, 157, 167))
    dr.text((4, H - B - 12), f"{y0:.0f}", fill=(146, 157, 167))
    if mark_label and marks:
        dr.text((W - R - 260, 10), mark_label, fill=CORAL)
    return im


# ---------------------------------------------------------------------------------------- audio

def audio_analysis(path) -> dict:
    from scipy.signal import welch  # noqa: PLC0415

    from app.witnesses.enf import load_audio  # noqa: PLC0415 - the same native-rate loader as ENF

    y, sr = load_audio(path, max_s=config.SYNTH_AUDIO_MAX_S)
    if y.size < sr // 2:
        raise ValueError("less than 0.5 s of audio")
    frame = max(1, int(sr * 0.05))
    k = y.size // frame
    rms = np.sqrt((y[: k * frame].reshape(k, frame) ** 2).mean(axis=1) + 1e-12)
    rms_db = 20 * np.log10(rms + 1e-12)
    floor_db = float(np.percentile(rms_db, 5))
    peak_db = float(np.percentile(rms_db, 95))

    f, p = welch(y, fs=sr, nperseg=min(4096, y.size))
    pdb = 10 * np.log10(p + 1e-20)
    nyq = sr / 2
    # Hard cutoff: the largest drop between the 500 Hz just below and just above a candidate frequency.
    best, cutoff = 0.0, None
    for fc in np.arange(3000, nyq - 600, 250):
        lo = pdb[(f >= fc - 500) & (f < fc)]
        hi = pdb[(f >= fc) & (f < fc + 500)]
        if lo.size and hi.size:
            drop = float(np.median(lo) - np.median(hi))
            if drop > best:
                best, cutoff = drop, float(fc)
    s = np.abs(np.fft.rfft(y[: k * frame].reshape(k, frame) * np.hanning(frame), axis=1)) + 1e-12
    flat = np.exp(np.log(s).mean(axis=1)) / s.mean(axis=1)
    loud = rms_db >= np.percentile(rms_db, 50)
    flatness = float(np.median(flat[loud])) if loud.any() else float(np.median(flat))
    return {"sample_rate": int(sr), "noise_floor_dbfs": round(floor_db, 1), "loud_level_dbfs": round(peak_db, 1),
            "cutoff_hz": round(cutoff) if cutoff else None, "cutoff_drop_db": round(best, 1),
            "spectral_flatness": round(flatness, 4),
            "spectrum": {"f": [round(float(x)) for x in f[::4]], "db": [round(float(x), 1) for x in pdb[::4]]}}


def audio_signals(a: dict) -> list[dict]:
    hard_cut = (a["cutoff_hz"] is not None and a["cutoff_drop_db"] >= config.SYNTH_CUTOFF_DROP_DB
                and a["cutoff_hz"] <= config.SYNTH_CUTOFF_MAX_HZ and a["sample_rate"] / 2 - a["cutoff_hz"] > 2000)
    return [
        {"name": "noise_floor", "strength": "weak" if a["noise_floor_dbfs"] <= config.SYNTH_DIGITAL_SILENCE_DBFS else "none",
         "reason": f"near-digital silence between sounds (floor {a['noise_floor_dbfs']} dBFS)"},
        {"name": "bandwidth", "strength": "weak" if hard_cut else "none",
         "reason": f"hard bandwidth limit at {(a['cutoff_hz'] or 0) / 1000:.1f} kHz ({a['cutoff_drop_db']} dB drop) "
                   f"in a {a['sample_rate'] / 1000:g} kHz file"},
        {"name": "flatness", "strength": "weak" if a["spectral_flatness"] <= config.SYNTH_FLATNESS_MAX else "none",
         "reason": f"unusually tonal, clean spectrum (flatness {a['spectral_flatness']})"},
    ]


# ---------------------------------------------------------------------------------------- witness

class SyntheticSignalsWitness(Witness):
    name = "synthetic_signals"
    label = "Synthetic signals"
    implemented = True

    def applicable(self, ctx: InvestigationContext) -> bool:
        return any(i.kind in ("image", "video", "audio") for i in ctx.inputs)

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        out: list[Finding] = []
        for inp in [i for i in ctx.inputs if i.kind in ("image", "video", "audio")][: config.SYNTH_MAX_INPUTS]:
            jobs = {"image": [self._image, self._splice], "video": [self._video, self._audio], "audio": [self._audio]}[inp.kind]
            for job in jobs:
                n = len(out) + 1
                try:
                    f = job(ctx, inp, n)
                except Exception as exc:  # noqa: BLE001 - principle 5: never crash the investigation
                    f = self._failed(inp, n, job.__name__.strip("_"), exc)
                if f is not None:
                    out.append(f)
        return out

    def _failed(self, inp: InputRef, n: int, what: str, exc: Exception) -> Optional[Finding]:
        if what == "audio" and inp.kind == "video":
            return None                      # a video without a soundtrack: nothing to say about audio
        return self.finding(
            n=n, status="inconclusive", reliability="low",
            title=f"Synthetic-signal {what} check could not be run",
            summary=f"The {what} check of {inp.name} could not be completed, so it gives no indication either way.",
            alternatives={"image": IMAGE_ALTERNATIVES, "video": VIDEO_ALTERNATIVES,
                          "splice": SPLICE_ALTERNATIVES}.get(what, AUDIO_ALTERNATIVES),
            limitations=[f"Error: {type(exc).__name__}: {exc}", *LIMITATIONS],
            details={"file_id": inp.file_id, "check": what, "strength": "none", "reasons": [],
                     "error": f"{type(exc).__name__}: {exc}"},
        )

    # -- images
    def _image(self, ctx: InvestigationContext, inp: InputRef, n: int) -> Finding:
        img = load_image(inp.path)
        y = _luma(img)
        cam = camera_signature(inp.path)
        spec, spec_im = spectrum_analysis(y, jpeg=cam["format"] == "JPEG")
        noise, noise_im = noise_analysis(y)
        signals = image_signals(spec, noise, cam)
        strength, status = combine(signals)
        reasons = [s["reason"] for s in signals if s["strength"] in ("weak", "strong")]
        if strength != "none" and not cam["camera_fields"]:
            reasons.append(signals[2]["reason"])
        spec_name, noise_name = f"synthetic_spectrum_{inp.file_id}.png", f"synthetic_noise_{inp.file_id}.png"
        spec_im.save(ctx.artifact_path(spec_name))
        noise_im.save(ctx.artifact_path(noise_name))

        if status == "contradiction":
            title = "Several pixel signals typical of generated images agree"
            summary = (f"{inp.name}: {'; '.join(reasons)}. Together these are typical of generated or heavily "
                       "processed images. An evidence indicator that requires human review.")
        elif status == "inconclusive":
            title = "Weak synthetic signal in the pixels"
            summary = f"{inp.name}: {'; '.join(reasons)}. One signal on its own is common in ordinary processed images."
        else:
            title = "No synthetic-generation signal found in the pixels"
            summary = (f"{inp.name}: no periodic spectral peaks and normal noise in flat areas "
                       f"(residual {noise['flat_noise_std']:.2f}). This does not show the picture is unedited.")
        limitations = list(LIMITATIONS)
        if cam["format"] != "JPEG":
            limitations.append(f"The file is {cam['format']}, which never carries camera JPEG tables")
        return self.finding(
            n=n, status=status, reliability="medium" if status == "contradiction" else "low",
            title=title, summary=summary, alternatives=IMAGE_ALTERNATIVES, limitations=limitations,
            details={"file_id": inp.file_id, "check": "image", "strength": strength, "reasons": reasons,
                     "signals": signals, "spectrum": spec, "noise": noise, "camera": cam,
                     "spectrum_image": ctx.artifact_ref(spec_name), "noise_map": ctx.artifact_ref(noise_name)},
            evidence=[{"kind": "artifact", "ref": ctx.artifact_ref(spec_name), "label": "Log frequency spectrum (peaks circled)"},
                      {"kind": "artifact", "ref": ctx.artifact_ref(noise_name), "label": "Noise-residual map (brighter = more noise)"}],
            locations=[{"kind": "region", "file_id": inp.file_id, "frame_s": None, "bbox": [0.0, 0.0, 1.0, 1.0]}],
        )

    # -- splice / compositing localisation (images)
    def _splice(self, ctx: InvestigationContext, inp: InputRef, n: int) -> Finding:
        sp, im = splice_analysis(load_image(inp.path))
        name = f"synthetic_splice_{inp.file_id}.png"
        im.save(ctx.artifact_path(name))
        agrees = sp["bbox"] is not None and (sp["ela_ratio"] or 0) >= config.SPLICE_ELA_AGREE_RATIO
        if sp["bbox"]:
            title = "Possible pasted-in region (noise level differs from the rest)"
            summary = (f"{inp.name}: {sp['region_blocks']} blocks with a noise level unlike the rest of the picture "
                       f"(robust z {sp['region_z']}); the error level there is {sp['ela_ratio']}x the rest"
                       + (", which agrees with error-level analysis" if agrees else "")
                       + ". An indicator for a human reviewer, not a verdict.")
        else:
            title = "No region with inconsistent noise found"
            summary = (f"{inp.name}: no group of blocks has a noise level strongly unlike the rest. "
                       "This does not show the picture is unedited.")
        return self.finding(
            n=n, status="inconclusive", reliability="medium" if agrees else "low", title=title, summary=summary,
            alternatives=SPLICE_ALTERNATIVES,
            limitations=[*LIMITATIONS, "Block-wise statistics: small pasted objects and regions re-compressed "
                                       "together with the rest are missed"],
            details={"file_id": inp.file_id, "check": "splice", "strength": "none", "reasons": [],
                     "splice": sp, "agrees_with_ela": agrees, "inconsistency_map": ctx.artifact_ref(name)},
            evidence=[{"kind": "artifact", "ref": ctx.artifact_ref(name),
                       "label": "Noise-inconsistency map (box = most inconsistent region)"}],
            locations=([{"kind": "region", "file_id": inp.file_id, "frame_s": None, "bbox": sp["bbox"]}]
                       if sp["bbox"] else []),
        )

    # -- video
    def _video(self, ctx: InvestigationContext, inp: InputRef, n: int) -> Finding:
        tm = temporal_analysis(inp.path)
        spikes = tm["spikes_s"]
        flicker = tm["flicker_rate"] >= config.SYNTH_FLICKER_RATE
        reasons = ([f"{len(spikes)} abrupt frame change(s) at {', '.join(f'{s:.1f} s' for s in spikes[:5])}"] if spikes else []) + \
                  ([f"brightness flicker in {tm['flicker_rate']:.0%} of frames"] if flicker else [])
        strength = "weak" if reasons else "none"
        name = f"synthetic_temporal_{inp.file_id}"
        line_chart(tm["t"], tm["diff"], spikes, "Frame-to-frame change (mean absolute difference)", "s",
                   hline=tm["spike_threshold"], mark_label="abrupt change").save(ctx.artifact_path(name + ".png"))
        ctx.artifact_path(name + ".json").write_text(json.dumps(tm), encoding="utf-8")
        if reasons:
            title = "Temporal discontinuities in the video"
            summary = f"{inp.name}: {'; '.join(reasons)}. Abrupt changes can come from splices or generated segments, and equally from normal cuts."
        else:
            title = "No temporal discontinuity found"
            summary = (f"{inp.name}: frame-to-frame change stays smooth (median {tm['median_diff']}, no spike above "
                       f"{tm['spike_threshold']}). This does not show the clip is unedited.")
        half = 0.5 / max(tm["sample_fps"], 0.1)
        return self.finding(
            n=n, status="inconclusive", reliability="low", title=title, summary=summary,
            alternatives=VIDEO_ALTERNATIVES,
            limitations=[*LIMITATIONS, f"Frames sampled at {tm['sample_fps']} fps; very short glitches can fall between samples"],
            details={"file_id": inp.file_id, "check": "video", "strength": strength, "reasons": reasons,
                     "median_diff": tm["median_diff"], "spike_threshold": tm["spike_threshold"], "spikes_s": spikes,
                     "flicker_rate": tm["flicker_rate"], "sample_fps": tm["sample_fps"],
                     "frame_change_chart": ctx.artifact_ref(name + ".png"), "series": ctx.artifact_ref(name + ".json")},
            evidence=[{"kind": "artifact", "ref": ctx.artifact_ref(name + ".png"), "label": "Frame-change graph"},
                      {"kind": "artifact", "ref": ctx.artifact_ref(name + ".json"), "label": "Frame-change series"}],
            locations=[{"kind": "timestamp", "file_id": inp.file_id, "start_s": max(0.0, s - half), "end_s": s + half}
                       for s in spikes[:10]],
        )

    # -- audio (audio files, and the soundtrack of videos)
    def _audio(self, ctx: InvestigationContext, inp: InputRef, n: int) -> Finding:
        a = audio_analysis(inp.path)
        signals = audio_signals(a)
        hits = [s for s in signals if s["strength"] != "none"]
        # One indicator alone is common in ordinary recordings (e.g. WhatsApp audio is cut at about 5.5 kHz),
        # so it is shown in the details but never raised as a signal.
        strength = ("strong" if len(hits) >= config.SYNTH_AUDIO_STRONG_COUNT
                    else "weak" if len(hits) >= config.SYNTH_AUDIO_WEAK_COUNT else "none")
        reasons = [s["reason"] for s in hits] if strength != "none" else []
        name = f"synthetic_audio_{inp.file_id}.png"
        sp = a.pop("spectrum")
        line_chart([x / 1000 for x in sp["f"]], sp["db"], [a["cutoff_hz"] / 1000] if any(s["name"] == "bandwidth" for s in hits) else [],
                   "Audio spectrum (dB)", "kHz", mark_label="hard cutoff").save(ctx.artifact_path(name))
        what = "the soundtrack of " + inp.name if inp.kind == "video" else inp.name
        if strength == "strong":
            title = "Several cloned-speech indicators in the audio"
        elif strength == "weak":
            title = "Weak cloned-speech indicator in the audio"
        else:
            title = "No cloned-speech indicator in the audio"
        if reasons:
            summary = f"{what}: {'; '.join(reasons)}. These also occur in phone calls and noise-suppressed recordings."
        elif hits:
            summary = (f"{what}: only one indicator ({hits[0]['reason']}), which is common in ordinary recordings "
                       "and messaging apps, so it is not counted. This does not show the voice is unaltered.")
        else:
            summary = (f"{what}: natural noise floor ({a['noise_floor_dbfs']} dBFS) and no hard bandwidth limit. "
                       "This does not show the voice is unaltered.")
        return self.finding(
            n=n, status="inconclusive", reliability="medium" if strength == "strong" else "low",
            title=title, summary=summary, alternatives=AUDIO_ALTERNATIVES,
            limitations=[*LIMITATIONS, "Audio indicators never contradict on their own: genuine recording chains produce them too"],
            details={"file_id": inp.file_id, "check": "audio", "strength": strength, "reasons": reasons,
                     "signals": signals, **a, "spectrum_chart": ctx.artifact_ref(name)},
            evidence=[{"kind": "artifact", "ref": ctx.artifact_ref(name), "label": "Audio spectrum"}],
        )


if __name__ == "__main__":
    run_cli(SyntheticSignalsWitness())
