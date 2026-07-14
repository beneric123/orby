import pytest

import watch
from core.watchlist import Watchlist


def _setup(monkeypatch, tmp_path, *, watched=False):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-123456789")
    wl = Watchlist(tmp_path / "watch.json")
    if watched:
        wl.set("sid-123456789", {"dm_channel": "D1", "thread_ts": "1.2"})
    monkeypatch.setattr(watch, "Watchlist", lambda: Watchlist(tmp_path / "watch.json"))
    monkeypatch.setattr(watch, "load_config", lambda: {
        "slack_bot_token": "xoxb-test",
        "notify_user": "U123",
    })
    monkeypatch.setattr(watch, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")  # hermetic: no real transcripts
    return tmp_path / "watch.json"


def _write_transcript(tmp_path, sid, *titles):
    proj = tmp_path / "projects" / "-home-user-somerepo"
    proj.mkdir(parents=True, exist_ok=True)
    lines = [f'{{"type":"custom-title","customTitle":"{t}","sessionId":"{sid}"}}' for t in titles]
    (proj / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")


def test_enroll_creates_entry_and_posts_root(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(watch, "slack_api", lambda m, p, t: (
        calls.append(("api", m, p)) or {"ok": True, "channel": {"id": "D1"}}))
    monkeypatch.setattr(watch, "post_message", lambda t, c, text, thread_ts=None: (
        calls.append(("post", c, text, thread_ts)) or {"ok": True, "ts": "9.9"}))
    path = _setup(monkeypatch, tmp_path)

    watch.enroll("my-label")

    entry = Watchlist(path).get("sid-123456789")
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
    assert "sid-123456789" in out  # FULL session id
    assert str(tmp_path / "status" / "sid-123456789.json") in out  # sentinel path


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

    assert Watchlist(path).get("sid-123456789") is None
    assert calls == [("D1", "🛑 Stopped watching", "1.2")]


def test_unwatch_removes_even_when_slack_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(watch, "post_message", lambda t, c, text, thread_ts=None: (
        {"ok": False, "error": "channel_not_found"}))
    path = _setup(monkeypatch, tmp_path, watched=True)

    watch.unwatch()

    assert Watchlist(path).get("sid-123456789") is None  # removal proceeds
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "channel_not_found" in out


def test_enroll_without_session_id_exits(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    with pytest.raises(SystemExit):
        watch.enroll(None)


def test_session_title_last_rename_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    _write_transcript(tmp_path, "sid-123456789", "old-name", "my-renamed-session")
    assert watch._session_title("sid-123456789") == "my-renamed-session"


def test_session_title_none_without_transcript(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    assert watch._session_title("sid-123456789") is None


def test_enroll_uses_rename_name_when_no_label(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(watch, "slack_api", lambda m, p, t: {"ok": True, "channel": {"id": "D1"}})
    monkeypatch.setattr(watch, "post_message", lambda t, c, text, thread_ts=None: (
        calls.append(text) or {"ok": True, "ts": "9.9"}))
    path = _setup(monkeypatch, tmp_path)
    _write_transcript(tmp_path, "sid-123456789", "my-renamed-session")

    watch.enroll(None)

    assert "👀 Watching *my-renamed-session* — " in calls[0]
    assert Watchlist(path).get("sid-123456789")["session_name"] == "my-renamed-session"


def test_enroll_label_overrides_rename_name(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(watch, "slack_api", lambda m, p, t: {"ok": True, "channel": {"id": "D1"}})
    monkeypatch.setattr(watch, "post_message", lambda t, c, text, thread_ts=None: (
        calls.append(text) or {"ok": True, "ts": "9.9"}))
    _setup(monkeypatch, tmp_path)
    _write_transcript(tmp_path, "sid-123456789", "my-renamed-session")

    watch.enroll("explicit-label")

    assert "👀 Watching *explicit-label* — " in calls[0]
    assert "my-renamed-session" not in calls[0]
