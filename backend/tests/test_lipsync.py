"""Lip-sync synchrony indicator (§6.5): missing-model safety + the pure analysis functions."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from app import config
from app.runner import run_witnesses
from app.witnesses import lipsync as L
from app.witnesses.base import InputRef, InvestigationContext


def _ctx(tmp_path: Path) -> InvestigationContext:
    video = InputRef(file_id="in_1", name="clip.mp4", mime="video/mp4", size=1, sha256="0" * 64,
                     path=tmp_path / "clip.mp4", duration_s=3.0)
    return InvestigationContext(inv_id="test", caption="", posted_at=datetime.now(timezone.utc),
                                inputs=[video], claims=[], artifacts_dir=tmp_path / "artifacts")


def test_missing_face_model_is_inconclusive_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LIPSYNC_FACE_MODEL", tmp_path / "nope" / "face_landmarker.task")
    findings = L.LipsyncWitness().run(_ctx(tmp_path))
    assert len(findings) == 1
    f = findings[0]
    assert (f.witness, f.family, f.status, f.reliability) == ("lipsync", "signal", "inconclusive", "low")
    assert "face model not found" in f.limitations
    assert f.alternatives and all(a.strip() for a in f.alternatives)


def test_missing_face_model_through_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LIPSYNC_FACE_MODEL", tmp_path / "missing.task")
    findings = run_witnesses(_ctx(tmp_path), [L.LipsyncWitness()])
    assert [f.status for f in findings] == ["inconclusive"]
    assert "face model not found" in findings[0].limitations


def _speechy(n: int, seed: int = 0) -> np.ndarray:
    """Smooth random 'mouth' signal, roughly syllable-rate at 30 fps."""
    rng = np.random.default_rng(seed)
    return np.convolve(rng.random(n), np.ones(5) / 5, mode="same")


def test_clip_sync_recovers_planted_lag_with_clear_peak():
    fps, n = 30.0, 600
    mouth = _speechy(n)
    audio = np.roll(mouth, 4) + 0.05 * np.random.default_rng(1).standard_normal(n)  # audio 4 frames late
    sync = L.clip_sync(mouth, audio, np.ones(n, bool), fps)
    assert sync["lag_s"] == round(4 / fps, 3)        # + = audio later than the mouth
    assert sync["corr"] > 0.9 and sync["prominence"] > 0.3


def test_unrelated_audio_has_a_flat_curve():
    fps, n = 30.0, 600
    synced = L.clip_sync(_speechy(n, 0), _speechy(n, 0), np.ones(n, bool), fps)
    unrelated = L.clip_sync(_speechy(n, 0), _speechy(n, 7), np.ones(n, bool), fps)
    assert unrelated["prominence"] < synced["prominence"]
    assert unrelated["corr"] < 0.3


def test_too_little_speech_is_none_and_insufficient_signal():
    n = 300
    speech = np.zeros(n, bool)
    speech[:10] = True                                # 0.33 s of speech < LIPSYNC_MIN_SPEECH_S
    assert L.clip_sync(_speechy(n), _speechy(n), speech, 30.0) is None
    s = L.summarize(1.0, None, [], has_audio=True)
    assert s["status"] == "insufficient_signal"       # absence of evidence, never a contradiction


def test_flat_audio_has_no_speech():
    speech, _ = L.speech_mask(np.full(500, -40.0) + np.random.default_rng(0).normal(0, 0.5, 500))
    assert not speech.any()


def test_no_face_or_no_audio_is_not_applicable():
    assert L.summarize(0.2, None, [], has_audio=True)["status"] == "not_applicable"
    assert L.summarize(1.0, None, [], has_audio=False)["status"] == "not_applicable"


def test_short_gaps_filled_long_gaps_kept():
    x = np.array([1.0, np.nan, np.nan, 4.0] + [np.nan] * 10 + [5.0])
    y = L._fill_short_gaps(x, max_gap=5)
    assert np.allclose(y[:4], [1, 2, 3, 4])
    assert np.isnan(y[4:14]).all()


# --- witness run() with a stubbed analysis ------------------------------------

def _fake_analysis(status: str, lag=0.30, corr=0.05, prom=0.04, has_audio=True, face_h=0.40) -> L.ClipAnalysis:
    fps, n = 30.0, 300
    probe = L.MediaProbe(width=640, height=360, rotation=0, video_start_s=0.0,
                         audio_start_s=0.0 if has_audio else None, duration_s=10.0, native_fps=30.0)
    t = np.arange(n) / fps
    mouth = L.MouthSeries(t=t, raw=_speechy(n), mouth=_speechy(n), face_found=np.ones(n, bool), frame_size=(640, 360),
                          face_height=np.full(n, face_h))
    windows = [{"start_s": float(i), "end_s": float(i + 3), "face_fraction": 1.0, "speech_fraction": 0.8,
                "is_speech": True, "corr": c, "best_lag_s": 0.1, "skip": None}
               for i, c in enumerate([0.4, -0.3, -0.2, 0.5, -0.4, 0.3, 0.2, 0.1])]
    sync = {"lag_s": lag, "corr": corr, "prominence": prom, "corr_at_zero": 0.01, "speech_frames": 200,
            "lags_s": [-0.1, 0.0, 0.1], "curve": [0.0, 0.01, 0.05]}
    summary = {"face_fraction": 1.0, "speech_window_fraction": 1.0, "lag_s": lag, "corr": corr,
               "prominence": prom, "face_height": face_h, "status": status, "reason": "test"}
    return L.ClipAnalysis(probe, mouth, np.linspace(-60, -20, n), np.ones(n, bool), {}, {"velocity": sync},
                          windows, summary, fps)


@pytest.fixture
def stub(tmp_path, monkeypatch):
    model = tmp_path / "face_landmarker.task"
    model.write_bytes(b"x")
    monkeypatch.setattr(config, "LIPSYNC_FACE_MODEL", model)
    monkeypatch.setattr(config, "LIPSYNC_MOUTH_FEATURE", "velocity")

    def _set(analysis):
        monkeypatch.setattr(L, "analyze_clip", lambda path, fps=None: analysis)
        ctx = _ctx(tmp_path)
        return run_witnesses(ctx, [L.LipsyncWitness()]), ctx

    return _set


FORBIDDEN = re.compile(config.FORBIDDEN_WORDS_RE, re.I)


def _check_text(f):
    for text in [f.title, f.summary, *f.alternatives, *f.limitations]:
        assert not FORBIDDEN.search(text), text
    assert f.alternatives and f.limitations


def test_contradiction_held_back_as_inconclusive_low(stub, monkeypatch):
    monkeypatch.setattr(config, "LIPSYNC_ALLOW_CONTRADICTION", False)
    (f,), ctx = stub(_fake_analysis("contradiction"))
    assert (f.status, f.reliability) == ("inconclusive", "low")
    assert f.title == "Lip-sync synchrony indicator: weak or unclear"
    assert f.details["rule_status"] == "contradiction"
    assert (f.details["lag_s"], f.details["corr"], f.details["prominence"]) == (0.30, 0.05, 0.04)
    # worst 3 windows (corr -0.4, -0.3, -0.2 at 4-7 s, 1-4 s, 2-5 s) -> merged timestamps
    assert [(loc.start_s, loc.end_s) for loc in f.locations] == [(1.0, 5.0), (4.0, 7.0)] or \
        [(loc.start_s, loc.end_s) for loc in f.locations] == [(1.0, 7.0)]
    assert all(loc.kind == "timestamp" and loc.file_id == "in_1" for loc in f.locations)
    assert any("Calibrated on only 5 genuine clips" in x for x in f.limitations)
    _check_text(f)

    series = json.loads((ctx.artifacts_dir / "lipsync_series.json").read_text(encoding="utf-8"))
    for key in ("t", "mouth", "audio", "window_lags", "window_corrs"):
        assert isinstance(series[key], list) and series[key], key
    assert len(series["t"]) == len(series["mouth"]) == len(series["audio"])
    assert len(series["window_lags"]) == len(series["window_corrs"]) == 8
    assert {"kind": "artifact", "ref": "artifacts/lipsync_series.json"}.items() <= f.evidence[0].model_dump().items()


def test_contradiction_allowed_is_still_capped_low(stub, monkeypatch):
    monkeypatch.setattr(config, "LIPSYNC_ALLOW_CONTRADICTION", True)
    (f,), _ = stub(_fake_analysis("contradiction"))
    assert (f.status, f.reliability) == ("contradiction", "low")    # LIPSYNC_MAX_RELIABILITY fallback
    assert f.locations
    _check_text(f)


def test_consistent_is_held_back_while_uncalibrated(stub, monkeypatch):
    monkeypatch.setattr(config, "LIPSYNC_ALLOW_CONTRADICTION", False)
    (f,), _ = stub(_fake_analysis("consistent", lag=0.0, corr=0.4, prom=0.3))
    assert (f.status, f.reliability) == ("inconclusive", "low")      # ALWAYS inconclusive until recalibration
    assert f.title == "Lip-sync synchrony indicator: weak or unclear"
    assert f.details["rule_status"] == "consistent"
    _check_text(f)


def test_consistent_when_calibrated_is_low_without_locations(stub, monkeypatch):
    monkeypatch.setattr(config, "LIPSYNC_ALLOW_CONTRADICTION", True)
    (f,), _ = stub(_fake_analysis("consistent", lag=0.0, corr=0.4, prom=0.3))
    assert (f.status, f.reliability) == ("consistent", "low")
    assert f.locations == []
    _check_text(f)


@pytest.mark.parametrize("rule_status", ["consistent", "contradiction", "inconclusive", "insufficient_signal"])
@pytest.mark.parametrize("allow", [False, True])
def test_face_too_small_is_inconclusive_low(stub, monkeypatch, rule_status, allow):
    monkeypatch.setattr(config, "LIPSYNC_ALLOW_CONTRADICTION", allow)
    monkeypatch.setattr(config, "LIPSYNC_MIN_FACE_HEIGHT", 0.25)
    (f,), _ = stub(_fake_analysis(rule_status, lag=0.0, corr=0.4, prom=0.3, face_h=0.10))
    assert (f.status, f.reliability) == ("inconclusive", "low")
    assert "face too small for a reliable synchrony measurement" in f.limitations
    assert f.details["face_height"] == 0.10 and f.details["min_face_height"] == 0.25
    _check_text(f)


def test_face_height_measured_from_landmarks():
    class P:
        def __init__(self, y):
            self.x, self.y = 0.5, y
    pts = {L.FOREHEAD_TOP: P(0.30), L.CHIN: P(0.62)}
    assert L.face_height_fraction(pts) == pytest.approx(0.32)
    ms = L.MouthSeries(t=np.arange(3), raw=np.zeros(3), mouth=np.zeros(3), face_found=np.ones(3, bool),
                       frame_size=(10, 10), face_height=np.array([0.2, np.nan, 0.3]))
    assert ms.median_face_height() == pytest.approx(0.25)


@pytest.mark.parametrize("status", ["inconclusive", "insufficient_signal", "not_applicable"])
def test_other_statuses_pass_through_low(stub, status):
    (f,), _ = stub(_fake_analysis(status))
    assert (f.status, f.reliability) == (status, "low")
    _check_text(f)


def test_analysis_error_is_inconclusive_not_a_crash(stub, monkeypatch, tmp_path):
    def boom(path, fps=None):
        raise RuntimeError("decoder exploded")
    monkeypatch.setattr(L, "analyze_clip", boom)
    (f,) = L.LipsyncWitness().run(_ctx(tmp_path))
    assert (f.status, f.reliability) == ("inconclusive", "low")
    assert any("decoder exploded" in x for x in f.limitations)
    _check_text(f)


def test_worst_windows_merges_overlaps():
    ws = [{"start_s": s, "end_s": s + 3, "corr": c} for s, c in [(0, 0.5), (1, -0.2), (2, -0.3), (8, -0.1), (9, None)]]
    assert L.worst_windows(ws, 3) == [(1, 5), (8, 11)]
