import pytest
from fastapi.testclient import TestClient

from hub import bridge, delayed, luna
from hub.main import app
from tests.test_app import ORIGIN, fake_get

client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_reads(monkeypatch):
    monkeypatch.setattr(bridge, 'get', fake_get)


def enable():
    delayed.enable('shop@example.com', now=1000)
    return delayed.rules()[0]


def preview(count, sender='shop@example.com', more=False):
    return {'preview_id': 'p', 'selected_messages': count, 'more_matches_exist': more,
            'sample': [{'id': str(i), 'from': sender} for i in range(count)]}


def test_requires_confirmation_and_does_not_archive_on_activation(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, 'post', lambda *a: calls.append(a))
    response = client.get('/gmail/delayed/review?sender=shop@example.com')
    assert response.status_code == 200
    assert 'existing' in response.text and '24 hours' in response.text and 'up to 50' in response.text
    assert not delayed.rules() and not calls
    assert client.post('/gmail/delayed/enable', data={'sender':'shop@example.com'}, headers=ORIGIN).status_code == 200
    assert delayed.rules()[0]['enabled'] == 1 and not calls


def test_sender_flow_returns_to_next_sender_after_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, 'post', lambda *a: calls.append(a))
    card = client.get('/gmail/quiet').text
    assert 'news@loud.example' in card and 'source=quiet' in card
    review = client.get('/gmail/delayed/review?sender=news%40loud.example&source=quiet').text
    assert 'Turn on and review next sender' in review
    assert 'href="/gmail/quiet">Cancel' in review
    assert not delayed.rules()
    response = client.post('/gmail/delayed/enable', data={'sender': 'news@loud.example', 'source': 'quiet'},
                           headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/gmail/quiet?delayed_enabled=1'
    next_card = client.get(response.headers['location']).text
    assert 'bob@example.com' in next_card and '1 reviewed · 1 left' in next_card
    assert '30-day rule turned on' in next_card
    assert 'Keep reviewing · 1 left' in client.get('/gmail/tools').text
    assert not calls


def test_last_sender_shows_completion_and_stopping_rule_makes_sender_available():
    from hub import store
    store.set_decision('bob@example.com', 'keep')
    response = client.post('/gmail/delayed/enable', data={'sender': 'NEWS@loud.example', 'source': 'quiet'},
                           headers=ORIGIN, follow_redirects=False)
    done = client.get(response.headers['location']).text
    assert 'No loud senders left' in done and '30-day rule turned on' in done
    delayed.disable(delayed.rules()[0]['id'])
    assert 'news@loud.example' in client.get('/gmail/quiet').text


def test_direct_rule_entry_returns_to_management_and_disallows_redirects():
    review = client.get('/gmail/delayed/review?sender=shop@example.com').text
    assert 'href="/gmail/delayed">Cancel' in review
    response = client.post('/gmail/delayed/enable', data={'sender': 'shop@example.com', 'source': 'https://evil.invalid'},
                           headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/gmail/delayed'


def test_rule_runs_only_when_due_with_bounded_exact_action(monkeypatch):
    calls = []
    def post(path, body):
        calls.append((path, body))
        if path.endswith('preview'):
            return preview(2)
        return {'complete': True, 'processed': 2, 'change_ids': ['c'], 'needs_review': False}
    monkeypatch.setattr(bridge, 'post', post)
    monkeypatch.setattr(bridge, 'get', lambda *a, **k: {'organization_paused': False})
    rule = enable()
    delayed.run_due(now=rule['next_due'] - 1)
    assert calls == []
    delayed.run_due(now=rule['next_due'])
    assert calls == [('/gmail/cleanup/preview', {'query': 'from:shop@example.com in:inbox older_than:30d -is:starred',
                                                   'remove_label_ids': ['INBOX'], 'max_messages': 10}),
                     ('/gmail/cleanup/apply', {'preview_id': 'p'})]
    assert delayed.runs()[0]['processed'] == 2 and delayed.runs()[0]['change_id'] == 'c'
    assert delayed.rules()[0]['next_due'] == rule['next_due'] + delayed.INTERVAL


def test_pause_or_stopped_rule_does_not_write(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, 'post', lambda *a: calls.append(a))
    monkeypatch.setattr(bridge, 'get', lambda *a, **kw: {'organization_paused': True})
    r = enable()
    delayed.run_due(now=r['next_due'])
    assert delayed.runs()[0]['status'] == 'paused' and not calls
    delayed.disable(r['id'])
    delayed.run_due(now=r['next_due'] + 10000)
    assert not calls and delayed.rules()[0]['enabled'] == 0


def test_uncertain_write_suspends_and_never_retries(monkeypatch):
    calls = []
    def post(path, body):
        calls.append(path)
        if path.endswith('preview'):
            return preview(3)
        raise bridge.BridgeError('unreachable')
    monkeypatch.setattr(bridge, 'post', post)
    monkeypatch.setattr(bridge, 'get', lambda *a, **kw: {'organization_paused': False})
    r = enable()
    delayed.run_due(now=r['next_due'])
    assert delayed.runs()[0]['status'] == 'needs_review' and delayed.rules()[0]['enabled'] == 0
    delayed.run_due(now=r['next_due'] + delayed.INTERVAL)
    assert calls == ['/gmail/cleanup/preview', '/gmail/cleanup/apply']
    with pytest.raises(ValueError):
        delayed.enable(r['sender'])


def test_restart_marks_inflight_as_needing_review():
    r = enable()
    with delayed.db() as conn:
        conn.execute("insert into delayed_runs(rule_id,started,status) values(?,?,'applying')", (r['id'], 1))
    delayed.recover_interrupted()
    assert delayed.runs()[0]['status'] == 'needs_review' and not delayed.rules()[0]['enabled']


def test_undo_stops_rule_and_handles_conflict(monkeypatch):
    monkeypatch.setattr(bridge, 'get', lambda *a, **kw: {'organization_paused': False})
    def post(path, body):
        if path.endswith('preview'): return preview(1)
        if path.endswith('apply'): return {'complete': True, 'processed': 1, 'change_ids': ['c']}
        return {'undone': False}
    monkeypatch.setattr(bridge, 'post', post)
    r = enable()
    delayed.run_due(now=r['next_due'])
    assert not delayed.undo(delayed.runs()[0]['id'])
    assert delayed.runs()[0]['status'] == 'needs_review' and not delayed.rules()[0]['enabled']


def test_sender_input_and_cross_origin_are_rejected():
    with pytest.raises(ValueError): delayed.enable('x@example.com OR from:y@example.com')
    response = client.post('/gmail/delayed/enable', data={'sender':'shop@example.com'}, headers={'Origin':'https://evil.invalid'})
    assert response.status_code == 403 and delayed.rules() == []


def test_custom_prompt_does_not_misinterpret_delayed_archive(monkeypatch):
    monkeypatch.setattr(luna, 'configured', lambda: True)
    monkeypatch.setattr(luna, 'propose', lambda *a, **kw: pytest.fail('Must not call Luna'))
    response = client.post('/gmail/quiet/custom/propose', headers=ORIGIN,
                           data={'sender':'shop@example.com', 'instruction':'Keep it in the inbox for 30 days then archive'})
    assert response.status_code == 200 and 'A Gmail filter cannot wait' in response.text


def test_mismatched_or_missing_sender_suspends_without_applying(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, 'get', lambda *a, **kw: {'organization_paused': False})
    def post(path, body):
        calls.append(path)
        return preview(1, sender='other@example.com')
    monkeypatch.setattr(bridge, 'post', post)
    r = enable()
    delayed.run_due(now=r['next_due'])
    assert calls == ['/gmail/cleanup/preview']
    assert delayed.runs()[0]['status'] == 'needs_review' and not delayed.rules()[0]['enabled']


def test_caps_each_daily_check_at_five_verified_batches(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, 'get', lambda *a, **kw: {'organization_paused': False})
    def post(path, body):
        calls.append(path)
        if path.endswith('preview'): return preview(10, more=True)
        return {'complete': True, 'processed': 10, 'change_ids': [str(len(calls))]}
    monkeypatch.setattr(bridge, 'post', post)
    r = enable()
    delayed.run_due(now=r['next_due'])
    assert len(calls) == 10 and len(delayed.runs()) == 5
    assert sum(x['processed'] for x in delayed.runs()) == 50


def test_successful_undo_stops_the_rule(monkeypatch):
    monkeypatch.setattr(bridge, 'get', lambda *a, **kw: {'organization_paused': False})
    def post(path, body):
        if path.endswith('preview'): return preview(1)
        if path.endswith('apply'): return {'complete': True, 'processed': 1, 'change_ids': ['c']}
        return {'undone': True}
    monkeypatch.setattr(bridge, 'post', post)
    r = enable()
    delayed.run_due(now=r['next_due'])
    assert delayed.undo(delayed.runs()[0]['id'])
    assert delayed.runs()[0]['status'] == 'undone' and not delayed.rules()[0]['enabled']


def test_outage_before_archiving_keeps_rule_on_and_retries(monkeypatch):
    calls = []
    def post(path, body):
        calls.append(path)
        raise bridge.BridgeError('Gmail bridge is unreachable')
    monkeypatch.setattr(bridge, 'post', post)
    monkeypatch.setattr(bridge, 'get', lambda *a, **kw: {'organization_paused': False})
    r = enable()
    delayed.run_due(now=r['next_due'])
    rule = delayed.rules()[0]
    assert delayed.runs()[0]['status'] == 'failed' and rule['enabled'] == 1 and rule['error'] is None
    assert rule['next_due'] == r['next_due'] + delayed.RETRY_SECONDS
    assert calls == ['/gmail/cleanup/preview']


def test_profile_outage_also_retries(monkeypatch):
    def get(*a, **kw):
        raise bridge.BridgeError('Gmail bridge is unreachable')
    monkeypatch.setattr(bridge, 'get', get)
    monkeypatch.setattr(bridge, 'post', lambda *a: pytest.fail('must not write'))
    r = enable()
    delayed.run_due(now=r['next_due'])
    assert delayed.rules()[0]['enabled'] == 1 and delayed.runs()[0]['status'] == 'failed'


def test_restart_before_archive_request_is_not_uncertain():
    r = enable()
    with delayed.db() as conn:
        conn.execute("insert into delayed_runs(rule_id,started,status) values(?,?,'applying')", (r['id'], 1))
        conn.execute("insert into delayed_runs(rule_id,started,status) values(?,?,'running')", (r['id'], 2))
    delayed.recover_interrupted()
    statuses = sorted(x['status'] for x in delayed.runs())
    assert statuses == ['failed', 'needs_review']


def _stop(rule, preview_id=None, change_id=None, error='Check the bridge journal before continuing.'):
    with delayed.db() as conn:
        conn.execute("insert into delayed_runs(rule_id,started,status,preview_id,change_id,error) values(?,?,?,?,?,?)",
                     (rule['id'], 1, 'needs_review', preview_id, change_id, error))
        conn.execute("update delayed_rules set enabled=0, error='A check needs review; no automatic retry.' where id=?", (rule['id'],))


def test_resume_safe_stop_without_confirmation():
    r = enable()
    _stop(r)  # like the live Nov 4 stops: no preview, nothing sent
    assert not delayed.needs_check(r['id'])
    assert delayed.health() == {'on': 0, 'stopped': 1, 'safe_to_resume': 1}
    assert delayed.resume(r['id'], now=5000)
    rule = delayed.rules()[0]
    assert rule['enabled'] == 1 and rule['error'] is None and rule['next_due'] == 5000


def test_uncertain_stop_requires_owner_check():
    r = enable()
    _stop(r, preview_id='p')
    assert delayed.needs_check(r['id'])
    assert not delayed.resume(r['id'])
    assert delayed.rules()[0]['error']
    assert delayed.resume(r['id'], checked=True)


def test_resume_routes_and_page(monkeypatch):
    r = enable()
    _stop(r)
    page = client.get('/gmail/delayed')
    assert 'Safe to resume' in page.text
    response = client.post('/gmail/delayed/resume', data={'rule_id': r['id']}, headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and 'resumed=1' in response.headers['location']
    assert delayed.rules()[0]['enabled'] == 1


def test_run_now_checks_enabled_rules_regardless_of_due(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, 'get', lambda *a, **kw: {'organization_paused': False})
    def post(path, body):
        calls.append(path)
        return preview(0)
    monkeypatch.setattr(bridge, 'post', post)
    enable()
    assert delayed.run_now(now=1) == 1
    assert calls == ['/gmail/cleanup/preview']
