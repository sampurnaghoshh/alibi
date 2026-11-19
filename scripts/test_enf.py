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
Analysis functions live in backend/app/witnesses/enf.py; thresholds in backend/app/config.py.
"""

import argparse
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

# The analysis lives in the witness module so the go/no-go test and the witness measure the same thing.
# Thresholds (SNR >= 6 dB in >= 60% of frames, +/-0.5 Hz peak vs 5 Hz band, 4-8 s STFT) are in config.py.
from app.witnesses.enf import analyse, load_audio, save_spectrogram  # noqa: E402
from app import config  # noqa: E402

GO_MIN_CLIPS = 3                   # GO if 50/100 Hz detected in >= 3 of 5 clips (scaled to 60% if != 5 clips)
MEDIA_EXTS = {".mov", ".mp4", ".m4a", ".m4v", ".3gp", ".aac", ".mp3", ".wav", ".flac"}


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
            y, sr = load_audio(path)                       # whole clip: the go/no-go test is not span-capped
            if len(y) < config.ENF_SHORT_CLIP_MIN_WIN_S * sr:
                raise RuntimeError(f"clip too short ({len(y) / sr:.1f} s)")
            results, freqs, times, Sxx, win_s, duration_s = analyse(y, sr)
        except Exception as e:  # one bad clip must not stop the test
            print(f"\n=== {path.name} ===\nERROR: {e}")
            continue

        n_ok += 1
        print_table(path, sr, duration_s, win_s, len(times), results)
        png = save_spectrogram(path.with_name(f"{path.stem}_enf.png"), f"ENF check: {path.name}",
                               freqs, times, Sxx, results, win_s)
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
