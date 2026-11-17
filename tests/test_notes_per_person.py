"""Each person's standing notes are their own (hub/engine/state.py): they steer that person's Ask and nobody else's,
only they can see, save or remove them, and the overnight review, which reads the owner's accounts, follows the
owner's. The people and logins here are made up."""
import json
import sqlite3

from fastapi.testclient import TestClient

from hub import config, people, store
from hub.engine import notes, overnight, state
from hub.main import app

ALEX, ALEX_LAPTOP, SAM = 'alex@example.com', 'alex@example.net', 'sam@example.com'
SAID = 'remember that the porch light goes off at midnight'
NOTE = 'The porch light goes off at midnight'


def _household(monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ALEX, ALEX_LAPTOP, SAM})
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'people': [
        {'name': 'Alex', 'logins': [ALEX, ALEX_LAPTOP], 'accounts': ['default']},
        {'name': 'Sam', 'logins': [SAM], 'accounts': []}]}))


def test_same_person_is_one_login_or_two_logins_of_one_member(monkeypatch):
    _household(monkeypatch)
    assert people.same(ALEX, ALEX) and people.same(ALEX, ' Alex@Example.NET ') and people.same(ALEX_LAPTOP, ALEX)
    assert not people.same(ALEX, SAM) and not people.same(None, None) and not people.same(ALEX, None)
    assert not people.same('stranger@example.com', ALEX)


def test_a_note_steers_only_the_person_who_saved_it(monkeypatch):
    _household(monkeypatch)
    alex, sam = state.add_instruction('Start music softly', ALEX), state.add_instruction('Say the weather in Celsius', SAM)
    assert alex and sam
    assert [i['text'] for i in state.instructions(ALEX)] == ['Start music softly']
    assert [i['text'] for i in state.instructions(ALEX_LAPTOP)] == ['Start music softly']     # the same person
    assert [i['text'] for i in state.instructions(SAM)] == ['Say the weather in Celsius']
    assert state.instructions(None) == [] and state.instructions('stranger@example.com') == []
    assert 'Start music softly' in notes.for_ask(ALEX) and 'Celsius' not in notes.for_ask(ALEX)
    assert 'Celsius' in notes.for_ask(SAM) and 'music' not in notes.for_ask(SAM)
    # Nobody removes another person's note; their own goes.
    assert not state.remove_instruction(alex, SAM) and not state.remove_instruction(alex, None)
    assert state.remove_instruction(alex, ALEX_LAPTOP) and state.instructions(ALEX) == []
    assert [i['text'] for i in state.instructions(SAM)] == ['Say the weather in Celsius']


def test_each_person_has_their_own_room_and_nobody_without_a_login_saves_one(monkeypatch):
    _household(monkeypatch)
    for n in range(state.MAX_INSTRUCTIONS):
        assert state.add_instruction(f'Synthetic {n}', ALEX)
    assert not state.add_instruction('One more', ALEX)
    assert state.add_instruction('Mine still fits', SAM)
    assert not state.add_instruction('From this Mac', None)


def test_the_overnight_review_follows_the_owners_notes_only(monkeypatch):
    _household(monkeypatch)
    state.add_instruction('Tell me about school deadlines', ALEX)
    state.add_instruction('Never mention the dentist', SAM)
    assert [i['text'] for i in state.owners_instructions()] == ['Tell me about school deadlines']
    user = overnight.build_request({'sources': []}, [], '2026-01-05')['messages'][1]['content']
    assert 'school deadlines' in user and 'dentist' not in user


def test_notes_from_before_are_the_owners(monkeypatch):
    _household(monkeypatch)
    store.db().close()
    conn = sqlite3.connect(config.DATA_DIR / 'hub.db')
    with conn:
        conn.execute("insert into engine_instructions(text, created_at) values ('Kept from before', 1)")
    conn.close()
    assert [i['text'] for i in state.instructions(ALEX)] == ['Kept from before']
    assert state.instructions(SAM) == [] and [i['text'] for i in state.owners_instructions()] == ['Kept from before']


def test_a_proposal_waits_for_the_tap_of_the_person_who_asked(monkeypatch):
    _household(monkeypatch)
    client = TestClient(app)
    proposed = notes.propose(NOTE, SAID, login=SAM)
    assert notes.waiting(ALEX) == [] and [p['text'] for p in notes.waiting(SAM)] == [NOTE]
    assert NOTE not in client.get('/engine/notes', headers={'tailscale-user-login': ALEX}).text
    tap = {'origin': 'http://testserver', 'tailscale-user-login': ALEX}
    assert client.post(f'/engine/notes/{proposed["id"]}/save', headers=tap).status_code != 303
    assert client.post('/app/api/engine/note-save', json={'key': proposed['id']}, headers=tap).status_code == 409
    client.post(f'/engine/notes/{proposed["id"]}/skip', headers=tap)
    assert state.instructions(ALEX) == [] and [p['text'] for p in notes.waiting(SAM)] == [NOTE]
    mine = {'origin': 'http://testserver', 'tailscale-user-login': SAM}
    assert client.post('/app/api/engine/note-save', json={'key': proposed['id']}, headers=mine).json()['ok']
    assert [i['text'] for i in state.instructions(SAM)] == [NOTE] and state.instructions(ALEX) == []


def test_the_app_shows_and_forgets_only_the_askers_own_notes(monkeypatch):
    _household(monkeypatch)
    client = TestClient(app)
    alex = state.add_instruction('Start music softly', ALEX)
    state.add_instruction('Say the weather in Celsius', SAM)
    as_sam = {'origin': 'http://testserver', 'tailscale-user-login': SAM}
    assert [i['text'] for i in client.get('/app/api/engine', headers=as_sam).json()['asked']] == ['Say the weather in Celsius']
    assert [n['note'] for n in client.get('/app/api/ask', headers=as_sam).json()['remembers']] == ['Say the weather in Celsius']
    assert client.post(f'/app/api/memory/{alex}/forget', headers=as_sam).status_code == 404
    assert client.post('/app/api/engine/forget', json={'key': alex}, headers=as_sam).status_code == 409
    assert [i['text'] for i in state.instructions(ALEX)] == ['Start music softly']
