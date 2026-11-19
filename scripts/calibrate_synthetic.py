r"""
Print the synthetic-signals scores for media files (calibration for config.SYNTH_*).

Genuine media (demo/controls, the archive clips) must never produce a contradiction; AI images in
demo/ai_test/ show how strong the signals get. Usage (from the repo root):
    backend.venv\Scripts\python.exe scripts\calibrate_synthetic.py demo/controls demo/ai_test
"""

from __future__ import annotations

import mimetypes
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.witnesses.base import InputRef, InvestigationContext  # noqa: E402
from app.witnesses.synthetic_signals import SyntheticSignalsWitness  # noqa: E402

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".mp4", ".mov", ".m4a", ".mp3", ".wav", ".ogg", ".bmp"}


def files(args):
    for a in args:
        p = (REPO / a) if not Path(a).is_absolute() else Path(a)
        yield from sorted(x for x in (p.rglob("*") if p.is_dir() else [p]) if x.suffix.lower() in EXTS)


def main(args):
    from datetime import datetime, timezone

    w = SyntheticSignalsWitness()
    tmp = Path(tempfile.mkdtemp())
    for path in files(args or ["demo/controls", "demo/ai_test"]):
        mime = {".webp": "image/webp", ".mov": "video/quicktime", ".heic": "image/heic"}.get(
            path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or ""
        inp = InputRef("in_1", path.name, mime, path.stat().st_size, "", path)
        ctx = InvestigationContext("calib", "", datetime.now(timezone.utc), [inp], [], tmp)
        for f in w.run(ctx):
            d = f.details
            nums = {k: d[k] for k in ("spectrum", "noise", "median_diff", "spikes_s", "flicker_rate", "noise_floor_dbfs",
                                      "cutoff_hz", "cutoff_drop_db", "spectral_flatness", "sample_rate") if k in d}
            if "spectrum" in nums:
                nums["spectrum"] = {k: nums["spectrum"][k] for k in ("peak_count", "max_peak_z")}
                nums["noise"] = nums["noise"]["flat_noise_std"]
                nums["camera"] = d["camera"]["camera_fields"], d["camera"]["jpeg_tables"]
            print(f"{path.relative_to(REPO) if REPO in path.parents else path} [{d.get('check')}] "
                  f"{f.status}/{f.reliability} strength={d.get('strength')} {nums}")


if __name__ == "__main__":
    main(sys.argv[1:])
