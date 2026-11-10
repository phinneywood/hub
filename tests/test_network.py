from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from hub.main import app
from hub.tools.network import analytics, ha

client = TestClient(app)


def state(entity_id, value, name, **attrs):
    now = datetime.now(timezone.utc).isoformat()
    return {"entity_id": entity_id, "state": value,
            "attributes": {"friendly_name": name, **attrs},
            "last_changed": now, "last_updated": now, "last_reported": now}


@pytest.fixture
def network_states():
    return [
        state("binary_sensor.router_wan", "on", "WAN connectivity", device_class="connectivity"),
        state("sensor.router_download_speed", "125", "Download speed", unit_of_measurement="Mbit/s"),
        state("sensor.router_upload_speed", "24", "Upload speed", unit_of_measurement="Mbit/s"),
        state("sensor.internet_ping", "25", "Internet ping", unit_of_measurement="ms"),
        state("sensor.router_total_bytes_received", "2000000", "Total bytes received", unit_of_measurement="B"),
        state("device_tracker.family_phone", "home", "Family phone", device_type="client", ip_address="192.168.68.33",
              mac="00:11:22:33:44:55", connection_type="band5", deco_device="Hall Deco",
              down_kilobytes_per_s=250, up_kilobytes_per_s=50),
        state("device_tracker.hall_deco", "home", "Hall Deco", device_type="deco", master=True,
              internet_online=True, signal_band5=4, device_model="BE11000"),
        state("device_tracker.personal_gps", "home", "Personal phone location", source_type="gps", latitude=47.6),
    ]


def test_network_pages_are_in_hub_and_auth_stays_global():
    paths = ("/network", "/network/devices", "/network/mesh")
    for path in paths:
        response = client.get(path, headers={"Tailscale-User-Login": "other@example.com"})
        assert response.status_code == 403


def test_unconfigured_dashboard_does_not_contact_home_assistant(monkeypatch, tmp_path):
    monkeypatch.setattr(ha, "TOKEN_FILE", tmp_path / "absent-token")
    monkeypatch.setattr(ha, "configured", lambda: False)
    monkeypatch.setattr(ha, "states", lambda: pytest.fail("must not connect without a token"))
    response = client.get("/network")
    assert response.status_code == 200
    assert "Connect Home Assistant" in response.text
    assert "hidden prompt" in response.text
    assert "Never paste the token into chat" in response.text


def test_deco_and_upnp_entities_are_detected_but_presence_is_not(network_states):
    devices, metrics = analytics.classify(network_states)
    assert [(d["name"], d["kind"]) for d in devices] == [("Hall Deco", "node"), ("Family phone", "client")]
    phone = next(d for d in devices if d["kind"] == "client")
    assert phone["online"] and phone["connection"] == "5 GHz"
    assert phone["node"] == "Hall Deco"
    assert phone["mac"] == "••:••:••:33:44:55"
    sources, _ = analytics.select_sources(network_states, metrics, {})
    assert sources['wan']['state'] == 'on'
    assert analytics.best_metric(metrics, "download_rate")["state"] == 125
    assert any(m["kind"] == "wan" and m["name"] == "Hall Deco internet" for m in metrics)
    assert all(d["entity_id"] != "device_tracker.personal_gps" for d in devices)


def test_device_rate_conversion_and_usage_rollover():
    assert analytics.device_speed({"download_kb": 1000}, "download") == pytest.approx(8)
    rows = [
        {"state": "1000000", "last_changed": "2026-10-31T12:00:00Z"},
        {"state": "3500000", "last_changed": "2026-10-31T13:00:00Z"},
    ]
    assert analytics.usage(rows, {"unit": "B"}) == 2_500_000
    assert analytics.format_bytes(2_500_000) == "2.50 MB"
    rollover = [
        {"state": "4294000000", "last_changed": "2026-10-31T12:00:00Z"},
        {"state": "1000000", "last_changed": "2026-10-31T13:00:00Z"},
    ]
    assert analytics.usage(rollover, {"unit": "B"}) is None  # reset/rollover is ambiguous
    assert analytics.counter_usage(rollover, {'unit': 'B'})['resets'] == 1


def test_outage_summary_only_claims_complete_recorded_coverage():
    now = datetime.now(timezone.utc)
    rows = [
        {"state": "on", "last_changed": (now - timedelta(hours=25)).isoformat()},
        {"state": "off", "last_changed": (now - timedelta(hours=6)).isoformat()},
        {"state": "on", "last_changed": (now - timedelta(hours=5)).isoformat()},
    ]
    summary = analytics.outage_summary(rows, {"kind": "wan"}, 1)
    assert summary["known"]
    assert summary["outages"] == 1
    assert summary["downtime"].total_seconds() == pytest.approx(3600, abs=2)
    assert summary["reliability"] == pytest.approx(100 * 23 / 24, abs=0.01)
    assert not analytics.outage_summary(rows[1:], {"kind": "wan"}, 1)["known"]


def test_home_assistant_token_file_requires_owner_only_permissions(monkeypatch, tmp_path):
    token_file = tmp_path / "network-token"
    token_file.write_text("x" * 80)
    token_file.chmod(0o600)
    monkeypatch.setattr(ha, "TOKEN_FILE", token_file)
    assert ha._token() == "x" * 80
    token_file.chmod(0o644)
    with pytest.raises(ha.HomeAssistantError, match="unsafe permissions"):
        ha._token()


def test_home_assistant_api_is_fixed_and_history_is_bounded(monkeypatch, tmp_path):
    token_file = tmp_path / "network-token"
    token_file.write_text("x" * 80)
    token_file.chmod(0o600)
    monkeypatch.setattr(ha, "TOKEN_FILE", token_file)
    with pytest.raises(ha.HomeAssistantError, match="route is not available"):
        ha._request("/api/services/light/turn_on")
    now = datetime.now(timezone.utc)
    with pytest.raises(ha.HomeAssistantError, match="between 1 and 12"):
        ha.history([], now - timedelta(hours=1))
    with pytest.raises(ha.HomeAssistantError, match="entity identifier"):
        ha.history(["../api/config"], now - timedelta(hours=1))
    with pytest.raises(ha.HomeAssistantError, match="allowed window"):
        ha.history(["sensor.safe"], now - timedelta(days=31))


def test_network_dashboard_renders_readonly_fake_state_and_history(monkeypatch, network_states):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(ha, "configured", lambda: True)
    monkeypatch.setattr(ha, "states", lambda: network_states)

    def history(ids, start, end=None, *, include_attributes=False):
        grouped = []
        for ident in ids:
            if ident == "binary_sensor.router_wan":
                grouped.append([
                    {"state": "on", "last_changed": (now - timedelta(hours=25)).isoformat()},
                    {"state": "off", "last_changed": (now - timedelta(hours=6)).isoformat()},
                    {"state": "on", "last_changed": (now - timedelta(hours=5)).isoformat()},
                ])
            elif "total_bytes" in ident:
                grouped.append([
                    {"state": "1000000", "last_changed": (now - timedelta(hours=12)).isoformat()},
                    {"state": "2000000", "last_changed": (now - timedelta(hours=6)).isoformat()},
                ])
            elif "download" in ident:
                grouped.append([
                    {"state": "25", "last_changed": (now - timedelta(hours=5)).isoformat()},
                    {"state": "125", "last_changed": (now - timedelta(hours=1)).isoformat()},
                ])
            else:
                grouped.append([
                    {"state": "5", "last_changed": (now - timedelta(hours=5)).isoformat()},
                    {"state": "24", "last_changed": (now - timedelta(hours=1)).isoformat()},
                ])
        for ident, group in zip(ids, grouped):
            if group:
                group[0]['entity_id'] = ident
        return grouped

    monkeypatch.setattr(ha, "history", history)
    response = client.get("/network?range=24h")
    assert response.status_code == 200
    assert "Reported up" in response.text
    assert "125.00 Mbit/s" in response.text
    assert "External latency" in response.text
    assert "Internet usability is not independently measured" in response.text
    assert "Family phone" in response.text
    assert "Peak 125.0 Mbit/s" in response.text
    assert "not ISP line speed" in response.text
    assert "Personal phone location" not in response.text

    mesh = client.get("/network/mesh")
    assert mesh.status_code == 200
    assert "Hall Deco" in mesh.text and "Family phone" in mesh.text
    devices = client.get("/network/devices?q=family")
    assert devices.status_code == 200 and "Family phone" in devices.text
    assert "Personal phone location" not in devices.text


def test_device_page_includes_live_rates_and_refuses_unknown_ids(monkeypatch, network_states):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(ha, "configured", lambda: True)
    monkeypatch.setattr(ha, "states", lambda: network_states)

    def history(ids, start, end=None, *, include_attributes=False):
        groups = []
        for ident in ids:
            if ident == "device_tracker.family_phone":
                groups.append([
                    {"state": "home", "last_changed": (now - timedelta(hours=3)).isoformat(),
                     "attributes": {"down_kilobytes_per_s": 25, "up_kilobytes_per_s": 10}},
                    {"state": "home", "last_changed": (now - timedelta(hours=1)).isoformat(),
                     "attributes": {"down_kilobytes_per_s": 250, "up_kilobytes_per_s": 50}},
                ])
            else:
                groups.append([])
        for ident, group in zip(ids, groups):
            if group:
                group[0]['entity_id'] = ident
        return groups

    monkeypatch.setattr(ha, "history", history)
    response = client.get("/network/device/device_tracker.family_phone")
    assert response.status_code == 200
    assert "2.00 Mbit/s" in response.text
    assert "Family phone" in response.text
    assert client.get("/network/device/device_tracker.unknown").status_code == 404


def test_untrusted_entity_name_is_escaped(monkeypatch, network_states):
    monkeypatch.setattr(ha, "configured", lambda: True)
    unsafe = list(network_states)
    index = next(i for i, item in enumerate(unsafe) if item["entity_id"] == "device_tracker.family_phone")
    unsafe[index] = {**unsafe[index], "attributes": {**unsafe[index]["attributes"], "friendly_name": "<script>unsafe()</script>"}}
    monkeypatch.setattr(ha, "states", lambda: unsafe)
    monkeypatch.setattr(ha, "history", lambda ids, start, end=None, **kwargs: [[] for _ in ids])
    response = client.get("/network/devices")
    assert response.status_code == 200
    assert "<script>unsafe()" not in response.text
    assert "&lt;script&gt;unsafe()" in response.text


def test_home_assistant_client_uses_fixed_host_get_and_rejects_redirect(monkeypatch, tmp_path):
    token_file = tmp_path / "network-token"
    token_file.write_text("x" * 80)
    token_file.chmod(0o600)
    monkeypatch.setattr(ha, "TOKEN_FILE", token_file)
    observed = {}

    class Response:
        status_code = 302

        def iter_bytes(self, size):
            return iter((b'{"message":"redirect"}',))

    class Client:
        def __init__(self, **kwargs):
            observed["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, **kwargs):
            observed.update({"method": method, "url": url, **kwargs})
            return ResponseContext()

    class ResponseContext:
        def __enter__(self):
            return Response()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(ha.httpx, "Client", Client)
    with pytest.raises(ha.HomeAssistantError, match="did not return"):
        ha._request("/api/")
    assert observed["method"] == "GET"
    assert observed["url"] == "http://127.0.0.1:8123/api/"
    assert observed["client"] == {"timeout": 8, "follow_redirects": False, "trust_env": False}


def test_home_assistant_address_is_loopback_unless_a_safe_address_is_configured():
    assert ha._base_url(None) == "http://127.0.0.1:8123"
    assert ha._base_url("http://192.168.1.20") == "http://192.168.1.20"
    assert ha._base_url("http://10.0.0.5:8123/") == "http://10.0.0.5:8123"
    assert ha._base_url("https://home.example.test:9443") == "https://home.example.test:9443"
    for bad in ("http://example.com", "http://8.8.8.8", "http://user@192.168.1.20", "https://user:pw@home.example.test",
                "http://192.168.1.20/api", "https://home.example.test?x=1", "http://[fd00::1]", "192.168.1.20",
                "ftp://192.168.1.20", "https://home.example.test:port"):
        with pytest.raises(ValueError):
            ha._base_url(bad)
