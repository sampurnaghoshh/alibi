"""
Shared media helpers: frame sampling and perceptual hashes. Owner: Sampurna.

scripts/build_archive.py and the prior_appearance witness MUST both use
sample_frames() + phash_hex() so archive and upload hashes are comparable.
OpenCV is imported lazily: images work with Pillow alone.
"""

from __future__ import annotations

import math
import mimetypes
from pathlib import Path
from typing import Iterator, Optional

from PIL import Image, ImageOps

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp"}


def media_kind(path: Path, mime: str | None = None) -> str:
    """'image' | 'video' | 'audio' | 'other' from mime (if given) or extension."""
    mime = mime or mimetypes.guess_type(path.name)[0] or ""
    top = mime.split("/", 1)[0]
    if top in ("image", "video", "audio"):
        return top
    ext = path.suffix.lower()
    return "image" if ext in IMAGE_EXTS else "video" if ext in VIDEO_EXTS else "other"


def load_image(path: Path) -> Image.Image:
    """RGB image with EXIF orientation applied."""
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def _cv2():
    try:
        import cv2  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError("OpenCV (opencv-contrib-python) is not installed; video frames unavailable") from exc
    return cv2


def video_info(path: Path) -> tuple[float, int]:
    """(fps, frame_count). fps falls back to 30 when the container doesn't say."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video {path.name}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        return float(fps), int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        cap.release()


def _fit_bgr(bgr, max_width: Optional[int]):
    """Shrink a BGR frame to max_width (keeping aspect) BEFORE colour conversion: cheap on 4K."""
    if max_width and bgr.shape[1] > max_width:
        cv2 = _cv2()
        h = max(1, round(bgr.shape[0] * max_width / bgr.shape[1]))
        bgr = cv2.resize(bgr, (max_width, h), interpolation=cv2.INTER_AREA)
    return bgr


def _fit_image(img: Image.Image, max_width: Optional[int]) -> Image.Image:
    if max_width and img.width > max_width:
        h = max(1, round(img.height * max_width / img.width))
        img = img.resize((max_width, h), Image.LANCZOS)
    return img


def _to_rgb_image(bgr) -> Image.Image:
    return Image.fromarray(_cv2().cvtColor(bgr, _cv2().COLOR_BGR2RGB))


def _iter_video(path: Path, every_s: float, max_width: Optional[int] = None) -> Iterator[tuple[float, Image.Image]]:
    """Keep one frame per `every_s` seconds. Frames in between are only grab()-bed (no colour
    conversion); kept frames are shrunk to max_width before conversion. Measured on a 4K iPhone
    clip this beats seeking to every sample (seeking re-decodes from the previous keyframe)."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video {path.name}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        idx, next_t = 0, 0.0
        while cap.grab():
            t = idx / fps
            if t + 1e-6 >= next_t:
                ok, bgr = cap.retrieve()
                if ok:
                    yield round(t, 3), _to_rgb_image(_fit_bgr(bgr, max_width))
                next_t += every_s
            idx += 1
    finally:
        cap.release()


def sample_frames(path: Path, fps: float = 1.0, mime: str | None = None,
                  max_width: Optional[int] = None) -> list[tuple[float, Image.Image]]:
    """[(t_seconds, RGB frame)]: images give one frame at t=0, videos one frame per 1/fps seconds.

    Frames are shrunk to `max_width` (default config.FRAME_SAMPLE_MAX_WIDTH = 640 px) before they
    are returned. The archive builder and the prior_appearance witness both call this with the
    defaults, so their hashes are computed from identical pixels.
    """
    from app import config  # noqa: PLC0415 - avoid a config import cycle at module load

    max_width = config.FRAME_SAMPLE_MAX_WIDTH if max_width is None else max_width
    kind = media_kind(path, mime)
    if kind == "image":
        return [(0.0, _fit_image(load_image(path), max_width))]
    if kind == "video":
        return list(_iter_video(path, 1.0 / fps, max_width))
    raise ValueError(f"{path.name}: not an image or video")


def frame_at(path: Path, t: float) -> tuple[float, Image.Image]:
    """The full-resolution video frame at (or just after) t seconds, found by seeking.

    Seeking decodes only from the nearest keyframe (0.6 s instead of 16.7 s for the middle of a
    4K iPhone clip). If the container can't seek, it falls back to decoding from the start."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video {path.name}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        idx = max(0, math.ceil(t * fps - 1e-6))
        if count:
            idx = min(idx, count - 1)
        if cap.set(cv2.CAP_PROP_POS_FRAMES, idx):
            ok, bgr = cap.read()
            if ok:
                return round(idx / fps, 3), _to_rgb_image(bgr)
    finally:
        cap.release()
    for ft, img in _iter_video(path, 0.0):     # fallback: sequential decode
        if ft + 1e-6 >= t:
            return ft, img
    raise RuntimeError(f"no frames decoded from {path.name}")


def phash_hex(img: Image.Image) -> str:
    """64-bit perceptual hash as 16 hex characters."""
    import imagehash  # noqa: PLC0415

    return str(imagehash.phash(img))


def hamming(a: str, b: str) -> int:
    """Bit distance between two phash_hex() strings."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")
