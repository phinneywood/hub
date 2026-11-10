"""Answer-first network verdict and steady-sensor freshness. Synthetic states only."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from hub.main import app
from hub.tools.network import analytics as a, ha, pages

client = TestClient(app)


def metric(role, state, age_seconds, unit=''):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {'entity_id': f'sensor.{role}', 'name': role, 'kind': role, 'state': state, 'unit': unit,
            'last_reported': stamp, 'last_updated': stamp, 'basis': 'synthetic'}


def test_steady_sensors_are_current_when_companion_reading_is_fresh():
    sources = {'reachability': metric('reachability', 'on', 300_000), 'latency': metric('latency', '9', 20, 'ms'),
               'loss': metric('loss', '0', 40_000, '%'), 'wan': metric('wan', 'on', 300_000),
               'download_rate': metric('download_rate', '2000', 10)}
    assert pages.current('reachability', sources) and pages.current('loss', sources) and pages.current('wan', sources)
    stale = {**sources, 'latency': metric('latency', '9', 300_000, 'ms'), 'download_rate': metric('download_rate', '1', 300_000)}
    assert not pages.current('reachability', stale) and not pages.current('wan', stale) and not pages.current('loss', stale)
    assert pages._source_status(sources['wan'], True) == 'Reported up'
    assert pages._source_status(stale['wan'], False) == 'Old / unverified report'


def test_verdict_leads_with_the_answer():
    up = pages._verdict([{'level': 'unknown'}], 'Reported up', 'Reported up', {'latency': 8.4, 'loss': 0}, True)
    assert up == {'headline': 'Internet is up', 'tone': 'ok', 'facts': '8 ms ping · router link up'}
    assert pages._verdict([{'level': 'warn'}], 'Reported up', 'Reported up', {}, True)['tone'] == 'warn'
    down = pages._verdict([{'level': 'bad'}], 'Reported up', 'Reported down', {}, True)
    assert (down['headline'], down['tone']) == ('Internet looks down', 'bad')
    unknown = pages._verdict([], 'Old / unverified report', 'Old / unverified report', {}, True)
    assert unknown['tone'] == 'warn' and 'old or missing' in unknown['facts']


def test_page_reports_up_for_steady_probe_with_fresh_ping(monkeypatch):
    sources = {'reachability': metric('reachability', 'on', 300_000), 'latency': metric('latency', '9', 20, 'ms')}
    monkeypatch.setattr(ha, 'configured', lambda: True)
    monkeypatch.setattr(ha, 'states', lambda: [])
    monkeypatch.setattr(ha, 'history', lambda *args, **kwargs: [])
    monkeypatch.setattr(a, 'select_sources', lambda states, metrics, choices: (sources, {}))
    body = client.get('/network').text
    assert 'Internet is up' in body and 'Old / unverified report' not in body.split('id="sources"')[0]
    assert 'Not monitored yet' in body  # per-device visibility gap is listed, not alarming
    assert 'Limited visibility' not in body
