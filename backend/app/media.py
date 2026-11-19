"""
Shared media helpers: frame sampling and perceptual hashes. Owner: Sampurna.

scripts/build_archive.py and the prior_appearance witness MUST both use
sample_frames() + phash_hex() so archive and upload hashes are comparable.
OpenCV is imported lazily: images work with Pillow alone.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Iterator

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


def _iter_video(path: Path, every_s: float) -> Iterator[tuple[float, Image.Image]]:
    """Decode sequentially and keep one frame per `every_s` seconds (seeking is unreliable across codecs)."""
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
                    yield round(t, 3), Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                next_t += every_s
            idx += 1
    finally:
        cap.release()


def sample_frames(path: Path, fps: float = 1.0, mime: str | None = None) -> list[tuple[float, Image.Image]]:
    """[(t_seconds, RGB frame)]: images give one frame at t=0, videos one frame per 1/fps seconds."""
    kind = media_kind(path, mime)
    if kind == "image":
        return [(0.0, load_image(path))]
    if kind == "video":
        return list(_iter_video(path, 1.0 / fps))
    raise ValueError(f"{path.name}: not an image or video")


def frame_at(path: Path, t: float) -> tuple[float, Image.Image]:
    """The decoded video frame closest to t seconds (sequential decode, fine for short clips)."""
    best = None
    for ft, img in _iter_video(path, 0.0):
        if best is None or abs(ft - t) < abs(best[0] - t):
            best = (ft, img)
        if ft > t:
            break
    if best is None:
        raise RuntimeError(f"no frames decoded from {path.name}")
    return best


def phash_hex(img: Image.Image) -> str:
    """64-bit perceptual hash as 16 hex characters."""
    import imagehash  # noqa: PLC0415

    return str(imagehash.phash(img))


def hamming(a: str, b: str) -> int:
    """Bit distance between two phash_hex() strings."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")
