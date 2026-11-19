"""
Demo presets from demo/captions.md. Owner: Sampurna.

Used by GET /api/demo, POST /api/demo/{case} (frontend Case A / Case B buttons) and
scripts/warm_cache.py. Format of captions.md: a "## <name>" heading per case, followed by
"key: value" lines (caption, posted_at, files, expect, note). "TODO" means not filled in yet.
File paths are relative to the repo root and must stay inside demo/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app import config
from app.storage import parse_posted_at


@dataclass
class DemoCase:
    name: str
    caption: str
    posted_at: str
    files: list[str]                       # as written in captions.md
    expect: str = ""
    paths: list[Path] = field(default_factory=list)   # resolved, only when ready
    problem: Optional[str] = None          # why it can't run yet; None = ready

    @property
    def ready(self) -> bool:
        return self.problem is None

    def public(self) -> dict:
        """What GET /api/demo returns per case."""
        return {
            "name": self.name, "caption": self.caption, "posted_at": self.posted_at,
            "files": self.files, "expect": self.expect, "ready": self.ready, "problem": self.problem,
        }


def parse_cases(path: Path) -> dict[str, dict[str, str]]:
    """'## name' headings followed by 'key: value' lines -> {name: {key: value}}."""
    cases: dict[str, dict[str, str]] = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"^##\s+(\S+)", line):
            current = cases.setdefault(m.group(1), {})
        elif current is not None and (m := re.match(r"^(\w+):\s*(.*)$", line)):
            current[m.group(1)] = m.group(2).strip()
    return cases


def _is_todo(value: str) -> bool:
    return not value or value.strip().upper() == "TODO"


def _check(case: DemoCase, demo_dir: Path) -> DemoCase:
    """Fill in case.paths, or case.problem with a message a person can act on."""
    if _is_todo(case.caption):
        case.problem = "The caption is not filled in yet in demo/captions.md."
        return case
    if not _is_todo(case.posted_at):
        try:
            parse_posted_at(case.posted_at)
        except ValueError:
            case.problem = f"posted_at '{case.posted_at}' in demo/captions.md is not a date (use YYYY-MM-DD)."
            return case
    if not case.files:
        case.problem = "No file is listed for this case in demo/captions.md."
        return case
    repo = demo_dir.parent
    for rel in case.files:
        p = (repo / rel).resolve()
        if not p.is_relative_to(demo_dir.resolve()):
            case.problem = f"'{rel}' must be inside the demo/ folder."
            return case
        if p.is_dir():
            case.problem = f"No file chosen yet: '{rel}' is a folder. Put the clip there and list its file name."
            return case
        if not p.is_file():
            case.problem = f"The demo file '{rel}' does not exist yet. Add it to the repo, then try again."
            return case
        case.paths.append(p)
    return case


def load_cases() -> dict[str, DemoCase]:
    """All cases in captions.md (ready or not), in file order."""
    path = config.DEMO_CAPTIONS
    if not path.is_file():
        return {}
    out = {}
    for name, kv in parse_cases(path).items():
        files = [f.strip() for f in kv.get("files", "").split(",") if f.strip() and not _is_todo(f)]
        case = DemoCase(
            name=name, caption=kv.get("caption", ""), posted_at=kv.get("posted_at", ""),
            files=files, expect=kv.get("expect", ""),
        )
        out[name] = _check(case, path.parent)
    return out
