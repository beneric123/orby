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


def test_enroll_creates_entry_and_posts_root(monkeypatch, tmp_path, capsys):
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
    out = capsys.readouterr().out
    assert "sid-123" in out  # FULL session id
    assert str(tmp_path / "status" / "sid-123.json") in out  # sentinel path


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


def test_unwatch_removes_even_when_slack_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(watch, "post_message", lambda t, c, text, thread_ts=None: (
        {"ok": False, "error": "channel_not_found"}))
    path = _setup(monkeypatch, tmp_path, watched=True)

    watch.unwatch()

    assert Watchlist(path).get("sid-123") is None  # removal proceeds
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "channel_not_found" in out


def test_enroll_without_session_id_exits(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    with pytest.raises(SystemExit):
        watch.enroll(None)
