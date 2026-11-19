"""
Persistent bot state in data/bot_chats.json (gitignored). Owner: Sampurna.

    {"approved": [chat ids approved with /start <passcode>],
     "results":  {"<chat_id>:<message_id>": "<investigation id>"}}   # result/report messages -> run

Chats in TELEGRAM_ALLOWED_CHAT_IDS are always allowed and are not stored here.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from app import config

MAX_RESULTS = 1000


class BotState:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or config.BOT_STATE_FILE)
        self._lock = threading.Lock()
        self.approved: set[int] = set()
        self.results: dict[str, str] = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.approved = {int(x) for x in data.get("approved", [])}
                self.results = {str(k): str(v) for k, v in data.get("results", {}).items()}
            except (ValueError, OSError):
                pass   # a damaged file starts fresh rather than stopping the bot

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"approved": sorted(self.approved), "results": self.results}, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)

    def is_allowed(self, chat_id: int) -> bool:
        return chat_id in config.TELEGRAM_ALLOWED_CHAT_IDS or chat_id in self.approved

    def approve(self, chat_id: int) -> None:
        with self._lock:
            self.approved.add(chat_id)
            self._save()

    def remember_result(self, chat_id: int, message_id: int, inv_id: str) -> None:
        with self._lock:
            self.results[f"{chat_id}:{message_id}"] = inv_id
            if len(self.results) > MAX_RESULTS:
                for key in list(self.results)[: len(self.results) - MAX_RESULTS]:
                    del self.results[key]
            self._save()

    def result_for(self, chat_id: int, message_id: int) -> Optional[str]:
        return self.results.get(f"{chat_id}:{message_id}")
