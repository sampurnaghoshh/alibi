"""ENF power-grid hum witness (§6.6): display-only while ENF_GO is False; synthetic audio, no real footage."""

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from app import config
from app.runner import run_witnesses
from app.schemas import Claim
from app.witnesses import enf as E
from app.witnesses.base import InputRef, InvestigationContext

sf = pytest.importorskip("soundfile")
needs_ffprobe = pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe not on PATH")
FORBIDDEN = re.compile(config.FORBIDDEN_WORDS_RE, re.I)
SR = 8000


def _wav(path: Path, hum_hz=None, seconds=12.0, hum_amp=0.05, noise_amp=0.02, seed=0) -> Path:
    """White noise, plus an optional steady mains-hum tone."""
    t = np.arange(int(seconds * SR)) / SR
    y = noise_amp * np.random.default_rng(seed).standard_normal(len(t))
    if hum_hz:
        y += hum_amp * np.sin(2 * np.pi * hum_hz * t)
    sf.write(str(path), y.astype(np.float32), SR)
    return path


def _ctx(tmp_path: Path, path: Path, mime="audio/wav", claims=(), caption="") -> InvestigationContext:
    inp = InputRef(file_id="in_1", name=path.name, mime=mime, size=1, sha256="0" * 64, path=path)
    return InvestigationContext(inv_id="test", caption=caption, posted_at=datetime.now(timezone.utc),
                                inputs=[inp], claims=list(claims), artifacts_dir=tmp_path / "artifacts")


def _check_text(f):
    for text in [f.title, f.summary, *f.alternatives, *f.limitations]:
        assert not FORBIDDEN.search(text), text
    assert f.alternatives and f.limitations


def _india_claim() -> tuple[str, Claim]:
    caption = "Speech in Chennai today"
    start = caption.index("Chennai")
    claim = Claim(id="c1", type="location", value="Chennai",
                  normalized={"lat": 13.08, "lon": 80.27, "country": "IN"},
                  source={"field": "caption", "start": start, "end": start + 7, "text": "Chennai"}, extracted_by="rules")
    return caption, claim


# --- analysis -----------------------------------------------------------------------

def test_50hz_tone_is_detected_and_60hz_is_not(tmp_path):
    y, sr = E.load_audio(_wav(tmp_path / "hum50.wav", hum_hz=50))
    results = E.analyse(y, sr)[0]
    assert results[50]["detected"] and results[50]["median_snr_db"] > 10
    assert not results[60]["detected"] and not results[120]["detected"]


def test_noise_only_detects_nothing(tmp_path):
    y, sr = E.load_audio(_wav(tmp_path / "noise.wav"))
    results = E.analyse(y, sr)[0]
    assert not any(r["detected"] for r in results.values())


def test_span_cap_on_load(tmp_path):
    y, sr = E.load_audio(_wav(tmp_path / "long.wav", seconds=20), max_s=12)
    assert len(y) == 12 * sr


# --- witness while ENF_GO is False (display-only) -------------------------------------

@needs_ffprobe
@pytest.mark.parametrize("hum", [50, 60, None])
def test_display_only_is_always_insufficient_signal(tmp_path, monkeypatch, hum):
    monkeypatch.setattr(config, "ENF_GO", False)
    caption, claim = _india_claim()        # even a matching India claim must not make it consistent
    path = _wav(tmp_path / "a.wav", hum_hz=hum)
    (f,) = run_witnesses(_ctx(tmp_path, path, claims=[claim], caption=caption), [E.EnfWitness()])
    assert (f.status, f.reliability) == ("insufficient_signal", "low")
    assert f.title == "Power-grid hum (ENF): not usable for this clip"
    assert f.details["enf_go"] is False
    freqs = f.details["frequencies"]
    assert set(freqs) == {"50", "60", "100", "120"}
    assert all({"median_snr_db", "max_snr_db", "frames_above_snr", "detected"} <= set(v) for v in freqs.values())
    assert freqs["50"]["detected"] is (hum == 50) and freqs["60"]["detected"] is (hum == 60)
    assert E.NO_GO_LIMITATION in f.limitations
    assert set(E.ALTERNATIVES) <= set(f.alternatives)
    png = f.evidence[0]
    assert png.kind == "artifact" and png.ref == "artifacts/enf_spectrogram.png"
    assert (tmp_path / "artifacts" / "enf_spectrogram.png").stat().st_size > 1000
    _check_text(f)


@needs_ffprobe
def test_no_audio_track_is_not_applicable(tmp_path, make_video):
    video = make_video(tmp_path / "silent.mp4")         # OpenCV writes video only, no audio stream
    (f,) = run_witnesses(_ctx(tmp_path, video, mime="video/mp4"), [E.EnfWitness()])
    assert (f.status, f.reliability) == ("not_applicable", "low")
    _check_text(f)


def test_image_only_is_not_applicable(tmp_path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    (f,) = run_witnesses(_ctx(tmp_path, img, mime="image/jpeg"), [E.EnfWitness()])
    assert f.status == "not_applicable"


def test_error_is_inconclusive_low_not_a_crash(tmp_path, monkeypatch):
    def boom(path):
        raise RuntimeError("probe exploded")
    monkeypatch.setattr(E, "audio_info", boom)
    (f,) = E.EnfWitness().run(_ctx(tmp_path, tmp_path / "missing.wav"))
    assert (f.status, f.reliability) == ("inconclusive", "low")
    assert any("probe exploded" in x for x in f.limitations)
    _check_text(f)


@needs_ffprobe
def test_short_clip_is_insufficient_signal(tmp_path):
    (f,) = E.EnfWitness().run(_ctx(tmp_path, _wav(tmp_path / "short.wav", hum_hz=50, seconds=1.0)))
    assert (f.status, f.reliability) == ("insufficient_signal", "low")
    _check_text(f)


@needs_ffprobe
def test_long_clip_gets_span_limitation(tmp_path):
    (f,) = E.EnfWitness().run(_ctx(tmp_path, _wav(tmp_path / "long.wav", seconds=20)))
    assert f"Only the first {config.ENF_MAX_SPAN_S:g} s of the audio were analysed" in f.limitations
    assert f.details["analysed_s"] == pytest.approx(config.ENF_MAX_SPAN_S)


# --- future path: ENF_GO True applies §6.6, capped at medium ---------------------------

@needs_ffprobe
def test_go_50hz_with_india_claim_is_consistent_medium(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENF_GO", True)
    caption, claim = _india_claim()
    (f,) = run_witnesses(_ctx(tmp_path, _wav(tmp_path / "a.wav", hum_hz=50), claims=[claim], caption=caption),
                         [E.EnfWitness()])
    assert (f.status, f.reliability) == ("consistent", "medium")
    assert f.claim_ids == ["c1"]
    _check_text(f)


@needs_ffprobe
def test_go_60hz_with_india_claim_is_contradiction_medium(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENF_GO", True)
    caption, claim = _india_claim()
    (f,) = run_witnesses(_ctx(tmp_path, _wav(tmp_path / "a.wav", hum_hz=60), claims=[claim], caption=caption),
                         [E.EnfWitness()])
    assert (f.status, f.reliability) == ("contradiction", "medium")
    _check_text(f)


@needs_ffprobe
def test_go_without_location_claim_stays_insufficient(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENF_GO", True)
    (f,) = run_witnesses(_ctx(tmp_path, _wav(tmp_path / "a.wav", hum_hz=60)), [E.EnfWitness()])
    assert (f.status, f.reliability) == ("insufficient_signal", "low")


def test_grid_rule_never_contradicts_mixed_or_60hz_countries():
    hum60 = {f: {"detected": f in (60, 120)} for f in (50, 60, 100, 120)}
    assert E.grid_rule(hum60, ["JP"])[0] == "insufficient_signal"
    assert E.grid_rule(hum60, ["US"])[0] == "insufficient_signal"
    assert E.grid_rule(hum60, [""])[0] == "insufficient_signal"
    both = {f: {"detected": True} for f in (50, 60, 100, 120)}
    assert E.grid_rule(both, ["IN"])[0] == "insufficient_signal"
