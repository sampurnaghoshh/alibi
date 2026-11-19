"""
ENF power-grid hum witness (CLAUDE.md §6.6). Owner: Nooman.   Family: signal. Max reliability: medium.

Hour-1 go/no-go test (scripts/test_enf.py) was NO-GO: mains hum was found in 0 of 5 iPhone clips, so
config.ENF_GO is False and this witness is DISPLAY-ONLY: it always reports insufficient_signal (low),
even when some hum is measured, and never consistent or contradiction. The per-frequency numbers and
the spectrogram are still shown so a reviewer can look.

Analysis (shared with scripts/test_enf.py):
  - load_audio():  mono audio at the file's NATIVE sample rate (ffmpeg -> WAV), first ENF_MAX_SPAN_S s.
  - analyse():     STFT (Hann, 50% overlap, 4-8 s window, >= 2 s for short clips). For 50/60/100/120 Hz:
                   per-frame SNR = mean power in f +/- 0.5 Hz vs the rest of f +/- 2.5 Hz.
                   Detected if SNR >= 6 dB in >= 60% of frames.
  - save_spectrogram(): PNG zoomed to 40-130 Hz with the 50/100 (and 60/120) Hz lines marked.

If ENF_GO were True (future), the §6.6 rules apply, capped at medium:
  50/100 Hz detected + location claim in India -> consistent; 60/120 Hz detected + claim in a 50 Hz
  country -> contradiction; anything else -> insufficient_signal.

Run alone (from backend/):
    python -m app.witnesses.enf --file ../demo/case_b/talking.mp4 --caption "Speech in Bengaluru today"
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import numpy as np

from app import config
from app.schemas import Finding
from app.witnesses.base import InputRef, InvestigationContext, Witness, run_cli

DIRECT_EXTS = {".wav", ".flac"}   # soundfile reads these without ffmpeg

ALTERNATIVES = [
    "Phone noise suppression and low-cut filters remove mains hum from recordings",
    "Outdoor or battery-only recordings often have no mains hum at all",
    "Imported or unusual equipment can carry a different grid frequency than the location",
]
NO_GO_LIMITATION = ("Hour-1 test detected mains hum in 0 of 5 iPhone clips, so ENF is display-only "
                    "and never used as a verdict.")
_RANK = {"low": 0, "medium": 1, "high": 2}


def target_freqs() -> list[int]:
    """50/100 Hz (50 Hz grids, e.g. India) and 60/120 Hz (60 Hz grids), in ascending order."""
    return sorted(int(f) for f in (*config.ENF_FREQS_50HZ, *config.ENF_FREQS_60HZ))


# ---------------------------------------------------------------------------
# Analysis (also used by scripts/test_enf.py)
# ---------------------------------------------------------------------------

def audio_info(path: Path) -> tuple[bool, float]:
    """(has an audio track, container duration in s) via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, timeout=20, check=True,
    ).stdout
    info = json.loads(out)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return has_audio, float(info.get("format", {}).get("duration") or 0.0)


def load_audio(path: Path, max_s: Optional[float] = None) -> tuple[np.ndarray, int]:
    """Mono float32 audio at the file's NATIVE sample rate (hum analysis needs the full band), first max_s s."""
    import soundfile as sf  # noqa: PLC0415

    if path.suffix.lower() in DIRECT_EXTS:
        y, sr = sf.read(str(path), dtype="float32", always_2d=True)
        y = y.mean(axis=1)
        return (y[: int(max_s * sr)] if max_s else y), sr
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    # No -ar, so ffmpeg keeps the native sample rate. -ac 1 mixes to mono.
    span = ["-t", f"{max_s:g}"] if max_s else []
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vn", *span, "-ac", "1", "-c:a", "pcm_s16le", tmp]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {res.stderr.strip() or 'no audio track?'}")
        y, sr = sf.read(tmp, dtype="float32")
        return y, sr
    finally:
        os.remove(tmp)


def pick_window_s(duration_s: float) -> float:
    """4-8 s window; aim for at least ~4 frames, shrink for short clips (never below 2 s = 0.5 Hz bins)."""
    lo, hi = config.ENF_STFT_WINDOW_S
    win = min(hi, max(lo, duration_s / 4))
    if win > duration_s / 2:  # short clip: make sure we still get a few frames
        win = max(config.ENF_SHORT_CLIP_MIN_WIN_S, duration_s / 2)
    return min(win, duration_s)


def analyse(y: np.ndarray, sr: int):
    """STFT power spectrogram plus per-frame SNR for each target frequency.

    Returns (results, freqs, times, Sxx, win_s, duration_s); results[f] = median/max SNR (dB),
    fraction of frames >= ENF_MIN_SNR_DB, detected.
    """
    from scipy import signal  # noqa: PLC0415

    duration_s = len(y) / sr
    win_s = pick_window_s(duration_s)
    nperseg = int(win_s * sr)
    freqs, times, Sxx = signal.spectrogram(
        y, fs=sr, window="hann", nperseg=nperseg, noverlap=nperseg // 2, scaling="density", mode="psd"
    )
    eps = 1e-20  # avoids log(0) on digital silence
    results = {}
    for f0 in target_freqs():
        peak = np.abs(freqs - f0) <= config.ENF_PEAK_HALF_WIDTH_HZ
        noise = (np.abs(freqs - f0) <= config.ENF_NOISE_BAND_HZ / 2) & ~peak
        # Mean power per bin in each band, so bands with different bin counts compare fairly.
        p_peak = Sxx[peak].mean(axis=0)
        p_noise = Sxx[noise].mean(axis=0)
        snr_db = 10 * np.log10((p_peak + eps) / (p_noise + eps))
        frac = float(np.mean(snr_db >= config.ENF_MIN_SNR_DB))
        results[f0] = {
            "median_snr_db": float(np.median(snr_db)),
            "max_snr_db": float(np.max(snr_db)),
            "frac_frames": frac,
            "detected": frac >= config.ENF_MIN_FRAME_FRACTION,
        }
    return results, freqs, times, Sxx, win_s, duration_s


def save_spectrogram(out: Path, title: str, freqs, times, Sxx, results, win_s) -> Path:
    """Spectrogram zoomed to 40-130 Hz with the hum lines marked (solid tick = detected, dotted = not).

    Uses matplotlib's Figure directly (no pyplot): witnesses run in threads and pyplot is not thread-safe.
    """
    from matplotlib.figure import Figure  # noqa: PLC0415

    band = (freqs >= 40) & (freqs <= 130)
    db = 10 * np.log10(Sxx[band] + 1e-20)
    fig = Figure(figsize=(10, 4.5))
    ax = fig.subplots()
    mesh = ax.pcolormesh(times, freqs[band], db, shading="auto", cmap="magma")
    fig.colorbar(mesh, ax=ax, label="Power (dB)")
    # Short ticks at both edges (so the hum line itself stays visible) and a label per frequency.
    for f0, r in results.items():
        color = "#4cc9f0" if f0 in (50, 100) else "#f9c74f"
        style = "-" if r["detected"] else ":"
        ax.axhline(f0, xmin=0.0, xmax=0.03, color=color, linestyle=style, linewidth=2.5)
        ax.axhline(f0, xmin=0.97, xmax=1.0, color=color, linestyle=style, linewidth=2.5)
        label = f"{f0} Hz {'detected' if r['detected'] else 'not detected'} ({r['frac_frames']:.0%} of frames)"
        ax.text(0.96, f0, label, transform=ax.get_yaxis_transform(), color=color, fontsize=8,
                ha="right", va="center", bbox=dict(facecolor="black", alpha=0.6, edgecolor="none", pad=1.5))
    ax.set_ylim(40, 130)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Frequency (Hz)")
    ax.set_title(f"{title}  (STFT window {win_s:.1f} s)")
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    return out


# ---------------------------------------------------------------------------
# §6.6 rules (only used when ENF_GO is True)
# ---------------------------------------------------------------------------

def grid_rule(results: dict, countries: list[str]) -> tuple[str, str]:
    """(status, reason) by §6.6 from detected frequencies and the claimed location's country codes."""
    hum50 = any(results[int(f)]["detected"] for f in config.ENF_FREQS_50HZ)
    hum60 = any(results[int(f)]["detected"] for f in config.ENF_FREQS_60HZ)
    if hum50 and hum60:
        return "insufficient_signal", "both 50/100 Hz and 60/120 Hz detected"
    if hum50 and any(c in config.ENF_CONSISTENT_COUNTRIES for c in countries):
        return "consistent", "50/100 Hz hum matches the claimed location's 50 Hz grid"
    in_50hz_country = [c for c in countries if c and c not in config.ENF_60HZ_COUNTRIES
                       and c not in config.ENF_MIXED_GRID_COUNTRIES]
    if hum60 and in_50hz_country:
        return "contradiction", "60/120 Hz hum, but the claimed location is on a 50 Hz grid"
    if hum50 or hum60:
        return "insufficient_signal", "hum detected, but no matching location claim to compare against"
    return "insufficient_signal", "no mains hum detected"


def _cap(reliability: str) -> str:
    """Never above the §5 max for ENF (medium)."""
    return min(reliability, "medium", key=_RANK.__getitem__)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------

class EnfWitness(Witness):
    name = "enf"
    label = "Power-grid hum"
    implemented = True

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(ctx.inputs_of("video") or ctx.inputs_of("audio"))

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        inputs = (ctx.inputs_of("video") + ctx.inputs_of("audio"))[: config.ENF_MAX_INPUTS]
        findings = []
        for n, inp in enumerate(inputs, 1):
            try:
                findings.append(self._analyse(ctx, inp, n, len(inputs)))
            except Exception as exc:  # noqa: BLE001 - principle 5: never crash an investigation
                findings.append(self._failed(inp, n, exc))
        return findings

    def _analyse(self, ctx: InvestigationContext, inp: InputRef, n: int, total: int) -> Finding:
        where = f" ({inp.name})" if total > 1 else ""
        has_audio, duration = audio_info(inp.path)
        if not has_audio:
            return self.finding(
                n=n, status="not_applicable", reliability="low", details={"file": inp.name, "enf_go": config.ENF_GO},
                title=f"Power-grid hum (ENF): no audio track{where}",
                summary="The file has no audio track, so there is no mains hum to measure.",
                alternatives=["Without audio the hum check says nothing either way"],
                limitations=[NO_GO_LIMITATION],
            )

        span = config.ENF_MAX_SPAN_S or None
        y, sr = load_audio(inp.path, span)
        if len(y) < config.ENF_SHORT_CLIP_MIN_WIN_S * sr:
            return self.finding(
                n=n, status="insufficient_signal", reliability="low",
                details={"file": inp.name, "enf_go": config.ENF_GO, "audio_s": round(len(y) / sr, 2)},
                title=f"Power-grid hum (ENF): clip too short{where}",
                summary=f"Only {len(y) / sr:.1f} s of audio; at least {config.ENF_SHORT_CLIP_MIN_WIN_S:g} s "
                        "are needed to resolve mains hum.",
                alternatives=ALTERNATIVES,
                limitations=[NO_GO_LIMITATION],
            )
        results, freqs, times, Sxx, win_s, analysed_s = analyse(y, sr)

        art = "enf_spectrogram.png" if n == 1 else f"enf_spectrogram_{inp.file_id}.png"
        save_spectrogram(ctx.artifact_path(art), f"Power-grid hum: {inp.name}", freqs, times, Sxx, results, win_s)
        evidence = [{"kind": "artifact", "ref": ctx.artifact_ref(art), "label": "Spectrogram 40-130 Hz (hum lines marked)"},
                    {"kind": "input", "ref": inp.file_id, "label": inp.name}]

        locs = ctx.claims_of("location")
        countries = [str((c.normalized or {}).get("country") or "").upper() for c in locs]
        rule_status, rule_reason = grid_rule(results, countries)
        details: dict[str, Any] = {
            "file": inp.name,
            "enf_go": config.ENF_GO,
            "rule_status": rule_status,
            "rule_reason": rule_reason,
            "frequencies": {str(f): {"median_snr_db": round(r["median_snr_db"], 2), "max_snr_db": round(r["max_snr_db"], 2),
                                     "frames_above_snr": round(r["frac_frames"], 3), "detected": r["detected"]}
                            for f, r in results.items()},
            "sample_rate_hz": sr,
            "stft_window_s": round(win_s, 2),
            "frames": int(len(times)),
            "analysed_s": round(analysed_s, 2),
            "clip_duration_s": round(duration, 2),
            "claimed_countries": [c for c in countries if c],
            "thresholds": {"min_snr_db": config.ENF_MIN_SNR_DB, "min_frame_fraction": config.ENF_MIN_FRAME_FRACTION,
                           "peak_half_width_hz": config.ENF_PEAK_HALF_WIDTH_HZ, "noise_band_hz": config.ENF_NOISE_BAND_HZ},
        }
        cut = [f"Only the first {span:g} s of the audio were analysed"] if span and duration > span + 0.05 else []
        detected = [f"{f} Hz" for f, r in results.items() if r["detected"]]
        measured = ", ".join(f"{f} Hz in {r['frac_frames']:.0%} of frames" for f, r in results.items())

        if config.ENF_GO and rule_status in ("consistent", "contradiction"):
            # Future path (GO): §6.6 rules, capped at medium. Not reachable while ENF_GO is False.
            claim_ids = [c.id for c in locs]
            if rule_status == "consistent":
                title = f"Power-grid hum matches the claimed location's 50 Hz grid{where}"
            else:
                title = f"Power-grid hum suggests a 60 Hz grid, unlike the claimed location{where}"
            return self.finding(
                n=n, status=rule_status, reliability=_cap("medium"), claim_ids=claim_ids, details=details,
                evidence=evidence, title=title,
                summary=f"Mains-hum check ({rule_reason}). Hum above {config.ENF_MIN_SNR_DB:g} dB: {measured}.",
                alternatives=ALTERNATIVES,
                limitations=["Mains hum is supporting evidence only; equipment and recording chains can add or remove it"]
                + cut,
            )

        # Display-only (ENF_GO False), or no usable rule outcome: always insufficient_signal / low.
        if detected:
            summary = (f"Some mains-hum energy was measured ({', '.join(detected)}), but the hum check is "
                       f"display-only and is not used as evidence. Hum above {config.ENF_MIN_SNR_DB:g} dB: {measured}.")
        else:
            summary = (f"No usable mains hum was found. Hum above {config.ENF_MIN_SNR_DB:g} dB: {measured}. "
                       "The spectrogram is shown for reference only.")
        return self.finding(
            n=n, status="insufficient_signal", reliability="low", details=details, evidence=evidence,
            title=f"Power-grid hum (ENF): not usable for this clip{where}",
            summary=summary, alternatives=ALTERNATIVES, limitations=[NO_GO_LIMITATION] + cut,
        )

    def _failed(self, inp: InputRef, n: int, exc: Exception) -> Finding:
        return self.finding(
            n=n, status="inconclusive", reliability="low",
            title="Power-grid hum (ENF) check could not be computed",
            summary=f"The hum analysis of {inp.name} failed, so no measurement is available.",
            details={"file": inp.name, "enf_go": config.ENF_GO, "error": f"{type(exc).__name__}: {exc}"},
            alternatives=["No hum analysis was completed, so this finding says nothing about the media"],
            limitations=[f"Analysis error: {type(exc).__name__}: {exc}", NO_GO_LIMITATION],
        )


if __name__ == "__main__":
    run_cli(EnfWitness())
