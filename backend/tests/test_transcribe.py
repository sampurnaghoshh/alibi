"""Speech-to-text (spoken claims): transcript claims, no speech, missing model; the model is mocked."""

import io
import json
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app import config, transcribe
from app.main import app
from app.report import render_report
from app.schemas import Investigation

client = TestClient(app)
pytestmark = pytest.mark.usefixtures("offline")


@pytest.fixture(autouse=True)
def runs_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")


class FakeWhisper:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio, **kw):
        assert audio.dtype == np.float32 and audio.size > 8000          # 16 kHz mono from ffmpeg
        segs = [SimpleNamespace(text=" " + w) for w in self.text.split("|") if w] if self.text else []
        return iter(segs), SimpleNamespace(language="en")


def _wav(seconds=2.0, silent=False) -> bytes:
    sr = 16000
    t = np.arange(int(sr * seconds)) / sr
    y = np.zeros_like(t) if silent else 0.2 * np.sin(2 * np.pi * 220 * t)
    buf = io.BytesIO()
    sf.write(buf, y.astype(np.float32), sr, format="WAV")
    return buf.getvalue()


def _post(caption, wav):
    return client.post("/api/investigations", data={"caption": caption, "posted_at": "2026-11-19"},
                       files=[("files", ("speech.wav", wav, "audio/wav"))])


def test_spoken_claims_are_extracted_with_transcript_spans(monkeypatch):
    monkeypatch.setattr(transcribe, "_model", FakeWhisper("There is heavy flooding|in Mumbai today"))
    r = _post("", _wav())
    assert r.status_code == 200, r.text
    inv = Investigation.model_validate(r.json())
    assert inv.transcript.status == "ok" and inv.transcript.text == "There is heavy flooding in Mumbai today"
    spoken = [c for c in inv.claims if c.source.field == "transcript"]
    assert {c.type for c in spoken} >= {"location", "time"}
    for c in spoken:
        assert inv.transcript.text[c.source.start:c.source.end] == c.source.text
    events = [h.event for h in inv.history]
    assert events.index("transcribed") < events.index("claims_extracted")
    weather = [f for f in inv.findings if f.witness == "weather"]      # spoken claims feed the context witnesses
    assert weather and weather[0].status != "not_applicable"
    html = render_report(inv)
    assert "Spoken (speech-to-text transcript)" in html and "Mumbai" in html


def test_caption_claims_win_over_the_same_spoken_claim(monkeypatch):
    monkeypatch.setattr(transcribe, "_model", FakeWhisper("Flooding in Chennai"))
    inv = Investigation.model_validate(_post("Flooding in Chennai yesterday", _wav()).json())
    places = [c for c in inv.claims if c.type == "location"]
    assert len(places) == 1 and places[0].source.field == "caption"


def test_no_speech_gives_no_claims(monkeypatch):
    monkeypatch.setattr(transcribe, "_model", FakeWhisper(""))
    inv = Investigation.model_validate(_post("", _wav(silent=True)).json())
    assert inv.transcript.status == "no_speech" and inv.claims == [] and inv.transcript.note


def test_missing_model_is_skipped_with_a_limitation(monkeypatch, tmp_path):
    monkeypatch.setattr(transcribe, "_model", None)
    monkeypatch.setattr(config, "WHISPER_MODEL_DIR", tmp_path / "nope")
    r = _post("Flooding in Chennai yesterday", _wav())
    assert r.status_code == 200
    inv = Investigation.model_validate(r.json())
    assert inv.transcript.status == "unavailable" and "not available" in inv.transcript.note
    assert all(c.source.field == "caption" for c in inv.claims)


def test_image_only_runs_have_no_transcript(monkeypatch):
    from tests.test_api import _png
    r = client.post("/api/investigations", data={"caption": "x", "posted_at": "2026-11-19"},
                    files=[("files", ("a.png", _png(), "image/png"))])
    assert r.json()["transcript"] is None


def test_contract_rejects_a_transcript_claim_without_its_span():
    raw = json.loads(config.FIXTURE_PATH.read_text(encoding="utf-8"))
    raw["claims"][0]["source"]["field"] = "transcript"
    with pytest.raises(Exception, match="transcript"):
        Investigation.model_validate(raw)
