"""
Calibrate the lip-sync synchrony indicator (CLAUDE.md §6.5). Owner: Nooman.

Runs the exact analysis the witness uses (app/witnesses/lipsync.py) on genuine clips and on
mismatched controls (video of one clip + audio of another; our stand-in for Case B), for BOTH
mouth features (aperture, velocity). Per clip: face %, speech-window %, and for each feature the
clip-level lag, correlation and peak prominence (best corr - median corr over all lags).

Then, per feature and metric, it checks whether any threshold cleanly separates genuine from
swapped clips. It also prints the median face height (% of frame height) per clip for the
LIPSYNC_MIN_FACE_HEIGHT guard. The rule that matters: ZERO genuine clips may ever come out as contradiction.

Per clip it saves <out>/<group>_<clip>_lipsync.png (upright analysis frame + landmarks, mouth vs
loudness z-scored on one axis with speech shaded, the correlation-vs-lag curve, and per-window
corr at the clip's lag) and <out>/calibration.json with all numbers.

Run (from the repo root):
    backend\\.venv\\Scripts\\python.exe scripts\\calibrate_lipsync.py --genuine C:\\Users\\mnoom\\alibi-media\\enf --swapped C:\\Users\\mnoom\\alibi-media\\enf\\swapped
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app import config  # noqa: E402
from app.media import VIDEO_EXTS  # noqa: E402
from app.witnesses.lipsync import ClipAnalysis, analyze_clip, mouth_features  # noqa: E402

FEATURES = ("aperture", "velocity")

# Reference palette (dataviz skill), light surface. Slot 1 blue = mouth, slot 2 orange = audio.
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
MOUTH_C, AUDIO_C, SPEECH_SHADE = "#2a78d6", "#eb6834", "#f1efe9"


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------

def _z(x: np.ndarray) -> np.ndarray:
    m, s = np.nanmean(x), np.nanstd(x)
    return (x - m) / s if s > 0 else x * 0


def _shade_speech(ax, t: np.ndarray, speech: np.ndarray) -> None:
    dt = t[1] - t[0] if len(t) > 1 else 0.04
    i = 0
    while i < len(speech):
        if speech[i]:
            j = i
            while j < len(speech) and speech[j]:
                j += 1
            ax.axvspan(t[i], t[j - 1] + dt, color=SPEECH_SHADE, lw=0)
            i = j
        else:
            i += 1


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def plot_clip(title: str, a: ClipAnalysis, out_png: Path) -> None:
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    ms, s = a.mouth, a.summary
    feat = config.LIPSYNC_MOUTH_FEATURE
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK_2,
                         "xtick.color": INK_2, "ytick.color": INK_2, "text.color": INK})
    fig = plt.figure(figsize=(14, 7.5), facecolor=SURFACE)
    gs = fig.add_gridspec(2, 3, width_ratios=[1, 1, 2.2], height_ratios=[1.6, 1], hspace=0.4, wspace=0.3)

    # Upright frame with the landmarks used: rotation + face-lock check.
    ax_img = fig.add_subplot(gs[0, 0])
    if ms.first_face_frame is not None:
        ax_img.imshow(ms.first_face_frame)
        p = ms.first_face_points
        ax_img.plot(p[:2, 0], p[:2, 1], "o-", color=MOUTH_C, ms=4, lw=2)
        ax_img.plot(p[2:, 0], p[2:, 1], "o--", color="#ffffff", ms=4, lw=1, alpha=0.8)
        ax_img.set_title("Analysis frame (should be upright)", color=INK_2, fontsize=9)
    ax_img.set_axis_off()

    # Correlation vs lag for both features (one axis: both are Pearson r).
    ax_lag = fig.add_subplot(gs[1, 0:2])
    for name, color in (("aperture", MOUTH_C), ("velocity", "#1baf7a")):
        sy = a.syncs.get(name)
        if sy:
            ax_lag.plot(sy["lags_s"], sy["curve"], "-", color=color, lw=2,
                        label=f"{name}: lag {sy['lag_s']:+.2f}s, r {sy['corr']:.2f}, prom {sy['prominence']:.2f}")
            ax_lag.plot([sy["lag_s"]], [sy["corr"]], "o", color=color, ms=8, markeredgecolor=SURFACE, markeredgewidth=2)
    ax_lag.axvline(0, color=INK_2, lw=1, ls="--")
    ax_lag.set_xlabel("lag (s)   + = audio later than mouth")
    ax_lag.set_ylabel("corr (speech frames)")
    ax_lag.legend(loc="lower center", frameon=False, fontsize=8)
    _style(ax_lag)

    # Mouth vs loudness, z-scored on one shared axis (different units -> common base).
    ax = fig.add_subplot(gs[0, 2])
    t = ms.t
    _shade_speech(ax, t, a.speech)
    ax.plot(t, _z(a.audio), color=AUDIO_C, lw=1.5, label="Loudness 300-3000 Hz (dB)")
    ax.plot(t, _z(mouth_features(ms.mouth)[feat]), color=MOUTH_C, lw=2, label=f"Mouth {feat}")
    ax.set_ylabel("z-score")
    ax.legend(loc="upper right", frameon=False, ncol=2, fontsize=8)
    _style(ax)

    # Per-window corr at the clip's lag: where sync drops (display only).
    ax_w = fig.add_subplot(gs[1, 2], sharex=ax)
    ws = [w for w in a.windows if w["corr"] is not None]
    ax_w.plot([(w["start_s"] + w["end_s"]) / 2 for w in ws], [w["corr"] for w in ws], "o", color=MOUTH_C,
              ms=8, markeredgecolor=SURFACE, markeredgewidth=2)
    ax_w.axhline(0, color=INK_2, lw=1)
    ax_w.set_ylim(-1, 1)
    ax_w.set_ylabel(f"window corr\nat clip lag ({feat})")
    ax_w.set_xlabel("time (s)   ·   shaded = speech frames   ·   one dot per 3 s speech window")
    _style(ax_w)

    fig.suptitle(f"{title}   [{feat}] status: {s['status']} ({s['reason']})   face {s['face_fraction']:.0%} · "
                 f"face height {s.get('face_height') or 0:.0%} · speech windows {s['speech_window_fraction']:.0%}", x=0.01, ha="left", fontsize=10)
    fig.savefig(out_png, dpi=110, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Separation analysis
# ---------------------------------------------------------------------------

def separation(rows: list[dict]) -> list[dict]:
    """For each feature x metric: is there a threshold with every genuine clip on one side and
    every swapped clip on the other? Higher = more in sync for corr/prominence, lower for |lag|."""
    out = []
    for feat in FEATURES:
        for metric, higher_is_sync in (("corr", True), ("prominence", True), ("abs_lag", False)):
            def vals(group):
                v = []
                for r in rows:
                    sy = r.get("syncs", {}).get(feat) if r["group"] == group else None
                    if sy:
                        v.append(abs(sy["lag_s"]) if metric == "abs_lag" else sy[metric])
                return v
            g, s = vals("genuine"), vals("swapped")
            if not g or not s:
                continue
            if higher_is_sync:
                gap = min(g) - max(s)          # > 0: all genuine above all swapped
                worst = f"genuine min {min(g):.3f} vs swapped max {max(s):.3f}"
            else:
                gap = min(s) - max(g)          # > 0: all genuine below all swapped
                worst = f"genuine max {max(g):.3f} vs swapped min {min(s):.3f}"
            out.append({"feature": feat, "metric": metric, "clean": gap > 0, "gap": round(gap, 3),
                        "threshold": round((min(g) + max(s)) / 2 if higher_is_sync else (max(g) + min(s)) / 2, 3),
                        "detail": worst})
    return out


def _fmt(v, spec: str = ".3f") -> str:
    return "—" if v is None else format(v, spec)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--genuine", type=Path, required=True, help="folder of genuine clips")
    ap.add_argument("--swapped", type=Path, default=None, help="folder of mismatched audio/video controls")
    ap.add_argument("--out", type=Path, default=None, help="plots folder (default <genuine>/lipsync_calibration)")
    args = ap.parse_args()

    groups = [("genuine", args.genuine)] + ([("swapped", args.swapped)] if args.swapped else [])
    out = args.out or args.genuine / "lipsync_calibration"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*_lipsync.png"):
        old.unlink()
    sys.stdout.reconfigure(encoding="utf-8")

    hdr = (f"{'group':<8}{'clip':<20}{'face':>5}{'faceH':>6}{'spch':>5} |"
           f"{'ap lag':>7}{'ap r':>7}{'ap prom':>8} |{'vel lag':>8}{'vel r':>7}{'vel prom':>9} |{'secs':>5}  status [{config.LIPSYNC_MOUTH_FEATURE}]")
    print(hdr)
    print("-" * (len(hdr) + 20))
    rows = []
    for group, folder in groups:
        clips = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
        for clip in clips:
            t0 = time.perf_counter()
            try:
                a = analyze_clip(clip)
            except Exception as exc:  # noqa: BLE001 - calibration keeps going on other clips
                print(f"{group:<8}{clip.name:<20}  ERROR: {exc}")
                rows.append({"group": group, "clip": clip.name, "error": str(exc)})
                continue
            secs = time.perf_counter() - t0
            s = a.summary
            cells = []
            for feat in FEATURES:
                sy = a.syncs.get(feat)
                cells.append(f"{_fmt(sy and sy['lag_s'], '+.2f'):>7}{_fmt(sy and sy['corr']):>7}{_fmt(sy and sy['prominence']):>8}"
                             if feat == "aperture" else
                             f"{_fmt(sy and sy['lag_s'], '+.2f'):>8}{_fmt(sy and sy['corr']):>7}{_fmt(sy and sy['prominence']):>9}")
            face_h = s.get("face_height")
            small = face_h is not None and face_h < config.LIPSYNC_MIN_FACE_HEIGHT
            print(f"{group:<8}{clip.name:<20}{s['face_fraction']:>5.0%}"
                  f"{'—' if face_h is None else format(face_h, '.0%'):>5}{'*' if small else ' '}"
                  f"{s['speech_window_fraction']:>5.0%} |"
                  f"{cells[0]} |{cells[1]} |{secs:>5.0f}  {s['status']}")
            if a.mouth is not None:
                plot_clip(f"{group} / {clip.name}", a, out / f"{group}_{clip.stem}_lipsync.png")
            rows.append({"group": group, "clip": clip.name, "seconds": round(secs, 1), "probe": asdict(a.probe),
                         "summary": s, "speech_info": a.speech_info, "syncs": a.syncs, "windows": a.windows})

    sep = separation(rows)
    print("\nSeparation (genuine vs swapped). clean = every genuine clip on the in-sync side of every swapped clip:")
    for r in sep:
        print(f"  {r['feature']:<9}{r['metric']:<11} {'CLEAN' if r['clean'] else 'overlap':<8} gap {r['gap']:+.3f}   "
              f"midpoint {r['threshold']:.3f}   ({r['detail']})")
    print(f"\nfaceH = median face height (forehead-chin) / frame height; * = below LIPSYNC_MIN_FACE_HEIGHT "
          f"({config.LIPSYNC_MIN_FACE_HEIGHT:.0%}): the witness reports these as inconclusive (face too small).")
    genuine_contra = [r["clip"] for r in rows if r["group"] == "genuine" and r.get("summary", {}).get("status") == "contradiction"]
    print(f"\nGenuine clips marked contradiction with current config: {genuine_contra or 'none'}")

    thresholds = {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(config).items() if k.startswith("LIPSYNC_")}
    (out / "calibration.json").write_text(
        json.dumps({"thresholds": thresholds, "separation": sep, "clips": rows}, indent=2, default=str),
        encoding="utf-8")
    print(f"plots + calibration.json: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
