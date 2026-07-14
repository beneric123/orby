# Orby Watch Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push-on-ready Slack notifications: any Claude Code session enrolled via `/watch` pings a per-session DM thread whenever it stops, needs input, or ends.

**Architecture:** A watchlist file (`~/.orby/watch.json`) maps Claude session IDs to Slack DM threads. A new `watch.py` CLI enrolls the current session (creates the DM thread root message). The existing hook script `hooks/notify.py` gets a push branch checked *before* attach-mode logic: watched sessions classify the event (sentinel file first, heuristic fallback) and post a threaded reply. No long-running process — one-way `chat.postMessage` only.

**Tech Stack:** Python 3.11, stdlib only for runtime (urllib, json, subprocess); pytest for tests. Spec: `docs/specs/2026-07-14-watch-notifications-design.md`.

## Global Constraints

- Repo root: `/home/ubuntu/orby` (symlinked as `~/.orby`); branch `feat/watch-notifications`.
- Python: `/home/ubuntu/orby/.venv/bin/python3` (3.11). Run tests as `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/ -v`.
- Hook path must stay stdlib-only (no slack-sdk) and must NEVER raise out of `main()` — log to `hooks.log` and return.
- Existing attach-mode behavior in `hooks/notify.py` must be untouched: a session NOT in the watchlist flows through the existing code exactly as before.
- Follow existing style: module docstrings, `datetime.utcnow().isoformat()` timestamps, atomic write via `.tmp` + `rename` (see `core/session.py`).
- Watch entries live in `~/.orby/watch.json`; sentinels in `~/.orby/status/<session_id>.json`. Both are runtime state → must be gitignored (the repo dir IS `~/.orby`).
- Message formats (verbatim, used across tasks):
  - Root: `👀 Watching *{repo}* @ \`{branch}\`` (+ ` — {label}` if label; non-git cwd: `👀 Watching \`{cwd}\``)
  - Sentinel reply: `{header}` + (` — {link}` if link) + (`\n{summary}` if summary), headers: `pr_ready` → `✅ *PR ready*`, `findings` → `📋 *Findings*`, `input` → `⏸️ *Needs input*`, `done`/unknown → `📋 *Done*`
  - Heuristic PR: `✅ *PR ready* — {url}\n{tail 300}`; heuristic other: `📋 *Done*\n{tail 500}`; empty: `📋 *Done* — _(no summary available)_`
  - Notification: `⏸️ *Needs input*` + (` — {msg[:500]}` if msg)
  - SessionEnd: `🏁 Session ended`; unwatch: `🛑 Stopped watching`

---

### Task 1: Test scaffolding + `core/watchlist.py`

**Files:**
- Create: `tests/conftest.py`, `tests/test_watchlist.py`, `core/watchlist.py`
- Modify: `requirements.txt`, `.gitignore`

**Interfaces:**
- Produces: `Watchlist(path: Path | None = None)` with `.path`, `.entries: dict`, `.get(session_id) -> dict | None`, `.set(session_id, data: dict)` (stamps `enrolled_at` once + `last_ping`), `.touch(session_id)` (refreshes `last_ping`), `.remove(session_id) -> bool`. Default path `~/.orby/watch.json`; entries older than 7 days (by `last_ping`) pruned on load.

- [ ] **Step 1: Install pytest and record the dev dependency**

```bash
/home/ubuntu/orby/.venv/bin/python3 -m pip install "pytest>=8.0"
```

Append to `/home/ubuntu/orby/requirements.txt`:

```
pytest>=8.0  # dev/test only
```

- [ ] **Step 2: Gitignore runtime state**

Append to `/home/ubuntu/orby/.gitignore` (create lines only if not already present):

```
watch.json
status/
.pytest_cache/
```

- [ ] **Step 3: Write conftest + failing watchlist tests**

`tests/conftest.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

`tests/test_watchlist.py`:

```python
import json
from datetime import datetime, timedelta

from core.watchlist import Watchlist


def _wl(tmp_path):
    return Watchlist(tmp_path / "watch.json")


def test_set_get_roundtrip(tmp_path):
    wl = _wl(tmp_path)
    wl.set("sid-1", {"label": "x", "dm_channel": "D1", "thread_ts": "123.456"})
    entry = wl.get("sid-1")
    assert entry["label"] == "x"
    assert entry["enrolled_at"]
    assert entry["last_ping"]


def test_persists_across_instances(tmp_path):
    _wl(tmp_path).set("sid-1", {"label": "x"})
    assert Watchlist(tmp_path / "watch.json").get("sid-1")["label"] == "x"


def test_get_missing_returns_none(tmp_path):
    assert _wl(tmp_path).get("nope") is None


def test_remove(tmp_path):
    wl = _wl(tmp_path)
    wl.set("sid-1", {})
    assert wl.remove("sid-1") is True
    assert wl.get("sid-1") is None
    assert wl.remove("sid-1") is False


def test_touch_updates_last_ping(tmp_path):
    wl = _wl(tmp_path)
    wl.set("sid-1", {})
    wl.entries["sid-1"]["last_ping"] = "2000-01-01T00:00:00"
    wl.touch("sid-1")
    assert wl.get("sid-1")["last_ping"] > "2000-01-01T00:00:00"


def test_prunes_stale_entries(tmp_path):
    path = tmp_path / "watch.json"
    stale = (datetime.utcnow() - timedelta(days=8)).isoformat()
    fresh = datetime.utcnow().isoformat()
    path.write_text(json.dumps({"old": {"last_ping": stale}, "new": {"last_ping": fresh}}))
    wl = Watchlist(path)
    assert wl.get("old") is None
    assert wl.get("new") is not None


def test_atomic_write_no_tmp_left(tmp_path):
    wl = _wl(tmp_path)
    wl.set("sid-1", {})
    assert not (tmp_path / "watch.tmp").exists()
    assert json.loads((tmp_path / "watch.json").read_text())
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/test_watchlist.py -v`
Expected: FAIL / collection error — `ModuleNotFoundError: No module named 'core.watchlist'`

- [ ] **Step 5: Implement `core/watchlist.py`**

```python
"""Watchlist store for push-mode session notifications.

Maps Claude Code session IDs to Slack DM threads. Written by watch.py
(enroll/unwatch), read by hooks/notify.py on every hook event.
Atomic writes and age-based pruning — same patterns as core/session.py.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from config import ORBY_DIR

WATCH_FILE = ORBY_DIR / "watch.json"
MAX_WATCH_AGE_DAYS = 7


class Watchlist:
    """Session-ID-to-DM-thread mapping with file persistence."""

    def __init__(self, path: Path | None = None):
        self.path = path or WATCH_FILE
        self.entries = self._prune(self._load())
        self._save()

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
                return {}
        return {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.entries, indent=2))
        tmp.rename(self.path)

    @staticmethod
    def _prune(entries: dict) -> dict:
        cutoff = (datetime.utcnow() - timedelta(days=MAX_WATCH_AGE_DAYS)).isoformat()
        return {k: v for k, v in entries.items() if v.get("last_ping", "") > cutoff}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/test_watchlist.py -v`
Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
cd /home/ubuntu/orby && git add tests/ core/watchlist.py requirements.txt .gitignore && git commit -m "feat: watchlist store for push-mode session notifications"
```

---

### Task 2: `core/slack.py` — thin Slack Web API helper

**Files:**
- Create: `core/slack.py`, `tests/test_slack.py`

**Interfaces:**
- Produces: `slack_api(method: str, payload: dict, bot_token: str, timeout: int = 4) -> dict` (parsed JSON; `{"ok": False, "error": str(e)}` on any exception) and `post_message(bot_token: str, channel: str, text: str, thread_ts: str | None = None) -> dict` (truncates >3800 chars, threads when `thread_ts` given).
- Note: existing `_post_to_slack` in `hooks/notify.py` stays as-is (attach path untouched); new push code uses these helpers.

- [ ] **Step 1: Write failing tests**

`tests/test_slack.py`:

```python
import json

import core.slack as slack


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body


def _capture(monkeypatch, captured, body=None):
    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode())
        captured["auth"] = req.headers.get("Authorization")
        return _FakeResponse(body or {"ok": True, "ts": "111.222"})

    monkeypatch.setattr(slack, "urlopen", fake_urlopen)


def test_slack_api_posts_json(monkeypatch):
    captured = {}
    _capture(monkeypatch, captured)
    resp = slack.slack_api("chat.postMessage", {"channel": "D1", "text": "hi"}, "xoxb-test")
    assert resp["ok"] is True
    assert captured["url"] == "https://slack.com/api/chat.postMessage"
    assert captured["payload"]["channel"] == "D1"
    assert captured["auth"] == "Bearer xoxb-test"


def test_slack_api_error_returns_not_ok(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("boom")

    monkeypatch.setattr(slack, "urlopen", boom)
    resp = slack.slack_api("chat.postMessage", {}, "xoxb-test")
    assert resp["ok"] is False
    assert "boom" in resp["error"]


def test_post_message_threads_and_truncates(monkeypatch):
    captured = {}
    _capture(monkeypatch, captured)
    slack.post_message("xoxb-test", "D1", "x" * 5000, thread_ts="1.2")
    assert captured["payload"]["thread_ts"] == "1.2"
    assert len(captured["payload"]["text"]) < 4000
    assert captured["payload"]["text"].endswith("_...truncated_")


def test_post_message_no_thread(monkeypatch):
    captured = {}
    _capture(monkeypatch, captured)
    slack.post_message("xoxb-test", "D1", "hi")
    assert "thread_ts" not in captured["payload"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/test_slack.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.slack'`

- [ ] **Step 3: Implement `core/slack.py`**

```python
"""Thin Slack Web API helper (stdlib only — used from the hook path)."""

import json
from urllib.request import Request, urlopen

SLACK_MSG_LIMIT = 3800  # matches formatter.SLACK_MSG_LIMIT


def slack_api(method: str, payload: dict, bot_token: str, timeout: int = 4) -> dict:
    """POST to https://slack.com/api/{method}. Never raises."""
    req = Request(
        f"https://slack.com/api/{method}",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bot_token}",
        },
    )
    try:
        return json.loads(urlopen(req, timeout=timeout).read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def post_message(bot_token: str, channel: str, text: str, thread_ts: str | None = None) -> dict:
    if len(text) > SLACK_MSG_LIMIT:
        text = text[:SLACK_MSG_LIMIT] + "\n\n_...truncated_"
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return slack_api("chat.postMessage", payload, bot_token)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/test_slack.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/orby && git add core/slack.py tests/test_slack.py && git commit -m "feat: stdlib Slack Web API helper for push notifications"
```

---

### Task 3: `core/classify.py` — sentinel + heuristic classification

**Files:**
- Create: `core/classify.py`, `tests/test_classify.py`

**Interfaces:**
- Produces: `consume_sentinel(session_id: str, status_dir: Path | None = None) -> dict | None` (reads AND deletes `~/.orby/status/<sid>.json`; None if absent/malformed — malformed files are still deleted), `extract_pr_url(text: str) -> str | None` (last GitHub PR URL), `classify_stop(session_id: str, last_message: str | None, status_dir: Path | None = None) -> str` (full Slack reply text per Global Constraints formats).

- [ ] **Step 1: Write failing tests**

`tests/test_classify.py`:

```python
import json

from core.classify import classify_stop, consume_sentinel, extract_pr_url


def _write_sentinel(tmp_path, sid, data):
    (tmp_path / f"{sid}.json").write_text(
        json.dumps(data) if isinstance(data, dict) else data
    )


def test_consume_sentinel_reads_and_deletes(tmp_path):
    _write_sentinel(tmp_path, "s1", {"category": "done", "summary": "all good"})
    data = consume_sentinel("s1", tmp_path)
    assert data["summary"] == "all good"
    assert not (tmp_path / "s1.json").exists()


def test_consume_sentinel_absent(tmp_path):
    assert consume_sentinel("nope", tmp_path) is None


def test_consume_sentinel_malformed_deletes_and_returns_none(tmp_path):
    _write_sentinel(tmp_path, "s1", "{not json")
    assert consume_sentinel("s1", tmp_path) is None
    assert not (tmp_path / "s1.json").exists()


def test_extract_pr_url_last_wins():
    text = (
        "see https://github.com/acme/repo/pull/1 and "
        "https://github.com/acme/repo/pull/42"
    )
    assert extract_pr_url(text) == "https://github.com/acme/repo/pull/42"
    assert extract_pr_url("no links here") is None


def test_classify_sentinel_pr_ready(tmp_path):
    _write_sentinel(tmp_path, "s1", {
        "category": "pr_ready",
        "summary": "3 tests added",
        "link": "https://github.com/acme/repo/pull/7",
    })
    msg = classify_stop("s1", "irrelevant", tmp_path)
    assert msg == "✅ *PR ready* — https://github.com/acme/repo/pull/7\n3 tests added"


def test_classify_sentinel_findings_no_link(tmp_path):
    _write_sentinel(tmp_path, "s1", {"category": "findings", "summary": "root cause is X"})
    assert classify_stop("s1", None, tmp_path) == "📋 *Findings*\nroot cause is X"


def test_classify_sentinel_unknown_category_falls_to_done(tmp_path):
    _write_sentinel(tmp_path, "s1", {"category": "wat", "summary": "hm"})
    assert classify_stop("s1", None, tmp_path).startswith("📋 *Done*")


def test_classify_heuristic_pr_url(tmp_path):
    msg = classify_stop("s1", "Opened https://github.com/acme/repo/pull/9 for review", tmp_path)
    assert msg.startswith("✅ *PR ready* — https://github.com/acme/repo/pull/9")


def test_classify_heuristic_done_tail(tmp_path):
    msg = classify_stop("s1", "Investigation finished. The cache was stale.", tmp_path)
    assert msg == "📋 *Done*\nInvestigation finished. The cache was stale."


def test_classify_heuristic_long_message_tail_truncated(tmp_path):
    msg = classify_stop("s1", "x" * 1000, tmp_path)
    body = msg.split("\n", 1)[1]
    assert body.startswith("…")
    assert len(body) == 501  # "…" + last 500 chars


def test_classify_no_message(tmp_path):
    assert "no summary" in classify_stop("s1", None, tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.classify'`

- [ ] **Step 3: Implement `core/classify.py`**

```python
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
    if sentinel:
        header = CATEGORY_HEADERS.get(sentinel.get("category"), CATEGORY_HEADERS["done"])
        link = (sentinel.get("link") or "").strip()
        summary = (sentinel.get("summary") or "").strip()
        text = header + (f" — {link}" if link else "")
        return text + (f"\n{summary}" if summary else "")

    text = (last_message or "").strip()
    url = extract_pr_url(text)
    if url:
        return f"✅ *PR ready* — {url}\n{_tail(text, 300)}"
    if not text:
        return "📋 *Done* — _(no summary available)_"
    return f"📋 *Done*\n{_tail(text, 500)}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/test_classify.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/orby && git add core/classify.py tests/test_classify.py && git commit -m "feat: sentinel-first stop classification with PR-URL heuristic fallback"
```

---

### Task 4: `watch.py` — enroll / unwatch / list CLI

**Files:**
- Create: `watch.py` (repo root, alongside `bot.py`), `tests/test_watch_cli.py`

**Interfaces:**
- Consumes: `Watchlist` (Task 1), `slack_api` / `post_message` (Task 2), `load_config()` — expects key `notify_user` (added in Task 7; until then reads `os.environ` via `load_config`, tests monkeypatch it).
- Produces: CLI `watch.py enroll [--label X] | unwatch | list`. `enroll` prints the FULL session id and the sentinel path (the `/watch` command relays these to Claude). Watch entry fields: `label, repo, branch, cwd, dm_channel, thread_ts` (+ timestamps from `Watchlist.set`).

- [ ] **Step 1: Write failing tests**

`tests/test_watch_cli.py`:

```python
import pytest

import watch
from core.watchlist import Watchlist


def _setup(monkeypatch, tmp_path, *, watched=False):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-123")
    wl = Watchlist(tmp_path / "watch.json")
    if watched:
        wl.set("sid-123", {"dm_channel": "D1", "thread_ts": "1.2"})
    monkeypatch.setattr(watch, "Watchlist", lambda: Watchlist(tmp_path / "watch.json"))
    monkeypatch.setattr(watch, "load_config", lambda: {
        "slack_bot_token": "xoxb-test",
        "notify_user": "U123",
    })
    return tmp_path / "watch.json"


def test_enroll_creates_entry_and_posts_root(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(watch, "slack_api", lambda m, p, t: (
        calls.append(("api", m, p)) or {"ok": True, "channel": {"id": "D1"}}))
    monkeypatch.setattr(watch, "post_message", lambda t, c, text, thread_ts=None: (
        calls.append(("post", c, text, thread_ts)) or {"ok": True, "ts": "9.9"}))
    path = _setup(monkeypatch, tmp_path)

    watch.enroll("my-label")

    entry = Watchlist(path).get("sid-123")
    assert entry["dm_channel"] == "D1"
    assert entry["thread_ts"] == "9.9"
    assert entry["label"] == "my-label"
    assert calls[0][1] == "conversations.open"
    _, channel, text, thread_ts = calls[1]
    assert channel == "D1"
    assert "👀 Watching" in text
    assert "my-label" in text
    assert thread_ts is None  # root message, not threaded
    assert (tmp_path / "status").is_dir()


def test_enroll_twice_is_noop(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, watched=True)
    monkeypatch.setattr(watch, "slack_api",
                        lambda *a: pytest.fail("must not call slack when already watching"))
    watch.enroll(None)
    assert "Already watching" in capsys.readouterr().out


def test_unwatch_removes_and_posts(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(watch, "post_message", lambda t, c, text, thread_ts=None: (
        calls.append((c, text, thread_ts)) or {"ok": True}))
    path = _setup(monkeypatch, tmp_path, watched=True)

    watch.unwatch()

    assert Watchlist(path).get("sid-123") is None
    assert calls == [("D1", "🛑 Stopped watching", "1.2")]


def test_enroll_without_session_id_exits(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    with pytest.raises(SystemExit):
        watch.enroll(None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/test_watch_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'watch'`

- [ ] **Step 3: Implement `watch.py`**

```python
#!/usr/bin/env python3
"""Orby watch CLI — enroll the current Claude Code session for push notifications.

Invoked from inside a session (via the /watch and /unwatch slash commands):

  ~/.orby/.venv/bin/python3 ~/.orby/watch.py enroll --label "flaky-test-fix"
  ~/.orby/.venv/bin/python3 ~/.orby/watch.py unwatch
  ~/.orby/.venv/bin/python3 ~/.orby/watch.py list

Reads CLAUDE_CODE_SESSION_ID from the environment (set by Claude Code in every
session shell), opens the notify user's DM, posts the thread-root message, and
records the mapping in ~/.orby/watch.json for hooks/notify.py to use.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ORBY_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ORBY_ROOT))

from config import load_config
from core.slack import post_message, slack_api
from core.watchlist import Watchlist


def _session_id() -> str:
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        sys.exit("ERROR: CLAUDE_CODE_SESSION_ID not set — run from inside a Claude Code session.")
    return sid


def _git_info(cwd: str) -> tuple[str | None, str | None]:
    """Best-effort (repo_name, branch) for the watch label. (None, None) outside git."""

    def run(*args):
        try:
            r = subprocess.run(["git", "-C", cwd, *args],
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip() or None if r.returncode == 0 else None
        except Exception:
            return None

    toplevel = run("rev-parse", "--show-toplevel")
    repo = Path(toplevel).name if toplevel else None
    branch = run("branch", "--show-current")
    return repo, branch


def enroll(label: str | None):
    sid = _session_id()
    wl = Watchlist()
    if wl.get(sid):
        print(f"Already watching session {sid[:8]}. Use `unwatch` first to re-enroll.")
        return

    cfg = load_config()
    user = cfg.get("notify_user")
    if not user:
        sys.exit("ERROR: ORBY_NOTIFY_USER not set in ~/.orby/config.env")
    token = cfg["slack_bot_token"]

    resp = slack_api("conversations.open", {"users": user}, token)
    if not resp.get("ok"):
        sys.exit(f"ERROR: conversations.open failed: {resp.get('error')}")
    channel = resp["channel"]["id"]

    cwd = os.getcwd()
    repo, branch = _git_info(cwd)
    target = f"*{repo}* @ `{branch}`" if repo and branch else f"`{cwd}`"
    text = f"👀 Watching {target}" + (f" — {label}" if label else "")
    resp = post_message(token, channel, text)
    if not resp.get("ok"):
        sys.exit(f"ERROR: chat.postMessage failed: {resp.get('error')}")

    status_dir = wl.path.parent / "status"
    status_dir.mkdir(exist_ok=True)
    wl.set(sid, {
        "label": label,
        "repo": repo,
        "branch": branch,
        "cwd": cwd,
        "dm_channel": channel,
        "thread_ts": resp["ts"],
    })
    print(
        f"Watching session {sid} ({target}).\n"
        f"Slack pings will thread under the DM root message.\n"
        f"Sentinel path for status updates: {status_dir / (sid + '.json')}"
    )


def unwatch():
    sid = _session_id()
    wl = Watchlist()
    entry = wl.get(sid)
    if not entry:
        print("This session is not being watched.")
        return
    cfg = load_config()
    post_message(cfg["slack_bot_token"], entry["dm_channel"],
                 "🛑 Stopped watching", thread_ts=entry["thread_ts"])
    wl.remove(sid)
    print(f"Stopped watching session {sid[:8]}.")


def list_watches():
    wl = Watchlist()
    if not wl.entries:
        print("No watched sessions.")
        return
    for sid, e in wl.entries.items():
        where = e.get("repo") or e.get("cwd") or "?"
        print(f"{sid[:8]}  {where} @ {e.get('branch') or '-'}  {e.get('label') or ''}")


def main():
    parser = argparse.ArgumentParser(description="Orby watch — Slack pings when this session is ready")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_enroll = sub.add_parser("enroll", help="watch the current session")
    p_enroll.add_argument("--label", default=None, help="short human label for the watch thread")
    sub.add_parser("unwatch", help="stop watching the current session")
    sub.add_parser("list", help="show watched sessions")
    args = parser.parse_args()

    if args.cmd == "enroll":
        enroll(args.label or None)  # "" (empty $ARGUMENTS) → None
    elif args.cmd == "unwatch":
        unwatch()
    else:
        list_watches()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/test_watch_cli.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/orby && git add watch.py tests/test_watch_cli.py && git commit -m "feat: watch CLI — enroll/unwatch/list sessions for Slack push pings"
```

---

### Task 5: Push branch in `hooks/notify.py`

**Files:**
- Modify: `hooks/notify.py` (three changes: imports, extract `_extract_stop_text` from the existing Stop branch, add `_handle_push` + routing in `main()`)
- Create: `tests/test_notify_push.py`

**Interfaces:**
- Consumes: `Watchlist` (Task 1), `post_message` (Task 2), `classify_stop` (Task 3).
- Produces: `_extract_stop_text(hook_data: dict) -> str | None`, `_handle_push(event: str, hook_data: dict, session_id: str, entry: dict, watchlist: Watchlist, bot_token: str) -> None`. Push mode reacts ONLY to `Stop` / `Notification` / `SessionEnd`; everything else silently returns. `SessionEnd` removes the watch entry; other pushes `touch()` it.

- [ ] **Step 1: Write failing tests**

`tests/test_notify_push.py`:

```python
import pytest

from core.watchlist import Watchlist
from hooks import notify


@pytest.fixture
def wl(tmp_path):
    wl = Watchlist(tmp_path / "watch.json")
    wl.set("sid-123", {"dm_channel": "D1", "thread_ts": "1.2"})
    return wl


def _capture_posts(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "post_message", lambda token, channel, text, thread_ts=None: (
        calls.append({"channel": channel, "text": text, "thread_ts": thread_ts}) or {"ok": True}))
    return calls


def test_stop_posts_classified_reply(monkeypatch, wl):
    calls = _capture_posts(monkeypatch)
    monkeypatch.setattr(notify, "classify_stop", lambda sid, text: f"📋 *Done*\n{text}")
    notify._handle_push("Stop", {"last_assistant_message": "wrapped up"},
                        "sid-123", wl.get("sid-123"), wl, "xoxb")
    assert calls == [{"channel": "D1", "text": "📋 *Done*\nwrapped up", "thread_ts": "1.2"}]
    assert wl.get("sid-123") is not None  # still watched


def test_notification_posts_needs_input(monkeypatch, wl):
    calls = _capture_posts(monkeypatch)
    notify._handle_push("Notification", {"message": "Permission needed"},
                        "sid-123", wl.get("sid-123"), wl, "xoxb")
    assert calls[0]["text"] == "⏸️ *Needs input* — Permission needed"


def test_session_end_posts_and_removes(monkeypatch, wl):
    calls = _capture_posts(monkeypatch)
    notify._handle_push("SessionEnd", {}, "sid-123", wl.get("sid-123"), wl, "xoxb")
    assert calls[0]["text"] == "🏁 Session ended"
    assert wl.get("sid-123") is None


def test_pre_tool_use_is_silent(monkeypatch, wl):
    calls = _capture_posts(monkeypatch)
    notify._handle_push("PreToolUse", {"tool_name": "Bash"},
                        "sid-123", wl.get("sid-123"), wl, "xoxb")
    assert calls == []


def test_extract_stop_text_string():
    assert notify._extract_stop_text({"last_assistant_message": "hi"}) == "hi"


def test_extract_stop_text_dict_blocks():
    msg = {"content": [{"type": "text", "text": "a"},
                       {"type": "tool_use"},
                       {"type": "text", "text": "b"}]}
    assert notify._extract_stop_text({"last_assistant_message": msg}) == "ab"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/test_notify_push.py -v`
Expected: FAIL — `AttributeError: module 'hooks.notify' has no attribute '_handle_push'` (and `_extract_stop_text`)

- [ ] **Step 3: Modify `hooks/notify.py`**

**(a)** After the existing `from formatter import format_tool_use` import (line 28), add:

```python
from core.classify import classify_stop
from core.slack import post_message
from core.watchlist import Watchlist
```

**(b)** Add these two functions after `_log` (after line 210):

```python
def _extract_stop_text(hook_data: dict) -> str | None:
    """Pull Claude's final message for a Stop event (hook field, then transcript)."""
    last_msg = hook_data.get("last_assistant_message")
    text = None
    if last_msg:
        if isinstance(last_msg, str):
            text = last_msg
        elif isinstance(last_msg, dict):
            parts = [b.get("text", "") for b in last_msg.get("content", [])
                     if isinstance(b, dict) and b.get("type") == "text"]
            text = "".join(parts) if parts else str(last_msg)
        else:
            text = str(last_msg)
    if not text:
        text = _get_last_response(hook_data.get("transcript_path"))
    return text


def _handle_push(event: str, hook_data: dict, session_id: str,
                 entry: dict, watchlist: Watchlist, bot_token: str):
    """Push-mode notification for a watched session (one-way, thread-per-session)."""
    if event == "Stop":
        text = classify_stop(session_id, _extract_stop_text(hook_data))
    elif event == "Notification":
        msg = (hook_data.get("message") or "").strip()
        text = "⏸️ *Needs input*" + (f" — {msg[:500]}" if msg else "")
    elif event == "SessionEnd":
        text = "🏁 Session ended"
    else:
        return  # watched sessions stay quiet during work (PreToolUse etc.)

    resp = post_message(bot_token, entry["dm_channel"], text, thread_ts=entry["thread_ts"])
    _log(f"  push[{event}]: {str(resp)[:200]}")
    if event == "SessionEnd":
        watchlist.remove(session_id)
    else:
        watchlist.touch(session_id)
```

**(c)** In `main()`, insert the push routing immediately after the `_log(f"  hook session_id: ...")` line (line 227) and BEFORE `mgr = SessionManager()`:

```python
    # Push mode: session enrolled via watch.py (/watch) — takes priority over attach mode.
    push_sid = hook_data.get("session_id")
    if push_sid:
        try:
            watchlist = Watchlist()
            entry = watchlist.get(push_sid)
        except Exception as e:
            _log(f"  watchlist error: {e}")
            entry = None
        if entry:
            try:
                cfg = load_config()
                _handle_push(args.event, hook_data, push_sid, entry, watchlist,
                             cfg["slack_bot_token"])
            except Exception as e:
                _log(f"  push error: {e}")
            return
```

**(d)** Replace the body of the existing attach-mode `Stop` branch (lines 267-288, from `# Use last_assistant_message directly...` through `text = response or ...`) with the extracted helper — behavior identical:

```python
    if args.event == "Stop":
        text = _extract_stop_text(hook_data) or "_Claude finished. Waiting for input._"
```

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/ -v`
Expected: all tests pass (7 + 4 + 11 + 4 + 6 = 32 passed)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/orby && git add hooks/notify.py tests/test_notify_push.py && git commit -m "feat: push-mode branch in notify hook for watched sessions"
```

---

### Task 6: `/watch` and `/unwatch` slash commands

**Files:**
- Create: `commands/watch.md`, `commands/unwatch.md` (in repo — source of truth)
- Create (symlinks, not committed): `~/.claude/commands/watch.md`, `~/.claude/commands/unwatch.md`

**Interfaces:**
- Consumes: `watch.py enroll --label` / `watch.py unwatch` (Task 4). The `!`-prefixed line runs at command-invocation time inside the session shell (where `CLAUDE_CODE_SESSION_ID` is set) and its output — including the sentinel path — is injected into Claude's context.

- [ ] **Step 1: Write `commands/watch.md`**

````markdown
---
allowed-tools: Bash(/home/ubuntu/.orby/.venv/bin/python3 /home/ubuntu/.orby/watch.py:*)
argument-hint: [label]
description: Push Slack pings when this session is ready (Orby watch)
---

## Enrollment result

!`/home/ubuntu/.orby/.venv/bin/python3 /home/ubuntu/.orby/watch.py enroll --label "$ARGUMENTS"`

## Standing instructions for this session (now watched)

The user has stepped away. Every time you stop, Orby posts a Slack ping built from
your last message. To make pings crisp, at every genuine stopping point — task
complete, PR created, findings ready, or blocked on the user — use the Write tool
to write the sentinel file at the exact "Sentinel path" shown in the enrollment
result above, containing a single JSON object:

{"category": "<pr_ready|findings|input|done>", "summary": "<one crisp line>", "link": "<URL, or omit the key>"}

- `pr_ready` — you created or updated a PR; link = the PR URL
- `findings` — an investigation/analysis finished; summary = the core ideas in one line
- `input` — you are blocked on a question or decision; summary = exactly what you need
- `done` — any other completed chunk of work

Rules:
- Write the sentinel BEFORE ending the turn it applies to.
- Only at genuine stopping points — skip routine mid-task turns (the fallback
  uses your last message, which is fine).
- summary is plain text, one line, no markdown.

Acknowledge in one short sentence that watch mode is active, then continue any
pending work.
````

- [ ] **Step 2: Write `commands/unwatch.md`**

````markdown
---
allowed-tools: Bash(/home/ubuntu/.orby/.venv/bin/python3 /home/ubuntu/.orby/watch.py:*)
description: Stop Orby watch pings for this session
---

!`/home/ubuntu/.orby/.venv/bin/python3 /home/ubuntu/.orby/watch.py unwatch`

Watch mode is now off for this session: stop writing status sentinel files; the
earlier watch-mode standing instruction no longer applies. Acknowledge in one
short sentence.
````

- [ ] **Step 3: Symlink into the global commands directory**

```bash
mkdir -p /home/ubuntu/.claude/commands
ln -sf /home/ubuntu/orby/commands/watch.md /home/ubuntu/.claude/commands/watch.md
ln -sf /home/ubuntu/orby/commands/unwatch.md /home/ubuntu/.claude/commands/unwatch.md
ls -la /home/ubuntu/.claude/commands/
```

Expected: both symlinks present, pointing into `/home/ubuntu/orby/commands/`.

- [ ] **Step 4: Smoke-test the CLI outside a session (graceful failure)**

Run: `/home/ubuntu/orby/.venv/bin/python3 /home/ubuntu/orby/watch.py list`
Expected: `No watched sessions.`

Run: `env -u CLAUDE_CODE_SESSION_ID /home/ubuntu/orby/.venv/bin/python3 /home/ubuntu/orby/watch.py enroll`
Expected: exit 1 with `ERROR: CLAUDE_CODE_SESSION_ID not set — run from inside a Claude Code session.`

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/orby && git add commands/ && git commit -m "feat: /watch and /unwatch slash commands"
```

---

### Task 7: Wiring — config, SessionEnd hook, README, E2E

**Files:**
- Modify: `config.py` (add `notify_user`), `config.env.example`, `README.md`
- Modify (local, untracked): `~/.orby/config.env` (add `ORBY_NOTIFY_USER=U091UFR4BKR`)
- Modify (user-global, not in repo): `~/.claude/settings.json` (add `SessionEnd` hook)
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `load_config()["notify_user"]` (str | None) — consumed by `watch.py enroll` (Task 4).

- [ ] **Step 1: Write failing config test**

`tests/test_config.py`:

```python
from config import load_config


def test_load_config_exposes_notify_user(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("ORBY_NOTIFY_USER", "U123")
    assert load_config()["notify_user"] == "U123"
```

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/test_config.py -v`
Expected: FAIL — `KeyError: 'notify_user'`

- [ ] **Step 2: Add `notify_user` to `config.py`**

In `load_config()`'s returned dict, after the `"permission_mode"` line, add:

```python
        "notify_user": os.environ.get("ORBY_NOTIFY_USER"),
```

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 3: Document the new key**

Append to `config.env.example`:

```
ORBY_NOTIFY_USER=U0XXXXXXX  # Slack member ID to DM for watch notifications
```

Append to the real `~/.orby/config.env` (untracked; do NOT commit):

```
ORBY_NOTIFY_USER=U091UFR4BKR
```

- [ ] **Step 4: Add the SessionEnd hook to `~/.claude/settings.json`**

Read `/home/ubuntu/.claude/settings.json`, then Edit: inside the `"hooks"` object, add a `"SessionEnd"` key alongside the existing `"Stop"` / `"PreToolUse"` / `"Notification"` entries:

```json
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/home/ubuntu/.orby/.venv/bin/python3 /home/ubuntu/.orby/hooks/notify.py --event SessionEnd",
            "async": true
          }
        ]
      }
    ]
```

Verify: `python3 -c "import json; json.load(open('/home/ubuntu/.claude/settings.json')); print('valid')"`
Expected: `valid`

- [ ] **Step 5: Add README section**

In `README.md`, after the "Slack Commands" table section, add:

```markdown
## Watch Mode (push notifications)

The opposite of attach mode: instead of pulling a session into a Slack thread,
a session pushes to you. Run `/watch [label]` inside any Claude Code session
before stepping away — Orby opens a DM thread (`👀 Watching <repo> @ <branch>`)
and replies to it whenever the session stops (`✅ PR ready`, `📋 Findings`,
`📋 Done`), needs input (`⏸️`), or ends (`🏁`). Run `/unwatch` when you're back.

One-way by design: the bot process does not need to be running — everything is
posted by the hook script. Message quality is hybrid: Claude writes a one-line
status sentinel (`~/.orby/status/<session_id>.json`) at natural stopping points;
when absent, the hook falls back to a heuristic over Claude's last message.
Requires `ORBY_NOTIFY_USER` (your Slack member ID) in `config.env`.
```

- [ ] **Step 6: Full suite + commit**

Run: `cd /home/ubuntu/orby && .venv/bin/python3 -m pytest tests/ -v`
Expected: 33 passed

```bash
cd /home/ubuntu/orby && git add config.py config.env.example README.md tests/test_config.py && git commit -m "feat: wire watch mode — notify_user config, SessionEnd hook docs, README"
git push -u origin feat/watch-notifications
```

- [ ] **Step 7: Manual E2E (from the spec)**

1. In THIS session (or any session): run `/watch e2e-test` → expect DM root message `👀 Watching ...` in Slack.
2. Ask something trivial, let the turn end → expect `📋 *Done*` reply threaded under the root.
3. Write a sentinel by hand:
   `echo '{"category":"findings","summary":"e2e sentinel works"}' > ~/.orby/status/$CLAUDE_CODE_SESSION_ID.json`
   then let a turn end → expect `📋 *Findings*\ne2e sentinel works` AND the file deleted.
4. Trigger a permission prompt (e.g. a non-allowlisted command) → expect `⏸️ *Needs input*` reply.
5. `/unwatch` → expect `🛑 Stopped watching` reply and the entry gone from `~/.orby/watch.json`.
6. `tail -20 ~/.orby/hooks.log` → push lines logged, no errors; verify a DIFFERENT (unwatched) session still logs `ABORT: no matching session found` (attach path untouched).

---

## Self-Review Notes

- **Spec coverage:** watchlist store (T1), Slack helper (T2), classification incl. consume-once + malformed sentinel (T3), enroll/unwatch/list CLI + root message + DM resolution (T4), push branch + event filtering + SessionEnd cleanup + 7-day prune (T1/T5), slash commands + sentinel convention (T6), `ORBY_NOTIFY_USER` + SessionEnd hook + docs + manual E2E (T7). Out-of-scope items from spec deliberately absent.
- **Type consistency:** `Watchlist.get/set/touch/remove`, `classify_stop(session_id, last_message, status_dir=None)`, `post_message(bot_token, channel, text, thread_ts=None)` used identically across Tasks 4–5.
- **Notify-user flow:** Task 4 consumes `load_config()["notify_user"]`; tests monkeypatch it so Task 4 passes before Task 7 lands the config key (env var already read via `os.environ` at runtime only after Task 7 — acceptable because the CLI is not exercised for real until E2E in Task 7).
