"""
Build the verified-clip archive for the prior_appearance witness (CLAUDE.md §6.4).
Owner: Sampurna (taken over from Adithi; Adithi still owns witnesses/prior_appearance.py).

Input:  data/archive/manifest.json, a hand-edited list of verified clips:
    [
      {
        "clip_id": "clip_001",
        "file": "clips/pune_rain_2024-07-25.mp4",      # relative to data/archive/
        "title": "Waterlogged street after monsoon rain",
        "verified_date": "2024-07-25",                  # YYYY-MM-DD
        "verified_location": "Pune, Maharashtra",
        "verified_lat": 18.5204, "verified_lon": 73.8567,   # optional, lets the witness measure distance
        "verified_by": "Recorded by team member (demo archive)",
        "source_note": "Original phone recording, metadata intact"
      }
    ]
Output: data/archive/archive.json, the same entries plus
    frame_hashes (64-bit pHash hex, 1 frame/s), frame_times_s, sample_fps, sha256, duration_s.

Frames are sampled with app/media.py, the same code the witness must use.
Entries with problems are reported and skipped; the rest are still written.

Usage (from the repo root, with the backend venv):
    backend\\.venv\\Scripts\\python.exe scripts\\build_archive.py
    backend\\.venv\\Scripts\\python.exe scripts\\build_archive.py --manifest path\\to\\manifest.json --out path\\to\\archive.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app import config  # noqa: E402
from app.media import media_kind, phash_hex, sample_frames, video_info  # noqa: E402
from app.storage import sha256_file  # noqa: E402

REQUIRED = ("clip_id", "file", "title", "verified_date", "verified_location", "verified_by", "source_note")
OPTIONAL = ("verified_lat", "verified_lon")


def check_entry(entry: Any, base: Path, seen: set[str]) -> list[str]:
    """Problems with one manifest entry (empty list = fine)."""
    if not isinstance(entry, dict):
        return ["entry is not an object"]
    problems = [f"missing '{k}'" for k in REQUIRED if not str(entry.get(k, "")).strip()]
    if extra := set(entry) - set(REQUIRED) - set(OPTIONAL):
        problems.append(f"unknown fields {sorted(extra)}")
    if entry.get("clip_id") in seen:
        problems.append(f"duplicate clip_id {entry['clip_id']!r}")
    if entry.get("verified_date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(entry["verified_date"])):
        problems.append("verified_date must be YYYY-MM-DD")
    if ("verified_lat" in entry) != ("verified_lon" in entry):
        problems.append("give both verified_lat and verified_lon, or neither")
    if entry.get("file"):
        path = (base / entry["file"]).resolve()
        if not path.is_file():
            problems.append(f"file not found: {entry['file']}")
        elif media_kind(path) not in ("image", "video"):
            problems.append(f"not an image or video: {entry['file']}")
    return problems


def build_entry(entry: dict, base: Path, fps: float) -> dict:
    path = (base / entry["file"]).resolve()
    frames = sample_frames(path, fps)
    if not frames:
        raise RuntimeError("no frames could be decoded")
    times = [t for t, _ in frames]
    duration = None
    if media_kind(path) == "video":
        vfps, count = video_info(path)
        duration = round(count / vfps, 3) if count else None
    return {
        **{k: entry[k] for k in REQUIRED},
        **{k: entry[k] for k in OPTIONAL if k in entry},
        "sha256": sha256_file(path),
        "duration_s": duration,
        "sample_fps": fps,
        "frame_times_s": times,
        "frame_hashes": [phash_hex(img) for _, img in frames],
    }


def build(manifest: Path, out: Path, fps: float) -> tuple[int, list[str]]:
    """Returns (number of entries written, list of error messages)."""
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"{manifest} must contain a JSON list")
    base = manifest.parent
    built, errors, seen = [], [], set()

    for i, entry in enumerate(entries):
        label = entry.get("clip_id", f"entry #{i}") if isinstance(entry, dict) else f"entry #{i}"
        if problems := check_entry(entry, base, seen):
            errors += [f"{label}: {p}" for p in problems]
            print(f"  SKIP {label}: {'; '.join(problems)}")
            continue
        seen.add(entry["clip_id"])
        try:
            row = build_entry(entry, base, fps)
        except Exception as exc:  # noqa: BLE001 - one bad clip must not stop the rest
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            print(f"  FAIL {label}: {type(exc).__name__}: {exc}")
            continue
        built.append(row)
        print(f"  ok   {label}: {len(row['frame_hashes'])} frame hash(es), {row['verified_location']}, {row['verified_date']}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_fps": fps,
        "frame_max_width": config.FRAME_SAMPLE_MAX_WIDTH,   # frames shrunk to this before hashing
        "hash": "imagehash.phash, 64-bit, hex",
        "clips": built,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(built), errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hash verified clips into data/archive/archive.json.")
    ap.add_argument("--manifest", type=Path, default=config.ARCHIVE_MANIFEST)
    ap.add_argument("--out", type=Path, default=config.ARCHIVE_JSON)
    ap.add_argument("--fps", type=float, default=config.PRIOR_SAMPLE_FPS)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if not args.manifest.is_file():
        print(f"No manifest at {args.manifest}. Create it (see the docstring of this script for the format).")
        return 1
    print(f"Building {args.out} from {args.manifest} at {args.fps:g} frame/s")
    n, errors = build(args.manifest, args.out, args.fps)
    print(f"\n{n} clip(s) written to {args.out}" + (f"; {len(errors)} problem(s) above" if errors else ""))
    if n == 0 and not errors:
        print("The manifest is empty: add verified clips to it and run again.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
