"""Cards in the chat and “Improve this”. Made-up household and messages only."""
import json
import time

from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from hub import config, today
from hub.agent import store
from hub.engine import catalog, improve, notes, recipes, waiting
from hub.main import app
from tests import golden_world

ME = 'me@example.com'
SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': ME}
CARD = {**SIGNED_IN, 'accept': 'application/json'}
SAID = 'remember the kitchen speaker plays with the living room speaker'
THREAD = 'thread-cards-01'


# ---- controls put a card in their answer; the same routes answer the card in JSON ----

def test_a_note_card_is_saved_from_the_chat_through_the_notes_route():
    found = catalog.remember(ME, 'the kitchen speaker plays with the living room speaker', said=SAID)
    card = found['card']
    assert card['kind'] == 'note' and 'kitchen speaker' in card['text']
    client = TestClient(app)
    assert client.get(f'/ask/card/note/{card["id"]}', headers=SIGNED_IN).json() == {'status': 'waiting'}
    signed_out = client.post(f'/engine/notes/{card["id"]}/save', headers={'origin': 'http://testserver', 'accept': 'application/json'})
    assert signed_out.status_code == 403 and 'phone or laptop' in signed_out.json()['error']
    saved = client.post(f'/engine/notes/{card["id"]}/save', headers=CARD)
    assert saved.json()['ok'] and 'Saved' in saved.json()['said']
    assert client.get(f'/ask/card/note/{card["id"]}', headers=SIGNED_IN).json() == {'status': 'saved'}
    again = client.post(f'/engine/notes/{card["id"]}/save', headers=CARD)
    assert again.status_code == 409 and 'isn’t waiting' in again.json()['error']
    form = client.post(f'/engine/notes/{card["id"]}/skip', headers=SIGNED_IN, follow_redirects=False)
    assert form.status_code == 303                                           # the Notes page still gets its redirect


def test_a_recipe_card_and_its_status(monkeypatch):
    golden_world.install(monkeypatch)
    steps = [{'tool': 'home_control', 'args': {'device': 'Reading Lamp', 'action': 'off'}}]
    found = catalog.propose_recipe(ME, 'lamp off', steps, said='save this as lamp off: turn off the reading lamp')
    card = found['card']
    assert card == {'kind': 'recipe', 'id': card['id'], 'text': 'lamp off', 'lines': ['Reading Lamp off']}
    client = TestClient(app)
    skipped = client.post(f'/engine/recipes/{card["id"]}/skip', headers=CARD).json()
    assert skipped == {'ok': True, 'said': 'Not saved.'} and waiting.status('recipe', card['id']) == 'skipped'
    assert recipes.saved() == []


def test_playlist_cards_make_and_delete_through_the_playlists_routes(monkeypatch):
    world = golden_world.install(monkeypatch)
    made = catalog.edit_playlist(ME, 'create', 'Sunday Morning', said='make a playlist called Sunday Morning')
    assert made['card']['kind'] == 'playlist' and made['card']['text'] == 'Sunday Morning'
    gone = catalog.edit_playlist(ME, 'delete', 'Calm Dinner', said='delete the calm dinner playlist')
    assert gone['card'] == {'kind': 'delete', 'id': '7', 'text': 'Calm Dinner', 'lines': []} and world.sent == []
    client = TestClient(app)
    assert client.get('/ask/card/delete/7', headers=SIGNED_IN).json() == {'status': 'waiting'}
    assert client.get('/ask/card/delete/404', headers=SIGNED_IN).json() == {'status': 'deleted'}
    reply = client.post(f'/house/playlists/make/{made["card"]["id"]}', headers=CARD)
    assert reply.json()['ok'] and [m[1] for m in world.sent] == ['music/playlists/create_playlist']
    assert waiting.status('playlist', made['card']['id']) == 'made'
    assert client.post('/house/playlists/make/nothing', headers=CARD).status_code == 409


def test_an_old_proposal_reads_as_expired_and_an_unknown_one_as_gone():
    proposed = notes.propose('the porch light stays on', 'remember the porch light stays on', login=ME)
    with notes._db() as conn:
        conn.execute('update ask_note_proposals set ts = ? where id = ?', (time.time() - 2 * 86400, proposed['id']))
    assert waiting.status('note', proposed['id']) == 'expired'
    assert waiting.status('note', 'nope') == 'gone' and waiting.status('anything', 'x') == 'gone'


# ---- Improve this ----

def _thread():
    """Two made-up turns: a read, then a refused change with an answer saying it couldn't."""
    return [
        ModelRequest(parts=[UserPromptPart('what needs me today? Alex Rivera wrote about Saturday')]),
        ModelResponse(parts=[ToolCallPart('today_list', {}, tool_call_id='a')]),
        ModelRequest(parts=[ToolReturnPart('today_list', {'items': [{'what': 'Reply to Alex Rivera about Saturday'}]}, tool_call_id='a')]),
        ModelResponse(parts=[TextPart('One thing: reply to Alex Rivera about Saturday.')]),
        ModelRequest(parts=[UserPromptPart('lock the front door and turn off the porch light')]),
        ModelResponse(parts=[ToolCallPart('home_control', {'device': 'Porch Light', 'action': 'off'}, tool_call_id='b')]),
        ModelRequest(parts=[ToolReturnPart('home_control', {'refused': 'Which one? Porch Lamp, Hall Light'}, tool_call_id='b')]),
        ModelResponse(parts=[TextPart('I can’t lock doors here yet, and I couldn’t find the porch light.')]),
    ]


def test_improve_this_files_a_brief_without_the_message_or_the_answer():
    store.save(THREAD, _thread())
    client = TestClient(app)
    assert client.post(f'/ask/threads/{THREAD}/improve', json={'turn': 1}, headers={'origin': 'http://testserver'}).status_code == 403
    assert client.post(f'/ask/threads/{THREAD}/improve', json={'turn': 2}, headers=SIGNED_IN).status_code == 404   # no such turn
    assert client.post(f'/ask/threads/{THREAD}/improve', json={'turn': 'x'}, headers=SIGNED_IN).status_code == 400
    reply = client.post(f'/ask/threads/{THREAD}/improve', json={'turn': 1}, headers=SIGNED_IN)
    assert reply.status_code == 200 and 'Filed' in reply.json()['said']
    [entry] = improve.requests()
    brief = entry['brief']
    assert 'home_control (refused: “Which one? Porch Lamp, Hall Light”)' in brief
    assert 'couldn’t do something' in brief and 'today_list' not in brief            # only the asked-about turn
    for private in ('lock the front door', 'Alex', 'I can’t lock', 'porch light'):
        assert private not in brief
    raw = (config.DATA_DIR / improve.FILE)
    assert oct(raw.stat().st_mode)[-3:] == '600' and json.loads(raw.read_text())[0]['source'] == 'ask'
    client.post(f'/ask/threads/{THREAD}/improve', json={'turn': 1}, headers=SIGNED_IN)
    assert len(improve.requests()) == 1                                             # the same tap twice is kept once


def test_a_read_only_turn_says_what_was_read_and_nothing_of_it():
    store.save(THREAD, _thread())
    TestClient(app).post(f'/ask/threads/{THREAD}/improve', json={'turn': 0}, headers=SIGNED_IN)
    brief = improve.requests()[0]['brief']
    assert 'today_list (read)' in brief and 'Alex' not in brief and 'Saturday' not in brief


def test_filed_requests_show_on_today_and_the_engine_page_and_can_be_removed(monkeypatch):
    entry = improve.file('From Ask: home_control was refused.', source='ask', login=ME)
    items = catalog.improvement_items()
    assert [i['key'] for i in items] == [f'improve-{entry["id"]}'] and items[0]['tone'] == 'info'
    client = TestClient(app)
    assert 'home_control was refused' in client.get('/engine', headers=SIGNED_IN).text
    client.post('/engine/improvements/remove', data={'request_id': entry['id']}, headers=SIGNED_IN)
    assert improve.requests() == [] and catalog.improvement_items() == []
    old = improve.file('Older one.', source='ask')
    rows = json.loads((config.DATA_DIR / improve.FILE).read_text())
    rows[0]['at'] -= 2 * 86400
    (config.DATA_DIR / improve.FILE).write_text(json.dumps(rows))
    assert catalog.improvement_items() == [] and improve.requests()[0]['id'] == old['id']


def test_today_rec_shape_is_what_the_items_use():
    assert set(today.rec('k', 'info', 't', 'w', href='/x', cta='Open')) >= {'key', 'tone'}
