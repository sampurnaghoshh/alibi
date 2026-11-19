"""Prior appearance witness (§6.4): perceptual-hash archive matching, tiny synthetic fixtures, no real footage."""

import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app import config
from app.claims.extract import extract_claims
from app.runner import run_witnesses
from app.witnesses import prior_appearance as P
from app.witnesses.base import InputRef, InvestigationContext

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_archive.py"
spec = importlib.util.spec_from_file_location("build_archive_for_prior", SCRIPT)
build_archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_archive)

FORBIDDEN = re.compile(config.FORBIDDEN_WORDS_RE, re.I)
POSTED = datetime(2026, 11, 19, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
CHENNAI = {"verified_location": "Chennai, Tamil Nadu", "verified_lat": 13.0827, "verified_lon": 80.2707}
PUNE = {"verified_location": "Pune, Maharashtra", "verified_lat": 18.5204, "verified_lon": 73.8567}
W, H, FPS = 320, 180, 5
ARCHIVE_SEEDS = [10, 11, 12, 13, 14, 15]          # one scene per second


def _scene(seed: int, w=W, h=H) -> np.ndarray:
    """Smooth, structured RGB scene (pHash needs real structure)."""
    rng = np.random.default_rng(seed)
    return np.asarray(Image.fromarray(rng.integers(0, 256, (9, 16, 3), dtype=np.uint8)).resize((w, h), Image.BICUBIC))


def _video(make_video, path: Path, seeds: list[int]) -> Path:
    scenes = [_scene(s)[:, :, ::-1].copy() for s in seeds]   # BGR for OpenCV
    return make_video(path, seconds=len(seeds), fps=FPS, size=(W, H), frame_fn=lambda i: scenes[i // FPS])


@pytest.fixture
def archive(tmp_path, make_video, monkeypatch):
    """archive(**entry_overrides) -> builds a 1-clip archive with build_archive.py and points config at it."""
    clip_dir = tmp_path / "archive"
    clip_dir.mkdir()
    _video(make_video, clip_dir / "flood.mp4", ARCHIVE_SEEDS)

    def _build(**over):
        entry = {"clip_id": "clip_001", "file": "flood.mp4", "title": "Waterlogged street",
                 "verified_date": "2026-11-18", "verified_by": "Team member (demo archive)",
                 "source_note": "Own phone recording", **CHENNAI}
        entry.update(over)
        entry = {k: v for k, v in entry.items() if v is not None}
        manifest = clip_dir / "manifest.json"
        manifest.write_text(json.dumps([entry]), encoding="utf-8")
        out = tmp_path / "archive.json"
        assert build_archive.main(["--manifest", str(manifest), "--out", str(out)]) == 0
        monkeypatch.setattr(config, "ARCHIVE_JSON", out)
        return out

    return _build


def _run(tmp_path: Path, path: Path, caption: str = "", mime="video/mp4"):
    inp = InputRef(file_id="in_1", name=path.name, mime=mime, size=1, sha256="0" * 64, path=path,
                   duration_s=None if mime.startswith("image") else 6.0)
    ctx = InvestigationContext(inv_id="test", caption=caption, posted_at=POSTED, inputs=[inp],
                               claims=extract_claims(caption, POSTED) if caption else [],
                               artifacts_dir=tmp_path / "artifacts")
    (f,) = run_witnesses(ctx, [P.PriorAppearanceWitness()])
    for text in [f.title, f.summary, *f.alternatives, *f.limitations]:
        assert not FORBIDDEN.search(text), text
    assert f.alternatives and f.limitations
    assert any(x.startswith("Archive is small (") for x in f.limitations) or f.details.get("archive_size") == 0
    return f


@pytest.fixture
def upload(tmp_path, make_video):
    """A separately encoded upload of the archived scenes (not a byte copy)."""
    (tmp_path / "up").mkdir()
    return _video(make_video, tmp_path / "up" / "reshared.mp4", ARCHIVE_SEEDS)


# --- match + claims ----------------------------------------------------------------

def test_match_and_agreeing_claims_is_consistent_high(tmp_path, archive, upload):
    archive()
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    assert (f.status, f.reliability) == ("consistent", "high")
    assert f.details["matched_frames"] == 6 and f.details["matched_fraction"] == 1.0
    assert f.details["min_hamming"] <= f.details["median_hamming"] <= config.PRIOR_MAX_HAMMING
    assert f.details["archive_size"] == 1
    assert set(f.claim_ids) == {"c2", "c3"}                          # location + time
    assert f.evidence[0].kind == "archive_match" and f.evidence[0].ref == "clip_001"
    assert [(l.start_s, l.end_s) for l in f.locations] == [(0.0, 6.0)]   # matched seconds merged
    assert "Archive is small (1 clips). In production this connects to reverse-image search and " \
           "fact-checker databases." in f.limitations


def test_date_disagreement_is_contradiction_high(tmp_path, archive, upload):
    archive(verified_date="2024-07-25")
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    assert (f.status, f.reliability) == ("contradiction", "high")
    assert f.title == "Frames match a clip verified as recorded in Chennai, Tamil Nadu on 25 Jul 2024"
    cmp = f.details["comparison"]
    assert cmp["date"]["result"] == "disagree" and cmp["date"]["days_apart"] > 2
    assert cmp["location"]["result"] == "agree"
    assert f.locations


def test_location_disagreement_is_contradiction_high(tmp_path, archive, upload):
    archive(**PUNE)
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    assert (f.status, f.reliability) == ("contradiction", "high")
    assert f.title == "Frames match a clip verified as recorded in Pune, Maharashtra on 18 Nov 2026"
    loc = f.details["comparison"]["location"]
    assert loc["result"] == "disagree" and loc["distance_km"] > 900
    assert f.details["comparison"]["date"]["result"] == "agree"


def test_dates_within_tolerance_agree(tmp_path, archive, upload):
    archive(verified_date="2026-11-16")           # 2 days before "yesterday" (2026-11-18): inside +/- 2 days
    assert _run(tmp_path, upload, "Flooding in Chennai yesterday").status == "consistent"


def test_match_without_comparable_claims_is_inconclusive_reporting_match(tmp_path, archive, upload):
    archive()
    f = _run(tmp_path, upload, "")
    assert (f.status, f.reliability) == ("inconclusive", "medium")
    assert f.details["matched_clip"]["clip_id"] == "clip_001" and f.evidence[0].kind == "archive_match"
    assert f.title.startswith("Frames match a clip verified as recorded in Chennai")


def test_place_name_without_coordinates_agrees_but_never_contradicts(tmp_path, archive, upload):
    archive(verified_location="Chennai, Tamil Nadu", verified_lat=None, verified_lon=None)
    f = _run(tmp_path, upload, "Flooding in Chennai yesterday")
    assert f.status == "consistent" and f.details["comparison"]["location"]["method"] == "place name"
    archive(verified_location="Madras (old name)", verified_lat=None, verified_lon=None)
    g = _run(tmp_path, upload, "Flooding in Chennai yesterday")
    assert g.status == "consistent"                                   # date agrees; location not compared
    assert g.details["comparison"]["location"]["result"] == "not_compared"


# --- no match / partial / photo ------------------------------------------------------

def test_no_match_is_inconclusive_low(tmp_path, archive, make_video):
    archive()
    other = _video(make_video, tmp_path / "other.mp4", [50, 51, 52, 53, 54, 55])
    f = _run(tmp_path, other, "Massive flooding in Chennai yesterday evening")
    assert (f.status, f.reliability) == ("inconclusive", "low")
    assert f.summary == "Not found in our archive of 1 verified clips. This does not mean the footage is new."
    assert f.details["matched_frames"] == 0 and f.details["min_hamming"] > config.PRIOR_MAX_HAMMING
    assert f.locations == []


def test_partial_match_below_threshold_is_not_a_match(tmp_path, archive, make_video):
    archive()
    # 2 of 10 sampled frames come from the archived clip: 2 < 3 frames and 20% < 30%
    mixed = _video(make_video, tmp_path / "mixed.mp4", [10, 11, 60, 61, 62, 63, 64, 65, 66, 67])
    f = _run(tmp_path, mixed, "Flooding in Chennai yesterday")
    assert f.status == "inconclusive" and f.reliability == "low"
    assert f.details["sampled_frames"] == 10 and f.details["matched_frames"] == 2
    assert f.details["matched_fraction"] == 0.2


def test_three_matching_frames_is_a_match(tmp_path, archive, make_video):
    archive()
    mixed = _video(make_video, tmp_path / "mixed3.mp4", [10, 11, 12, 60, 61, 62, 63, 64, 65, 66, 67])
    f = _run(tmp_path, mixed, "Flooding in Chennai yesterday")
    assert f.status == "consistent" and f.details["matched_frames"] == 3
    assert [(l.start_s, l.end_s) for l in f.locations] == [(0.0, 3.0)]


def test_photo_uses_strict_threshold(tmp_path, archive, monkeypatch):
    archive()
    photo = tmp_path / "still.jpg"
    Image.fromarray(_scene(12, 1280, 720)).save(photo, quality=85)  # a still of the archived scene, bigger + JPEG
    f = _run(tmp_path, photo, "Flooding in Chennai yesterday", mime="image/jpeg")
    assert f.status == "consistent"
    assert f.details["sampled_frames"] == 1 and f.details["hamming_threshold"] == config.PRIOR_SHORT_MAX_HAMMING == 4
    assert f.locations == []                                          # whole image, no timestamps
    assert any("stricter threshold" in x for x in f.limitations)
    monkeypatch.setattr(config, "PRIOR_SHORT_MAX_HAMMING", -1)       # nothing can pass -> no match
    assert _run(tmp_path, photo, "Flooding in Chennai yesterday", mime="image/jpeg").status == "inconclusive"


# --- archive problems ----------------------------------------------------------------

@pytest.mark.parametrize("content", ['{"built_at": "x", "sample_fps": 1.0, "clips": []}', "not json", None])
def test_empty_missing_or_broken_archive_is_inconclusive_low(tmp_path, upload, monkeypatch, content):
    path = tmp_path / "archive.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(config, "ARCHIVE_JSON", path)
    f = _run(tmp_path, upload, "Flooding in Chennai yesterday")
    assert (f.status, f.reliability) == ("inconclusive", "low")


def test_unreadable_upload_is_inconclusive_not_a_crash(tmp_path, archive):
    archive()
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video")
    f = _run(tmp_path, bad, "Flooding in Chennai yesterday")
    assert (f.status, f.reliability) == ("inconclusive", "low")


def test_matched_spans_merge_consecutive_samples():
    assert P.matched_spans([0, 1, 2, 3, 4, 5], [0, 1, 3, 4], 1.0, 5.5) == [(0, 2), (3, 5)]
    assert P.matched_spans([0, 1, 2], [2], 1.0, 2.4) == [(2, 2.4)]


# --- guards: ambiguity + near-duplicate gate -------------------------------------------

def _build_two(tmp_path, make_video, monkeypatch, second: dict, second_seeds=ARCHIVE_SEEDS):
    """Archive with clip_001 (Chennai, 2026-11-18) and clip_002 (same/other scenes, given context)."""
    d = tmp_path / "archive2"
    d.mkdir()
    _video(make_video, d / "a.mp4", ARCHIVE_SEEDS)
    _video(make_video, d / "b.mp4", second_seeds)
    base = {"title": "Street", "verified_by": "Team member", "source_note": "Own recording"}
    entries = [{"clip_id": "clip_001", "file": "a.mp4", "verified_date": "2026-11-18", **CHENNAI, **base},
               {"clip_id": "clip_002", "file": "b.mp4", **base, **second}]
    (d / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")
    out = tmp_path / "archive2.json"
    assert build_archive.main(["--manifest", str(d / "manifest.json"), "--out", str(out)]) == 0
    monkeypatch.setattr(config, "ARCHIVE_JSON", out)


def test_ambiguous_match_with_different_context_is_inconclusive_medium(tmp_path, make_video, monkeypatch, upload):
    _build_two(tmp_path, make_video, monkeypatch, {"verified_date": "2024-07-25", **PUNE})
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    assert (f.status, f.reliability) == ("inconclusive", "medium")
    assert f.details["guards"]["ambiguous"] is True
    assert {e.ref for e in f.evidence if e.kind == "archive_match"} == {"clip_001", "clip_002"}
    assert "clip_001" in f.summary and "clip_002" in f.summary


def test_second_match_with_same_context_is_not_ambiguous(tmp_path, make_video, monkeypatch, upload):
    _build_two(tmp_path, make_video, monkeypatch, {"verified_date": "2026-11-17", **CHENNAI})  # within +/- 2 days
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    assert (f.status, f.reliability) == ("consistent", "high")
    assert f.details["guards"]["ambiguous"] is False


def test_non_matching_second_clip_does_not_trigger_ambiguity(tmp_path, make_video, monkeypatch, upload):
    _build_two(tmp_path, make_video, monkeypatch, {"verified_date": "2024-07-25", **PUNE}, second_seeds=[70, 71, 72, 73])
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    assert (f.status, f.reliability) == ("consistent", "high")


def test_near_duplicate_gate_blocks_high_for_a_looser_match(tmp_path, archive, upload, monkeypatch):
    archive(verified_date="2024-07-25")                       # would be a contradiction
    monkeypatch.setattr(config, "PRIOR_HIGH_MAX_MEDIAN_HAMMING", -1)   # nothing counts as a near-identical copy
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    assert (f.status, f.reliability) == ("inconclusive", "medium")
    g = f.details["guards"]
    assert g["near_duplicate"] is False and g["rule_status"] == "contradiction"
    assert f.title.startswith("Frames resemble a clip verified as recorded in Chennai")
    assert f.evidence[0].kind == "archive_match" and f.locations       # the match is still reported


def test_refilmed_scene_passes_frame_threshold_but_not_the_gate(tmp_path, archive, make_video):
    """A 're-filmed' take: same scenes, slightly brighter and shifted. Frames still match (<= 8) but the
    median distance (6 here) is above the configured near-copy gate, so no high-reliability contradiction."""
    archive(verified_date="2024-07-25")

    def refilm(seed):
        img = _scene(seed).astype(np.int16)
        return np.clip(np.roll(img, 6, axis=1) + 18, 0, 255).astype(np.uint8)[:, :, ::-1].copy()
    scenes = [refilm(s) for s in ARCHIVE_SEEDS]
    take2 = make_video(tmp_path / "take2.mp4", seconds=6, fps=FPS, size=(W, H), frame_fn=lambda i: scenes[i // FPS])
    f = _run(tmp_path, take2, "Massive flooding in Chennai yesterday evening")
    assert f.details["matched_frames"] >= config.PRIOR_MIN_MATCHED_FRAMES     # it IS a frame-level match ...
    assert f.details["guards"]["rule_status"] == "contradiction"              # ... the rule alone would accuse ...
    assert (f.status, f.reliability) == ("inconclusive", "medium")           # ... but the gate stops it
