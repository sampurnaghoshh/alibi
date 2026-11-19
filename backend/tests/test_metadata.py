from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from app import config
from app.claims.extract import extract_claims
from app.schemas import Claim
from app.witnesses import metadata
from app.witnesses.base import InvestigationContext, InputRef
from app.witnesses.metadata import MetadataWitness, _capture_time, _compare_time, _extract_gps

IST = timezone(timedelta(hours=5, minutes=30))
needs_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")


def make_claim(
    value: str,
    start: str,
    end: str,
    precision: str,
) -> Claim:
    return Claim.model_validate(
        {
            "id": "c_time",
            "type": "time",
            "value": value,
            "normalized": {
                "date": start[:10],
                "start": start,
                "end": end,
                "precision": precision,
            },
            "source": {
                "field": "caption",
                "start": 0,
                "end": len(value),
                "text": value,
            },
            "extracted_by": "rules",
        }
    )


def make_context(
    tmp_path: Path,
    media_path: Path,
    claims: list[Claim],
    mime: str = "image/jpeg",
    caption: str | None = None,
    posted_at: datetime | None = None,
) -> InvestigationContext:
    return InvestigationContext(
        inv_id="metadata_test",
        caption=caption if caption is not None else (claims[0].value if claims else ""),
        posted_at=posted_at or datetime(2026, 11, 19, tzinfo=timezone.utc),
        inputs=[
            InputRef(
                file_id="in_1",
                name=media_path.name,
                mime=mime,
                size=media_path.stat().st_size,
                sha256="test_hash",
                path=media_path,
            )
        ],
        claims=claims,
        artifacts_dir=tmp_path / "artifacts",
    )


def create_jpg(path: Path, capture_date: str | None = None) -> Path:
    """Create a synthetic image and optionally write its capture date with ExifTool."""
    Image.new("RGB", (32, 32), (100, 150, 200)).save(path, format="JPEG")

    if capture_date:
        result = subprocess.run(
            [shutil.which("exiftool"), "-overwrite_original", f"-DateTimeOriginal={capture_date}", str(path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr or result.stdout

    return path


def fake_tools(monkeypatch, exif: dict, ffprobe: dict | None = None):
    """Run the witness on a chosen set of tags, without touching exiftool or ffprobe."""
    monkeypatch.setattr(metadata, "_exiftool_path", lambda: "exiftool")
    monkeypatch.setattr(metadata, "_run_exiftool",
                        lambda path, exe: {"parsed": dict(exif), "executable_found": True, "returncode": 0})
    monkeypatch.setattr(metadata, "_run_ffprobe", lambda path: {"parsed": ffprobe or {}, "returncode": 0})


def date_finding(findings):
    return next(f for f in findings if "date" in f.title.lower())


@pytest.fixture
def blank_file(tmp_path):
    p = tmp_path / "clip.mov"
    p.write_bytes(b"\0" * 64)
    return p


@pytest.fixture
def no_metadata_image(tmp_path):
    return create_jpg(tmp_path / "no_metadata.jpg")


@pytest.fixture
def november_image(tmp_path):
    return create_jpg(
        tmp_path / "november.jpg",
        "2026:11:15 12:30:00",
    )


@pytest.fixture
def october_image(tmp_path):
    return create_jpg(
        tmp_path / "october.jpg",
        "2026:10:01 12:30:00",
    )


# --- Adithi's original tests (real exiftool) ------------------------------------------------

@needs_exiftool
def test_no_metadata_is_inconclusive(no_metadata_image, tmp_path):
    claim = make_claim(
        "November 2026",
        "2026-11-01T00:00:00+00:00",
        "2026-12-01T00:00:00+00:00",
        "month",
    )
    ctx = make_context(tmp_path, no_metadata_image, [claim])

    findings = MetadataWitness().run(ctx)

    assert date_finding(findings).status == "inconclusive"


@needs_exiftool
def test_capture_date_outside_claim_window_is_contradiction(
    october_image,
    tmp_path,
):
    claim = make_claim(
        "November 1, 2026",
        "2026-11-01T00:00:00+00:00",
        "2026-11-02T00:00:00+00:00",
        "day",
    )
    ctx = make_context(tmp_path, october_image, [claim])

    findings = MetadataWitness().run(ctx)

    assert date_finding(findings).status == "contradiction"


@needs_exiftool
def test_mid_november_capture_is_consistent_with_november_claim(
    november_image,
    tmp_path,
):
    claim = make_claim(
        "November 2026",
        "2026-11-01T00:00:00+00:00",
        "2026-12-01T00:00:00+00:00",
        "month",
    )
    ctx = make_context(tmp_path, november_image, [claim])

    findings = MetadataWitness().run(ctx)

    assert date_finding(findings).status == "consistent"


def test_southern_hemisphere_gps_latitude_keeps_negative_sign():
    latitude, longitude = _extract_gps(
        {
            "EXIF:GPSLatitude": 33.86,
            "EXIF:GPSLatitudeRef": "S",
            "EXIF:GPSLongitude": 151.21,
            "EXIF:GPSLongitudeRef": "E",
        }
    )

    assert latitude == pytest.approx(-33.86)
    assert longitude == pytest.approx(151.21)
    assert latitude < 0


def test_composite_gps_is_preferred_and_already_signed():
    lat, lon = _extract_gps({"GPS:GPSLatitude": 33.86, "GPS:GPSLatitudeRef": "S",
                             "Composite:GPSLatitude": -33.86, "Composite:GPSLongitude": -70.65})
    assert lat == pytest.approx(-33.86) and lon == pytest.approx(-70.65)


# --- the time rule: known time zone ±12 h, unknown time zone cautious -----------------------

WINDOW = (datetime(2026, 11, 18, tzinfo=IST), datetime(2026, 11, 19, tzinfo=IST))   # "yesterday"


def test_known_tz_11h_after_window_is_consistent():
    r = _compare_time(WINDOW[1] + timedelta(hours=11), True, *WINDOW)
    assert r["status"] == "consistent" and r["hours_outside"] == pytest.approx(11)


def test_known_tz_13h_after_window_is_contradiction():
    r = _compare_time(WINDOW[1] + timedelta(hours=13), True, *WINDOW)
    assert r["status"] == "contradiction" and r["side"] == "after the end"


def test_known_tz_inside_window_is_consistent_and_offsets_are_respected():
    # 23:00 UTC on 17 Nov is 04:30 IST on 18 Nov: inside the IST "yesterday" window
    r = _compare_time(datetime(2026, 11, 17, 23, 0, tzinfo=timezone.utc), True, *WINDOW)
    assert r["status"] == "consistent" and r["hours_outside"] == 0


def test_unknown_tz_one_and_a_half_days_out_is_inconclusive():
    r = _compare_time((WINDOW[1] + timedelta(hours=36)).replace(tzinfo=None), False, *WINDOW)
    assert r["status"] == "inconclusive" and not r["tz_known"]


def test_unknown_tz_three_days_out_is_contradiction():
    r = _compare_time((WINDOW[0] - timedelta(days=3)).replace(tzinfo=None), False, *WINDOW)
    assert r["status"] == "contradiction" and r["side"] == "before the start"


def test_unknown_tz_inside_window_is_consistent():
    r = _compare_time(datetime(2026, 11, 18, 15, 0), False, *WINDOW)
    assert r["status"] == "consistent"


def test_known_tz_13h_witness_level_is_medium_contradiction(monkeypatch, blank_file, tmp_path):
    fake_tools(monkeypatch, {"Keys:CreationDate": "2026:11:19 13:00:00+05:30"})
    claim = make_claim("yesterday", "2026-11-18T00:00:00+05:30", "2026-11-19T00:00:00+05:30", "day")
    f = date_finding(MetadataWitness().run(make_context(tmp_path, blank_file, [claim], mime="video/quicktime")))
    assert (f.status, f.reliability) == ("contradiction", "medium")


def test_unknown_tz_witness_level_is_inconclusive(monkeypatch, blank_file, tmp_path):
    fake_tools(monkeypatch, {"ExifIFD:DateTimeOriginal": "2026:11:20 06:00:00"})
    claim = make_claim("yesterday", "2026-11-18T00:00:00+05:30", "2026-11-19T00:00:00+05:30", "day")
    f = date_finding(MetadataWitness().run(make_context(tmp_path, blank_file, [claim])))
    assert (f.status, f.reliability) == ("inconclusive", "low")


def test_week_claim_uses_the_whole_window(monkeypatch, blank_file, tmp_path):
    fake_tools(monkeypatch, {"Keys:CreationDate": "2026:11:12 09:00:00+05:30"})
    claim = make_claim("last week", "2026-11-09T00:00:00+05:30", "2026-11-16T00:00:00+05:30", "week")
    f = date_finding(MetadataWitness().run(make_context(tmp_path, blank_file, [claim], mime="video/quicktime")))
    assert f.status == "consistent"


# --- which tag is the capture time ----------------------------------------------------------

def test_keys_creationdate_wins_over_quicktime_createdate():
    c = _capture_time({"QuickTime:CreateDate": "2026:11:19 13:03:23",
                       "Keys:CreationDate": "2026:11:19 18:33:22+05:30"})
    assert c["source"] == "Keys:CreationDate" and c["tz_known"]
    assert c["time"] == datetime(2026, 11, 19, 18, 33, 22, tzinfo=IST)


def test_quicktime_createdate_alone_is_read_as_utc():
    c = _capture_time({"QuickTime:CreateDate": "2026:11:19 13:03:23"})
    assert c["tz_known"] and c["time"] == datetime(2026, 11, 19, 13, 3, 23, tzinfo=timezone.utc)


def test_image_offset_time_original_makes_the_time_zone_known():
    c = _capture_time({"ExifIFD:DateTimeOriginal": "2026:11:19 18:33:22", "ExifIFD:OffsetTimeOriginal": "+05:30"})
    assert c["tz_known"] and c["time"] == datetime(2026, 11, 19, 18, 33, 22, tzinfo=IST)


def test_image_without_offset_has_unknown_time_zone():
    c = _capture_time({"ExifIFD:DateTimeOriginal": "2026:11:19 18:33:22"})
    assert c["tz_known"] is False


def test_ffprobe_creation_time_is_used_when_exiftool_has_nothing():
    c = _capture_time({}, {"format": {"tags": {"creation_time": "2026-11-19T13:03:23.000000Z"}}})
    assert c["tz_known"] and c["time"] == datetime(2026, 11, 19, 13, 3, 23, tzinfo=timezone.utc)


def test_zero_dates_are_ignored():
    assert _capture_time({"QuickTime:CreateDate": "0000:00:00 00:00:00"}) is None


# --- the real lie: IMG_5417.MOV, recorded 19 Nov 18:33 IST, captioned "yesterday" -----------

DELHI_TAGS = {
    "QuickTime:CreateDate": "2026:11:19 13:03:23",
    "Track1:MediaCreateDate": "2026:11:19 13:03:23",
    "Keys:Make": "Apple",
    "Keys:Model": "iPhone 14 Pro",
    "Keys:Software": "27.2.1",
    "Keys:CreationDate": "2026:11:19 18:33:22+05:30",
}


def test_delhi_replay_is_a_contradiction(monkeypatch, tmp_path):
    clip = tmp_path / "IMG_5417.MOV"
    clip.write_bytes(b"\0" * 64)
    fake_tools(monkeypatch, DELHI_TAGS)
    posted = datetime(2026, 11, 19, 18, 40, 56, tzinfo=IST)
    caption = "Heavy flood in Delhi yesterday"
    claims = extract_claims(caption, posted)
    ctx = make_context(tmp_path, clip, claims, mime="video/quicktime", caption=caption, posted_at=posted)

    findings = MetadataWitness().run(ctx)
    f = date_finding(findings)

    assert (f.status, f.reliability) == ("contradiction", "medium")
    assert len(f.claim_ids) == len(set(f.claim_ids)) == 1
    best = f.details["comparisons"][0]
    assert best["capture_source"] == "Keys:CreationDate"
    assert best["hours_outside"] == pytest.approx(18.56, abs=0.01)
    assert f.details["files"][0]["date_status"] == "contradiction"
    assert "18:33" in f.summary
    # iOS version in Keys:Software is not a re-encode hint; the camera fields are present
    assert f.details["files"][0]["reencode_hints"] == []
    assert {"make", "model", "capture_time"} <= set(f.details["files"][0]["fields_present"])


def test_genuine_clip_captioned_today_stays_consistent(monkeypatch, tmp_path):
    clip = tmp_path / "control.MOV"
    clip.write_bytes(b"\0" * 64)
    fake_tools(monkeypatch, DELHI_TAGS)
    posted = datetime(2026, 11, 19, 18, 40, 56, tzinfo=IST)
    caption = "Street scene in Delhi today"
    ctx = make_context(tmp_path, clip, extract_claims(caption, posted), mime="video/quicktime",
                       caption=caption, posted_at=posted)
    assert date_finding(MetadataWitness().run(ctx)).status == "consistent"


# --- per-file status, limitations, hints ----------------------------------------------------

def test_per_file_status_is_in_details_files(monkeypatch, tmp_path):
    a, b = tmp_path / "a.mov", tmp_path / "b.mov"
    a.write_bytes(b"\0" * 64)
    b.write_bytes(b"\0" * 64)
    tags = {str(a): {"Keys:CreationDate": "2026:11:18 10:00:00+05:30"},
            str(b): {"Keys:CreationDate": "2026:10:01 10:00:00+05:30"}}
    monkeypatch.setattr(metadata, "_exiftool_path", lambda: "exiftool")
    monkeypatch.setattr(metadata, "_run_exiftool", lambda path, exe: {"parsed": tags[str(path)], "executable_found": True})
    monkeypatch.setattr(metadata, "_run_ffprobe", lambda path: {"parsed": {}})
    claim = make_claim("yesterday", "2026-11-18T00:00:00+05:30", "2026-11-19T00:00:00+05:30", "day")
    ctx = make_context(tmp_path, a, [claim], mime="video/quicktime")
    ctx.inputs.append(InputRef(file_id="in_2", name="b.mov", mime="video/quicktime", size=64, sha256="x", path=b))

    f = date_finding(MetadataWitness().run(ctx))

    assert f.status == "contradiction"
    statuses = {row["file_id"]: row["date_status"] for row in f.details["files"]}
    assert statuses == {"in_1": "consistent", "in_2": "contradiction"}
    assert all(row["gps_status"] == "not_checked" for row in f.details["files"])
    assert "1 of 2 files" in f.summary


def test_exiftool_missing_is_always_a_limitation(monkeypatch, tmp_path):
    monkeypatch.setattr(metadata, "_exiftool_path", lambda: None)
    monkeypatch.setattr(metadata, "_run_ffprobe", lambda path: {"parsed": {}})
    audio = tmp_path / "voice.m4a"
    audio.write_bytes(b"\0" * 64)
    claim = make_claim("yesterday", "2026-11-18T00:00:00+05:30", "2026-11-19T00:00:00+05:30", "day")
    for path, mime in ((audio, "audio/mp4"), (tmp_path / "x.jpg", "image/jpeg")):
        if not path.exists():
            path.write_bytes(b"\0" * 64)
        findings = MetadataWitness().run(make_context(tmp_path, path, [claim], mime=mime))
        assert findings and all(any("exiftool not found" in l for l in f.limitations) for f in findings)


def test_limitations_mention_time_zone_not_date_precision(monkeypatch, blank_file, tmp_path):
    fake_tools(monkeypatch, DELHI_TAGS)
    claim = make_claim("yesterday", "2026-11-18T00:00:00+05:30", "2026-11-19T00:00:00+05:30", "day")
    f = date_finding(MetadataWitness().run(make_context(tmp_path, blank_file, [claim], mime="video/quicktime")))
    text = " ".join(f.limitations).lower()
    assert "time zone" in text and "date precision" not in text
    assert not any("exiftool not found" in l for l in f.limitations)


def test_reencode_hints_follow_section_6_2():
    hints = metadata._reencode_hints("VID-20261118-WA0007.mp4", ["Lavf60.16.100", "CapCut"], None, None)
    joined = " | ".join(hints)
    assert "Lavf60.16.100" in joined and "CapCut" in joined
    assert "WhatsApp" in joined and "No camera make or model" in joined
    assert metadata._reencode_hints("IMG_5417.MOV", ["27.2.1"], "Apple", "iPhone 14 Pro") == []


def test_editing_software_uses_config_list(monkeypatch, blank_file, tmp_path):
    monkeypatch.setattr(config, "METADATA_EDITING_SOFTWARE", ["specialeditor"])
    fake_tools(monkeypatch, {"IFD0:Software": "SpecialEditor 2.1", "Keys:CreationDate": "2026:11:18 10:00:00+05:30"})
    findings = MetadataWitness().run(make_context(tmp_path, blank_file, [], mime="video/quicktime"))
    assert any(f.title == "Editing software tag present" and f.status == "inconclusive" for f in findings)
    assert MetadataWitness.label == "Metadata"
