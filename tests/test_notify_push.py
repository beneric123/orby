import io
import json
import sys

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
    wl.entries["sid-123"]["last_ping"] = "2000-01-01T00:00:00"
    notify._handle_push("Stop", {"last_assistant_message": "wrapped up"},
                        "sid-123", wl.get("sid-123"), wl, "xoxb")
    assert calls == [{"channel": "D1", "text": "📋 *Done*\nwrapped up", "thread_ts": "1.2"}]
    assert wl.get("sid-123")["last_ping"] > "2000-01-01T00:00:00"  # touch() refreshed it


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


def test_main_watched_session_returns_even_when_push_errors(monkeypatch, tmp_path):
    """The routing guarantee: a watched session never reaches attach mode, even on push failure."""
    wl = Watchlist(tmp_path / "watch.json")
    wl.set("sid-x", {"dm_channel": "D1", "thread_ts": "1.2"})
    monkeypatch.setattr(notify, "Watchlist", lambda: Watchlist(tmp_path / "watch.json"))
    monkeypatch.setattr(notify, "load_config",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(notify, "SessionManager",
                        lambda: pytest.fail("attach mode must not be reached for a watched session"))
    monkeypatch.setattr(sys, "argv", ["notify.py", "--event", "Stop"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "sid-x"})))
    notify.main()  # must swallow the error and return without raising


def test_main_routes_watched_session_to_push(monkeypatch, tmp_path):
    wl = Watchlist(tmp_path / "watch.json")
    wl.set("sid-x", {"dm_channel": "D1", "thread_ts": "1.2"})
    calls = []
    monkeypatch.setattr(notify, "Watchlist", lambda: Watchlist(tmp_path / "watch.json"))
    monkeypatch.setattr(notify, "load_config", lambda: {"slack_bot_token": "xoxb"})
    monkeypatch.setattr(notify, "_handle_push",
                        lambda event, hook_data, sid, entry, watchlist, token: calls.append((event, sid, token)))
    monkeypatch.setattr(notify, "SessionManager",
                        lambda: pytest.fail("attach mode must not be reached for a watched session"))
    monkeypatch.setattr(sys, "argv", ["notify.py", "--event", "Stop"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "sid-x"})))
    notify.main()
    assert calls == [("Stop", "sid-x", "xoxb")]
