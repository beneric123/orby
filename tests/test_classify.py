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
