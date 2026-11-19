from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from app.schemas import Claim
from app.witnesses.base import InvestigationContext, InputRef
from app.witnesses.metadata import MetadataWitness, _extract_gps


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
) -> InvestigationContext:
    return InvestigationContext(
        inv_id="metadata_test",
        caption=claims[0].value if claims else "",
        posted_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
        inputs=[
            InputRef(
                file_id="in_1",
                name=media_path.name,
                mime="image/jpeg",
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
        exiftool = shutil.which("exiftool") or r"C:\ExifTool\exiftool.exe"
        result = subprocess.run(
            [
                exiftool,
                "-overwrite_original",
                f"-DateTimeOriginal={capture_date}",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr or result.stdout

    return path


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


def test_no_metadata_is_inconclusive(no_metadata_image, tmp_path):
    claim = make_claim(
        "November 2026",
        "2026-11-01T00:00:00+00:00",
        "2026-12-01T00:00:00+00:00",
        "month",
    )
    ctx = make_context(tmp_path, no_metadata_image, [claim])

    findings = MetadataWitness().run(ctx)

    date_finding = next(
        finding for finding in findings
        if "date" in finding.title.lower()
    )
    assert date_finding.status == "inconclusive"


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

    date_finding = next(
        finding for finding in findings
        if "date" in finding.title.lower()
    )
    assert date_finding.status == "contradiction"


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

    date_finding = next(
        finding for finding in findings
        if "date" in finding.title.lower()
    )
    assert date_finding.status == "consistent"


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