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
