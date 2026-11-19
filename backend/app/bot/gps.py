"""Does a downloaded file carry a GPS location? Used only for the bot's sending tip. Owner: Sampurna."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

GPS_TAGS = ("GPSLatitude", "GPSCoordinates", "GPSPosition")


def has_gps(content: bytes, filename: str = "upload") -> Optional[bool]:
    """True / False, or None when it can't be checked (no exiftool, unreadable file).
    Looks at the top-level tags and the embedded timed metadata (-ee), with exiftool."""
    exe = shutil.which("exiftool")
    if not exe or not content:
        return None
    fd, tmp = tempfile.mkstemp(suffix=Path(filename).suffix or ".bin")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        out = subprocess.run([exe, "-json", "-n", "-ee", *(f"-{t}" for t in GPS_TAGS), tmp],
                             capture_output=True, text=True, timeout=20)
        data = json.loads(out.stdout or "[]")
        if not data:
            return None
        return any(t in data[0] for t in GPS_TAGS)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
