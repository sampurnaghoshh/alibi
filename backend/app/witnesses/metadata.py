from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app import config
from app.witnesses.base import InvestigationContext, Witness, run_cli


LIMITATIONS = [
    "Metadata can be edited or removed; it is supporting evidence, not ground truth.",
    "Metadata timestamps may reflect copying, exporting, timezone conversion, or device settings rather than the original capture time.",
    "GPS information may be missing, inaccurate, or manually altered.",
    "Capture timestamps are compared at date precision; exact time-of-day cannot be verified from date-only metadata.",
]

DEFAULT_EDITING_SOFTWARE = (
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

MAKE_TAGS = ("Make", "CameraMake")
MODEL_TAGS = ("Model", "CameraModelName")

METADATA_FIELD_TAGS = {
    "capture_date": DATE_TAGS,
    "gps": ("GPSLatitude", "GPSLongitude"),
    "make": MAKE_TAGS,
    "model": MODEL_TAGS,
    "software": SOFTWARE_TAGS,
}


def _tag_value(data: dict, names: tuple[str, ...]) -> Any:
    """Find a tag by its name, ignoring ExifTool group prefixes."""
    wanted_names = {name.lower() for name in names}

    for key, value in data.items():
        if key.split(":")[-1].lower() in wanted_names:
            if value not in (None, "", "-", "0000:00:00 00:00:00"):
                return value

    return None


def _all_tag_values(data: dict, names: tuple[str, ...]) -> list[str]:
    """Collect matching metadata values without duplicating identical values."""
    wanted_names = {name.lower() for name in names}
    found = []

    for key, value in data.items():
        if key.split(":")[-1].lower() in wanted_names:
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

    candidates = [
        text,
        re.sub(
            r"^(\d{4}):(\d{2}):(\d{2})",
            r"\1-\2-\3",
            text,
        ),
    ]

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

    # -n requests numeric values, including signed GPS coordinates.
    result = _run_command(
        [executable, "-n", "-json", "-G", str(file_path)],
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
    result["executable_found"] = (
        shutil.which("exiftool") is not None
        or Path(r"C:\ExifTool\exiftool.exe").exists()
    )

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


def _metadata_fields(exif: dict, capture_date, latitude, longitude, software) -> dict:
    """Build metadata inventory fields for frontend display."""
    make = _tag_value(exif, MAKE_TAGS)
    model = _tag_value(exif, MODEL_TAGS)

    fields_present = []
    fields_missing = []

    if capture_date:
        fields_present.append("capture_date")
    else:
        fields_missing.append("capture_date")

    if latitude is not None and longitude is not None:
        fields_present.append("gps")
    else:
        fields_missing.append("gps")

    if make:
        fields_present.append("make")
    else:
        fields_missing.append("make")

    if model:
        fields_present.append("model")
    else:
        fields_missing.append("model")

    if software:
        fields_present.append("software")
    else:
        fields_missing.append("software")

    reencode_hints = []

    if software:
        reencode_hints.extend(software)

    if any(
        hint.lower() in value.lower()
        for value in software
        for hint in (
            "encoder",
            "export",
            "edited",
            "photoshop",
            "lightroom",
            "capcut",
            "canva",
            "premiere",
            "davinci",
            "final cut",
            "gimp",
            "snapseed",
            "picsart",
        )
    ):
        reencode_hints.append(
            "Metadata contains software/encoder tags that may indicate processing or re-encoding."
        )

    return {
        "fields_present": fields_present,
        "fields_missing": fields_missing,
        "make": str(make) if make else None,
        "model": str(model) if model else None,
        "reencode_hints": list(dict.fromkeys(reencode_hints)),
    }


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
    software_values = list(dict.fromkeys(software_values))

    inventory = _metadata_fields(
        exif,
        capture_date,
        latitude,
        longitude,
        software_values,
    )

    return {
        "file_id": input_ref.file_id,
        "filename": input_ref.name,
        "mime": input_ref.mime,
        "kind": input_ref.kind,
        "capture_date": capture_date.isoformat() if capture_date else None,
        "gps_latitude": latitude,
        "gps_longitude": longitude,
        "software": software_values,
        **inventory,
        "exiftool": exif_result,
        "ffprobe": ffprobe_result,
    }


# ---------------------------------------------------------
# Claim normalization helpers
# ---------------------------------------------------------

def _claimed_window(claim) -> tuple[date | None, date | None, str]:
    """Get the normalized start/end date window and precision from a time claim."""
    normalized = claim.normalized or {}

    start_value = normalized.get("start")
    end_value = normalized.get("end")
    precision = normalized.get("precision", "day")

    if start_value and end_value:
        try:
            start = datetime.fromisoformat(
                str(start_value).replace("Z", "+00:00")
            ).date()

            end = datetime.fromisoformat(
                str(end_value).replace("Z", "+00:00")
            ).date()

            # The normalized end is exclusive for day/week/month/year claims.
            if precision in {"day", "week", "month", "year"}:
                end -= timedelta(days=1)

            return start, end, precision
        except (ValueError, TypeError):
            pass

    # Support older nested format.
    time_data = normalized.get("time", {})

    if isinstance(time_data, dict):
        value = time_data.get("date")
        if value:
            parsed = _parse_date(value)
            if parsed:
                return parsed, parsed, precision

    parsed = _parse_date(claim.value)
    if parsed:
        return parsed, parsed, precision

    return None, None, precision


def _claimed_location(claim) -> tuple[float | None, float | None]:
    """Get normalized coordinates from a location claim."""
    normalized = claim.normalized or {}

    location = normalized

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


def _unique_claim_ids(rows: list[dict]) -> list[str]:
    return list(dict.fromkeys(row["claim_id"] for row in rows))


def _date_tolerance_days(precision: str) -> int:
    """Tolerance for date-only metadata comparisons."""
    return {
        "hour": 1,
        "part_of_day": 1,
        "day": 1,
        "week": 2,
        "month": 2,
        "year": 2,
    }.get(precision, 2)


def _date_comparison(
    media_date: date,
    claimed_start: date,
    claimed_end: date,
    precision: str,
) -> dict:
    """Compare a capture date to a claimed window, including precision tolerance."""
    tolerance = _date_tolerance_days(precision)

    if claimed_start <= media_date <= claimed_end:
        difference = 0
    elif media_date < claimed_start:
        difference = (claimed_start - media_date).days
    else:
        difference = (media_date - claimed_end).days

    return {
        "metadata_date": media_date.isoformat(),
        "claimed_start": claimed_start.isoformat(),
        "claimed_end": claimed_end.isoformat(),
        "precision": precision,
        "difference_days": difference,
        "threshold_days": tolerance,
        "within_window": claimed_start <= media_date <= claimed_end,
        "consistent": difference <= tolerance,
    }


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
    label = "Metadata"
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
                    "fields_present": [],
                    "fields_missing": list(METADATA_FIELD_TAGS),
                    "make": None,
                    "model": None,
                    "reencode_hints": [],
                })

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

        limitations = list(LIMITATIONS)

        if any(
            not item.get("exiftool", {}).get("executable_found", False)
            for item in extracted
        ):
            limitations.append("exiftool not found")

        metadata_inventory = [
            {
                "file_id": item.get("file_id"),
                "filename": item.get("filename"),
                "capture_date": item.get("capture_date"),
                "gps_latitude": item.get("gps_latitude"),
                "gps_longitude": item.get("gps_longitude"),
                "software_tags": item.get("software", []),
                "fields_present": item.get("fields_present", []),
                "fields_missing": item.get("fields_missing", []),
                "make": item.get("make"),
                "model": item.get("model"),
                "reencode_hints": item.get("reencode_hints", []),
                "extraction_error": item.get("error"),
            }
            for item in extracted
        ]

        # ---------------------------------------------------------
        # Finding 1: capture date comparison
        # ---------------------------------------------------------
        if time_claims:
            usable_dates = [
                item for item in extracted
                if item.get("capture_date")
            ]

            claim_windows = [
                (claim, *_claimed_window(claim))
                for claim in time_claims
            ]

            claim_windows = [
                (claim, start, end, precision)
                for claim, start, end, precision in claim_windows
                if start is not None and end is not None
            ]

            if not usable_dates or not claim_windows:
                findings.append(
                    self.finding(
                        status="inconclusive",
                        reliability="low",
                        title="Capture date could not be verified",
                        summary=(
                            "A usable capture date or normalized date claim "
                            "was unavailable for comparison."
                        ),
                        claim_ids=list(dict.fromkeys(
                            claim.id for claim in time_claims
                        )),
                        details={
                            "metadata_dates": [
                                item.get("capture_date")
                                for item in extracted
                            ],
                            "claimed_windows": [
                                {
                                    "start": start.isoformat(),
                                    "end": end.isoformat(),
                                    "precision": precision,
                                    "claim_id": claim.id,
                                }
                                for claim, start, end, precision in claim_windows
                            ],
                            "files": metadata_inventory,
                        },
                        evidence=artifact_evidence,
                        alternatives=[
                            "Metadata may have been removed or may use an unsupported date field.",
                            "The media may have been exported, copied, or processed.",
                            "The claim may not contain a normalized date.",
                            "Metadata is commonly stripped by messaging apps and social platforms",
                        ],
                        limitations=limitations,
                        n=1,
                    )
                )
            else:
                comparisons = []

                for item in usable_dates:
                    media_date = _parse_date(item["capture_date"])

                    if not media_date:
                        continue

                    file_comparisons = []

                    for claim, claimed_start, claimed_end, precision in claim_windows:
                        comparison = _date_comparison(
                            media_date,
                            claimed_start,
                            claimed_end,
                            precision,
                        )

                        comparison.update({
                            "file": item["filename"],
                            "file_id": item["file_id"],
                            "claim_id": claim.id,
                        })

                        file_comparisons.append(comparison)
                        comparisons.append(comparison)

                    # Each file is evaluated against all time claims.
                    closest = min(
                        file_comparisons,
                        key=lambda row: row["difference_days"],
                    )

                    item["date_status"] = (
                        "consistent" if closest["consistent"]
                        else "contradiction"
                    )

                contradictory_files = [
                    item for item in usable_dates
                    if item.get("date_status") == "contradiction"
                ]

                if contradictory_files:
                    findings.append(
                        self.finding(
                            status="contradiction",
                            reliability="medium",
                            title="Capture date differs from claimed date",
                            summary=(
                                "One or more media files have metadata dates "
                                "outside the claimed time windows and their "
                                "precision-based tolerances."
                            ),
                            claim_ids=list(dict.fromkeys(
                                row["claim_id"] for row in comparisons
                            )),
                            details={
                                "comparisons": comparisons,
                                "files": metadata_inventory,
                                "contradictory_files": [
                                    item["filename"]
                                    for item in contradictory_files
                                ],
                            },
                            evidence=artifact_evidence,
                            alternatives=[
                                "The device date or timezone may have been incorrect.",
                                "The file may have been exported or copied.",
                                "Metadata may have been edited independently of the media.",
                            ],
                            limitations=limitations,
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
                                "All files with usable capture dates fall "
                                "within or sufficiently close to the claimed "
                                "time windows."
                            ),
                            claim_ids=list(dict.fromkeys(
                                row["claim_id"] for row in comparisons
                            )),
                            details={
                                "comparisons": comparisons,
                                "files": metadata_inventory,
                            },
                            evidence=artifact_evidence,
                            alternatives=[
                                "Matching metadata does not independently establish when the media was captured.",
                                "Metadata timestamps may have been altered.",
                            ],
                            limitations=limitations,
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

        editing_software = getattr(
            config,
            "METADATA_EDITING_SOFTWARE",
            DEFAULT_EDITING_SOFTWARE,
        )

        editing_tags = [
            software
            for software in all_software
            if any(
                str(known).lower() in software.lower()
                for known in editing_software
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
                        "files": metadata_inventory,
                    },
                    evidence=artifact_evidence,
                    alternatives=[
                        "The software may have been used for routine cropping, compression, formatting, or export.",
                        "The tag does not establish that the depicted event or content was altered.",
                        "Software metadata may be incomplete or manually changed.",
                    ],
                    limitations=limitations,
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
                        claim_ids=list(dict.fromkeys(
                            claim.id for claim in location_claims
                        )),
                        details={
                            "gps_found": [
                                {
                                    "file": item["filename"],
                                    "latitude": item.get("gps_latitude"),
                                    "longitude": item.get("gps_longitude"),
                                }
                                for item in gps_files
                            ],
                            "files": metadata_inventory,
                        },
                        evidence=artifact_evidence,
                        alternatives=[
                            "The media may not contain GPS metadata.",
                            "Location metadata may have been removed during export.",
                            "The location claim may not include normalized coordinates.",
                            "Metadata is commonly stripped by messaging apps and social platforms",
                        ],
                        limitations=limitations,
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
                    file_comparisons = []

                    for claim, claimed_lat, claimed_lon in valid_claims:
                        distance = _distance_km(
                            item["gps_latitude"],
                            item["gps_longitude"],
                            claimed_lat,
                            claimed_lon,
                        )

                        comparison = {
                            "file": item["filename"],
                            "file_id": item["file_id"],
                            "metadata_latitude": item["gps_latitude"],
                            "metadata_longitude": item["gps_longitude"],
                            "claimed_latitude": claimed_lat,
                            "claimed_longitude": claimed_lon,
                            "distance_km": round(distance, 3),
                            "threshold_km": max_distance,
                            "consistent": distance <= max_distance,
                            "claim_id": claim.id,
                        }

                        file_comparisons.append(comparison)
                        comparisons.append(comparison)

                    closest = min(
                        file_comparisons,
                        key=lambda row: row["distance_km"],
                    )

                    item["gps_status"] = (
                        "consistent" if closest["consistent"]
                        else "contradiction"
                    )

                contradictory_files = [
                    item for item in gps_files
                    if item.get("gps_status") == "contradiction"
                ]

                if contradictory_files:
                    findings.append(
                        self.finding(
                            status="contradiction",
                            reliability="medium",
                            title="GPS location differs from claimed location",
                            summary=(
                                "One or more media files have GPS "
                                "coordinates beyond the configured "
                                "distance tolerance from the claimed location."
                            ),
                            claim_ids=list(dict.fromkeys(
                                row["claim_id"] for row in comparisons
                            )),
                            details={
                                "comparisons": comparisons,
                                "threshold_km": max_distance,
                                "contradictory_files": [
                                    item["filename"]
                                    for item in contradictory_files
                                ],
                                "files": metadata_inventory,
                            },
                            evidence=artifact_evidence,
                            alternatives=[
                                "GPS may have been inaccurate or recorded at another point.",
                                "The media may have been copied from another device or location.",
                                "The metadata may have been changed.",
                            ],
                            limitations=limitations,
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
                                "All files with usable GPS metadata are "
                                "within the configured distance tolerance "
                                "of at least one claimed location."
                            ),
                            claim_ids=list(dict.fromkeys(
                                row["claim_id"] for row in comparisons
                            )),
                            details={
                                "comparisons": comparisons,
                                "threshold_km": max_distance,
                                "files": metadata_inventory,
                            },
                            evidence=artifact_evidence,
                            alternatives=[
                                "GPS metadata may have been edited or copied.",
                                "Coordinates do not establish the identity of people or the event depicted.",
                            ],
                            limitations=limitations,
                            n=3,
                        )
                    )

        # ---------------------------------------------------------
        # General inventory finding if no date or location was checked
        # ---------------------------------------------------------
        if not time_claims and not location_claims:
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
                        "Metadata is commonly stripped by messaging apps and social platforms",
                    ],
                    limitations=limitations,
                    n=1,
                )
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        for finding in findings:
            finding.elapsed_ms = elapsed_ms

        return findings


if __name__ == "__main__":
    run_cli(MetadataWitness())