"""The Engine screen in the app (hub/app/engine.py): the older Engine page's reading and taps. Synthetic data only."""
from fastapi.testclient import TestClient

from hub import config
from hub.engine import catalog, notes, state
from hub.main import app

client = TestClient(app)
OWNER = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
SAID = 'remember that the porch light goes off at midnight'
NOTE = 'The porch light goes off at midnight'


def test_the_screen_reads_what_the_engine_keeps_and_calls_no_model(monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com'})
    state.add_instruction('Always tell me about school mail with a deadline.', 'me@example.com')
    state.add_request('A button that warms the house before we wake')
    notes.propose(NOTE, SAID, login='me@example.com')
    seen = client.get('/app/api/engine', headers=OWNER).json()
    assert [i['text'] for i in seen['asked']] == ['Always tell me about school mail with a deadline.']
    assert [r['text'] for r in seen['requests']] == ['A button that warms the house before we wake']
    assert [n['text'] for n in seen['notes']] == [NOTE] and not seen['full']
    assert {a['slug'] for a in seen['actions']} == {a.slug for a in catalog.ACTIONS}
    assert seen['spend']['month'] == 0 and seen['runs'] == [] and seen['sources']


def test_every_tap_needs_a_signed_in_device_and_does_what_the_older_page_did(monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com'})
    state.add_request('Something to build')
    proposed = notes.propose(NOTE, SAID, login='me@example.com')
    [request] = state.requests()
    assert client.post('/app/api/engine/note-save', json={'key': proposed['id']}, headers={'origin': 'http://testserver'}).status_code == 403
    assert client.post('/app/api/engine/tell', json={'text': 'x'}, headers={'origin': 'http://testserver'}).status_code == 403
    assert state.instructions('me@example.com') == []
    assert client.post('/app/api/engine/note-save', json={'key': proposed['id']}, headers=OWNER).json()['ok']
    [kept] = state.instructions('me@example.com')
    assert kept['text'] == NOTE
    assert client.post('/app/api/engine/note-save', json={'key': proposed['id']}, headers=OWNER).status_code == 409
    assert client.post('/app/api/engine/forget', json={'key': kept['id']}, headers=OWNER).json()['ok'] and state.instructions('me@example.com') == []
    assert client.post('/app/api/engine/request-done', json={'key': request['id']}, headers=OWNER).json()['ok'] and state.requests() == []
    action = next(a for a in catalog.ACTIONS if len(a.levels()) > 1)
    other = next(level for level in action.levels() if level != state.trust(action))
    assert client.post('/app/api/engine/trust', json={'key': action.slug, 'value': other}, headers=OWNER).json()['ok']
    assert state.trust(action) == other
    assert client.post('/app/api/engine/trust', json={'key': action.slug, 'value': 'everything'}, headers=OWNER).status_code == 409
    assert client.post('/app/api/engine/nonsense', json={'key': '1'}, headers=OWNER).status_code == 409
    assert client.post('/app/api/engine/forget', json={'key': 'not a number'}, headers=OWNER).status_code == 400
