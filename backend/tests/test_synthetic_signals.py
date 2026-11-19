"""Synthetic-generation signals witness: images, video temporal check, audio indicators, badge wiring."""

import json
from datetime import datetime, timezone

import numpy as np
import pytest
import soundfile as sf
from PIL import Image

from app import config
from app.engine.summary import ai_check
from app.schemas import Investigation
from app.witnesses import synthetic_signals as ss
from app.witnesses.base import InputRef, InvestigationContext

W = ss.SyntheticSignalsWitness()


def _ctx(tmp_path, path, mime):
    inp = InputRef("in_1", path.name, mime, path.stat().st_size, "0" * 64, path)
    return InvestigationContext("inv_test", "", datetime.now(timezone.utc), [inp], [], tmp_path / "artifacts")


def _photo_like(seed=0):
    """Smooth content + sensor-like noise everywhere."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:512, 0:512]
    base = 120 + 60 * np.sin(xx / 70.0) * np.cos(yy / 90.0)
    return np.clip(base + rng.normal(0, 3, base.shape), 0, 255)


def test_photo_like_jpeg_with_camera_fields_is_never_a_contradiction(tmp_path):
    p = tmp_path / "photo.jpg"
    img = Image.fromarray(_photo_like().astype(np.uint8)).convert("RGB")
    exif = Image.Exif()
    exif[271], exif[272] = "Apple", "iPhone 15"
    img.save(p, quality=92, exif=exif)
    [f] = W.run(_ctx(tmp_path, p, "image/jpeg"))
    assert f.status == "consistent" and f.reliability == "low" and f.details["strength"] == "none"
    assert f.details["camera"]["camera_fields"] and f.alternatives and f.limitations
    for e in f.evidence:                                      # spectrum + noise map saved as artifacts
        assert (tmp_path / e.ref).is_file()


def test_flat_screenshot_like_png_is_only_a_weak_signal(tmp_path):
    p = tmp_path / "flat.png"
    arr = np.full((400, 600), 200, np.uint8)
    arr[100:300, 150:450] = 60                                # flat shapes, no noise at all
    Image.fromarray(arr).convert("RGB").save(p)
    [f] = W.run(_ctx(tmp_path, p, "image/png"))
    assert f.status == "inconclusive" and f.details["strength"] == "weak"
    assert any("sensor noise" in r for r in f.details["reasons"])
    assert any("camera" in r for r in f.details["reasons"])


def test_periodic_grid_is_found_in_the_residual_spectrum():
    rng = np.random.default_rng(1)
    y = _photo_like().astype(np.float32)
    plain, _ = ss.spectrum_analysis(y + rng.normal(0, 0.1, y.shape).astype(np.float32), jpeg=False)
    yy, xx = np.mgrid[0:512, 0:512]
    grid = y + 2.0 * np.cos(2 * np.pi * xx / 4) * np.cos(2 * np.pi * yy / 4)      # an up-sampling-like grid
    spec, im = ss.spectrum_analysis(grid.astype(np.float32), jpeg=False)
    assert spec["max_peak_z"] > plain["max_peak_z"] and spec["peak_count"] >= config.SYNTH_FFT_MIN_PEAKS
    assert spec["max_peak_z"] >= config.SYNTH_FFT_WEAK_Z and im.size[0] > 0


def test_contradiction_needs_both_strong_pixel_signals_and_no_camera():
    sig = lambda s, n, cam: [{"name": "spectrum", "strength": s, "reason": "a"},      # noqa: E731
                             {"name": "noise", "strength": n, "reason": "b"},
                             {"name": "camera", "strength": cam, "reason": "c"}]
    assert ss.combine(sig("strong", "strong", "context")) == ("strong", "contradiction")
    assert ss.combine(sig("strong", "strong", "none")) == ("weak", "inconclusive")        # camera fields present
    assert ss.combine(sig("strong", "weak", "context")) == ("weak", "inconclusive")
    assert ss.combine(sig("none", "none", "context")) == ("none", "consistent")          # camera absence alone: nothing


def _video(path, cut_at=None, n=40, fps=10):
    import cv2
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 120))
    for i in range(n):
        level = 220 if cut_at is not None and i >= cut_at else 60
        frame = np.full((120, 160, 3), level, np.uint8)
        frame[40:80, (i * 2) % 120:(i * 2) % 120 + 40] = 128            # slow motion
        vw.write(frame)
    vw.release()


def test_video_cut_is_flagged_with_a_timestamp_but_never_contradicts(tmp_path):
    p = tmp_path / "cut.mp4"
    _video(p, cut_at=20)
    fs = W.run(_ctx(tmp_path, p, "video/mp4"))
    [f] = [x for x in fs if x.details["check"] == "video"]       # no soundtrack -> no audio finding
    assert f.status == "inconclusive" and f.details["strength"] == "weak"
    assert f.locations and abs(f.locations[0].start_s - 2.0) < 0.3
    assert (tmp_path / f.details["frame_change_chart"]).is_file()
    series = json.loads((tmp_path / f.details["series"]).read_text())
    assert len(series["t"]) == len(series["diff"])


def test_smooth_video_has_no_signal(tmp_path):
    p = tmp_path / "smooth.mp4"
    _video(p)
    [f] = W.run(_ctx(tmp_path, p, "video/mp4"))
    assert f.status == "inconclusive" and f.details["strength"] == "none" and not f.locations


def test_audio_tts_like_indicators(tmp_path):
    """22 kHz-style speech upsampled to 44.1 kHz with digital silence: several indicators, still inconclusive."""
    sr = 44100
    rng = np.random.default_rng(2)
    spec = np.fft.rfft(rng.normal(0, 0.3, sr * 2))
    spec[np.fft.rfftfreq(sr * 2, 1 / sr) > 8000] = 0          # brick-wall limit, as after resampling 16 -> 44.1 kHz
    voiced = np.fft.irfft(spec, sr * 2)
    tone = 0.3 * np.sin(2 * np.pi * 220 * np.arange(sr * 2) / sr)
    y = np.concatenate([voiced + tone, np.zeros(sr), voiced + tone, np.zeros(sr)]).astype(np.float32)
    p = tmp_path / "voice.wav"
    sf.write(p, y, sr)
    [f] = W.run(_ctx(tmp_path, p, "audio/wav"))
    assert f.status == "inconclusive"
    assert f.details["strength"] in ("weak", "strong") and len(f.details["reasons"]) >= 2
    assert (tmp_path / f.details["spectrum_chart"]).is_file()


def test_broken_file_gives_an_inconclusive_finding(tmp_path):
    p = tmp_path / "broken.png"
    p.write_bytes(b"not an image")
    [f] = W.run(_ctx(tmp_path, p, "image/png"))
    assert f.status == "inconclusive" and f.reliability == "low" and "Error" in f.limitations[0]


# --- the AI & manipulation badge ------------------------------------------------------------

def _with_synth(strength, reasons, status="inconclusive", reliability="low"):
    raw = json.loads(config.FIXTURE_PATH.read_text(encoding="utf-8"))
    raw["findings"].append({
        "id": "f_synthetic_signals_1", "witness": "synthetic_signals", "family": "pixels", "claim_ids": [],
        "status": status, "reliability": reliability, "title": "t", "summary": "s",
        "details": {"strength": strength, "reasons": reasons}, "evidence": [], "locations": [],
        "alternatives": ["a"], "limitations": ["l"]})
    raw["assessment"]["counts"][status] += 1
    if status == "contradiction" and reliability == "low":
        raw["assessment"]["weak_indicators"].append("f_synthetic_signals_1")
    return Investigation.model_validate(raw).findings


def test_badge_strong_weak_and_none():
    strong = ai_check(_with_synth("strong", ["periodic spectral peaks", "almost no sensor noise"], "contradiction", "medium"))
    assert (strong["badge"], strong["alert"]) == ("AI SIGNALS: STRONG", True)
    assert "periodic spectral peaks" in strong["badge_detail"]
    weak = ai_check(_with_synth("weak", ["almost no sensor noise in flat areas"]))
    assert (weak["badge"], weak["alert"]) == ("AI SIGNALS: WEAK", False)
    none = ai_check(_with_synth("none", []))
    assert none["badge"] == "AI: NOT DETECTED"
    for b in (strong, weak, none):
        assert not config.forbidden_word(b["answer"] + " " + b["badge"] + " " + b["badge_detail"] + " " + b["note"])


@pytest.mark.parametrize("cls", ["image", "video", "audio"])
def test_alternatives_never_empty(cls):
    alts = {"image": ss.IMAGE_ALTERNATIVES, "video": ss.VIDEO_ALTERNATIVES, "audio": ss.AUDIO_ALTERNATIVES}[cls]
    assert alts and ss.LIMITATIONS
