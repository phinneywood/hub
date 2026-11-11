import pytest
from fastapi.testclient import TestClient

from hub import bridge, config
from hub.main import app

client = TestClient(app)
REAL_GET = bridge.get


OVERVIEW = {"estimated_matching_messages": 201, "sample_unread": 1, "sample_size": 10,
            "top_senders_in_sample": [["news@loud.example", 6], ["bob@example.com", 3], ["not an address", 1]],
            "mailing_lists_in_sample": [], "recurring_subjects_in_sample": [["Hi #", 2]],
            "label_counts": [{"id": "INBOX", "name": "INBOX", "messagesTotal": 93012, "messagesUnread": 16104}], "note": "n"}
PROFILE = {"mailbox": "a@example.com", "messages": 10, "organization_paused": True,
           "filter_editing_enabled": True, "sending_available": False}
STATE = {"profile": dict(PROFILE)}


def fake_get(path, ttl=60, **params):
    data = {
        "/gmail/profile": STATE["profile"],
        "/gmail/labels": {"labels": [{"id": "Label_1", "name": "<b>Bills</b>", "type": "user"},
                                     {"id": "INBOX", "name": "INBOX", "type": "system"}]},
        "/gmail/filters": {"filters": [{"id": "f1", "criteria": {"from": "x@y.z"},
                                        "action": {"addLabelIds": ["Label_1"]}, "editable_by_connector": True}],
                           "total": 1, "exact_duplicate_groups": []},
        "/gmail/overview": OVERVIEW,
        "/gmail/count": {"count": 1234, "exact": True},
        "/gmail/search": {"messages": [{"subject": "<script>x</script>", "from": "Loud News <news@loud.example>", "snippet": "s", "date": "d", "labels": []}],
                          "estimated_matches": 1},
        "/gmail/changes": {"changes": [{"id": "c1", "kind": "filter", "status": "applied", "created_at": 1700000000},
                                       {"id": "c2", "kind": "label", "status": "applied", "created_at": 1700000001, "label": {"id": "Label_9", "name": "Receipts"}}]},
    }
    return data[path]


ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    STATE["profile"] = dict(PROFILE)
    monkeypatch.setattr(bridge, "get", fake_get)


@pytest.mark.parametrize("path", ["/", "/gmail", "/gmail/tools", "/gmail/backlog", "/gmail/filters", "/gmail/labels", "/gmail/search?q=a", "/gmail/activity",
                                  "/gmail/explore", "/gmail/overview", "/gmail/quiet", "/gmail/plan", "/gmail/health", "/gmail/health?run=1", "/status", "/manifest.webmanifest"])
def test_pages_render(path):
    assert client.get(path).status_code == 200


def test_untrusted_content_is_escaped():
    body = client.get("/gmail/search?q=a").text
    assert "<script>x" not in body
    assert "<b>Bills" not in client.get("/gmail/labels").text


def test_tailscale_identity():
    assert client.get("/", headers={"Tailscale-User-Login": "me@example.com"}).status_code == 200
    assert client.get("/", headers={"Tailscale-User-Login": "other@example.com"}).status_code == 403


def test_identity_fails_closed_without_allowlist(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_LOGINS", set())
    assert client.get("/", headers={"Tailscale-User-Login": "me@example.com"}).status_code == 403


def test_bridge_client_rejects_unlisted_paths():
    with pytest.raises(bridge.BridgeError):
        REAL_GET("/gmail/archive")


def test_message_reads_bypass_cache(monkeypatch):
    requests = []
    monkeypatch.setattr(bridge, "_cache", {})
    def request(method, path, params=None, body=None, timeout=None):
        requests.append((method, path, params))
        return {"text": "fake message body"}
    monkeypatch.setattr(bridge, "_request", request)
    for _ in range(2):
        assert REAL_GET("/gmail/message", message_id="fake-id")["text"] == "fake message body"
    assert len(requests) == 2
    assert bridge._cache == {}


def test_refresh_clears_cache(monkeypatch):
    cleared = []
    monkeypatch.setattr(bridge, "clear_cache", lambda: cleared.append(1))
    client.get("/gmail?refresh=1")
    client.get("/gmail")
    assert cleared == [1]


def test_status_reports_bridge_down(monkeypatch):
    monkeypatch.setattr(bridge, "health", lambda: False)
    body = client.get("/status").text
    assert "Bridge reachable" in body and 'dot bad' in body


def test_error_page_has_no_inline_script_links(monkeypatch):
    def boom(path, **kw): raise bridge.BridgeError("down")
    monkeypatch.setattr(bridge, "get", boom)
    response = client.get("/gmail/filters")
    assert response.status_code == 502 and "javascript:" not in response.text


def test_activity_shows_created_labels():
    assert "Created label" in client.get("/gmail/activity").text
