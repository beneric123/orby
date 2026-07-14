"""Classify a watched session's Stop event into a Slack reply.

Priority: structured sentinel written by Claude (consume-once) →
heuristic over the last assistant message (PR URL → tail summary).
"""

import json
import re
from pathlib import Path

from config import ORBY_DIR

STATUS_DIR = ORBY_DIR / "status"

PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")

CATEGORY_HEADERS = {
    "pr_ready": "✅ *PR ready*",
    "findings": "📋 *Findings*",
    "input": "⏸️ *Needs input*",
    "done": "📋 *Done*",
}


def consume_sentinel(session_id: str, status_dir: Path | None = None) -> dict | None:
    """Read and delete the session's status sentinel. None if absent or malformed."""
    path = (status_dir or STATUS_DIR) / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        data = None
    try:
        path.unlink()  # consume-once: stale statuses must never re-fire
    except OSError:
        pass
    return data if isinstance(data, dict) else None


def extract_pr_url(text: str) -> str | None:
    matches = PR_URL_RE.findall(text or "")
    return matches[-1] if matches else None


def _tail(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def classify_stop(session_id: str, last_message: str | None, status_dir: Path | None = None) -> str:
    """Build the Slack reply text for a Stop event."""
    sentinel = consume_sentinel(session_id, status_dir)
    if sentinel:  # content-free sentinels ({}) intentionally fall through to the heuristic
        def _field(key: str) -> str:
            val = sentinel.get(key)
            return val.strip() if isinstance(val, str) else ""

        category = sentinel.get("category")
        header = (CATEGORY_HEADERS.get(category) if isinstance(category, str) else None) or CATEGORY_HEADERS["done"]
        link = _field("link")
        summary = _field("summary")
        text = header + (f" — {link}" if link else "")
        return text + (f"\n{summary}" if summary else "")

    text = (last_message or "").strip()
    url = extract_pr_url(text)
    if url:
        return f"✅ *PR ready* — {url}\n{_tail(text, 300)}"
    if not text:
        return "📋 *Done* — _(no summary available)_"
    return f"📋 *Done*\n{_tail(text, 500)}"
