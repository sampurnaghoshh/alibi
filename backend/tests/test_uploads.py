"""Upload hardening: real content types only, size/count limits, friendly errors, corrupt files -> inconclusive."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.main import app
from app.uploads import sniff

client = TestClient(app)
pytestmark = pytest.mark.usefixtures("offline")   # no live weather calls from pipeline tests


@pytest.fixture(autouse=True)
def runs_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (40, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _post(*files, caption="Flooding in Chennai yesterday"):
    return client.post("/api/investigations", data={"caption": caption, "posted_at": "2026-11-19"},
                       files=[("files", f) for f in files])


def _no_runs_stored():
    return not config.RUNS_DIR.exists() or not any(config.RUNS_DIR.iterdir())


# --- signature sniffing -------------------------------------------------------------

@pytest.mark.parametrize("head,expected", [
    (b"\xff\xd8\xff\xe0\x00\x10JFIF", ("image", "image/jpeg")),
    (b"\x89PNG\r\n\x1a\n\x00\x00", ("image", "image/png")),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", ("image", "image/webp")),
    (b"\x00\x00\x00\x18ftypheic\x00\x00", ("image", "image/heic")),
    (b"\x00\x00\x00\x18ftypmp42\x00\x00", ("video", "video/mp4")),
    (b"\x00\x00\x00\x14ftypqt  \x00\x00", ("video", "video/quicktime")),
    (b"\x00\x00\x00\x1cftypM4A \x00\x00", ("audio", "audio/mp4")),
    (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81", ("video", "video/webm")),
    (b"RIFF\x00\x00\x00\x00WAVEfmt ", ("audio", "audio/wav")),
    (b"ID3\x04\x00\x00\x00\x00", ("audio", "audio/mpeg")),
    (b"OggS\x00\x02\x00\x00", ("audio", "audio/ogg")),
    (b"%PDF-1.7\n", None),
    (b"MZ\x90\x00\x03\x00", None),                     # Windows .exe
    (b"<html><body>", None),
    (b"PK\x03\x04\x14\x00", None),                     # zip / docx
])
def test_sniff(head, expected):
    assert sniff(head) == expected


# --- API errors ---------------------------------------------------------------------

def test_pdf_renamed_to_jpg_rejected_with_friendly_message():
    r = _post(("photo.jpg", b"%PDF-1.7\n" + b"x" * 500, "image/jpeg"))
    assert r.status_code == 415
    assert "'photo.jpg' is not a supported image, video or audio file" in r.json()["detail"]
    assert _no_runs_stored()


def test_too_large_rejected_before_storing(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1000)
    r = _post(("big.jpg", b"\xff\xd8\xff" + b"\x00" * 2000, "image/jpeg"))
    assert r.status_code == 413
    assert r.json()["detail"] == "'big.jpg' is 2.0 KB. The limit is 1000 bytes per file."
    assert _no_runs_stored()


def test_human_size():
    from app.uploads import human_size
    assert human_size(200 * 1024 * 1024) == "200 MB"
    assert human_size(250_000_000) == "238 MB"
    assert human_size(1536) == "1.5 KB"


def test_size_limit_default_is_200_mb():
    assert config.MAX_UPLOAD_BYTES == 200 * 1024 * 1024 and config.MAX_UPLOAD_FILES == 5


def test_too_many_files():
    files = [(f"p{i}.png", _png(), "image/png") for i in range(6)]
    r = _post(*files)
    assert r.status_code == 422 and "at most 5 files" in r.json()["detail"]
    assert _no_runs_stored()


def test_empty_file():
    r = _post(("empty.png", b"", "image/png"))
    assert r.status_code == 422 and "'empty.png' is empty" in r.json()["detail"]


def test_one_bad_file_rejects_whole_request():
    r = _post(("ok.png", _png(), "image/png"), ("notes.txt", b"hello world", "text/plain"))
    assert r.status_code == 415 and "notes.txt" in r.json()["detail"]
    assert _no_runs_stored()


# --- accepted files -----------------------------------------------------------------

def test_detected_type_wins_over_extension_and_is_logged():
    r = _post(("clip.mp4", _png(), "video/mp4"))
    assert r.status_code == 200
    inv = r.json()
    assert inv["inputs"][0]["mime"] == "image/png"
    assert "declared video/mp4, detected image/png" in inv["history"][0]["detail"]


@pytest.mark.parametrize("name,data,mime", [
    ("broken.jpg", b"\xff\xd8\xff\xe0" + b"\x13\x37" * 400, "image/jpeg"),
    ("broken.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00\xde\xad\xbe\xef" * 300, "video/mp4"),
])
def test_corrupt_file_gives_inconclusive_not_500(name, data, mime):
    r = _post((name, data, mime))
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["assessment"]["counts"]["contradiction"] == 0
    assert all(f["status"] in ("inconclusive", "not_applicable") for f in inv["findings"])
    ela = next(f for f in inv["findings"] if f["witness"] == "ela")
    assert ela["title"] == "Error-level analysis could not be run"
    assert ela["limitations"][0].startswith("Error:")
