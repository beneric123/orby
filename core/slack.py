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
