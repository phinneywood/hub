"""Regressions for evidence, window boundaries and misleading reassurance."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from hub.main import app
from hub.tools.network import analytics as a, ha, pages, store

client = TestClient(app)
ORIGIN = {'Origin': 'http://testserver'}


def row(t, value, **extra):
    return {'state': str(value), 'last_updated': t.isoformat(), **extra}


def sensor(ident, value, unit='', **attrs):
    now = datetime.now(timezone.utc).isoformat()
    return {'entity_id': ident, 'state': str(value), 'last_updated': now, 'last_reported': now,
            'attributes': {'friendly_name': ident.split('.', 1)[-1].replace('_', ' '), 'unit_of_measurement': unit, **attrs}}


def test_unknown_gap_is_not_100_percent_uptime():
    end = datetime.now(timezone.utc)
    rows = [row(end - timedelta(hours=25), 'on'), row(end - timedelta(hours=12), 'unavailable'), row(end - timedelta(hours=1), 'on')]
    result = a.outage_summary(rows, {}, 1, end=end)
    assert not result['known'] and result['reliability'] is None
    assert result['coverage'] == pytest.approx(100 * 13 / 24)
    assert any(i['label'] == 'Monitoring gap' and i['duration'] == '11h 0m' for i in result['incidents'])
    assert result['outages'] == 0


def test_history_clips_start_end_and_does_not_treat_gap_recovery_as_outage():
    end = datetime.now(timezone.utc)
    rows = [row(end - timedelta(hours=30), 'off'), row(end - timedelta(hours=26), 'on'),
            row(end - timedelta(hours=12), 'unknown'), row(end - timedelta(hours=11), 'off'),
            row(end - timedelta(hours=10), 'on'), row(end + timedelta(hours=1), 'off')]
    result = a.outage_summary(rows, {}, 1, end=end)
    assert result['downtime'].total_seconds() == 3600
    assert result['outages'] == 1
    assert result['unknown_text'] == '1h 0m'


def test_unavailable_deco_does_not_reuse_saved_internet_attribute():
    item = sensor('device_tracker.primary_deco', 'unavailable', device_type='deco', master=True, internet_online=True)
    _, metrics = a.classify([item])
    assert metrics[0]['state'] == 'unknown'
    result = a.outage_summary([row(datetime.now(timezone.utc) - timedelta(hours=25), 'unavailable', attributes={'internet_online': True})],
                              {'attribute': 'internet_online'}, 1)
    assert result['coverage'] == 0


def test_internet_scope_excludes_appliance_and_deco_api():
    states = [sensor('binary_sensor.appliance_connection', 'on', device_class='connectivity'),
              sensor('sensor.deco_api_response_time', 25, 'ms'), sensor('sensor.internet_ping', 25, 'ms')]
    _, metrics = a.classify(states)
    assert not any(m['kind'] == 'wan' for m in metrics)
    sources, notes = a.select_sources(states, metrics, {})
    assert 'latency' not in sources  # named Internet is not sufficient to verify target
    assert not a.source_candidate(states[1], 'latency')
    assert a.source_candidate(states[2], 'latency')


def test_unambiguous_external_target_and_millisecond_conversion():
    states = [sensor('sensor.external_ping', 0.025, 's', target='1.1.1.1')]
    _, metrics = a.classify(states)
    sources, _ = a.select_sources(states, metrics, {})
    assert a.value(sources['latency']) == 25
    assert not a.external_target({'target': '192.168.68.1'})
    assert not a.external_target({'target': '127.0.0.1'})


def test_ambiguity_and_missing_saved_source_fail_closed():
    states = [sensor('binary_sensor.router_wan', 'on'), sensor('binary_sensor.other_wan', 'off')]
    _, metrics = a.classify(states)
    sources, notes = a.select_sources(states, metrics, {})
    assert 'wan' not in sources and 'Multiple' in notes['wan']
    sources, notes = a.select_sources(states, metrics, {'wan': 'binary_sensor.absent'})
    assert 'wan' not in sources and 'missing' in notes['wan']
    sources, _ = a.select_sources(states, metrics, {'wan': states[0]['entity_id']})
    assert sources['wan']['entity_id'] == states[0]['entity_id']


def test_history_uses_entity_identity_not_order_or_missing_groups(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(ha, 'history', lambda *args, **kwargs: [
        [row(now, 20, entity_id='sensor.upload'), row(now, 21)], [],
        [row(now, 999, entity_id='sensor.unrequested')]])
    mapped, error = pages._history(['sensor.download', 'sensor.upload'], now - timedelta(days=1))
    assert not error and mapped['sensor.download'] == []
    assert len(mapped['sensor.upload']) == 2 and mapped['sensor.upload'][0]['state'] == '20'


def test_history_rejects_cross_entity_group(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(ha, 'history', lambda *args, **kwargs: [[row(now, 20, entity_id='sensor.a'), row(now, 5, entity_id='sensor.b')]])
    mapped, _ = pages._history(['sensor.a', 'sensor.b'], now - timedelta(hours=1))
    assert not any(mapped.values())


def test_30_day_window_accepts_normal_call_delay_but_not_31_days(monkeypatch):
    captured = []
    monkeypatch.setattr(ha, '_request', lambda *args, **kwargs: captured.append(kwargs) or [])
    now = datetime.now(timezone.utc)
    ha.history(['sensor.fake'], now - timedelta(days=30, milliseconds=50), end=now)
    assert captured
    with pytest.raises(ha.HomeAssistantError):
        ha.history(['sensor.fake'], now - timedelta(days=31), end=now)


def test_chart_is_time_aligned_breaks_gaps_and_keeps_peaks():
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=24)
    rows = [row(start, 1), row(start + timedelta(seconds=30), 2), row(start + timedelta(hours=12), 'unknown'), row(end, 3)]
    graph = a.chart(rows, {'kind': 'value'}, start=start, end=end)
    assert len(graph['segments']) == 2
    x = [float(point.split(',')[0]) for point in graph['segments'][0].split()]
    assert x[1] - x[0] < 1  # 30 seconds must not occupy half a day
    dense = [row(start + timedelta(seconds=i), 100 if i == 153 else 1) for i in range(1000)]
    graph = a.chart(dense, {'kind': 'value'}, limit=16, start=start, end=end)
    assert graph['max'] == 100 and ',8.0' in graph['points']


def test_counter_resets_return_partial_minimum_not_fabricated_total():
    now = datetime.now(timezone.utc)
    rows = [row(now + timedelta(seconds=i), val) for i, val in enumerate((1000, 3000, 0, 2000))]
    result = a.counter_usage(rows, {'unit': 'B'})
    assert result == {'bytes': 4000, 'complete': False, 'resets': 1}
    assert a.usage(rows, {'unit': 'B'}) is None
    assert a.counter_usage([row(now, 1), row(now, 'unavailable')], {'unit': 'B'})['complete'] is False


def test_old_reports_never_present_as_live_or_clean_health(monkeypatch):
    item = sensor('binary_sensor.router_wan', 'on')
    item['last_reported'] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    monkeypatch.setattr(ha, 'configured', lambda: True)
    monkeypatch.setattr(ha, 'states', lambda: [item])
    monkeypatch.setattr(ha, 'history', lambda *args, **kwargs: [])
    body = client.get('/network').text
    assert 'Old / unverified report' in body and 'Availability unknown' in body
    assert 'No issues in the measured signals' not in body
    assert 'Page fetched' in body and 'source has its own reporting time' in body


def test_source_settings_are_same_origin_local_only_and_confirm_scope(monkeypatch):
    states = [sensor('sensor.internet_ping', 20, 'ms')]
    monkeypatch.setattr(ha, 'states', lambda: states)
    assert client.post('/network/source', data={'role': 'latency', 'entity_id': states[0]['entity_id']}).status_code == 403
    response = client.post('/network/source', data={'role': 'latency', 'entity_id': states[0]['entity_id']}, headers=ORIGIN)
    assert response.status_code == 400
    response = client.post('/network/source', data={'role': 'latency', 'entity_id': states[0]['entity_id'], 'external': 'yes'}, headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303
    assert store.get('sources')['latency'] == states[0]['entity_id']
    assert client.post('/network/source', data={'role': 'reboot', 'entity_id': 'sensor.fake'}, headers=ORIGIN).status_code == 400


def test_expected_device_preference_and_mesh_grouping(monkeypatch):
    states = [sensor('device_tracker.node_a', 'home', device_type='deco'),
              sensor('device_tracker.phone', 'not_home', device_type='client', deco_device='node a'),
              sensor('device_tracker.laptop', 'home', device_type='client', deco_device='node a', down_kilobytes_per_s=100),
              sensor('device_tracker.unassigned', 'home', device_type='client')]
    monkeypatch.setattr(ha, 'states', lambda: states)
    monkeypatch.setattr(ha, 'history', lambda *args, **kwargs: [])
    devices, _ = pages._device_rows(states)
    assert not next(d for d in devices if d['entity_id'].endswith('phone'))['attention']
    result = client.post('/network/device/device_tracker.phone/expected', data={'enabled': '1'}, headers=ORIGIN, follow_redirects=False)
    assert result.status_code == 303
    devices, _ = pages._device_rows(states)
    assert next(d for d in devices if d['entity_id'].endswith('phone'))['attention']
    assert 'phone' in client.get('/network/devices?status=attention').text
    assert 'laptop' not in client.get('/network/devices?status=attention').text
    mesh = client.get('/network/mesh')
    assert mesh.status_code == 200 and 'Clients without a matched node' in mesh.text
    assert '1 freshly reported online / 2 associated clients' in mesh.text


@pytest.mark.parametrize('range_key', ['6h', '24h', '7d', '30d', 'invalid'])
def test_whole_page_with_empty_history_and_ranges(monkeypatch, range_key):
    monkeypatch.setattr(ha, 'configured', lambda: True)
    monkeypatch.setattr(ha, 'states', lambda: [sensor('sensor.router_download_speed', 1, 'Mbit/s')])
    monkeypatch.setattr(ha, 'history', lambda *args, **kwargs: [])
    response = client.get('/network?range=' + range_key)
    assert response.status_code == 200
    assert 'Availability unknown' in response.text and 'Sources, freshness' in response.text
    assert 'network.js' in response.text


def test_ha_failure_is_monitoring_unknown_not_internet_down(monkeypatch):
    monkeypatch.setattr(ha, 'configured', lambda: True)
    def unavailable():
        raise ha.HomeAssistantError('Home Assistant unavailable')
    monkeypatch.setattr(ha, 'states', unavailable)
    body = client.get('/network').text
    assert 'Monitoring unavailable — network condition unknown' in body


def test_source_failure_does_not_overwrite_saved_choices(monkeypatch):
    store.put('sources', {'wan': 'binary_sensor.router_wan'})
    def fail():
        raise ha.HomeAssistantError('unavailable')
    monkeypatch.setattr(ha, 'states', fail)
    response = client.post('/network/source', data={'role': 'wan', 'entity_id': 'binary_sensor.other_wan'}, headers=ORIGIN)
    assert response.status_code == 502
    assert store.get('sources') == {'wan': 'binary_sensor.router_wan'}


def test_repeated_expected_disconnects_and_shared_timing_are_not_causation(monkeypatch):
    end = datetime.now(timezone.utc)
    states = [sensor('binary_sensor.router_wan', 'on'), sensor('device_tracker.node', 'home', device_type='deco')]
    monkeypatch.setattr(ha, 'configured', lambda: True)
    monkeypatch.setattr(ha, 'states', lambda: states)
    def history(ids, start, *args, **kwargs):
        result = []
        for ident in ids:
            rows = [row(end - timedelta(hours=25), 'on' if ident.startswith('binary') else 'home', entity_id=ident)]
            for hour in (12, 8, 4):
                rows += [row(end - timedelta(hours=hour), 'off' if ident.startswith('binary') else 'not_home'),
                         row(end - timedelta(hours=hour - 1), 'on' if ident.startswith('binary') else 'home')]
            result.append(rows)
        return result
    monkeypatch.setattr(ha, 'history', history)
    response = client.get('/network')
    assert response.status_code == 200
    assert 'repeated offline reports' in response.text
    assert 'overlaps selected WAN/probe offline interval' in response.text
    assert 'correlation, not a diagnosed cause' in response.text


def test_node_api_diagnostics_are_visible_but_not_external_latency(monkeypatch):
    states = [sensor('device_tracker.hall', 'home', device_type='deco'),
              sensor('sensor.hall_cpu', 24, '%'), sensor('sensor.hall_api_response_time', 20, 'ms')]
    monkeypatch.setattr(ha, 'states', lambda: states)
    monkeypatch.setattr(ha, 'history', lambda *args, **kwargs: [])
    mesh = client.get('/network/mesh')
    assert '24 %' in mesh.text and '20 ms' in mesh.text
    assert 'not internet latency' in mesh.text
    detail = client.get('/network/device/device_tracker.hall')
    assert detail.status_code == 200 and '24 %' in detail.text
    _, metrics = a.classify(states)
    assert not any(m['kind'] == 'latency' for m in metrics)


def test_source_and_expected_preferences_persist_and_are_private():
    from hub import config
    import stat
    store.put('sources', {'wan': 'none'})
    store.put('expected', ['device_tracker.synthetic'])
    assert store.get('sources') == {'wan': 'none'}
    assert store.get('expected') == ['device_tracker.synthetic']
    assert stat.S_IMODE((config.DATA_DIR / 'network.db').stat().st_mode) == 0o600


def test_current_negative_client_rates_are_not_shown():
    device = a.client_or_node(sensor('device_tracker.synthetic', 'home', device_type='client', down_kilobytes_per_s=-10))
    assert device['download_kb'] is None


def test_usage_does_not_count_bytes_before_selected_window():
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=24)
    rows = [row(start - timedelta(hours=4), 0), row(start + timedelta(hours=1), 5000), row(end, 6000)]
    text = pages._usage(rows, {'unit': 'B'}, start, end)
    assert text.startswith('1.00 KB minimum')


def test_stale_tail_makes_current_availability_unknown():
    end = datetime.now(timezone.utc)
    metric = {'entity_id': 'binary_sensor.router_wan', 'last_reported': (end - timedelta(hours=3)).isoformat()}
    rows = [row(end - timedelta(hours=25), 'on')]
    result = a.outage_summary(pages._with_stale_tail(rows, metric, end), {}, 1, end=end)
    assert not result['known'] and result['coverage'] < 100


@pytest.mark.parametrize('status, expected', [
    (401, "rejected Hub's saved token (HTTP 401)"),
    (403, 'denied this read (HTTP 403)'),
    (429, 'rate-limiting reads (HTTP 429)'),
    (503, 'reported a server error'),
])
def test_ha_failure_diagnostics_are_specific_without_response_or_token_leaks(monkeypatch, status, expected):
    import httpx
    fake_token = 'synthetic-secret-' * 5
    monkeypatch.setattr(ha, '_token', lambda: fake_token)
    calls = []
    real_client = httpx.Client
    def handler(request):
        calls.append(request.method)
        return httpx.Response(status, text='PRIVATE_RESPONSE_SENTINEL ' + fake_token)
    def fake_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(ha.httpx, 'Client', fake_client)
    with pytest.raises(ha.HomeAssistantError) as caught:
        ha._request('/api/')
    assert expected in str(caught.value)
    assert fake_token not in str(caught.value) and 'PRIVATE_RESPONSE_SENTINEL' not in str(caught.value)
    assert calls == ['GET']  # no retry after failed authorization or rate limiting
