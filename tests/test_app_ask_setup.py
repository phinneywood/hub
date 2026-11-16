"""Ask changing how the house is set up, and working devices beyond lights and speakers (hub/app/ask/tools.py over
hub/tools/house/admin.py).

Whole turns on the made-up house with the stand-in for the model (tests/ask_script.py), as in test_app_ask.py:
every tool, plan, rule, record and route underneath is the real one.
"""
import json

import pytest
from fastapi.testclient import TestClient

from hub.app import activity, levels
from hub.app.ask import route, tools
from hub.engine import catalog
from hub.engine import state as engine
from hub.main import app
from hub.tools.house import admin, control, read
from tests import app_world, ask_script
from tests.ask_script import SEEN

client = TestClient(app)
SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
ON_THIS_MAC = {'origin': 'http://testserver'}        # a program on the Mac itself: no login


@pytest.fixture
def house(monkeypatch):
    control._taps.clear()
    control._recent.clear()
    levels.forget()
    SEEN.clear()
    made = app_world.install(monkeypatch)
    ask_script.library(monkeypatch, made)
    monkeypatch.setattr(route, 'build', lambda http_client=None: ask_script.MODEL)
    monkeypatch.setattr(route, 'ready', lambda: None)
    monkeypatch.setattr(activity, 'collect', lambda: None)
    return made


def ask(text):
    with client.stream('POST', '/app/api/ask', json={'text': text}, headers={**SIGNED_IN, 'accept': 'text/event-stream'}) as stream:
        events = [json.loads(line.removeprefix('data: ')) for line in stream.iter_lines() if line.startswith('data: ')]
    return ''.join(e['text'] for e in events if e['t'] == 'say').strip(), [e['card'] for e in events if e['t'] == 'card'], events[-1]


def picture():
    read.clear_cache()
    return client.get('/app/api/home').json()


def lights_in(room):
    return [light['name'] for light in picture()['rooms'][room]['lights']]


def tool(run, strangers=False, login='me@example.com', **args):
    turn = tools.Turn(text='', login=login, strangers=strangers)
    return run(type('Ctx', (), {'deps': turn}), **args), turn


# ---- at once, with Undo ----

def test_a_light_is_moved_to_another_room_in_one_trip_and_can_be_put_back(house):
    answer, cards, end = ask('Move the reading lamp to the bedroom')
    assert answer == 'Reading lamp moved from Living room to Bedroom.' and end['status'] == 'ok' and len(SEEN) == 1
    assert cards == [{'kind': 'setup', 'lines': [], 'missed': [], 'batch': cards[0]['batch']}]
    assert 'Reading lamp' in lights_in('bedroom') and 'Reading lamp' not in lights_in('living-room')
    assert client.post('/app/api/undo/' + cards[0]['batch'], headers=SIGNED_IN).json() == {'ok': True, 'said': 'Reading lamp put back', 'batch': None}
    assert 'Reading lamp' in lights_in('living-room')


def test_a_rename_a_new_room_and_the_thermostat_each_say_what_was_done(house):
    assert ask('rename the reading lamp to Corner lamp')[0] == 'Reading lamp renamed Corner lamp.'
    assert 'Corner lamp' in lights_in('living-room')
    assert ask('Add a room called Sun room upstairs')[0] == 'Room “Sun room” added on Upstairs.'
    assert ask('move the corner lamp to the sun room')[0] == 'Corner lamp moved from Living room to Sun room.'
    assert lights_in('sun-room') == ['Corner lamp']
    answer, cards, _ = ask('set the thermostat to 70')
    assert answer == 'Thermostat set to 70°.' and house.states['climate.hallway']['attributes']['temperature'] == 70
    client.post('/app/api/undo/' + cards[0]['batch'], headers=SIGNED_IN)
    assert house.states['climate.hallway']['attributes']['temperature'] == 68
    assert ask('move the corner lamp to the attic')[0].startswith('Hub couldn’t do that. There is no room called “attic”.')


def test_several_changes_in_one_request_are_listed_on_the_card_with_one_undo(house):
    answer, cards, _ = ask('move the kitchen lights to the dining room')
    assert answer == '3 changes made.' and cards[0]['lines'] == [
        'Under the cabinets moved from Kitchen to Dining', 'Ceiling moved from Kitchen to Dining', 'Island pendants moved from Kitchen to Dining']
    assert len(lights_in('dining')) == 5
    client.post('/app/api/undo/' + cards[0]['batch'], headers=SIGNED_IN)
    assert len(lights_in('dining')) == 2 and len(lights_in('kitchen')) == 3


def test_what_is_already_so_is_said_without_a_second_trip(house):
    answer, cards, _ = ask('move the reading lamp to the living room')
    assert answer == 'Reading lamp is already in Living room.' and cards == [] and len(SEEN) == 1 and house.setup == []


def test_a_scene_is_changed_by_name_and_a_new_one_keeps_a_room_as_it_is(house):
    assert ask('In the dinner scene, set the island pendants to 30')[0] == 'Scene “Dinner” changed: Island pendants at 30%.'
    assert ask('dinner')[0] == 'Dinner is on.'
    assert picture()['rooms']['kitchen']['lights'][1]['level'] == 30
    assert ask('save the living room as a scene called Reading')[0] == 'Scene “Reading” saved: living room as it is now.'
    assert 'Reading' in [s['name'] for s in picture()['scenes']]


def test_a_scene_is_run_by_the_words_that_single_it_out(house):
    house.states['scene.upstairs_wind_down'] = {'entity_id': 'scene.upstairs_wind_down', 'state': 'unknown',
                                              'attributes': {'friendly_name': 'Upstairs: Wind down', 'id': 'wind-down'}}
    house.configs['scene']['wind-down'] = {'id': 'wind-down', 'name': 'Upstairs: Wind down', 'entities': {'light.kitchen_ceiling': {'state': 'off'}}}
    out, turn = tool(tools.scene, name='wind down')
    assert turn.said == ['Upstairs: Wind down is on.'] and house.sent == [('scene/turn_on', {'entity_id': 'scene.upstairs_wind_down'})]


def test_an_automation_is_written_from_what_was_found_and_reads_back(house):
    answer, cards, _ = ask('Every evening at 6:45 turn on the hallway light')
    assert answer == 'Automation “Hallway ceiling on at 6:45 pm” made: at 6:45 pm, Ceiling (Hallway) on.' and len(SEEN) == 2     # looked, then wrote it
    (kept,) = [c for c in house.configs['automation'].values() if c['alias'] == 'Hallway ceiling on at 6:45 pm']
    assert kept['triggers'] == [{'trigger': 'time', 'at': '18:45:00'}]
    assert kept['actions'] == [{'action': 'light.turn_on', 'target': {'entity_id': 'light.hall_ceiling'}}]
    assert ask('what does hallway ceiling on at 6:45 pm do?')[0] == 'Turns the hallway ceiling on every day.'
    assert ask('turn the hallway ceiling on at 6:45 pm automation off')[0] == 'Automation “Hallway ceiling on at 6:45 pm” switched off.'
    client.post('/app/api/undo/' + cards[0]['batch'], headers=SIGNED_IN)
    assert 'Hallway ceiling on at 6:45 pm' not in [c['alias'] for c in house.configs['automation'].values()]


# ---- what waits for a tap ----

def test_removing_a_room_waits_on_a_card_and_only_a_signed_in_tap_does_it(house):
    answer, cards, end = ask('Remove the dining room')
    (card,) = cards
    assert answer == 'That one waits for your tap.' and end['status'] == 'ok' and house.setup == []
    assert card == {'kind': 'confirm', 'id': card['id'], 'title': 'Remove Dining?', 'do': 'Remove',
                    'lines': ['Room “Dining” removed; its 2 things are in no room'], 'why': 'This removes it. Undo can put it back for a day.'}
    assert client.get(f'/app/api/setup/{card["id"]}').json() == {'status': 'waiting', 'batch': None}
    assert client.post(f'/app/api/setup/{card["id"]}/confirm', headers=ON_THIS_MAC).status_code == 403
    assert 'dining' in picture()['rooms']
    done = client.post(f'/app/api/setup/{card["id"]}/confirm', headers=SIGNED_IN).json()
    assert done['ok'] and done['said'] == 'Room “Dining” removed; its 2 things are in no room' and 'dining' not in picture()['rooms']
    assert client.get(f'/app/api/setup/{card["id"]}').json() == {'status': 'done', 'batch': done['batch']}
    assert client.post(f'/app/api/setup/{card["id"]}/confirm', headers=SIGNED_IN).json() == {'error': 'That is already done.'}
    assert client.post('/app/api/undo/' + done['batch'], headers=SIGNED_IN).json()['ok'] and len(lights_in('dining')) == 2


def test_a_card_can_be_put_away_and_typing_yes_does_nothing(house):
    _, (card,), _ = ask('delete the porch at sunset automation')
    assert card['title'] == 'Remove Porch at sunset?'
    assert ask('yes')[0].startswith('Hub can switch and dim lights') and 'a-porch' in house.configs['automation']
    assert client.post(f'/app/api/setup/{card["id"]}/dismiss', headers=ON_THIS_MAC).status_code == 403
    assert client.post(f'/app/api/setup/{card["id"]}/dismiss', headers=SIGNED_IN).json() == {'ok': True, 'said': 'Left as it was'}
    assert client.post(f'/app/api/setup/{card["id"]}/confirm', headers=SIGNED_IN).json() == {'error': 'That card isn’t waiting any more. Ask again.'}
    assert 'a-porch' in house.configs['automation'] and client.post('/app/api/setup/999/nonsense', headers=SIGNED_IN).status_code == 404


def test_locking_is_done_at_once_and_unlocking_and_opening_wait_for_a_tap(house):
    house.states['lock.front_door']['state'] = 'unlocked'
    answer, cards, _ = ask('Lock the front door')
    assert answer == 'Front door lock locked.' and house.states['lock.front_door']['state'] == 'locked' and cards[0]['batch']
    answer, (card,), _ = ask('unlock the front door')
    assert answer == 'That one waits for your tap.' and card['title'] == 'Unlock Front door lock?' and card['do'] == 'Do it'
    assert card['lines'] == ['Front door lock unlocked'] and house.states['lock.front_door']['state'] == 'locked'
    assert client.post(f'/app/api/setup/{card["id"]}/confirm', headers=SIGNED_IN).json()['ok']
    assert house.states['lock.front_door']['state'] == 'unlocked'
    _, (garage,), _ = ask('open the garage')
    assert garage['title'] == 'Open Garage door?' and house.states['cover.garage_door']['state'] == 'closed'
    _, (restart,), _ = ask('restart home assistant')
    assert restart['title'] == 'Restart Home Assistant?' and restart['do'] == 'Restart' and ('homeassistant/restart', {}) not in house.sent


# ---- what holds Ask back ----

def test_nothing_about_the_setup_changes_on_a_turn_that_read_what_strangers_wrote(house):
    said = 'Hub changes the house’s setup in a request of its own, never alongside mail, the calendar or school. Ask for it by itself.'
    for run, args in ((tools.arrange, {'things': ['reading lamp'], 'room': 'bedroom'}),
                      (tools.device, {'action': 'lock.unlock', 'things': ['front door lock']}),
                      (tools.routine, {'kind': 'automation', 'do': 'off', 'name': 'porch at sunset'})):
        out, turn = tool(run, strangers=True, **args)
        assert turn.said == [said] and not turn.again and turn.cards == []
    assert house.setup == [] and house.sent == [] and admin.recent() == []
    turn = tools.Turn(text='when is the dentist', login='me@example.com')
    tools.look_up(type('Ctx', (), {'deps': turn}), topics=['calendar'])
    assert turn.strangers                                               # reading the calendar mid-turn counts too


def test_each_kind_of_change_has_its_own_suggest_only(house):
    try:
        engine.set_trust(catalog.action('house.setup'), 'suggest')
        assert ask('move the reading lamp to the bedroom')[0] == 'Changing the house’s setup is set to “Suggest only” on the Engine page, so Hub changed nothing.'
        assert ask('set the thermostat to 70')[0] == 'Thermostat set to 70°.'
        engine.set_trust(catalog.action('house.security'), 'suggest')
        assert ask('lock the front door')[0] == 'Locks, the garage and the alarm is set to “Suggest only” on the Engine page, so Hub changed nothing.'
        assert ask('open the garage')[0].endswith('so Hub changed nothing.')
        assert ask('dim the kitchen')[0] == 'The kitchen is at 40%.'
    finally:
        engine.set_trust(catalog.action('house.setup'), 'undo')
        engine.set_trust(catalog.action('house.security'), 'undo')


def test_a_program_without_a_login_changes_nothing(house):
    out, turn = tool(tools.arrange, login=None, things=['reading lamp'], room='bedroom')
    assert turn.said == ['Change the house from your phone or laptop.'] and house.setup == []


def test_home_assistants_no_goes_back_to_the_model_and_nothing_is_claimed(house):
    house.refuse = 'config/entity_registry/update'
    answer, cards, end = ask('move the reading lamp to the bedroom')
    assert answer == 'Hub couldn’t do that. Home Assistant said no (invalid_format), so nothing was changed.' and cards == []
    assert 'Reading lamp' in lights_in('living-room')
    house.refuse, house.down = None, True
    out, turn = tool(tools.arrange, things=['reading lamp'], room='bedroom')
    assert turn.said == ['The house isn’t answering, so nothing was changed.']


def test_looking_and_reading_give_the_model_names_and_ids_and_change_nothing(house):
    out, turn = tool(tools.find, words='porch')
    assert turn.again and {'id': 'automation.porch_at_sunset', 'name': 'Porch at sunset', 'kind': 'automation', 'state': 'on'} in out['found']
    out, turn = tool(tools.details, thing='thermostat')
    assert turn.again and out['attributes']['hvac_modes'] == ['off', 'heat', 'cool'] and out['room'] == 'Hallway'
    out, turn = tool(tools.details, thing='hot tub')
    assert out == {'not_done': 'Hub found nothing called “hot tub” in Home Assistant.'}
    assert house.setup == [] and house.sent == []


# ---- the record ----

def test_setup_changes_are_on_activity_and_changes_with_undo(house):
    _, (moved,), _ = ask('move the reading lamp to the bedroom')
    ask('lock the front door')
    house.states['lock.front_door']['state'] = 'unlocked'
    ask('lock the front door')
    ask('turn the porch at sunset automation off')
    rows = [e for e in client.get('/app/api/activity').json()['events'] if e['source'] == 'hub']
    assert [(e['kind'], e['what'], e['cause'], e['undo']) for e in rows] == [
        ('routines', 'Automation “Porch at sunset” switched off', 'from Ask', True),
        ('security', 'Front door lock locked', 'from Ask', True),
        ('setup', 'Reading lamp moved from Living room to Bedroom', 'from Ask', True)]
    assert [e['what'] for e in client.get('/app/api/activity?kinds=setup').json()['events']] == ['Reading lamp moved from Living room to Bedroom']
    listed = client.get('/app/api/changes').json()['changes']
    assert [(c['where'], c['what'], c['undo']) for c in listed][-1] == ('setup', 'Reading lamp moved from Living room to Bedroom', True)
    client.post('/app/api/undo/' + moved['batch'], headers=SIGNED_IN)
    listed = client.get('/app/api/changes').json()['changes']
    assert [(c['what'], c['via'], c['undone']) for c in listed if c['where'] == 'setup'][:1] == [('Reading lamp put back', 'Undo', False)]
    assert [c['undone'] for c in listed if c['what'] == 'Reading lamp moved from Living room to Bedroom'] == [True]


def test_home_assistants_own_line_for_what_hub_did_is_left_out_of_activity(house, monkeypatch):
    import time
    ask('lock the front door')          # already locked: nothing done, nothing recorded
    house.states['lock.front_door']['state'] = 'unlocked'
    ask('lock the front door')
    with activity._db() as conn:
        activity._keep(conn, 'ha', [{'key': 'ha|1', 'ts': time.time(), 'source': 'ha', 'entity': 'lock.front_door', 'name': 'Front door lock',
                                     'room': 'Entry', 'kind': 'security', 'what': 'locked', 'cause': 'Alex'}], time.time())
    events = client.get('/app/api/activity').json()['events']
    assert [(e['source'], e['what']) for e in events] == [('hub', 'Front door lock locked')]
