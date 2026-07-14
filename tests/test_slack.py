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
