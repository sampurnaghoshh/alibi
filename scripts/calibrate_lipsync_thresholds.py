"""
Propose lip-sync contradiction thresholds from a calibration run, then apply them unchanged to a
held-out test run. Owner: Nooman. Reads the calibration.json written by calibrate_lipsync.py.

Rule shape (same as app/witnesses/lipsync.py summarize()):
    contradiction  <=>  speech in >= LIPSYNC_MIN_SPEECH_FRACTION of windows
                        AND ( |lag| >= L  OR  corr < C  OR  prominence < P )

Proposal, per mouth feature: each condition must not fire on ANY genuine calibration clip. Within that,
its threshold sits at the midpoint between the most extreme genuine value and the nearest swapped value
beyond it (max margin, same catches as the tightest threshold). A condition no swap lies beyond is
disabled. The proposal is frozen to a JSON file BEFORE the test set is looked at.

Run (from the repo root):
    # 1. propose from the calibration set, freeze to proposal.json
    backend\\.venv\\Scripts\\python.exe scripts\\calibrate_lipsync_thresholds.py propose <calib>\\calibration.json --out proposal.json
    # 2. apply the frozen proposal once to the test set
    backend\\.venv\\Scripts\\python.exe scripts\\calibrate_lipsync_thresholds.py apply proposal.json <test>\\calibration.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app import config  # noqa: E402

FEATURES = ("aperture", "velocity")
NEVER = None   # a disabled condition


def _rows(calib: dict, feature: str) -> list[dict]:
    """One row per clip with the numbers the rule needs (clips with no sync numbers are skipped)."""
    out = []
    for r in calib["clips"]:
        sy = (r.get("syncs") or {}).get(feature)
        if not sy:
            continue
        out.append({"group": r["group"], "clip": r["clip"], "abs_lag": abs(sy["lag_s"]), "corr": sy["corr"],
                    "prominence": sy["prominence"], "speech": r["summary"]["speech_window_fraction"]})
    return out


def fires(row: dict, th: dict, min_speech: float) -> list[str]:
    """Which conditions fire for this clip (empty list = no contradiction)."""
    if row["speech"] < min_speech:
        return []
    hit = []
    if th["min_abs_lag_s"] is not NEVER and row["abs_lag"] >= th["min_abs_lag_s"]:
        hit.append("lag")
    if th["max_corr"] is not NEVER and row["corr"] < th["max_corr"]:
        hit.append("corr")
    if th["max_prominence"] is not NEVER and row["prominence"] < th["max_prominence"]:
        hit.append("prom")
    return hit


def propose(calib: dict, feature: str, min_speech: float) -> dict:
    rows = _rows(calib, feature)
    gen = [r for r in rows if r["group"] == "genuine" and r["speech"] >= min_speech]   # only these can fire
    swp = [r for r in rows if r["group"] == "swapped" and r["speech"] >= min_speech]
    th, notes = {}, {}
    # |lag| >= L: L must be above every eligible genuine |lag|.
    g = max(r["abs_lag"] for r in gen)
    beyond = sorted(r["abs_lag"] for r in swp if r["abs_lag"] > g)
    th["min_abs_lag_s"] = round((g + beyond[0]) / 2, 4) if beyond else NEVER
    notes["lag"] = f"genuine max |lag| {g:.3f}; nearest swap beyond {beyond[0]:.3f}" if beyond else \
        f"genuine max |lag| {g:.3f}: no swap beyond it -> disabled"
    # corr < C and prominence < P: C/P must be at or below every eligible genuine value.
    for key, name in (("corr", "max_corr"), ("prominence", "max_prominence")):
        g = min(r[key] for r in gen)
        beyond = sorted((r[key] for r in swp if r[key] < g), reverse=True)
        th[name] = round((g + beyond[0]) / 2, 4) if beyond else NEVER
        notes[key] = (f"genuine min {g:.3f}; nearest swap below {beyond[0]:.3f} (margin {g - beyond[0]:.3f})"
                      if beyond else f"genuine min {g:.3f}: no swap below it -> disabled")
    return {"thresholds": th, "notes": notes}


def score(calib: dict, feature: str, th: dict, min_speech: float) -> tuple[list[str], list[str], int, list[str]]:
    """(genuine contradictions, swaps caught, n swaps, report lines)."""
    lines, g_hits, s_hits, n_swaps = [], [], [], 0
    for r in _rows(calib, feature):
        hit = fires(r, th, min_speech)
        n_swaps += r["group"] == "swapped"
        if hit:
            (g_hits if r["group"] == "genuine" else s_hits).append(r["clip"])
        lines.append(f"    {r['group']:<8}{r['clip']:<20} |lag| {r['abs_lag']:.3f}  corr {r['corr']:+.3f}  "
                     f"prom {r['prominence']:+.3f}  speech {r['speech']:.0%}  -> "
                     f"{'CONTRADICTION (' + '+'.join(hit) + ')' if hit else '-'}")
    return g_hits, s_hits, n_swaps, lines


def _fmt_th(th: dict) -> str:
    def f(v, op):
        return "off" if v is NEVER else f"{op} {v:g}"
    return (f"|lag| {f(th['min_abs_lag_s'], '>=')}, corr {f(th['max_corr'], '<')}, "
            f"prominence {f(th['max_prominence'], '<')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("propose", help="propose thresholds from a calibration run and freeze them")
    p1.add_argument("calibration_json", type=Path)
    p1.add_argument("--out", type=Path, required=True, help="frozen proposal (JSON)")
    p2 = sub.add_parser("apply", help="apply a frozen proposal, unchanged, to another run")
    p2.add_argument("proposal_json", type=Path)
    p2.add_argument("calibration_json", type=Path)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.cmd == "propose":
        calib = json.loads(args.calibration_json.read_text(encoding="utf-8"))
        min_speech = config.LIPSYNC_MIN_SPEECH_FRACTION
        proposal = {"source": str(args.calibration_json), "min_speech_window_fraction": min_speech, "features": {}}
        for feat in FEATURES:
            p = propose(calib, feat, min_speech)
            g, s, n, lines = score(calib, feat, p["thresholds"], min_speech)
            p.update(calib_genuine_contradictions=g, calib_swaps_caught=s)
            proposal["features"][feat] = p
            print(f"\n[{feat}] proposed: {_fmt_th(p['thresholds'])}  (contradiction also needs speech >= {min_speech:.0%})")
            for k, v in p["notes"].items():
                print(f"    {k:<11}{v}")
            print(*lines, sep="\n")
            print(f"  calibration: genuine contradictions {len(g)} of {sum(1 for r in _rows(calib, feat) if r['group'] == 'genuine')}"
                  f", swaps caught {len(s)} of {n}")
        args.out.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
        print(f"\nfrozen proposal: {args.out}")
    else:
        proposal = json.loads(args.proposal_json.read_text(encoding="utf-8"))
        test = json.loads(args.calibration_json.read_text(encoding="utf-8"))
        min_speech = proposal["min_speech_window_fraction"]
        for feat, p in proposal["features"].items():
            g, s, n, lines = score(test, feat, p["thresholds"], min_speech)
            print(f"\n[{feat}] frozen thresholds: {_fmt_th(p['thresholds'])}")
            print(*lines, sep="\n")
            n_gen = sum(1 for r in _rows(test, feat) if r["group"] == "genuine")
            print(f"  TEST: genuine contradictions {len(g)} of {n_gen} {'(OK)' if not g else '(FAIL: must be 0)'}"
                  f", swaps caught {len(s)} of {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
