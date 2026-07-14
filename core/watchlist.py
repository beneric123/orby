"""Watchlist store for push-mode session notifications.

Maps Claude Code session IDs to Slack DM threads. Written by watch.py
(enroll/unwatch), read by hooks/notify.py on every hook event.
Atomic writes and age-based pruning — same patterns as core/session.py.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from config import ORBY_DIR

log = logging.getLogger("orby.watchlist")

WATCH_FILE = ORBY_DIR / "watch.json"
MAX_WATCH_AGE_DAYS = 7


class Watchlist:
    """Session-ID-to-DM-thread mapping with file persistence."""

    def __init__(self, path: Path | None = None):
        self.path = path or WATCH_FILE
        loaded = self._load()
        self.entries = self._prune(loaded)
        if len(self.entries) != len(loaded):
            self._save()  # persist the prune; plain reads (every hook event) stay write-free

    def get(self, session_id: str) -> dict | None:
        return self.entries.get(session_id)

    def set(self, session_id: str, data: dict):
        now = datetime.utcnow().isoformat()
        data.setdefault("enrolled_at", now)
        data["last_ping"] = now
        self.entries[session_id] = data
        self._save()

    def touch(self, session_id: str):
        """Refresh last_ping so active watches survive the age prune."""
        if session_id in self.entries:
            self.entries[session_id]["last_ping"] = datetime.utcnow().isoformat()
            self._save()

    def remove(self, session_id: str) -> bool:
        if session_id in self.entries:
            del self.entries[session_id]
            self._save()
            return True
        return False

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                log.warning("Corrupt %s, starting fresh", self.path.name)
                return {}
        return {}

    def _save(self):
        # PID-unique temp name: concurrent hook processes must never share a temp file,
        # or interleaved writes could garble watch.json and silently wipe all enrollments.
        tmp = self.path.with_name(f"{self.path.stem}-{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.entries, indent=2))
        tmp.rename(self.path)

    @staticmethod
    def _prune(entries: dict) -> dict:
        cutoff = (datetime.utcnow() - timedelta(days=MAX_WATCH_AGE_DAYS)).isoformat()
        return {k: v for k, v in entries.items() if v.get("last_ping", "") > cutoff}
