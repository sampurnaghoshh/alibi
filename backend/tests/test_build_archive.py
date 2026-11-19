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


def _scenes_video(make_video, path, w=1280, h=720, seconds=4, fps=10):
    """Distinct smooth scenes per second, wider than the 640 px hashing width."""
    rng = np.random.default_rng(7)
    scenes = [np.asarray(Image.fromarray(rng.integers(0, 256, (9, 16, 3), dtype=np.uint8))
                         .resize((w, h), Image.BICUBIC))[:, :, ::-1].copy() for _ in range(seconds)]
    return make_video(path, seconds=seconds, fps=fps, size=(w, h), frame_fn=lambda i: scenes[i // fps])


def test_archive_and_witness_hashes_are_identical(tmp_path, make_video):
    """The witness samples an uploaded copy with the same function; hashes must match the archive exactly."""
    import shutil

    from app import config
    from app.media import phash_hex, sample_frames

    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    _scenes_video(make_video, archive_dir / "clip.mp4")
    _scene(5).resize((1600, 1200)).save(archive_dir / "photo.jpg", quality=92)
    manifest = _write_manifest(archive_dir, [_entry("clip_v", "clip.mp4"), _entry("clip_p", "photo.jpg")])
    out = tmp_path / "archive.json"
    assert build_archive.main(["--manifest", str(manifest), "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["frame_max_width"] == config.FRAME_SAMPLE_MAX_WIDTH == 640
    clips = {c["clip_id"]: c for c in data["clips"]}

    # What the prior_appearance witness does with an upload stored somewhere else:
    uploads = tmp_path / "originals"
    uploads.mkdir()
    for name, cid in (("clip.mp4", "clip_v"), ("photo.jpg", "clip_p")):
        copy = uploads / f"in_1_{name}"
        shutil.copyfile(archive_dir / name, copy)
        frames = sample_frames(copy, config.PRIOR_SAMPLE_FPS)
        assert [phash_hex(img) for _, img in frames] == clips[cid]["frame_hashes"]
        assert [t for t, _ in frames] == clips[cid]["frame_times_s"]
        assert all(img.width == 640 for _, img in frames)           # downscaled before hashing


def test_frame_at_seeks_to_the_right_full_resolution_frame(tmp_path, make_video):
    """Seeking must return exactly the frame that decoding from the start reaches (pixel-identical)."""
    import cv2

    from app.media import frame_at

    video = make_video(tmp_path / "count.mp4", seconds=2.5, fps=10, size=(320, 240),
                       frame_fn=lambda i: np.full((240, 320, 3), 10 * i, dtype=np.uint8))
    cap = cv2.VideoCapture(str(video))
    decoded = []
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        decoded.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    cap.release()
    assert len(decoded) == 25

    for t, idx in ((0.0, 0), (1.0, 10), (1.05, 11), (2.3, 23), (99.0, 24)):
        ft, img = frame_at(video, t)
        assert ft == pytest.approx(idx / 10)
        assert img.size == (320, 240)                                    # ELA needs full resolution
        assert np.array_equal(np.asarray(img), decoded[idx])             # exactly that frame
        assert abs(int(np.asarray(img).mean()) - 10 * idx) <= 4          # and visibly not a neighbour


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
