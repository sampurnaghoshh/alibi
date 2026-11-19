"""ELA witness (§6.7): always inconclusive/low, a weak indicator, never crashes."""

import hashlib
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.engine.rules import assess
from app.runner import run_witnesses
from app.witnesses.base import InputRef, InvestigationContext
from app.witnesses.ela import ElaWitness, error_level_analysis
from tests.conftest import gradient, noise


def _ref(path: Path, file_id: str = "in_1", mime: str | None = None) -> InputRef:
    data = path.read_bytes()
    return InputRef(
        file_id=file_id, name=path.name, mime=mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        size=len(data), sha256=hashlib.sha256(data).hexdigest(), path=path,
    )


def _ctx(tmp_path: Path, *inputs: InputRef) -> InvestigationContext:
    art = tmp_path / "artifacts"
    art.mkdir(exist_ok=True)
    return InvestigationContext(
        inv_id="test", caption="", posted_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
        inputs=list(inputs), claims=[], artifacts_dir=art,
    )


def _spliced(tmp_path: Path) -> Path:
    """Smooth gradient with a never-compressed noise patch at x 128-192, y 64-128, saved losslessly."""
    img = gradient(256, 256)
    img.paste(noise(64, 64), (128, 64))
    path = tmp_path / "spliced.png"
    img.save(path)
    return path


def test_ela_flags_spliced_region():
    img = gradient(256, 256)
    img.paste(noise(64, 64), (128, 64))
    res = error_level_analysis(img)
    x, y, w, h = res.hotspot_bbox
    assert x == pytest.approx(0.5, abs=0.07) and y == pytest.approx(0.25, abs=0.07)
    assert w == pytest.approx(0.25, abs=0.13) and h == pytest.approx(0.25, abs=0.13)
    assert x + w <= 1 and y + h <= 1


def test_ela_smooth_image_has_no_hotspot():
    res = error_level_analysis(gradient(256, 256))
    assert res.hotspot_bbox is None and res.hotspot_fraction == 0


def test_witness_is_always_inconclusive_low_and_weak(tmp_path):
    ctx = _ctx(tmp_path, _ref(_spliced(tmp_path)))
    [f] = run_witnesses(ctx, [ElaWitness()])
    assert (f.status, f.reliability, f.family) == ("inconclusive", "low", "pixels")
    assert f.title == "Localised error-level differences visible in the heatmap"
    assert (ctx.artifacts_dir / "ela_in_1.png").is_file()
    assert f.evidence[0].ref == "artifacts/ela_in_1.png"
    assert f.locations[0].kind == "region" and f.locations[0].bbox != [0.0, 0.0, 1.0, 1.0]
    assert any("not JPEG" in lim for lim in f.limitations)          # PNG input is called out
    assert f.alternatives

    a = assess([f])
    assert a.weak_indicators == [f.id]
    assert a.families.pixels == "inconclusive"
    assert a.headline == "insufficient_evidence" and a.human_review == "standard"


def test_jpeg_without_hotspot_uses_full_frame(tmp_path):
    path = tmp_path / "smooth.jpg"
    gradient(128, 96).save(path, quality=95)
    [f] = run_witnesses(_ctx(tmp_path, _ref(path)), [ElaWitness()])
    assert f.title == "No strong localised compression anomaly"
    assert f.locations[0].bbox == [0.0, 0.0, 1.0, 1.0]
    assert not any("not JPEG" in lim for lim in f.limitations)


def test_broken_image_does_not_crash(tmp_path):
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"this is not an image")
    good = tmp_path / "ok.png"
    gradient(64, 64).save(good)
    findings = run_witnesses(_ctx(tmp_path, _ref(bad, "in_1"), _ref(good, "in_2")), [ElaWitness()])
    assert [f.id for f in findings] == ["f_ela_1", "f_ela_2"]
    assert findings[0].title == "Error-level analysis could not be run"
    assert findings[0].limitations[0].startswith("Error:")
    assert findings[1].details["heatmap"] == "artifacts/ela_in_2.png"


def test_not_applicable_without_pictures(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    [f] = run_witnesses(_ctx(tmp_path, _ref(audio, mime="audio/wav")), [ElaWitness()])
    assert f.status == "not_applicable"


def test_video_keyframe(tmp_path, make_video):
    video = make_video(tmp_path / "clip.mp4", seconds=3, fps=10)
    [f] = run_witnesses(_ctx(tmp_path, _ref(video, mime="video/mp4")), [ElaWitness()])
    assert f.status == "inconclusive" and f.reliability == "low"
    assert f.details["frame_s"] == pytest.approx(1.5, abs=0.11)
    assert f.locations[0].frame_s == f.details["frame_s"]
    assert any("video codec" in lim for lim in f.limitations)
    assert (tmp_path / "artifacts" / f"ela_in_1_{f.details['frame_s']:.1f}s.png").is_file()
