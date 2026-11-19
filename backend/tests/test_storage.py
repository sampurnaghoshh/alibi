"""Storage: originals are hashed, read-only, never overwritten; files outside the run cannot be served."""

import hashlib
import io
import os

import pytest

from app.storage import RunStore, list_investigations, parse_posted_at


def test_save_original_hash_readonly_and_history(tmp_path):
    store = RunStore.create(root=tmp_path)
    data = b"not really a video"
    info, path = store.save_original("in_1", "../../evil name.mp4", io.BytesIO(data), "video/mp4")

    assert info.sha256 == hashlib.sha256(data).hexdigest()
    assert info.size == len(data)
    assert info.stored_path == "originals/in_1_evil_name.mp4"   # name sanitised, stays inside originals/
    assert not os.access(path, os.W_OK)                          # read-only
    assert store.verify_original(info)
    [ev] = store.history()
    assert ev.event == "file_received" and info.sha256 in ev.detail

    with pytest.raises(FileExistsError):                         # never overwrite an original
        store.save_original("in_1", "evil name.mp4", io.BytesIO(b"x"))


def test_resolve_blocks_traversal(tmp_path):
    store = RunStore.create(root=tmp_path)
    (store.artifacts_dir / "a.json").write_text("{}")
    store.history_path.write_text("")
    assert store.resolve("artifacts/a.json").name == "a.json"
    for bad in ("history.jsonl", "../x", "artifacts/../history.jsonl", "artifacts/missing.png"):
        with pytest.raises(FileNotFoundError):
            store.resolve(bad)


def test_bad_ids_rejected():
    for bad in ("../etc", "inv_1", "inv_20261119_101500_AB12"):
        with pytest.raises(ValueError):
            RunStore(bad)


def test_list_skips_junk(tmp_path):
    (tmp_path / "_cli").mkdir()
    RunStore.create(root=tmp_path)            # no investigation.json yet
    assert list_investigations(tmp_path) == []


def test_video_duration_without_ffprobe(tmp_path, make_video, monkeypatch):
    """No ffprobe on PATH -> duration still comes from OpenCV."""
    import app.storage as storage
    monkeypatch.setattr(storage.shutil, "which", lambda name: None)
    video = make_video(tmp_path / "clip.mp4", seconds=3, fps=10)
    assert storage.probe_duration(video, "video/mp4") == pytest.approx(3.0, abs=0.2)
    assert storage.probe_duration(video, "image/png") is None


@pytest.mark.parametrize("ffprobe_out", ["-12.5", "0", "N/A", "", "nan", "inf", "99999999"])
def test_bad_ffprobe_duration_falls_back_to_clip_duration(tmp_path, make_video, monkeypatch, ffprobe_out):
    """Garbage from ffprobe is never stored; the shared media.clip_duration answers instead."""
    import subprocess as sp

    import app.storage as storage
    monkeypatch.setattr(storage.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(storage.subprocess, "run",
                        lambda *a, **k: sp.CompletedProcess(a, 0, stdout=ffprobe_out + "\n", stderr=""))
    video = make_video(tmp_path / "clip.mp4", seconds=3, fps=10)
    assert storage.probe_duration(video, "video/mp4") == pytest.approx(3.0, abs=0.2)


@pytest.mark.parametrize("opencv", [-9.2e15, 0, None, float("nan"), 10 ** 9])
def test_no_negative_or_absurd_duration_without_ffprobe(tmp_path, monkeypatch, opencv):
    """No ffprobe and a broken frame count (e.g. a streamed Matroska file) -> None, never a bogus number."""
    import app.media as media
    import app.storage as storage
    monkeypatch.setattr(storage.shutil, "which", lambda name: None)
    monkeypatch.setattr(media, "clip_duration", lambda path, mime=None: opencv)
    clip = tmp_path / "stream.mkv"
    clip.write_bytes(b"\0" * 64)
    assert storage.probe_duration(clip, "video/x-matroska") is None
    assert storage.probe_duration(clip, "audio/mpeg") is None          # audio without ffprobe: unknown


def test_probe_duration_uses_the_shared_clip_duration(tmp_path, make_video, monkeypatch):
    import app.media as media
    import app.storage as storage
    monkeypatch.setattr(storage.shutil, "which", lambda name: None)
    video = make_video(tmp_path / "clip.mp4", seconds=3, fps=10)
    assert storage.probe_duration(video, "video/mp4") == media.clip_duration(video, "video/mp4")


def test_parse_posted_at():
    assert parse_posted_at("2026-11-19").utcoffset().total_seconds() == 5.5 * 3600
    assert parse_posted_at("2026-11-19T10:00:00Z").utcoffset().total_seconds() == 0
    with pytest.raises(ValueError):
        parse_posted_at("yesterday")
