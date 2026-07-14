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
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads((tmp_path / "watch.json").read_text())


def test_read_only_construction_does_not_write(tmp_path):
    Watchlist(tmp_path / "watch.json")
    assert not (tmp_path / "watch.json").exists()  # plain read never touches disk
