"""Shared test helpers: tiny synthetic media (no real footage needed)."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def gradient(w: int = 256, h: int = 256) -> Image.Image:
    """Smooth image: compresses almost losslessly, so ELA error is near zero."""
    x = np.linspace(0, 255, w, dtype=np.float32)
    y = np.linspace(0, 255, h, dtype=np.float32)[:, None]
    rgb = np.stack([np.broadcast_to(x, (h, w)), np.broadcast_to(y, (h, w)), np.full((h, w), 128.0)], axis=2)
    return Image.fromarray(rgb.astype(np.uint8))


def noise(w: int, h: int, seed: int = 0) -> Image.Image:
    return Image.fromarray(np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8))


@pytest.fixture
def make_video():
    """make_video(path, seconds=3, fps=10, frame_fn=None) -> path. Skips if OpenCV is missing."""
    cv2 = pytest.importorskip("cv2")

    def _make(path: Path, seconds: float = 3.0, fps: int = 10, size: int = 64, frame_fn=None) -> Path:
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (size, size))
        assert writer.isOpened(), "OpenCV cannot write mp4v on this machine"
        for i in range(int(seconds * fps)):
            frame = frame_fn(i) if frame_fn else np.full((size, size, 3), (i * 7) % 255, dtype=np.uint8)
            writer.write(frame)
        writer.release()
        return path

    return _make
