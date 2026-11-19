"""
ELA heatmap (CLAUDE.md §6.7). Owner: Sampurna (taken over from Adithi).
Family: pixels. Reliability: ALWAYS low. Status: ALWAYS inconclusive. Always a weak indicator.

Error-level analysis: re-save the picture as JPEG (quality 90), take the absolute difference
x15, and save it as a heatmap. Regions that were compressed differently from the rest can
stand out. Many innocent things also stand out (edges, text, textures, resizing), so ELA is
a visual aid for a human, never a verdict.

For videos, the keyframe nearest the middle of the clip is analysed.

Run alone (from backend/):
    python -m app.witnesses.ela --file ../demo/controls/photo.jpg
    python -m app.witnesses.ela --file ../demo/case_a/clip.mp4
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image

from app import config
from app.media import frame_at, load_image, video_info
from app.schemas import Finding
from app.witnesses.base import InputRef, InvestigationContext, Witness, run_cli

ALTERNATIVES = [
    "High-contrast edges, text, fine textures and noise naturally show higher error levels",
    "Resizing, filters, screenshots and repeated re-compression change error levels across the picture",
    "Heavy re-compression (messaging apps, social platforms) flattens ELA differences and can hide or mimic edits",
]
LIMITATIONS = [
    "ELA is a visual aid only and never a verdict",
    "Not calibrated: the hotspot thresholds only choose the wording and the highlighted region",
]


@dataclass
class ElaResult:
    heatmap: Image.Image
    mean_level: float          # mean absolute difference, 0-255, before amplification
    p99_level: float
    max_level: float
    hotspot_fraction: float    # share of blocks flagged as hotspots
    hotspot_bbox: Optional[list[float]]   # normalized [x, y, w, h] around hotspot blocks, if localised


def error_level_analysis(img: Image.Image) -> ElaResult:
    """Pure function: RGB image -> heatmap + summary numbers."""
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=config.ELA_JPEG_QUALITY)
    buf.seek(0)
    resaved = Image.open(buf).convert("RGB")

    diff = np.abs(np.asarray(img, dtype=np.int16) - np.asarray(resaved, dtype=np.int16))
    heat = np.clip(diff * config.ELA_AMPLIFICATION, 0, 255).astype(np.uint8)
    level = diff.mean(axis=2)                                # per-pixel error, 0-255

    # Block means: where is the error concentrated?
    b = config.ELA_BLOCK_PX
    h, w = level.shape
    bh, bw = h // b, w // b
    fraction, bbox = 0.0, None
    if bh and bw:
        blocks = level[: bh * b, : bw * b].reshape(bh, b, bw, b).mean(axis=(1, 3))
        threshold = max(config.ELA_HOTSPOT_RATIO * float(np.median(blocks)), config.ELA_HOTSPOT_MIN_LEVEL)
        hot = blocks >= threshold
        fraction = float(hot.mean())
        if hot.any() and fraction <= config.ELA_HOTSPOT_MAX_FRACTION:
            rows, cols = np.where(hot)
            x0, y0 = round(cols.min() * b / w, 4), round(rows.min() * b / h, 4)
            x1, y1 = min((cols.max() + 1) * b / w, 1.0), min((rows.max() + 1) * b / h, 1.0)
            # Round width/height DOWN so x + w never exceeds 1 (the contract rejects that).
            bbox = [x0, y0, math.floor((x1 - x0) * 1e4) / 1e4, math.floor((y1 - y0) * 1e4) / 1e4]

    return ElaResult(
        heatmap=Image.fromarray(heat),
        mean_level=round(float(level.mean()), 3),
        p99_level=round(float(np.percentile(level, 99)), 3),
        max_level=round(float(level.max()), 3),
        hotspot_fraction=round(fraction, 4),
        hotspot_bbox=bbox,
    )


class ElaWitness(Witness):
    name = "ela"
    label = "ELA"
    implemented = True

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(ctx.inputs_of("image") or ctx.inputs_of("video"))

    def run(self, ctx: InvestigationContext) -> list[Finding]:
        targets = [i for i in ctx.inputs if i.kind in ("image", "video")][: config.ELA_MAX_INPUTS]
        return [self._analyse(ctx, inp, n) for n, inp in enumerate(targets, 1)]

    def _analyse(self, ctx: InvestigationContext, inp: InputRef, n: int) -> Finding:
        """One finding per input. Errors stay local to this input (principle 5)."""
        try:
            frame_s: Optional[float] = None
            if inp.kind == "image":
                img = load_image(inp.path)
            else:
                fps, count = video_info(inp.path)
                frame_s, img = frame_at(inp.path, (count / fps) / 2 if count else 0.0)
            res = error_level_analysis(img)
        except Exception as exc:  # noqa: BLE001
            return self.finding(
                n=n, status="inconclusive", reliability="low",
                title="Error-level analysis could not be run",
                summary=f"The picture in {inp.name} could not be analysed, so ELA gives no indication either way.",
                alternatives=ALTERNATIVES,
                limitations=[f"Error: {type(exc).__name__}: {exc}", *LIMITATIONS],
                details={"file_id": inp.file_id, "error": f"{type(exc).__name__}: {exc}"},
            )

        tag = inp.file_id if frame_s is None else f"{inp.file_id}_{frame_s:.1f}s"
        name = f"ela_{tag}.png"
        res.heatmap.save(ctx.artifact_path(name))
        where = "the image" if frame_s is None else f"the keyframe at {frame_s:.1f} s"

        if res.hotspot_bbox:
            title = "Localised error-level differences visible in the heatmap"
            summary = (f"Error-level analysis of {where} shows a region with higher error levels than the rest "
                       f"({res.hotspot_fraction:.1%} of blocks). This is a visual cue for a human reviewer only.")
            bbox = res.hotspot_bbox
        else:
            title = "No strong localised compression anomaly"
            summary = (f"Error-level analysis of {where} shows no isolated region of higher error levels "
                       f"(mean level {res.mean_level:.1f} of 255).")
            bbox = [0.0, 0.0, 1.0, 1.0]

        limitations = list(LIMITATIONS)
        if inp.kind == "video":
            limitations.append("Video frames come from a video codec, so ELA is less meaningful than for original JPEG photos")
        elif inp.mime not in ("image/jpeg", "image/jpg"):
            limitations.append(f"The input is {inp.mime}, not JPEG; ELA is designed for JPEG photos")

        return self.finding(
            n=n, status="inconclusive", reliability="low",   # always, by design (§6.7)
            title=title, summary=summary,
            alternatives=ALTERNATIVES, limitations=limitations,
            details={
                "file_id": inp.file_id,
                "frame_s": frame_s,
                "jpeg_quality": config.ELA_JPEG_QUALITY,
                "amplification": config.ELA_AMPLIFICATION,
                "heatmap": ctx.artifact_ref(name),
                "mean_level": res.mean_level,
                "p99_level": res.p99_level,
                "max_level": res.max_level,
                "hotspot_fraction": res.hotspot_fraction,
                "hotspot_bbox": res.hotspot_bbox,
            },
            evidence=[{"kind": "artifact", "ref": ctx.artifact_ref(name), "label": f"ELA heatmap ({where})"}],
            locations=[{"kind": "region", "file_id": inp.file_id, "frame_s": frame_s, "bbox": bbox}],
        )


if __name__ == "__main__":
    run_cli(ElaWitness())
