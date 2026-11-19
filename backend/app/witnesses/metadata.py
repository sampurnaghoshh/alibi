"""Metadata integrity witness (CLAUDE.md §6.2). Owner: Adithi.

Reads the capture time, GPS position, camera and software tags that the file carries, and
compares them with the caption's time and location claims.

Capture time (the part that catches "old footage, new caption"):
- Sources, best first: Keys:CreationDate (local time with offset, written by iPhones and most
  phones), EXIF DateTimeOriginal + OffsetTimeOriginal (images), QuickTime:CreateDate (UTC by the
  QuickTime spec), ffprobe creation_time (UTC), then any date without an offset.
- Compared as a full datetime against the claim's [start, end] window:
    time zone known:   inside the window +/- METADATA_TIME_MARGIN_HOURS -> consistent (low),
                       further outside -> contradiction (medium)
    time zone unknown: inside the window -> consistent (low); outside but within
                       METADATA_MAX_DATE_DIFF_DAYS -> inconclusive; further -> contradiction (medium)
- Missing metadata is never a contradiction (§2): it is commonly stripped by messaging apps.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app import config
from app.witnesses.base import InvestigationContext, Witness, run_cli


LIMITATIONS = [
    "Metadata can be edited or removed; it is supporting evidence, not ground truth.",
    "Metadata timestamps may reflect copying, exporting, timezone conversion, or device settings rather than the original capture time.",
    "GPS information may be missing, inaccurate, or manually altered.",
    "Capture times depend on the device clock and its time zone setting; a time stored without a time zone offset is compared cautiously.",
]

EXIFTOOL_MISSING = (
    "exiftool not found: capture time, GPS and camera fields inside the file could not be read "
    "(video files fall back to ffprobe)."
)

SOFTWARE_TAGS = ("Software", "ProcessingSoftware", "Encoder", "CreatorTool", "HistorySoftwareAgent")
MAKE_TAGS = ("Make", "CameraMake")
MODEL_TAGS = ("Model", "CameraModelName")
FIELD_NAMES = ("capture_time", "gps", "make", "model", "software")

QUICKTIME_GROUP = re.compile(r"^(QuickTime|Track\d+)$", re.IGNORECASE)   # exiftool -G1 groups
WHATSAPP_NAME = re.compile(r"(^|[^A-Za-z])(IMG|VID|AUD|PTT)-\d{8}-WA\d{3,}|whatsapp", re.IGNORECASE)
STATUS_RANK = {"consistent": 0, "inconclusive": 1, "contradiction": 2}


# ---------------------------------------------------------
# Tag helpers
# ---------------------------------------------------------

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


def _tags(data: dict, name: str, group: Optional[re.Pattern] = None,
          exclude: Optional[re.Pattern] = None) -> list[tuple[str, Any]]:
    """All (key, value) pairs for one tag name, optionally filtered by exiftool group."""
    out = []
    for key, value in data.items():
        grp, _, tag = key.rpartition(":")
        if tag.lower() != name.lower() or value in (None, "", "-"):
            continue
        if group is not None and not group.match(grp):
            continue
        if exclude is not None and exclude.match(grp):
            continue
        out.append((key, value))
    return out


# ---------------------------------------------------------
# Dates and times
# ---------------------------------------------------------

_DT_RE = re.compile(
    r"^(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*(Z|[+\-]\d{2}:?\d{2})?$"
)
_OFFSET_RE = re.compile(r"^\s*(Z|[+\-]\d{2}:?\d{2})\s*$")


def _parse_offset(text: Any) -> Optional[timezone]:
    """'+05:30' / '-0400' / 'Z' -> tzinfo."""
    m = _OFFSET_RE.match(str(text or ""))
    if not m:
        return None
    token = m.group(1)
    if token == "Z":
        return timezone.utc
    sign = -1 if token[0] == "-" else 1
    digits = token[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:])
    if hours > 14 or minutes > 59:
        return None
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _parse_datetime(value: Any) -> Optional[datetime]:
    """EXIF / QuickTime / ffprobe timestamp -> datetime (aware if it carries an offset)."""
    m = _DT_RE.match(str(value or "").strip())
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) if x else 0 for x in m.groups()[:6])
    if y < 1971:                       # zero dates and the QuickTime 1904 epoch: no value
        return None
    try:
        dt = datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None
    if m.group(7):
        tz = _parse_offset(m.group(7))
        if tz is not None:
            dt = dt.replace(tzinfo=tz)
    return dt


def _parse_date(value: Any) -> date | None:
    """Parse common EXIF and FFprobe date formats (date only)."""
    dt = _parse_datetime(value)
    if dt is not None:
        return dt.date()
    text = str(value or "").strip()
    try:
        return date.fromisoformat(re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", text)[:10])
    except ValueError:
        return None


def _capture_time(exif: dict, ffprobe: Optional[dict] = None) -> Optional[dict]:
    """Best capture time in the file: {"time", "tz_known", "source"} or None."""
    naive: list[tuple[datetime, str]] = []

    # 1. Keys:CreationDate (or the same tag in another QuickTime box): local time + offset
    for key, value in _tags(exif, "CreationDate"):
        dt = _parse_datetime(value)
        if dt and dt.tzinfo:
            return {"time": dt, "tz_known": True, "source": key}
        if dt:
            naive.append((dt, key))

    # 2. Images: EXIF DateTimeOriginal, with OffsetTimeOriginal when present
    offset = _parse_offset(_tag_value(exif, ("OffsetTimeOriginal", "OffsetTime")))
    for key, value in _tags(exif, "DateTimeOriginal", exclude=QUICKTIME_GROUP):
        dt = _parse_datetime(value)
        if dt and dt.tzinfo:
            return {"time": dt, "tz_known": True, "source": key}
        if dt and offset:
            return {"time": dt.replace(tzinfo=offset), "tz_known": True, "source": key + " + OffsetTimeOriginal"}
        if dt:
            naive.append((dt, key))

    # 3. QuickTime:CreateDate (movie header), then media/track dates: UTC by the QuickTime spec
    for tag in ("CreateDate", "MediaCreateDate", "TrackCreateDate"):
        for key, value in _tags(exif, tag, group=QUICKTIME_GROUP):
            dt = _parse_datetime(value)
            if dt:
                return {"time": dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc), "tz_known": True,
                        "source": key + " (UTC)"}

    # 4. ffprobe creation_time (ISO 8601, normally with Z)
    ff = ffprobe or {}
    tag_dicts = [ff.get("format", {}).get("tags", {})] + [s.get("tags", {}) for s in ff.get("streams", [])]
    for tags in tag_dicts:
        value = _tag_value(tags or {}, ("creation_time",))
        dt = _parse_datetime(value)
        if dt:
            return {"time": dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc), "tz_known": True,
                    "source": "ffprobe creation_time (UTC)"}

    # 5. Other dates (EXIF/XMP CreateDate ...), offset if they carry one
    for key, value in _tags(exif, "CreateDate", exclude=QUICKTIME_GROUP):
        dt = _parse_datetime(value)
        if dt and dt.tzinfo:
            return {"time": dt, "tz_known": True, "source": key}
        if dt:
            naive.append((dt, key))

    if naive:
        dt, key = naive[0]
        return {"time": dt, "tz_known": False, "source": key + " (no time zone)"}
    return None


def _claimed_window(claim, fallback_tz: Optional[timezone] = None) -> Optional[tuple[datetime, datetime, str]]:
    """The claim's normalized [start, end] as aware datetimes, plus its precision."""
    normalized = claim.normalized or {}
    precision = normalized.get("precision", "day")
    start_value, end_value = normalized.get("start"), normalized.get("end")
    if not (start_value and end_value):
        return None
    try:
        start = datetime.fromisoformat(str(start_value).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(end_value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    tz = fallback_tz or timezone.utc
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=tz)
    if end < start:
        return None
    return start, end, precision


def _compare_time(capture: datetime, tz_known: bool, start: datetime, end: datetime) -> dict:
    """Capture time vs one claimed window, by the §6.2 rule (thresholds in config)."""
    if not tz_known:
        # No offset: read it as wall-clock time in the claim's time zone, and judge cautiously.
        capture = capture.replace(tzinfo=start.tzinfo)
    if capture < start:
        outside = start - capture
        side = "before the start"
    elif capture > end:
        outside = capture - end
        side = "after the end"
    else:
        outside = timedelta(0)
        side = "inside"
    hours = outside.total_seconds() / 3600

    margin_h = config.METADATA_TIME_MARGIN_HOURS
    unknown_days = config.METADATA_MAX_DATE_DIFF_DAYS
    if tz_known:
        status = "consistent" if hours <= margin_h else "contradiction"
        rule = "time zone known: consistent within the window +/- %g h" % margin_h
    else:
        status = ("consistent" if hours == 0 else
                  "inconclusive" if hours <= unknown_days * 24 else "contradiction")
        rule = "time zone unknown: outside the window but within %g days is inconclusive" % unknown_days

    shown = capture.astimezone(start.tzinfo)
    return {
        "capture_time": capture.isoformat(),
        "capture_time_claim_tz": shown.isoformat(),
        "tz_known": tz_known,
        "claimed_start": start.isoformat(),
        "claimed_end": end.isoformat(),
        "hours_outside": round(hours, 2),
        "side": side,
        "threshold_hours": margin_h if tz_known else unknown_days * 24,
        "rule": rule,
        "status": status,
    }


def _fmt(dt_iso: str) -> str:
    """'2026-11-19T18:33:22+05:30' -> '19 Nov 2026 18:33 (+05:30)'."""
    dt = datetime.fromisoformat(dt_iso)
    off = dt.strftime("%z")
    off = (off[:3] + ":" + off[3:]) if off else ""
    return dt.strftime("%d %b %Y %H:%M").lstrip("0") + (" (%s)" % off if off else "")


# ---------------------------------------------------------
# GPS
# ---------------------------------------------------------

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
    """Signed decimal GPS. Prefers exiftool's Composite tags (already signed with -n)."""
    composite = {k: v for k, v in data.items() if k.lower().startswith("composite:")}
    source = composite if _tag_value(composite, ("GPSLatitude",)) is not None else data

    lat = _tag_value(source, ("GPSLatitude",))
    lon = _tag_value(source, ("GPSLongitude",))
    lat_ref = None if source is composite else _tag_value(data, ("GPSLatitudeRef",))
    lon_ref = None if source is composite else _tag_value(data, ("GPSLongitudeRef",))

    latitude = _parse_coordinate(lat, lat_ref)
    longitude = _parse_coordinate(lon, lon_ref)

    if latitude is not None and not -90 <= latitude <= 90:
        latitude = None

    if longitude is not None and not -180 <= longitude <= 180:
        longitude = None

    return latitude, longitude


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance between two coordinates."""
    earth_radius_km = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    a = min(1.0, max(0.0, a))
    return earth_radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _claimed_location(claim) -> tuple[float | None, float | None]:
    """Get normalized coordinates from a location claim."""
    normalized = claim.normalized or {}
    location = normalized
    nested_location = normalized.get("location")
    if isinstance(nested_location, dict):
        location = nested_location

    try:
        lat = float(location["lat"]) if location.get("lat") is not None else None
        lon = float(location["lon"]) if location.get("lon") is not None else None
    except (TypeError, ValueError):
        return None, None

    if lat is not None and not -90 <= lat <= 90:
        return None, None
    if lon is not None and not -180 <= lon <= 180:
        return None, None
    return lat, lon


# ---------------------------------------------------------
# Tools
# ---------------------------------------------------------

def _exiftool_path() -> Optional[str]:
    return shutil.which("exiftool")


def _run_command(command: list[str], timeout: int = 30) -> dict:
    """Execute a metadata tool safely and return output or error information."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        return {"command": command, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "returncode": -1, "stdout": "", "stderr": str(exc)}


def _run_exiftool(file_path: Path, executable: Optional[str]) -> dict:
    """exiftool -n (numeric, signed GPS) -G1 (specific groups, e.g. Keys:CreationDate)."""
    if executable is None:
        return {"command": None, "returncode": None, "stdout": "", "stderr": "exiftool not found",
                "parsed": {}, "executable_found": False}
    result = _run_command([executable, "-n", "-json", "-G1", str(file_path)], timeout=30)
    parsed = {}
    if result["returncode"] == 0:
        try:
            decoded = json.loads(result["stdout"])
            if decoded and isinstance(decoded[0], dict):
                parsed = decoded[0]
        except (json.JSONDecodeError, IndexError, TypeError):
            parsed = {}
    result["parsed"] = parsed
    result["executable_found"] = True
    return result


def _run_ffprobe(file_path: Path) -> dict:
    executable = shutil.which("ffprobe") or "ffprobe"
    result = _run_command(
        [executable, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(file_path)],
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


# ---------------------------------------------------------
# One file
# ---------------------------------------------------------

def _editing_software() -> list[str]:
    return [str(s).lower() for s in config.METADATA_EDITING_SOFTWARE]


def _reencode_hints(filename: str, software: list[str], make: Any, model: Any) -> list[str]:
    """Signs of re-encoding or editing (§6.2). Hints only, never a verdict."""
    hints = []
    editors = _editing_software()
    for value in software:
        low = value.lower()
        if any(e in low for e in editors):
            hints.append("Editing software tag: %s" % value)
        if "lavf" in low or "ffmpeg" in low:
            hints.append("Encoder %s (FFmpeg): the file was re-encoded or remuxed" % value)
    if WHATSAPP_NAME.search(filename or ""):
        hints.append("File name follows the WhatsApp pattern")
    if not make and not model:
        hints.append("No camera make or model (common after messaging-app re-encoding)")
    return list(dict.fromkeys(hints))


def _metadata_for_file(input_ref, exiftool: Optional[str]) -> dict:
    """Extract useful fields for one media file."""
    path = Path(input_ref.path)
    exif_result = _run_exiftool(path, exiftool)
    exif = exif_result.get("parsed", {})

    ffprobe_result, ffprobe = None, {}
    if input_ref.kind == "video":
        ffprobe_result = _run_ffprobe(path)
        ffprobe = ffprobe_result.get("parsed", {})

    capture = _capture_time(exif, ffprobe)
    latitude, longitude = _extract_gps(exif)

    software = _all_tag_values(exif, SOFTWARE_TAGS)
    encoder = _tag_value(ffprobe.get("format", {}).get("tags", {}), ("encoder",))
    if encoder:
        software.append(str(encoder))
    software = list(dict.fromkeys(software))

    make, model = _tag_value(exif, MAKE_TAGS), _tag_value(exif, MODEL_TAGS)
    present = {
        "capture_time": capture is not None,
        "gps": latitude is not None and longitude is not None,
        "make": bool(make),
        "model": bool(model),
        "software": bool(software),
    }
    return {
        "file_id": input_ref.file_id,
        "filename": input_ref.name,
        "mime": input_ref.mime,
        "kind": input_ref.kind,
        "capture": capture,
        "capture_time": capture["time"].isoformat() if capture else None,
        "capture_date": capture["time"].date().isoformat() if capture else None,
        "capture_tz_known": capture["tz_known"] if capture else None,
        "capture_source": capture["source"] if capture else None,
        "gps_latitude": latitude,
        "gps_longitude": longitude,
        "software": software,
        "make": str(make) if make else None,
        "model": str(model) if model else None,
        "fields_present": [f for f in FIELD_NAMES if present[f]],
        "fields_missing": [f for f in FIELD_NAMES if not present[f]],
        "reencode_hints": _reencode_hints(input_ref.name, software, make, model),
        "exiftool": exif_result,
        "ffprobe": ffprobe_result,
    }


def _inventory(extracted: list[dict]) -> list[dict]:
    """Per-file summary for details.files[], including each file's own date and GPS status."""
    keys = ("file_id", "filename", "capture_time", "capture_date", "capture_tz_known", "capture_source",
            "gps_latitude", "gps_longitude", "make", "model", "fields_present", "fields_missing",
            "reencode_hints", "date_status", "gps_status")
    out = []
    for item in extracted:
        row = {k: item.get(k) for k in keys}
        row["software_tags"] = item.get("software", [])
        row["extraction_error"] = item.get("error")
        out.append(row)
    return out


def _artifact_evidence() -> list[dict]:
    return [{"kind": "artifact", "ref": InvestigationContext.artifact_ref("metadata_raw.json"),
             "label": "Raw metadata extraction"}]


# ---------------------------------------------------------
# The witness
# ---------------------------------------------------------

class MetadataWitness(Witness):
    name = "metadata"
    label = "Metadata"
    implemented = True

    def applicable(self, ctx: InvestigationContext) -> bool:
        return bool(ctx.inputs)

    # --- capture time vs time claims ---
    def _date_analysis(self, ctx, extracted: list[dict], time_claims) -> dict:
        tz = ctx.posted_at.tzinfo if ctx.posted_at.tzinfo else None
        windows = []
        for claim in time_claims:
            w = _claimed_window(claim, tz)
            if w:
                windows.append((claim, *w))
        comparisons = []
        for item in extracted:
            if not time_claims:
                item["date_status"] = "not_checked"
                continue
            capture = item.get("capture")
            if not capture or not windows:
                item["date_status"] = "missing" if not capture else "not_checked"
                continue
            rows = []
            for claim, start, end, precision in windows:
                row = _compare_time(capture["time"], capture["tz_known"], start, end)
                row.update({"file": item["filename"], "file_id": item["file_id"], "claim_id": claim.id,
                            "precision": precision, "capture_source": capture["source"]})
                rows.append(row)
            comparisons.extend(rows)
            best = min(rows, key=lambda r: (STATUS_RANK[r["status"]], r["hours_outside"]))
            item["date_status"] = best["status"]
            item["date_best"] = best
        return {"windows": windows, "comparisons": comparisons}

    # --- GPS vs location claims ---
    def _gps_analysis(self, extracted: list[dict], location_claims) -> dict:
        valid = [(c, *_claimed_location(c)) for c in location_claims]
        valid = [(c, lat, lon) for c, lat, lon in valid if lat is not None and lon is not None]
        max_distance = config.METADATA_MAX_GPS_DIST_KM
        comparisons = []
        for item in extracted:
            if not location_claims:
                item["gps_status"] = "not_checked"
                continue
            if item.get("gps_latitude") is None or item.get("gps_longitude") is None:
                item["gps_status"] = "missing"
                continue
            if not valid:
                item["gps_status"] = "not_checked"
                continue
            rows = []
            for claim, lat, lon in valid:
                distance = _distance_km(item["gps_latitude"], item["gps_longitude"], lat, lon)
                rows.append({
                    "file": item["filename"], "file_id": item["file_id"], "claim_id": claim.id,
                    "metadata_latitude": item["gps_latitude"], "metadata_longitude": item["gps_longitude"],
                    "claimed_latitude": lat, "claimed_longitude": lon,
                    "distance_km": round(distance, 3), "threshold_km": max_distance,
                    "consistent": distance <= max_distance,
                })
            comparisons.extend(rows)
            best = min(rows, key=lambda r: r["distance_km"])
            item["gps_status"] = "consistent" if best["consistent"] else "contradiction"
        return {"valid_claims": valid, "comparisons": comparisons, "threshold_km": max_distance}

    def run(self, ctx: InvestigationContext):
        started = time.perf_counter()
        exiftool = _exiftool_path()
        media_inputs = [item for item in ctx.inputs if item.kind in ("image", "video")]

        extracted = []
        for item in media_inputs:
            try:
                extracted.append(_metadata_for_file(item, exiftool))
            except Exception as exc:     # one bad file never stops the others (§2.5)
                extracted.append({
                    "file_id": item.file_id, "filename": item.name, "kind": item.kind,
                    "error": "%s: %s" % (type(exc).__name__, exc), "capture": None,
                    "software": [], "fields_present": [], "fields_missing": list(FIELD_NAMES),
                    "reencode_hints": [],
                })

        time_claims = ctx.claims_of("time")
        location_claims = ctx.claims_of("location")
        dates = self._date_analysis(ctx, extracted, time_claims)
        gps = self._gps_analysis(extracted, location_claims)
        files = _inventory(extracted)          # after the analyses: carries per-file statuses

        with ctx.artifact_path("metadata_raw.json").open("w", encoding="utf-8") as handle:
            json.dump({"built_at": datetime.now().isoformat(),
                       "files": [{k: v for k, v in item.items() if k not in ("capture", "date_best")}
                                 for item in extracted]},
                      handle, indent=2, ensure_ascii=False, default=str)

        limitations = list(LIMITATIONS)
        if exiftool is None:
            limitations.append(EXIFTOOL_MISSING)
        evidence = _artifact_evidence()
        stripped = "Metadata is commonly stripped by messaging apps and social platforms"
        findings = []

        # Finding 1: capture time vs the claimed time window
        if time_claims:
            findings.append(self._date_finding(time_claims, extracted, dates, files, evidence,
                                               limitations, stripped))

        # Finding 2: editing-software tag
        all_software = list(dict.fromkeys(s for item in extracted for s in item.get("software", [])))
        editors = _editing_software()
        editing_tags = [s for s in all_software if any(e in s.lower() for e in editors)]
        if editing_tags:
            findings.append(self.finding(
                status="inconclusive",
                reliability="low",
                title="Editing software tag present",
                summary="Metadata names software associated with editing or processing: %s." % ", ".join(editing_tags),
                details={"software_tags": editing_tags, "all_software_tags": all_software, "files": files},
                evidence=evidence,
                alternatives=[
                    "The software may have been used for routine cropping, compression, formatting, or export.",
                    "The tag does not establish that the depicted event or content was altered.",
                    "Software metadata may be incomplete or manually changed.",
                ],
                limitations=limitations,
                n=2,
            ))

        # Finding 3: GPS vs the claimed location
        if location_claims:
            findings.append(self._gps_finding(location_claims, extracted, gps, files, evidence,
                                              limitations, stripped))

        # Nothing to compare: report the inventory
        if not time_claims and not location_claims:
            findings.append(self.finding(
                status="inconclusive",
                reliability="low",
                title="Media metadata inventory",
                summary="Metadata fields were collected, but the caption has no time or location claim to compare them with.",
                details={"files": files, "file_count": len(extracted)},
                evidence=evidence,
                alternatives=[
                    "Metadata fields may be absent from the original media.",
                    "Metadata may reflect processing or export rather than capture.",
                    stripped,
                ],
                limitations=limitations,
                n=1,
            ))

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        for finding in findings:
            finding.elapsed_ms = elapsed_ms
        return findings

    def _date_finding(self, time_claims, extracted, dates, files, evidence, limitations, stripped):
        claim_ids = list(dict.fromkeys(c.id for c in time_claims))
        checked = [item for item in extracted if item.get("date_best")]
        details = {
            "comparisons": dates["comparisons"],
            "claimed_windows": [{"claim_id": c.id, "start": s.isoformat(), "end": e.isoformat(), "precision": p}
                                for c, s, e, p in dates["windows"]],
            "thresholds": {"known_tz_margin_hours": config.METADATA_TIME_MARGIN_HOURS,
                           "unknown_tz_max_days": config.METADATA_MAX_DATE_DIFF_DAYS},
            "files": files,
        }
        if not checked:
            return self.finding(
                status="inconclusive",
                reliability="low",
                title="Capture date could not be verified",
                summary=("No usable capture time was found in the file." if dates["windows"] else
                         "The time claim has no usable window to compare with."),
                claim_ids=claim_ids,
                details=details,
                evidence=evidence,
                alternatives=[
                    stripped,
                    "The media may have been exported, copied, or processed, which can drop capture fields.",
                    "The capture time may be stored in a field this witness does not read.",
                ],
                limitations=limitations,
                n=1,
            )

        worst = max(checked, key=lambda i: (STATUS_RANK[i["date_status"]], i["date_best"]["hours_outside"]))
        b = worst["date_best"]
        status = worst["date_status"]
        when = _fmt(b["capture_time_claim_tz"])
        window = "%s to %s" % (_fmt(b["claimed_start"]), _fmt(b["claimed_end"]))
        tz_note = "" if b["tz_known"] else " The file stores no time zone, so it is read in the claim's time zone."
        many = " (%d of %d files)" % (sum(1 for i in checked if i["date_status"] == status), len(checked)) \
            if len(checked) > 1 else ""

        if status == "contradiction":
            return self.finding(
                status="contradiction",
                reliability="medium",
                title="Capture date and time fall outside the claimed window",
                summary="%s: recorded %s per %s, %.1f h %s of the claimed window (%s)%s.%s" % (
                    worst["filename"], when, b["capture_source"], b["hours_outside"], b["side"], window, many, tz_note),
                claim_ids=claim_ids,
                details=details,
                evidence=evidence,
                alternatives=[
                    "The device clock or time zone setting may have been wrong.",
                    "The file may have been exported or copied, rewriting the stored time.",
                    "Metadata may have been edited independently of the media.",
                ],
                limitations=limitations,
                n=1,
            )
        if status == "inconclusive":
            return self.finding(
                status="inconclusive",
                reliability="low",
                title="Capture date is close to the claimed window, but the time zone is unknown",
                summary="%s: recorded %s per %s, %.1f h %s of the claimed window (%s)%s.%s" % (
                    worst["filename"], when, b["capture_source"], b["hours_outside"], b["side"], window, many, tz_note),
                claim_ids=claim_ids,
                details=details,
                evidence=evidence,
                alternatives=[
                    "Without a time zone the stored time can be off by up to about 14 hours.",
                    "The device clock or time zone setting may have been wrong.",
                    "The file may have been exported or copied, rewriting the stored time.",
                ],
                limitations=limitations,
                n=1,
            )
        return self.finding(
            status="consistent",
            reliability="low",
            title="Capture date is consistent with the claimed time",
            summary="%s: recorded %s per %s, within the claimed window (%s) or its margin%s.%s" % (
                worst["filename"], when, b["capture_source"], window, many, tz_note),
            claim_ids=claim_ids,
            details=details,
            evidence=evidence,
            alternatives=[
                "Matching metadata does not independently establish when the media was captured.",
                "Metadata timestamps may have been altered.",
            ],
            limitations=limitations,
            n=1,
        )

    def _gps_finding(self, location_claims, extracted, gps, files, evidence, limitations, stripped):
        claim_ids = list(dict.fromkeys(c.id for c in location_claims))
        checked = [i for i in extracted if i.get("gps_status") in ("consistent", "contradiction")]
        details = {"comparisons": gps["comparisons"], "threshold_km": gps["threshold_km"], "files": files}
        if not checked:
            return self.finding(
                status="inconclusive",
                reliability="low",
                title="GPS location could not be verified",
                summary=("No GPS position was found in the file." if gps["valid_claims"] else
                         "The location claim has no coordinates to compare with."),
                claim_ids=claim_ids,
                details=details,
                evidence=evidence,
                alternatives=[
                    stripped,
                    "The media may not contain GPS metadata, for example when location access was off.",
                    "The location claim may not include normalized coordinates.",
                ],
                limitations=limitations,
                n=3,
            )
        contradicting = [i for i in checked if i["gps_status"] == "contradiction"]
        if contradicting:
            nearest = min((r for r in gps["comparisons"] if r["file_id"] == contradicting[0]["file_id"]),
                          key=lambda r: r["distance_km"])
            details["contradictory_files"] = [i["filename"] for i in contradicting]
            return self.finding(
                status="contradiction",
                reliability="medium",
                title="GPS location differs from claimed location",
                summary="%s: GPS position is %.1f km from the claimed location (threshold %.0f km)." % (
                    contradicting[0]["filename"], nearest["distance_km"], gps["threshold_km"]),
                claim_ids=claim_ids,
                details=details,
                evidence=evidence,
                alternatives=[
                    "GPS may have been inaccurate or recorded at another point.",
                    "The media may have been copied from another device or location.",
                    "The metadata may have been changed.",
                ],
                limitations=limitations,
                n=3,
            )
        return self.finding(
            status="consistent",
            reliability="low",
            title="GPS location is consistent with claim",
            summary="All files with GPS metadata are within %.0f km of the claimed location." % gps["threshold_km"],
            claim_ids=claim_ids,
            details=details,
            evidence=evidence,
            alternatives=[
                "GPS metadata may have been edited or copied.",
                "Coordinates do not establish the identity of people or the event depicted.",
            ],
            limitations=limitations,
            n=3,
        )


if __name__ == "__main__":
    run_cli(MetadataWitness())
