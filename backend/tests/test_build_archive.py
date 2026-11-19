"""scripts/build_archive.py (§6.4): hashes verified clips, reports bad entries, keeps going."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.media import hamming, phash_hex, sample_frames
from tests.conftest import gradient, noise

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_archive.py"
spec = importlib.util.spec_from_file_location("build_archive", SCRIPT)
build_archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_archive)


def _entry(clip_id: str, file: str, **over) -> dict:
    e = {
        "clip_id": clip_id, "file": file, "title": f"Title {clip_id}",
        "verified_date": "2024-07-25", "verified_location": "Pune, Maharashtra",
        "verified_by": "Team member", "source_note": "Own recording",
    }
    e.update(over)
    return e


def _write_manifest(tmp_path: Path, entries: list) -> Path:
    m = tmp_path / "manifest.json"
    m.write_text(json.dumps(entries), encoding="utf-8")
    return m


def test_images_build_and_bad_entries_are_skipped(tmp_path):
    gradient().save(tmp_path / "a.jpg")
    noise(128, 128).save(tmp_path / "b.png")
    manifest = _write_manifest(tmp_path, [
        _entry("clip_001", "a.jpg", verified_lat=18.52, verified_lon=73.86),
        _entry("clip_002", "b.png"),
        _entry("clip_003", "missing.mp4"),
        _entry("clip_004", "a.jpg", verified_date="25/07/2024"),
        _entry("clip_001", "b.png"),                       # duplicate id
        {"clip_id": "clip_005"},                            # missing fields
    ])
    out = tmp_path / "archive.json"

    assert build_archive.main(["--manifest", str(manifest), "--out", str(out)]) == 1   # problems -> exit 1
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["sample_fps"] == 1.0
    clips = {c["clip_id"]: c for c in data["clips"]}
    assert set(clips) == {"clip_001", "clip_002"}                                      # good ones still written

    a = clips["clip_001"]
    assert a["verified_lat"] == 18.52 and a["verified_location"] == "Pune, Maharashtra"
    assert len(a["frame_hashes"]) == 1 and len(a["frame_hashes"][0]) == 16
    assert a["frame_times_s"] == [0.0] and a["duration_s"] is None
    assert len(a["sha256"]) == 64
    assert "verified_lat" not in clips["clip_002"]


def test_empty_manifest_is_fine(tmp_path):
    out = tmp_path / "archive.json"
    assert build_archive.main(["--manifest", str(_write_manifest(tmp_path, [])), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["clips"] == []


def test_missing_manifest(tmp_path):
    assert build_archive.main(["--manifest", str(tmp_path / "nope.json"), "--out", str(tmp_path / "a.json")]) == 1


def _scene(seed: int):
    """Smooth blobs with real structure (a pure gradient is degenerate for pHash)."""
    return noise(12, 12, seed).resize((256, 256), Image.BICUBIC)


def test_phash_matches_copies_not_different_scenes(tmp_path):
    img = _scene(1)
    img.save(tmp_path / "orig.png")
    img.resize((200, 200)).save(tmp_path / "copy.jpg", quality=60)   # re-encoded, resized copy
    _scene(2).save(tmp_path / "other.png")
    h = {n: phash_hex(sample_frames(tmp_path / n)[0][1]) for n in ("orig.png", "copy.jpg", "other.png")}
    assert hamming(h["orig.png"], h["copy.jpg"]) <= 8
    assert hamming(h["orig.png"], h["other.png"]) > 8


def test_video_sampled_at_one_frame_per_second(tmp_path, make_video):
    rng = np.random.default_rng(1)
    scenes = [rng.integers(0, 256, (64, 64, 3), dtype=np.uint8) for _ in range(3)]
    make_video(tmp_path / "clip.mp4", seconds=3, fps=10, frame_fn=lambda i: scenes[i // 10])
    manifest = _write_manifest(tmp_path, [_entry("clip_010", "clip.mp4")])
    out = tmp_path / "archive.json"

    assert build_archive.main(["--manifest", str(manifest), "--out", str(out)]) == 0
    [clip] = json.loads(out.read_text(encoding="utf-8"))["clips"]
    assert clip["frame_times_s"] == [0.0, 1.0, 2.0]
    assert len(clip["frame_hashes"]) == 3
    assert clip["duration_s"] == pytest.approx(3.0, abs=0.2)
