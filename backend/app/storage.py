"""
Evidence storage and chain of custody (principle 6). Owner: Sampurna.

Layout of one investigation:

    data/runs/<inv_id>/
        originals/<file_id>_<name>   uploads, read-only, never modified
        artifacts/                   everything derived (frames, heatmaps, spectrograms)
        history.jsonl                append-only chain-of-custody log (also the live event stream)
        investigation.json           the final Investigation

history.jsonl lines are {ts, event, detail} plus an optional "data" payload (claims, finding,
assessment, graph) that the live event stream (GET /api/investigations/{id}/events) sends on.
Investigation.history exposes only {ts, event, detail}.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import string
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Optional

from app import config
from app.schemas import INVESTIGATION_ID_PATTERN, HistoryEvent, InputFile, Investigation

CHUNK = 1024 * 1024


# --- helpers ----------------------------------------------------------------

def now() -> datetime:
    return datetime.now(config.DEFAULT_TZ)


def new_investigation_id(at: Optional[datetime] = None) -> str:
    """inv_YYYYMMDD_HHMMSS_xxxx"""
    at = at or now()
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    return f"inv_{at:%Y%m%d_%H%M%S}_{suffix}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name: str) -> str:
    """Keep only filesystem-safe characters; never trust upload names."""
    name = Path(name or "upload").name
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:100] or "upload"


def parse_posted_at(value: Optional[str]) -> datetime:
    """'2026-11-19' or an ISO datetime -> aware datetime (DEFAULT_TZ if none given). None -> now."""
    if not value:
        return now()
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=config.DEFAULT_TZ)


MAX_MEDIA_DURATION_S = 24 * 3600    # anything longer is a broken header, not a real upload


def _sane_duration(value: Any) -> Optional[float]:
    """A usable duration in seconds, or None: never negative, zero, NaN/inf or absurdly long."""
    try:
        d = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(d) or d <= 0 or d > MAX_MEDIA_DURATION_S:
        return None
    return round(d, 3)


def probe_duration(path: Path, mime: str) -> Optional[float]:
    """Duration in seconds for audio/video: ffprobe if installed, else (video) media.clip_duration, the
    same frame-count rule the archive builder and prior_appearance use. None for other files, when neither
    can read it, or when the value is negative / absurd (some containers report garbage)."""
    if not mime.startswith(("video/", "audio/")):
        return None
    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
            d = _sane_duration(out)
            if d is not None:
                return d
        except (subprocess.SubprocessError, OSError):
            pass
    if mime.startswith("video/"):
        try:
            from app.media import clip_duration  # noqa: PLC0415 - OpenCV is heavy, load only when needed

            return _sane_duration(clip_duration(path, mime))
        except Exception:  # noqa: BLE001 - duration is optional metadata
            return None
    return None


# --- one investigation on disk ------------------------------------------------

class RunStore:
    def __init__(self, inv_id: str, root: Optional[Path] = None):
        if not re.match(INVESTIGATION_ID_PATTERN, inv_id):
            raise ValueError(f"bad investigation id: {inv_id!r}")
        self.inv_id = inv_id
        self.run_dir = (root or config.RUNS_DIR) / inv_id
        self.originals_dir = self.run_dir / "originals"
        self.artifacts_dir = self.run_dir / "artifacts"
        self.history_path = self.run_dir / "history.jsonl"
        self.investigation_path = self.run_dir / "investigation.json"
        self._log_lock = threading.Lock()   # witnesses log from several threads

    @classmethod
    def create(cls, root: Optional[Path] = None) -> "RunStore":
        store = cls(new_investigation_id(), root)
        store.originals_dir.mkdir(parents=True)
        store.artifacts_dir.mkdir(parents=True)
        return store

    def exists(self) -> bool:
        return self.investigation_path.is_file()

    def status(self) -> Optional[str]:
        """'done' | 'failed' | 'running' for an existing run folder, None if there is no such run."""
        if self.investigation_path.is_file():
            return "done"
        if not self.run_dir.is_dir():
            return None
        events = self.events()
        return "failed" if events and events[-1]["event"] == "failed" else "running"

    # Originals ---------------------------------------------------------------

    def save_original(self, file_id: str, filename: str, src: BinaryIO, mime: Optional[str] = None,
                      declared_mime: Optional[str] = None) -> tuple[InputFile, Path]:
        """Copy an upload into originals/, hash it while copying, then make it read-only.
        `mime` is the type detected from content; `declared_mime` (from the client) is only logged."""
        import mimetypes

        dest = self.originals_dir / f"{file_id}_{safe_name(filename)}"
        h, size = hashlib.sha256(), 0
        with open(dest, "xb") as out:   # "x": never overwrite an existing original
            while chunk := src.read(CHUNK):
                h.update(chunk)
                size += len(chunk)
                out.write(chunk)
        os.chmod(dest, stat.S_IREAD)    # read-only (Windows: read-only attribute)

        if not mime or mime == "application/octet-stream":   # browsers sometimes send nothing useful
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        info = InputFile(
            file_id=file_id,
            name=filename,
            mime=mime,
            size=size,
            sha256=h.hexdigest(),
            duration_s=probe_duration(dest, mime),
            stored_path=dest.relative_to(self.run_dir).as_posix(),
        )
        detail = f"{filename} sha256={info.sha256} stored read-only at {info.stored_path}"
        if declared_mime and declared_mime not in (mime, "application/octet-stream"):
            detail += f" (declared {declared_mime}, detected {mime} from file content)"
        self.log("file_received", detail, data={"input": info.model_dump(mode="json")})
        return info, dest

    def verify_original(self, info: InputFile) -> bool:
        """Re-hash a stored original and compare with the recorded SHA-256."""
        return sha256_file(self.run_dir / info.stored_path) == info.sha256

    def copy_original_from(self, parent: "RunStore", info: InputFile) -> tuple[InputFile, Path]:
        """Copy a parent run's original into this run (caption re-runs), checking its SHA-256 first."""
        src = parent.run_dir / info.stored_path
        if sha256_file(src) != info.sha256:
            raise ValueError(f"{info.name}: stored original no longer matches its recorded SHA-256")
        dest = self.run_dir / info.stored_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        os.chmod(dest, stat.S_IREAD)
        self.log("file_received",
                 f"{info.name} sha256={info.sha256} copied read-only from {parent.inv_id}/{info.stored_path} "
                 f"(hash verified)", data={"input": info.model_dump(mode="json"), "copied_from": parent.inv_id})
        return info, dest

    # History (chain of custody) ---------------------------------------------

    def log(self, event: str, detail: str = "", data: Optional[dict[str, Any]] = None) -> HistoryEvent:
        """Append one event. `data` (JSON-serialisable) is kept for the live event stream only."""
        ev = HistoryEvent(ts=now(), event=event, detail=detail)
        line = json.loads(ev.model_dump_json())
        if data is not None:
            line["data"] = data
        with self._log_lock:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            with open(self.history_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return ev

    def events(self) -> list[dict[str, Any]]:
        """Raw history lines, including data payloads."""
        if not self.history_path.exists():
            return []
        lines = self.history_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def history(self) -> list[HistoryEvent]:
        """The chain-of-custody log as it appears in the contract (no data payloads)."""
        return [HistoryEvent(ts=e["ts"], event=e["event"], detail=e.get("detail", "")) for e in self.events()]

    # Investigation JSON -------------------------------------------------------

    def save_investigation(self, inv: Investigation) -> None:
        self.investigation_path.write_text(inv.model_dump_json(indent=2), encoding="utf-8")

    def load_investigation(self) -> Investigation:
        return Investigation.model_validate_json(self.investigation_path.read_text(encoding="utf-8"))

    # Serving files ------------------------------------------------------------

    def resolve(self, rel_path: str) -> Path:
        """Map 'originals/..' or 'artifacts/..' to a real file inside this run. Blocks path traversal."""
        target = (self.run_dir / rel_path).resolve()
        allowed = (self.originals_dir.resolve(), self.artifacts_dir.resolve())
        if not any(target.is_relative_to(d) for d in allowed) or not target.is_file():
            raise FileNotFoundError(rel_path)
        return target


def list_investigations(root: Optional[Path] = None) -> list[Investigation]:
    """All saved investigations, newest first. Unreadable runs are skipped."""
    root = root or config.RUNS_DIR
    out = []
    if root.exists():
        for d in root.iterdir():
            if not re.match(INVESTIGATION_ID_PATTERN, d.name):
                continue
            store = RunStore(d.name, root)
            if store.exists():
                try:
                    out.append(store.load_investigation())
                except Exception:  # noqa: BLE001 - a broken run must not break the list
                    continue
    return sorted(out, key=lambda inv: inv.created_at, reverse=True)
