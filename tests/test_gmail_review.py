"""Gmail review improvements: bigger sample, queue past 20, quick archive, trend, SWR. Fake bridge only."""
import time

import pytest
from fastapi.testclient import TestClient

from hub import bridge, store
from hub.main import app
from hub.tools.gmail import common
from tests.test_app import OVERVIEW, ORIGIN, fake_get

client = TestClient(app)


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def recording(path, ttl=60, **params):
        seen.append((path, params))
        if path == "/gmail/overview" and "-from:" in params.get("query", ""):
            return {**OVERVIEW, "top_senders_in_sample": [["next@quiet.example", 4], ["news@loud.example", 6]]}
        if path == "/gmail/count" and "is:unread" in params.get("query", ""):
            return {"count": 1200, "exact": True}
        return fake_get(path, ttl, **params)
    monkeypatch.setattr(bridge, "get", recording)
    return seen


def test_overview_uses_the_bridge_maximum_sample(calls):
    client.get("/gmail/tools")
    sizes = {p.get("sample_size") for path, p in calls if path == "/gmail/overview"}
    assert sizes == {common.SAMPLE_SIZE} == {500}


def test_queue_continues_past_the_first_batch_without_decided_senders(calls):
    for sender in ("news@loud.example", "bob@example.com"):
        store.set_decision(sender, "keep")
    body = client.get("/gmail/quiet").text
    assert "next@quiet.example" in body
    query = [p["query"] for path, p in calls if path == "/gmail/overview" and "-from:" in p.get("query", "")][0]
    assert query.startswith("in:inbox") and "-from:news@loud.example" in query and "-from:bob@example.com" in query


def test_excluding_respects_query_bound_and_skips_invalid():
    many = [f"sender{i:03d}@example-domain.com" for i in range(200)]
    query = common.excluding("in:inbox", ["bad address", *many])
    assert len(query) <= common.QUERY_LIMIT and "bad address" not in query and "-from:sender000@" in query


def test_card_shows_unread_share_and_hint(calls):
    body = client.get("/gmail/quiet").text
    assert "97%" in body and "left almost all of these unread" in body
    assert 'data-key="a"' in body and "data-shortcuts" in body


def test_quick_archive_plans_one_sender_and_never_writes(calls, monkeypatch):
    monkeypatch.setattr(bridge, "post", lambda *a, **k: pytest.fail("no writes"))
    response = client.post("/gmail/senders/archive", data={"sender": "news@loud.example", "q": "in:inbox older_than:1y"},
                           headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and "added=news%40loud.example" in response.headers["location"]
    [item] = store.items()
    assert (item["sender"], item["decision"], item["count"], item["status"]) == ("news@loud.example", "archive", 1234, "planned")
    assert client.post("/gmail/senders/archive", data={"sender": "from:x OR y"}, headers=ORIGIN,
                       follow_redirects=False).headers["location"].endswith("invalid=1")


def test_quick_archive_does_not_replace_started_actions(calls):
    store.set_decision("news@loud.example", "filter")
    item = store.items()[0]
    store.update(item["id"], status="done")
    client.post("/gmail/senders/archive", data={"sender": "news@loud.example"}, headers=ORIGIN)
    assert store.items()[0]["decision"] == "filter"


def test_inbox_trend_from_daily_snapshots():
    assert common.inbox_trend() is None
    store.record_inbox("2026-03-01", 1000, 100)
    store.record_inbox("2026-03-04", 900, 90)
    store.record_inbox("2026-03-04", 880, 80)  # latest that day wins
    assert common.inbox_trend() == {"change": -120, "days": 3, "since": "2026-03-01"}


def test_today_page_shows_trend_and_no_dead_mission(calls):
    store.record_inbox("2000-01-01", 99000, 1)
    body = client.get("/gmail/tools").text
    assert "Catch up" not in body and "Coming next" not in body
    assert "More ways to clear it" in body


def test_get_swr_serves_cache_then_refreshes_in_background(monkeypatch):
    fetched = []

    def fake_request(method, path, params=None, body=None):
        fetched.append(params["n"])
        return {"n": len(fetched)}
    monkeypatch.setattr(bridge, "_request", fake_request)
    assert bridge.get_swr("/gmail/overview", fresh=60, n=1) == {"n": 1}
    key = ("/gmail/overview", (("n", 1),))
    stamp, value = bridge._cache[key]
    bridge._cache[key] = (stamp - 120, value)  # age it past `fresh`
    assert bridge.get_swr("/gmail/overview", fresh=60, n=1) == {"n": 1}  # stale value served at once
    for _ in range(50):
        if bridge._cache[key][1] == {"n": 2}:
            break
        time.sleep(0.02)
    assert bridge._cache[key][1] == {"n": 2}


def _overview(total):
    return {'label_counts': [{'id': 'INBOX', 'messagesTotal': total, 'messagesUnread': 1}], 'senders': []}


def test_inbox_overview_is_saved_and_served_after_restart(monkeypatch):
    import json, stat, time
    from hub import bridge, config
    from hub.tools.gmail import common
    calls = []
    monkeypatch.setattr(bridge, '_request', lambda method, path, params=None, body=None: calls.append(path) or _overview(42))
    assert common.inbox_counts(common.overview())['messagesTotal'] == 42
    saved = config.DATA_DIR / common.SAVED_OVERVIEW
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert json.loads(saved.read_text())['overview']['label_counts'][0]['messagesTotal'] == 42

    # "Restart": empty memory, bridge slow/unreachable. The saved copy answers at once.
    bridge.clear_cache()
    common._loaded.clear()
    common._saved_at.clear()
    def unreachable(*args, **kwargs):
        raise bridge.BridgeError('Gmail bridge is unreachable')
    monkeypatch.setattr(bridge, '_request', unreachable)
    data = json.loads(saved.read_text())
    data['fetched_at'] = time.time() - 3 * 3600
    saved.write_text(json.dumps(data))
    assert common.inbox_counts(common.overview())['messagesTotal'] == 42
    assert common.age_label(common.overview_fetched_at()) == '3 hr ago'


def test_saved_overview_ignored_when_sample_size_changes(monkeypatch):
    import json, time
    from hub import bridge, config
    from hub.tools.gmail import common
    (config.DATA_DIR / common.SAVED_OVERVIEW).write_text(json.dumps(
        {'fetched_at': time.time(), 'sample_size': 200, 'overview': _overview(7)}))
    monkeypatch.setattr(bridge, '_request', lambda *a, **k: _overview(99))
    assert common.inbox_counts(common.overview())['messagesTotal'] == 99


def test_other_queries_are_not_saved(monkeypatch):
    from hub import bridge, config
    from hub.tools.gmail import common
    monkeypatch.setattr(bridge, '_request', lambda *a, **k: _overview(5))
    common.overview('older_than:1y')
    assert not (config.DATA_DIR / common.SAVED_OVERVIEW).exists()


def test_age_label():
    from hub.tools.gmail.common import age_label
    assert age_label(1000, now=1030) == 'just now'
    assert age_label(0, now=12 * 60) == '12 min ago'
    assert age_label(0, now=2 * 86400) == '2 days ago'
    assert age_label(0, now=86400) == '1 day ago'
