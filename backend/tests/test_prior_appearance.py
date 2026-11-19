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
        out = clip_dir / "archive.json"            # next to the manifest, as in data/archive/
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
    assert f.details["sample_fps"] == 4.0                                # 6 s clip < SHORT_CLIP_S -> 4 fps
    assert f.details["matched_frames"] == f.details["sampled_frames"] == 24 and f.details["matched_fraction"] == 1.0
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
    out = d / "archive.json"
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


# --- Live Desk "Time Machine" fields (§14) ----------------------------------------------

FIXTURE = Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "sample_investigation.json"


def _fixture_prior_details() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return next(f["details"] for f in data["findings"] if f["witness"] == "prior_appearance")


def test_time_machine_field_names_match_the_fixture(tmp_path, archive, upload):
    archive(verified_date="2024-07-25")
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    fx = _fixture_prior_details()
    for key in ("verified_date", "verified_location", "verified_by", "matched_clip_id", "matched_clip_title",
                "frame_matches"):
        assert key in fx and key in f.details, key
    assert set(f.details["frame_matches"][0]) == set(fx["frame_matches"][0])   # upload_s, archive_s, hamming, ..._frame
    assert (f.details["verified_date"], f.details["verified_location"]) == ("2024-07-25", "Chennai, Tamil Nadu")
    assert f.details["verified_by"] == "Team member (demo archive)"


def test_time_machine_jpgs_exist_and_are_small(tmp_path, archive, upload):
    archive(verified_date="2024-07-25")
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    pairs = f.details["frame_matches"]
    assert 1 <= len(pairs) <= config.PRIOR_TIME_MACHINE_MAX_PAIRS
    assert [p["upload_s"] for p in pairs] == sorted(p["upload_s"] for p in pairs)   # spread in time order
    for p in pairs:
        for ref in (p["upload_frame"], p["archive_frame"]):
            assert ref.startswith("artifacts/frames/") and ref.endswith(".jpg")
            path = tmp_path / ref
            assert path.is_file() and 0 < path.stat().st_size < 300_000
            with Image.open(path) as im:
                assert im.format == "JPEG" and im.width <= config.PRIOR_TIME_MACHINE_WIDTH
        assert p["hamming"] <= config.PRIOR_MAX_HAMMING
        assert int(p["archive_s"]) == int(p["upload_s"])   # same scene (one per second) on both sides


def test_time_machine_picks_best_pair_from_each_third():
    assert config.PRIOR_TIME_MACHINE_MAX_PAIRS == 3
    # 9 matched frames: thirds are upload frames 0-2, 3-5, 6-8
    pairs = [(i, i, d) for i, d in enumerate([5, 0, 3,   4, 6, 1,   2, 7, 3])]
    assert P.spread_pairs(pairs, 3) == [(1, 1, 0), (5, 5, 1), (6, 6, 2)]   # best of start / middle / end
    assert P.spread_pairs(pairs[:2], 3) == pairs[:2]                       # fewer than 3 matches: all of them


def test_time_machine_three_pairs_from_three_thirds(tmp_path, archive, upload):
    archive(verified_date="2024-07-25")
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    pairs = f.details["frame_matches"]
    duration = f.details["upload_duration_s"]
    thirds = [min(int(3 * p["upload_s"] / duration), 2) for p in pairs]
    assert len(pairs) == 3 and thirds == [0, 1, 2]                          # one pair in each third, in order


def test_missing_archive_clip_gives_null_archive_frame_and_a_limitation(tmp_path, archive, upload):
    archive(verified_date="2024-07-25")
    (tmp_path / "archive" / "flood.mp4").unlink()                # archive.json still lists it
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    assert (f.status, f.reliability) == ("contradiction", "high")   # the verdict does not depend on display frames
    pairs = f.details["frame_matches"]
    assert pairs and all(p["archive_frame"] is None for p in pairs)
    assert all((tmp_path / p["upload_frame"]).is_file() for p in pairs)
    assert any("archived source file for clip_001 was not found" in x for x in f.limitations)


def test_time_machine_failure_never_breaks_the_finding(tmp_path, archive, upload, monkeypatch):
    archive(verified_date="2024-07-25")
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(P, "_save_jpg", boom)
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    assert (f.status, f.reliability) == ("contradiction", "high")
    assert f.details["frame_matches"] == [] and f.details["verified_date"] == "2024-07-25"
    assert any("could not be saved for display" in x for x in f.limitations)


def test_photo_archive_entry_frame_is_saved(tmp_path, monkeypatch):
    d = tmp_path / "archive_p"
    d.mkdir()
    Image.fromarray(_scene(12, 1280, 720)).save(d / "still.png")
    (d / "manifest.json").write_text(json.dumps([{
        "clip_id": "photo_001", "file": "still.png", "title": "Flooded road", "verified_date": "2024-07-25",
        "verified_by": "Team member", "source_note": "Own photo", **CHENNAI}]), encoding="utf-8")
    out = d / "archive.json"
    assert build_archive.main(["--manifest", str(d / "manifest.json"), "--out", str(out)]) == 0
    monkeypatch.setattr(config, "ARCHIVE_JSON", out)
    up = tmp_path / "up.jpg"
    Image.fromarray(_scene(12, 1280, 720)).save(up, quality=85)
    f = _run(tmp_path, up, "Flooding in Chennai yesterday", mime="image/jpeg")
    (p,) = f.details["frame_matches"]
    assert p["archive_frame"] and (tmp_path / p["archive_frame"]).is_file()


def test_ambiguous_match_has_no_time_machine(tmp_path, make_video, monkeypatch, upload):
    _build_two(tmp_path, make_video, monkeypatch, {"verified_date": "2024-07-25", **PUNE})
    f = _run(tmp_path, upload, "Massive flooding in Chennai yesterday evening")
    assert f.status == "inconclusive" and "frame_matches" not in f.details and "verified_date" not in f.details


# --- short clips at 4 fps (one rule shared by build_archive.py and the witness) -------------

def test_sample_fps_for_rule(monkeypatch):
    from app.media import sample_fps_for
    monkeypatch.setattr(config, "SHORT_CLIP_S", 10.0)
    monkeypatch.setattr(config, "SHORT_CLIP_FPS", 4.0)
    assert [sample_fps_for(d) for d in (2.87, 9.99, 10.0, 25.0, None)] == [4.0, 4.0, 1.0, 1.0, 1.0]
    assert sample_fps_for(3.0, 0.5) == 4.0 and sample_fps_for(30.0, 0.5) == 0.5


def _two_length_archive(tmp_path, make_video):
    """Real build_archive.py on a short (3 s) and a long (12 s) synthetic clip."""
    d = tmp_path / "arch"
    d.mkdir()
    _video(make_video, d / "short.mp4", [20, 21, 22])
    _video(make_video, d / "long.mp4", list(range(30, 42)))
    base = {"verified_date": "2026-11-18", "verified_by": "Team member", "source_note": "Own recording", **CHENNAI}
    (d / "manifest.json").write_text(json.dumps([
        {"clip_id": "short", "file": "short.mp4", "title": "Short", **base},
        {"clip_id": "long", "file": "long.mp4", "title": "Long", **base}]), encoding="utf-8")
    out = d / "archive.json"
    assert build_archive.main(["--manifest", str(d / "manifest.json"), "--out", str(out)]) == 0
    return d, out


def test_builder_and_witness_hash_identical_frames_at_per_clip_fps(tmp_path, make_video, monkeypatch):
    from app.media import phash_hex, sample_fps_for, sample_frames
    d, out = _two_length_archive(tmp_path, make_video)
    data = json.loads(out.read_text(encoding="utf-8"))
    clips = {c["clip_id"]: c for c in data["clips"]}
    assert data["sample_fps"] == config.PRIOR_SAMPLE_FPS == 1.0              # top level kept for old readers
    assert clips["short"]["sample_fps"] == 4.0 and len(clips["short"]["frame_hashes"]) == 12
    assert clips["long"]["sample_fps"] == 1.0 and len(clips["long"]["frame_hashes"]) == 12
    monkeypatch.setattr(config, "ARCHIVE_JSON", out)
    for cid in ("short", "long"):
        up = tmp_path / f"upload_{cid}.mp4"
        up.write_bytes((d / f"{cid}.mp4").read_bytes())                     # the same clip, uploaded
        frames = sample_frames(up, sample_fps_for(clips[cid]["duration_s"]))
        assert [phash_hex(i) for _, i in frames] == clips[cid]["frame_hashes"]   # identical hashes ...
        assert [t for t, _ in frames] == clips[cid]["frame_times_s"]             # ... at identical times
        f = _run(tmp_path, up, "Flooding in Chennai yesterday")
        assert f.details["sample_fps"] == clips[cid]["sample_fps"]
        assert f.details["matched_clip"]["clip_id"] == cid and f.details["matched_clip"]["sample_fps"] == clips[cid]["sample_fps"]
        assert f.details["median_hamming"] == 0 and f.details["matched_frames"] == len(frames)
        assert (f.status, f.reliability) == ("consistent", "high")
        for p in f.details["frame_matches"]:
            assert p["archive_s"] in clips[cid]["frame_times_s"] and p["upload_s"] == p["archive_s"]


def test_old_archive_without_per_clip_fps_still_works(tmp_path, make_video, monkeypatch):
    d = tmp_path / "old"
    d.mkdir()
    _video(make_video, d / "flood.mp4", ARCHIVE_SEEDS)                      # 6 s: short under the new rule
    (d / "manifest.json").write_text(json.dumps([{
        "clip_id": "clip_old", "file": "flood.mp4", "title": "Old entry", "verified_date": "2024-07-25",
        "verified_by": "Team member", "source_note": "Built before per-clip fps", **CHENNAI}]), encoding="utf-8")
    out = d / "archive.json"
    monkeypatch.setattr(config, "SHORT_CLIP_S", 0)                          # build it the old way: 1 fps for all
    assert build_archive.main(["--manifest", str(d / "manifest.json"), "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    for c in data["clips"]:                                                 # old format: no per-clip fps / times
        c.pop("sample_fps"), c.pop("frame_times_s")
    out.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config, "SHORT_CLIP_S", 10.0)                       # the witness uses the new rule
    monkeypatch.setattr(config, "ARCHIVE_JSON", out)
    up = tmp_path / "reshared.mp4"
    up.write_bytes((d / "flood.mp4").read_bytes())
    f = _run(tmp_path, up, "Massive flooding in Chennai yesterday evening")
    assert (f.status, f.reliability) == ("contradiction", "high")          # 4 fps upload vs 1 fps archive still matches
    assert f.details["sample_fps"] == 4.0 and f.details["matched_clip"]["sample_fps"] == 1.0
    assert f.details["median_hamming"] == 0
    assert all(p["archive_s"] == float(int(p["archive_s"])) for p in f.details["frame_matches"])   # index / 1 fps


def test_spans_cover_uneven_sample_gaps():
    # 4 fps sampling of a 5 fps video lands at 0, 0.4, 0.6, 0.8, 1.0 ... : one continuous match, not fragments
    times = [0.0, 0.4, 0.6, 0.8, 1.0, 1.4]
    assert P.matched_spans(times, [0, 1, 2, 3, 4, 5], 0.25, 1.6) == [(0.0, 1.6)]
