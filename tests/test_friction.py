"""Friction logging and the morning fix cards. Made-up messages and devices only."""
import time

from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from hub import today
from hub.engine import catalog, friction, improve
from hub.main import app
from tests import golden_world
from tests.test_ask import ask, ready  # noqa: F401 - the fixture

ME = 'me@example.com'
SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': ME}


def _turn(tool, content, reply, args=None):
    return [ModelResponse(parts=[ToolCallPart(tool, args or {}, tool_call_id='c')]),
            ModelRequest(parts=[ToolReturnPart(tool, content, tool_call_id='c')]),
            ModelResponse(parts=[TextPart(reply)])]


def _kinds():
    return [(r['kind'], r['key']) for r in friction.lines()]


# ---- noticing it ----

def test_a_refused_control_is_kept_in_hubs_own_words():
    friction.from_turn('turn off the porch light', _turn('home_control', {'refused': 'Which one? Porch Lamp, Hall Light'},
                                                          'I couldn’t tell which light you meant.'), [])
    [row] = friction.lines()
    assert row['kind'] == 'cant' and row['key'] == 'cant:home_control:refused'
    assert row['line'] == 'home_control refused: Which one? Porch Lamp, Hall Light'
    assert 'porch light' not in row['line']                       # never the owner's message


def test_an_answer_that_says_it_cant_is_kept_without_its_words():
    friction.from_turn('book a table for Friday', _turn('calendar_week', {'next_7_days': []},
                                                        'I can’t book tables, sorry. Alex is free Friday.'), [])
    [row] = friction.lines()
    assert row['key'] == 'cant:answer' and 'calendar_week' in row['line'] and 'Alex' not in row['line']
    friction.from_turn('what is on today?', _turn('calendar_week', {}, 'Nothing on today.'), [])
    assert len(friction.lines()) == 1                             # a plain answer is no friction


def test_wishes_are_kept_scrubbed_and_corrections_only_after_a_change():
    friction.from_turn('Hub should tell me when alex@friends.example writes https://plans.example', [], [])
    [row] = friction.lines()
    assert row['kind'] == 'wish' and 'alex@friends.example' not in row['line'] and 'https://' not in row['line']
    earlier = [ModelRequest(parts=[UserPromptPart('play calm piano')])] + _turn('play_music', {'done': ['Den playing']}, 'Playing.')
    friction.from_turn('no, not that one', [], earlier)
    friction.from_turn('no, thanks', [], _turn('today_list', {'items': []}, 'Nothing today.'))   # only a read before it
    assert ('correction', 'correction:play_music') in _kinds() and len(friction.lines()) == 2


def test_noticing_never_breaks_a_chat():
    friction.from_turn(None, [object()], [object()])
    assert friction.lines() == []


def test_a_chat_records_friction_through_the_real_route(ready):  # noqa: F811
    reply, _ = ask(ready, 'Hub should remind me about bin day')
    assert reply.status_code == 200 and _kinds() == [('wish', 'wish:owner')]


def test_quick_undos_are_friction_but_slow_ones_and_golden_put_backs_are_not(monkeypatch):
    from hub.tools.house import broker, control
    golden_world.install(monkeypatch)
    monkeypatch.setattr(broker, '_exchange', lambda m, t: [] if m['op'] == 'request' else golden_world.World().exchange(m, t))
    first = control.change(['light.reading_lamp'], 'off', login=ME, via='ask')
    control.undo(first['batch'], login=ME, via='tap')
    assert [r['key'] for r in friction.lines()] == ['undo:off'] and 'Reading Lamp off' in friction.lines()[0]['line']
    second = control.change(['light.reading_lamp'], 'off', login=ME, via='ask')
    control.undo(second['batch'], login=ME, via='golden')
    third = control.change(['light.reading_lamp'], 'off', login=ME, via='ask')
    with control._db() as conn:
        conn.execute('update house_changes set ts = ? where batch = ?', (time.time() - 3600, third['batch']))
    control.undo(third['batch'], login=ME, via='tap')
    assert len(friction.lines()) == 1


# ---- the morning cards ----

def _seed():
    for _ in range(3):
        friction.note('cant', 'home_control:refused', 'home_control refused: Which one? Porch Lamp, Hall Light')
    friction.note('wish', 'owner', 'Hub should remind me about bin day')
    friction.note('undo', 'off', 'Undid “Reading Lamp off” 2 min after Ask did it')
    friction.note('undo', 'off', 'Undid “Reading Lamp off” 3 min after Ask did it')


def test_the_morning_job_ranks_at_most_two_cards_once_a_day():
    _seed()
    catalog.hub_prepare()
    cards = friction.cards()
    assert [c['key'] for c in cards] == ['cant:home_control:refused', 'undo:off'] and cards[0]['count'] == 3
    friction.note('wish', 'owner', 'Hub should remind me about recycling too')
    friction.prepare()
    assert [c['key'] for c in friction.cards()] == ['cant:home_control:refused', 'undo:off']   # once a day


def test_cards_show_on_today_with_build_not_now_and_never():
    _seed()
    friction.prepare()
    items = catalog.friction_items()
    assert len(items) == 2 and [a['label'] for a in items[0]['acts']] == ['Build', 'Not now', 'Never']
    page = TestClient(app).get('/today', headers=SIGNED_IN).text
    assert 'Ask couldn’t do this' in page and 'Never' in page and f'/engine/friction/{friction.cards()[0]["id"]}/build' in page
    assert today.rec('k', 'info', 't', 'w', href='/x', cta='Go')['acts'] is None


def test_build_files_a_brief_for_the_manager_and_never_keeps_it_off_for_good():
    _seed()
    friction.prepare()
    [first, second] = friction.cards()
    client = TestClient(app)
    assert client.post(f'/engine/friction/{first["id"]}/build', headers={'origin': 'http://testserver'}).status_code == 403
    reply = client.post(f'/engine/friction/{first["id"]}/build', headers=SIGNED_IN, follow_redirects=False)
    assert reply.status_code == 303 and reply.headers['location'] == '/engine#improvements'
    [entry] = improve.requests()
    assert entry['source'] == 'friction' and 'Which one? Porch Lamp, Hall Light' in entry['brief'] and '3 times' in entry['brief']
    client.post(f'/engine/friction/{second["id"]}/never', headers=SIGNED_IN)
    assert friction.cards() == []
    again = client.post(f'/engine/friction/{second["id"]}/build', headers=SIGNED_IN, follow_redirects=False)
    assert 'problem=' in again.headers['location'] and len(improve.requests()) == 1
    # Tomorrow: the never key stays off; the built one comes back only if it happens again.
    with friction._db() as conn:
        conn.execute("update friction_cards set day = '2000-01-01'")
    friction.note('undo', 'off', 'Undid “Reading Lamp off” 1 min after Ask did it')
    friction.prepare()
    assert [c['key'] for c in friction.cards()] == ['wish:owner']
    friction.note('cant', 'home_control:refused', 'home_control refused: Which one? Porch Lamp, Hall Light')
    with friction._db() as conn:
        conn.execute("update friction_cards set day = '2000-01-02'")
    friction.prepare()
    assert 'cant:home_control:refused' in [c['key'] for c in friction.cards()]


def test_not_now_hides_it_until_it_happens_again():
    friction.note('wish', 'owner', 'Hub should remind me about bin day')
    friction.prepare()
    [card] = friction.cards()
    TestClient(app).post(f'/engine/friction/{card["id"]}/later', headers=SIGNED_IN)
    with friction._db() as conn:
        conn.execute("update friction_cards set day = '2000-01-01'")
    friction.prepare()
    assert friction.cards() == []
