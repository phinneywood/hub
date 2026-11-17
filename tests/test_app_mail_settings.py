"""Mail settings in the app (hub/app/mailrules.py): the older Mail pages' settings and plan, as data and taps.
Synthetic data only; the Gmail helper is never reached."""
from fastapi.testclient import TestClient

from hub import config, delayed, store
from hub.app import mailrules
from hub.engine import catalog
from hub.main import app
from hub.tools.gmail import filing, missions, triage

client = TestClient(app)
OWNER = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}


def _ready(monkeypatch, paused=False):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com'})
    monkeypatch.setattr(missions, 'pause_state', lambda: 'paused' if paused else 'ok')
    started = []
    monkeypatch.setattr(mailrules.worker, 'start', lambda: started.append(1))
    monkeypatch.setattr(mailrules.worker, 'running', lambda: False)
    return started


def test_the_screen_shows_levels_charges_senders_and_the_plan(monkeypatch):
    _ready(monkeypatch)
    triage.always('alex@example.com', 'reading')
    store.set_decision('news@example.com', 'archive', 120, True)
    seen = client.get('/app/api/mail-settings', headers=OWNER).json()
    assert {k['kind'] for k in seen['levels']} == set(filing.ACTIONS) and not seen['paused']
    assert seen['senders'] == [{'sender': 'alex@example.com', 'kind': triage.NAMES['reading']}]
    [item] = seen['plan']['planned']
    assert item['who'] == 'news@example.com' and item['does'] and seen['plan']['leaving'] == 120
    assert seen['charge'] == triage.charge_limit() and seen['rules'] == []


def test_taps_need_a_signed_in_device_and_use_the_older_pages_handlers(monkeypatch):
    started = _ready(monkeypatch)
    store.set_decision('news@example.com', 'archive', 120, True)
    [item] = [i for i in store.items() if i['sender'] == 'news@example.com']
    assert client.post('/app/api/mail-settings/apply', json={}, headers={'origin': 'http://testserver'}).status_code == 403
    assert client.post('/app/api/mail-settings/apply', json={}, headers=OWNER).json()['ok'] and started == [1]
    assert client.post('/app/api/mail-settings/charge', json={'value': 250}, headers=OWNER).json()['ok']
    assert triage.charge_limit() == 250
    assert client.post('/app/api/mail-settings/charge', json={'value': 0}, headers=OWNER).status_code == 409
    kind = next(iter(filing.ACTIONS))
    other = next(v for v in catalog.action(filing.ACTIONS[kind]).levels() if v != filing.trusted(kind))
    assert client.post('/app/api/mail-settings/level', json={'key': kind, 'value': other}, headers=OWNER).json()['ok']
    assert filing.trusted(kind) == other
    assert client.post('/app/api/mail-settings/level', json={'key': 'nonsense', 'value': other}, headers=OWNER).status_code == 409
    triage.always('alex@example.com', 'reading')
    assert client.post('/app/api/mail-settings/sender', json={'key': 'alex@example.com'}, headers=OWNER).json()['ok']
    assert triage.sender_kinds() == {}
    assert client.post('/app/api/mail-settings/remove', json={'key': item['id']}, headers=OWNER).json()['ok']
    assert not [i for i in store.items() if i['sender'] == 'news@example.com' and i['status'] == 'planned']
    assert client.post('/app/api/mail-settings/nonsense', json={}, headers=OWNER).status_code == 409


def test_a_paused_helper_applies_nothing_and_says_so(monkeypatch):
    started = _ready(monkeypatch, paused=True)
    store.set_decision('news@example.com', 'archive', 120, True)
    answer = client.post('/app/api/mail-settings/apply', json={}, headers=OWNER)
    assert answer.status_code == 409 and 'paused' in answer.json()['error'] and started == []
    assert client.get('/app/api/mail-settings', headers=OWNER).json()['paused']
    assert client.post('/app/api/mail-settings/rules-check', json={}, headers=OWNER).status_code == 409
    assert delayed.rules() == []
