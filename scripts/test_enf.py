"""Hour-1 ENF go/no-go test (CLAUDE.md section 6.6).

Records from team phones are checked for mains hum (electrical network
frequency). For each clip we measure 50, 60, 100 and 120 Hz and decide whether
the hum is strong enough to be a usable witness at the venue.

Usage (Windows-friendly, no shell globbing needed):
    python scripts/test_enf.py demo/enf_test            # every media file in a folder
    python scripts/test_enf.py clip1.MOV clip2.MOV ...  # explicit files

Outputs:
    - a table per file (SNR stats and detected yes/no per frequency)
    - <clip>_enf.png next to each clip: spectrogram zoomed to 40-130 Hz
    - GO / NO-GO: GO if 50 or 100 Hz is detected in >= 3 of 5 clips

Needs ffmpeg on PATH for video files (.wav / .flac are read directly).
Thresholds below are starting values; final values go in backend/app/config.py.
"""

import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no GUI window, just write PNGs
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf
from scipy import signal

# --- Starting thresholds (from CLAUDE.md 6.6) ---------------------------------
TARGET_FREQS = [50, 60, 100, 120]  # Hz: 50 Hz grid (India) + 60 Hz grid, and 2nd harmonics
PEAK_HALF_BW = 0.5                 # Hz: "signal" band is f +/- 0.5 Hz
NOISE_HALF_BW = 2.5                # Hz: "noise" band is the surrounding 5 Hz (f +/- 2.5), minus the peak band
SNR_MIN_DB = 6.0                   # a frame counts if its SNR is at least this
FRAME_FRACTION_MIN = 0.60          # a frequency is detected if >= 60% of frames pass
MAX_WIN_S = 8.0                    # STFT window, 4-8 s normally ...
MIN_WIN_S = 4.0
SHORT_CLIP_MIN_WIN_S = 2.0         # ... but never below 2 s (0.5 Hz resolution) for very short clips
GO_MIN_CLIPS = 3                   # GO if 50/100 Hz detected in >= 3 of 5 clips (scaled to 60% if != 5 clips)

MEDIA_EXTS = {".mov", ".mp4", ".m4a", ".m4v", ".3gp", ".aac", ".mp3", ".wav", ".flac"}
DIRECT_EXTS = {".wav", ".flac"}  # soundfile reads these without ffmpeg


def collect_files(args: list[str]) -> list[Path]:
    """Expand folder arguments into media files; keep explicit file paths as-is."""
    files = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in MEDIA_EXTS)
        elif p.is_file():
            files.append(p)
        else:
            print(f"WARNING: not found, skipping: {a}")
    return files


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    """Return mono float audio at the file's native sample rate."""
    if path.suffix.lower() in DIRECT_EXTS:
        y, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return y.mean(axis=1), sr

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    # No -ar flag, so ffmpeg keeps the native sample rate. -ac 1 mixes to mono.
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-c:a", "pcm_s16le", tmp]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {res.stderr.strip() or 'no audio track?'}")
        y, sr = sf.read(tmp, dtype="float32")
        return y, sr
    finally:
        os.remove(tmp)


def pick_window_s(duration_s: float) -> float:
    """4-8 s window; aim for at least ~4 frames, shrink for short clips."""
    win = min(MAX_WIN_S, max(MIN_WIN_S, duration_s / 4))
    if win > duration_s / 2:  # short clip: make sure we still get a few frames
        win = max(SHORT_CLIP_MIN_WIN_S, duration_s / 2)
    return min(win, duration_s)


def analyse(y: np.ndarray, sr: int):
    """STFT power spectrogram plus per-frame SNR for each target frequency."""
    duration_s = len(y) / sr
    win_s = pick_window_s(duration_s)
    nperseg = int(win_s * sr)
    # 50% overlap, Hann window, power spectral density per frame.
    freqs, times, Sxx = signal.spectrogram(
        y, fs=sr, window="hann", nperseg=nperseg, noverlap=nperseg // 2, scaling="density", mode="psd"
    )

    eps = 1e-20  # avoids log(0) on digital silence
    results = {}
    for f0 in TARGET_FREQS:
        peak = np.abs(freqs - f0) <= PEAK_HALF_BW
        noise = (np.abs(freqs - f0) <= NOISE_HALF_BW) & ~peak
        # Mean power per bin in each band, so the bands are compared fairly
        # even though they contain different numbers of bins.
        p_peak = Sxx[peak].mean(axis=0)
        p_noise = Sxx[noise].mean(axis=0)
        snr_db = 10 * np.log10((p_peak + eps) / (p_noise + eps))
        frac = float(np.mean(snr_db >= SNR_MIN_DB))
        results[f0] = {
            "median_snr_db": float(np.median(snr_db)),
            "max_snr_db": float(np.max(snr_db)),
            "frac_frames": frac,
            "detected": frac >= FRAME_FRACTION_MIN,
        }
    return results, freqs, times, Sxx, win_s, duration_s


def save_spectrogram(path: Path, freqs, times, Sxx, results, win_s) -> Path:
    """Spectrogram zoomed to 40-130 Hz with the hum lines marked."""
    band = (freqs >= 40) & (freqs <= 130)
    db = 10 * np.log10(Sxx[band] + 1e-20)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    mesh = ax.pcolormesh(times, freqs[band], db, shading="auto", cmap="magma")
    fig.colorbar(mesh, ax=ax, label="Power (dB)")

    # Mark each target frequency with short ticks at both edges (so the hum
    # line itself stays visible) and a label: solid tick if detected, dotted if not.
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
    ax.set_title(f"ENF check: {path.name}  (STFT window {win_s:.1f} s)")
    fig.tight_layout()

    out = path.with_name(f"{path.stem}_enf.png")
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def print_table(path: Path, sr: int, duration_s: float, win_s: float, n_frames: int, results) -> None:
    print(f"\n=== {path.name} ===")
    print(f"sample rate {sr} Hz | duration {duration_s:.1f} s | window {win_s:.1f} s | {n_frames} frames")
    print(f"{'freq':>6} | {'median SNR':>10} | {'max SNR':>8} | {'frames >= 6dB':>13} | detected")
    print("-" * 60)
    for f0, r in results.items():
        print(
            f"{f0:>4} Hz | {r['median_snr_db']:>7.1f} dB | {r['max_snr_db']:>5.1f} dB "
            f"| {r['frac_frames']:>12.0%} | {'YES' if r['detected'] else 'no'}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="ENF hour-1 go/no-go test")
    ap.add_argument("paths", nargs="+", help="folder(s) and/or media file(s)")
    args = ap.parse_args()

    files = collect_files(args.paths)
    if not files:
        print("No media files found.")
        return 1

    n_ok = 0       # clips analysed successfully
    n_india = 0    # clips with 50 or 100 Hz detected
    for path in files:
        try:
            y, sr = load_audio(path)
            if len(y) < SHORT_CLIP_MIN_WIN_S * sr:
                raise RuntimeError(f"clip too short ({len(y) / sr:.1f} s)")
            results, freqs, times, Sxx, win_s, duration_s = analyse(y, sr)
        except Exception as e:  # one bad clip must not stop the test
            print(f"\n=== {path.name} ===\nERROR: {e}")
            continue

        n_ok += 1
        print_table(path, sr, duration_s, win_s, len(times), results)
        png = save_spectrogram(path, freqs, times, Sxx, results, win_s)
        print(f"spectrogram: {png}")
        if results[50]["detected"] or results[100]["detected"]:
            n_india += 1

    # Spec says ">= 3 of 5". With a different number of clips, keep the same 60% ratio.
    needed = GO_MIN_CLIPS if n_ok == 5 else max(GO_MIN_CLIPS, math.ceil(0.6 * n_ok))
    if n_ok != 5:
        print(f"\nNOTE: the protocol expects 5 clips; {n_ok} analysed, so {needed} needed.")

    print("\n" + "=" * 60)
    print(f"50/100 Hz detected in {n_india} of {n_ok} clips (need {needed})")
    if n_ok > 0 and n_india >= needed:
        print("RESULT: GO - ENF can ship as a witness (max reliability medium).")
    else:
        print("RESULT: NO-GO - ENF ships display-only, always insufficient_signal.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
