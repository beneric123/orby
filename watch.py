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
    resp = post_message(cfg["slack_bot_token"], entry["dm_channel"],
                        "🛑 Stopped watching", thread_ts=entry["thread_ts"])
    if not resp.get("ok"):
        print(f"WARNING: could not post to Slack thread: {resp.get('error')}")
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
