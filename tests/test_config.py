from config import load_config


def test_load_config_exposes_notify_user(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("ORBY_NOTIFY_USER", "U123")
    assert load_config()["notify_user"] == "U123"
