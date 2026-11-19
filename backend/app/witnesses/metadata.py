from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app import config
from app.witnesses.base import InvestigationContext, Witness, run_cli


LIMITATIONS = [
    "Metadata can be edited or removed; it is supporting evidence, not ground truth.",
    "Metadata timestamps may reflect copying, exporting, timezone conversion, or device settings rather than the original capture time.",
    "GPS information may be missing, inaccurate, or manually altered.",
]

EDITING_SOFTWARE = (
    "Adobe Photoshop",
    "Adobe Lightroom",
    "Snapseed",
    "CapCut",
    "Canva",
    "Picsart",
    "Premiere Pro",
    "DaVinci Resolve",
    "Final Cut Pro",
    "GIMP",
)

DATE_TAGS = (
    "DateTimeOriginal",
    "CreateDate",
    "DateCreated",
    "MediaCreateDate",
    "TrackCreateDate",
    "CreationDate",
    "creation_time",
)

SOFTWARE_TAGS = (
    "Software",
    "ProcessingSoftware",
    "Encoder",
)


def _tag_value(data: dict, names: tuple[str, ...]) -> Any:
    """Find a tag by its name, ignoring ExifTool group prefixes."""
    for wanted in names:
        for key, value in data.items():
            if key.split(":")[-1].lower() == wanted.lower():
                if value not in (None, "", "-", "0000:00:00 00:00:00"):
                    return value
    return None


def _all_tag_values(data: dict, names: tuple[str, ...]) -> list[str]:
    """Collect matching metadata values without duplicating identical values."""
    found = []

    for key, value in data.items():
        if key.split(":")[-1].lower() in {
            name.lower() for name in names
        }:
            if value not in (None, "", "-"):
                text = str(value).strip()
                if text and text not in found:
                    found.append(text)

    return found


def _parse_date(value: Any) -> date | None:
    """Parse common EXIF and FFprobe date formats."""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    # Remove EXIF timezone suffixes such as +05:30 where appropriate.
    candidates = [text]

    # EXIF: 2026:11:19 12:30:45
    candidates.append(
        re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", text)
    )

    # FFprobe and ISO-style timestamps.
    for candidate in candidates:
        candidate = candidate.replace("Z", "+00:00")

        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            pass

        try:
            return date.fromisoformat(candidate[:10])
        except ValueError:
            pass

    return None


def _parse_coordinate(value: Any, reference: Any = None) -> float | None:
    """Convert numeric or degrees/minutes/seconds GPS values into decimal degrees."""
    if value is None:
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()

        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:deg|°)?"
            r"(?:\s*(\d+(?:\.\d+)?)\s*['′])?"
            r"(?:\s*(\d+(?:\.\d+)?)\s*['\"″])?"
            r"\s*([NSEW])?",
            text,
            re.IGNORECASE,
        )

        if not match:
            return None

        degrees = float(match.group(1))
        minutes = float(match.group(2) or 0)
        seconds = float(match.group(3) or 0)
        direction = match.group(4)

        result = degrees + minutes / 60 + seconds / 3600

        if direction and direction.upper() in ("S", "W"):
            result = -result

    ref = str(reference or "").strip().upper()

    if ref in ("S", "W"):
        result = -abs(result)
    elif ref in ("N", "E"):
        result = abs(result)

    if not math.isfinite(result):
        return None

    return result


def _extract_gps(data: dict) -> tuple[float | None, float | None]:
    lat = _tag_value(data, ("GPSLatitude",))
    lon = _tag_value(data, ("GPSLongitude",))
    lat_ref = _tag_value(data, ("GPSLatitudeRef",))
    lon_ref = _tag_value(data, ("GPSLongitudeRef",))

    latitude = _parse_coordinate(lat, lat_ref)
    longitude = _parse_coordinate(lon, lon_ref)

    if latitude is not None and not -90 <= latitude <= 90:
        latitude = None

    if longitude is not None and not -180 <= longitude <= 180:
        longitude = None

    return latitude, longitude


def _distance_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Haversine distance between two coordinates."""
    earth_radius_km = 6371.0

    lat1, lon1, lat2, lon2 = map(
        math.radians, (lat1, lon1, lat2, lon2)
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    a = min(1.0, max(0.0, a))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return earth_radius_km * c


def _run_command(command: list[str], timeout: int = 30) -> dict:
    """Execute a metadata tool safely and return output or error information."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        return {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": command,
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
        }


def _run_exiftool(file_path: Path) -> dict:
    executable = shutil.which("exiftool")

    if executable is None:
        fallback = Path(r"C:\ExifTool\exiftool.exe")
        executable = str(fallback) if fallback.exists() else "exiftool"

    result = _run_command(
        [executable, "-json", "-G", str(file_path)],
        timeout=30,
    )

    parsed = {}

    if result["returncode"] == 0:
        try:
            decoded = json.loads(result["stdout"])
            if decoded and isinstance(decoded[0], dict):
                parsed = decoded[0]
        except (json.JSONDecodeError, IndexError, TypeError):
            parsed = {}

    result["parsed"] = parsed
    return result


def _run_ffprobe(file_path: Path) -> dict:
    executable = shutil.which("ffprobe") or "ffprobe"

    result = _run_command(
        [
            executable,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ],
        timeout=30,
    )

    parsed = {}

    if result["returncode"] == 0:
        try:
            parsed = json.loads(result["stdout"])
        except json.JSONDecodeError:
            parsed = {}

    result["parsed"] = parsed
    return result


def _metadata_for_file(input_ref) -> dict:
    """Extract useful fields for one media file."""
    path = Path(input_ref.path)

    exif_result = _run_exiftool(path)
    exif = exif_result.get("parsed", {})

    ffprobe_result = None
    ffprobe = {}

    if input_ref.kind == "video":
        ffprobe_result = _run_ffprobe(path)
        ffprobe = ffprobe_result.get("parsed", {})

    capture_date = None

    for tag in DATE_TAGS:
        value = _tag_value(exif, (tag,))
        parsed_date = _parse_date(value)

        if parsed_date:
            capture_date = parsed_date
            break

    # If ExifTool did not produce a date, try FFprobe's creation_time.
    if capture_date is None:
        format_tags = ffprobe.get("format", {}).get("tags", {})
        stream_tags = [
            stream.get("tags", {})
            for stream in ffprobe.get("streams", [])
        ]

        for tag_dict in [format_tags, *stream_tags]:
            value = _tag_value(tag_dict, ("creation_time",))
            parsed_date = _parse_date(value)

            if parsed_date:
                capture_date = parsed_date
                break

    latitude, longitude = _extract_gps(exif)

    software_values = _all_tag_values(exif, SOFTWARE_TAGS)

    # Include FFprobe encoder information when present.
    format_tags = ffprobe.get("format", {}).get("tags", {})
    encoder = _tag_value(format_tags, ("encoder",))

    if encoder:
        software_values.append(str(encoder))

    # Keep values unique.
    software_values = list(dict.fromkeys(software_values))

    return {
        "file_id": input_ref.file_id,
        "filename": input_ref.name,
        "mime": input_ref.mime,
        "kind": input_ref.kind,
        "capture_date": capture_date.isoformat() if capture_date else None,
        "gps_latitude": latitude,
        "gps_longitude": longitude,
        "software": software_values,
        "exiftool": exif_result,
        "ffprobe": ffprobe_result,
    }


# ---------------------------------------------------------
# Claim normalization helpers
# ---------------------------------------------------------

def _claimed_date(claim) -> date | None:
    """Get a normalized date from a time claim."""
    normalized = claim.normalized or {}

    # Current claim format:
    # normalized = {"date": "...", "start": "...", "end": "..."}
    value = normalized.get("date")

    if value:
        parsed = _parse_date(value)
        if parsed:
            return parsed

    # Support older nested format:
    # normalized = {"time": {"date": "..."}}
    time_data = normalized.get("time", {})

    if isinstance(time_data, dict):
        value = time_data.get("date")

        if value:
            parsed = _parse_date(value)
            if parsed:
                return parsed

    # Try the claim value if it is date-like.
    return _parse_date(claim.value)


def _claimed_location(claim) -> tuple[float | None, float | None]:
    """Get normalized coordinates from a location claim."""
    normalized = claim.normalized or {}

    # Current claim format:
    # normalized = {"lat": ..., "lon": ..., "country": "..."}
    location = normalized

    # Support older nested format:
    # normalized = {"location": {"lat": ..., "lon": ...}}
    nested_location = normalized.get("location")

    if isinstance(nested_location, dict):
        location = nested_location

    lat = location.get("lat")
    lon = location.get("lon")

    try:
        lat = float(lat) if lat is not None else None
        lon = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        return None, None

    if lat is not None and not -90 <= lat <= 90:
        return None, None

    if lon is not None and not -180 <= lon <= 180:
        return None, None

    return lat, lon


def _artifact_evidence() -> list[dict]:
    return [
        {
            "kind": "artifact",
            "ref": InvestigationContext.artifact_ref("metadata_raw.json"),
            "label": "Raw metadata extraction",
        }
    ]


class MetadataWitness(Witness):
    name = "metadata"
    label = "Metadata Witness"
    implemented = True

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(ctx.inputs)

    def run(self, ctx: InvestigationContext):
        started = time.perf_counter()
        findings = []

        media_inputs = [
            item for item in ctx.inputs
            if item.kind in ("image", "video")
        ]

        # Extract metadata for all supported media.
        extracted = []

        for item in media_inputs:
            try:
                extracted.append(_metadata_for_file(item))
            except Exception as exc:
                extracted.append({
                    "file_id": item.file_id,
                    "filename": item.name,
                    "kind": item.kind,
                    "error": str(exc),
                    "capture_date": None,
                    "gps_latitude": None,
                    "gps_longitude": None,
                    "software": [],
                })

        # Save raw output as an investigation artifact.
        raw_path = ctx.artifact_path("metadata_raw.json")

        with raw_path.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "built_at": datetime.now().isoformat(),
                    "files": extracted,
                },
                handle,
                indent=2,
                ensure_ascii=False,
                default=str,
            )

        artifact_evidence = _artifact_evidence()

        time_claims = ctx.claims_of("time")
        location_claims = ctx.claims_of("location")

        # ---------------------------------------------------------
        # Finding 1: capture date comparison
        # ---------------------------------------------------------
        if time_claims:
            usable_dates = [
                item for item in extracted
                if item.get("capture_date")
            ]

            claim_dates = [
                (claim, _claimed_date(claim))
                for claim in time_claims
            ]

            claim_dates = [
                (claim, parsed)
                for claim, parsed in claim_dates
                if parsed is not None
            ]

            if not usable_dates or not claim_dates:
                findings.append(
                    self.finding(
                        status="inconclusive",
                        reliability="low",
                        title="Capture date could not be verified",
                        summary=(
                            "A usable capture date or normalized date claim "
                            "was unavailable for comparison."
                        ),
                        claim_ids=[claim.id for claim in time_claims],
                        details={
                            "metadata_dates": [
                                item.get("capture_date")
                                for item in extracted
                            ],
                            "claimed_dates": [
                                str(parsed) if parsed else None
                                for _, parsed in claim_dates
                            ],
                        },
                        evidence=artifact_evidence,
                        alternatives=[
                            "Metadata may have been removed or may use an unsupported date field.",
                            "The media may have been exported, copied, or processed.",
                            "The claim may not contain a normalized date.",
                        ],
                        limitations=LIMITATIONS,
                        n=1,
                    )
                )
            else:
                max_diff = getattr(
                    config,
                    "METADATA_MAX_DATE_DIFF_DAYS",
                    2,
                )

                comparisons = []

                for item in usable_dates:
                    media_date = _parse_date(item["capture_date"])

                    if not media_date:
                        continue

                    for claim, claimed in claim_dates:
                        difference = abs((media_date - claimed).days)

                        comparisons.append({
                            "file": item["filename"],
                            "metadata_date": media_date.isoformat(),
                            "claimed_date": claimed.isoformat(),
                            "difference_days": difference,
                            "claim_id": claim.id,
                        })

                if comparisons:
                    best = min(
                        comparisons,
                        key=lambda row: row["difference_days"],
                    )

                    if best["difference_days"] > max_diff:
                        findings.append(
                            self.finding(
                                status="contradiction",
                                reliability="medium",
                                title="Capture date differs from claimed date",
                                summary=(
                                    "The available capture date differs from "
                                    "the claimed date beyond the configured "
                                    "tolerance."
                                ),
                                claim_ids=[
                                    row["claim_id"] for row in comparisons
                                ],
                                details={
                                    "comparisons": comparisons,
                                    "threshold_days": max_diff,
                                },
                                evidence=artifact_evidence,
                                alternatives=[
                                    "The device date or timezone may have been incorrect.",
                                    "The file may have been exported or copied.",
                                    "Metadata may have been edited independently of the media.",
                                ],
                                limitations=LIMITATIONS,
                                n=1,
                            )
                        )
                    else:
                        findings.append(
                            self.finding(
                                status="consistent",
                                reliability="low",
                                title="Capture date is consistent with claim",
                                summary=(
                                    "The available metadata date is within "
                                    "the configured tolerance of the "
                                    "claimed date."
                                ),
                                claim_ids=[
                                    row["claim_id"] for row in comparisons
                                ],
                                details={
                                    "comparisons": comparisons,
                                    "threshold_days": max_diff,
                                },
                                evidence=artifact_evidence,
                                alternatives=[
                                    "Matching metadata does not independently establish when the media was captured.",
                                    "Metadata timestamps may have been altered.",
                                ],
                                limitations=LIMITATIONS,
                                n=1,
                            )
                        )

        # ---------------------------------------------------------
        # Finding 2: editing-software metadata tag
        # ---------------------------------------------------------
        all_software = list(dict.fromkeys(
            software
            for item in extracted
            for software in item.get("software", [])
        ))

        editing_tags = [
            software
            for software in all_software
            if any(
                known.lower() in software.lower()
                for known in EDITING_SOFTWARE
            )
        ]

        if editing_tags:
            findings.append(
                self.finding(
                    status="inconclusive",
                    reliability="low",
                    title="Editing software tag present",
                    summary=(
                        "Metadata contains software information associated "
                        "with editing or processing."
                    ),
                    details={
                        "software_tags": editing_tags,
                        "all_software_tags": all_software,
                    },
                    evidence=artifact_evidence,
                    alternatives=[
                        "The software may have been used for routine cropping, compression, formatting, or export.",
                        "The tag does not establish that the depicted event or content was altered.",
                        "Software metadata may be incomplete or manually changed.",
                    ],
                    limitations=LIMITATIONS,
                    n=2,
                )
            )

        # ---------------------------------------------------------
        # Finding 3: GPS comparison
        # ---------------------------------------------------------
        if location_claims:
            gps_files = [
                item for item in extracted
                if item.get("gps_latitude") is not None
                and item.get("gps_longitude") is not None
            ]

            normalized_claims = [
                (claim, *_claimed_location(claim))
                for claim in location_claims
            ]

            valid_claims = [
                (claim, lat, lon)
                for claim, lat, lon in normalized_claims
                if lat is not None and lon is not None
            ]

            if not gps_files or not valid_claims:
                findings.append(
                    self.finding(
                        status="inconclusive",
                        reliability="low",
                        title="GPS location could not be verified",
                        summary=(
                            "GPS metadata or normalized claimed coordinates "
                            "were unavailable for comparison."
                        ),
                        claim_ids=[claim.id for claim in location_claims],
                        details={
                            "gps_found": [
                                {
                                    "file": item["filename"],
                                    "latitude": item.get("gps_latitude"),
                                    "longitude": item.get("gps_longitude"),
                                }
                                for item in gps_files
                            ],
                        },
                        evidence=artifact_evidence,
                        alternatives=[
                            "The media may not contain GPS metadata.",
                            "Location metadata may have been removed during export.",
                            "The location claim may not include normalized coordinates.",
                        ],
                        limitations=LIMITATIONS,
                        n=3,
                    )
                )
            else:
                max_distance = getattr(
                    config,
                    "METADATA_MAX_GPS_DIST_KM",
                    50.0,
                )

                comparisons = []

                for item in gps_files:
                    for claim, claimed_lat, claimed_lon in valid_claims:
                        distance = _distance_km(
                            item["gps_latitude"],
                            item["gps_longitude"],
                            claimed_lat,
                            claimed_lon,
                        )

                        comparisons.append({
                            "file": item["filename"],
                            "metadata_latitude": item["gps_latitude"],
                            "metadata_longitude": item["gps_longitude"],
                            "claimed_latitude": claimed_lat,
                            "claimed_longitude": claimed_lon,
                            "distance_km": round(distance, 3),
                            "claim_id": claim.id,
                        })

                if comparisons:
                    best = min(
                        comparisons,
                        key=lambda row: row["distance_km"],
                    )

                    if best["distance_km"] > max_distance:
                        findings.append(
                            self.finding(
                                status="contradiction",
                                reliability="medium",
                                title="GPS location differs from claimed location",
                                summary=(
                                    "The embedded GPS coordinates are farther "
                                    "from the claimed location than the "
                                    "configured distance tolerance."
                                ),
                                claim_ids=[
                                    row["claim_id"] for row in comparisons
                                ],
                                details={
                                    "comparisons": comparisons,
                                    "threshold_km": max_distance,
                                },
                                evidence=artifact_evidence,
                                alternatives=[
                                    "GPS may have been inaccurate or recorded at another point.",
                                    "The media may have been copied from another device or location.",
                                    "The metadata may have been changed.",
                                ],
                                limitations=LIMITATIONS,
                                n=3,
                            )
                        )
                    else:
                        findings.append(
                            self.finding(
                                status="consistent",
                                reliability="low",
                                title="GPS location is consistent with claim",
                                summary=(
                                    "Available GPS metadata is within the "
                                    "configured distance tolerance of the "
                                    "claimed location."
                                ),
                                claim_ids=[
                                    row["claim_id"] for row in comparisons
                                ],
                                details={
                                    "comparisons": comparisons,
                                    "threshold_km": max_distance,
                                },
                                evidence=artifact_evidence,
                                alternatives=[
                                    "GPS metadata may have been edited or copied.",
                                    "Coordinates do not establish the identity of people or the event depicted.",
                                ],
                                limitations=LIMITATIONS,
                                n=3,
                            )
                        )

        # ---------------------------------------------------------
        # General inventory finding if no date or location was checked
        # ---------------------------------------------------------
        if not time_claims and not location_claims:
            metadata_inventory = [
                {
                    "filename": item.get("filename"),
                    "capture_date": item.get("capture_date"),
                    "gps_present": (
                        item.get("gps_latitude") is not None
                        and item.get("gps_longitude") is not None
                    ),
                    "software_tags": item.get("software", []),
                    "extraction_error": item.get("error"),
                }
                for item in extracted
            ]

            findings.append(
                self.finding(
                    status="inconclusive",
                    reliability="low",
                    title="Media metadata inventory",
                    summary=(
                        "Metadata fields were collected, but there were no "
                        "normalized time or location claims to compare."
                    ),
                    details={
                        "files": metadata_inventory,
                        "file_count": len(extracted),
                    },
                    evidence=artifact_evidence,
                    alternatives=[
                        "Metadata fields may be absent from the original media.",
                        "Metadata may reflect processing or export rather than capture.",
                    ],
                    limitations=LIMITATIONS,
                    n=1,
                )
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        # Update elapsed time for each finding.
        for finding in findings:
            finding.elapsed_ms = elapsed_ms

        return findings


if __name__ == "__main__":
    run_cli(MetadataWitness())