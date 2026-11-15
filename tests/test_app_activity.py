"""The Activity screen (hub/app/activity.py): Home Assistant's logbook, Apple Home through HomeClaw, and Hub's own
changes, as one list. The house is the made-up one in app_world; HomeClaw is a stand-in; nothing real is reached."""
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from hub.app import activity
from hub.main import app
from hub.tools.house import apple, broker

from . import app_world

client = TestClient(app)
OWNER = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
NOW = time.time()


def _iso(ts):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))


class Claw:
    """A stand-in for HomeClaw's two reads."""
    def __init__(self, logging=True, events=(), down=False):
        self.logging, self.found, self.down, self.since = logging, list(events), down, []

    def setup(self):
        if self.down:
            raise apple.AppleDown('HomeClaw isn’t answering. Is the app open?')
        return {'logging': self.logging, 'home': 'home-1'}

    def events(self, since):
        self.since.append(since)
        return list(self.found)


@pytest.fixture
def house(monkeypatch):
    world = app_world.install(monkeypatch)
    world.states['person.alex'] = {'entity_id': 'person.alex', 'state': 'home',
                                   'attributes': {'friendly_name': 'Alex', 'user_id': 'u-alex'}}
    world.states['automation.evening'] = {'entity_id': 'automation.evening', 'state': 'on',
                                          'attributes': {'friendly_name': 'Evening lights'}}
    claw = Claw()
    monkeypatch.setattr(activity.apple, 'setup', claw.setup)
    monkeypatch.setattr(activity.apple, 'events', claw.events)
    return world, claw


def test_the_logbook_becomes_plain_rows_with_rooms_and_causes(house):
    world, _ = house
    world.logbook = [
        {'when': NOW - 300, 'entity_id': 'light.kitchen_ceiling', 'state': 'on', 'context_user_id': 'u-alex'},
        {'when': NOW - 240, 'entity_id': 'light.porch_light', 'state': 'off', 'context_event_type': 'automation_triggered',
         'context_domain': 'automation', 'context_name': 'Evening lights', 'context_entity_id': 'automation.evening'},
        {'when': NOW - 200, 'entity_id': 'binary_sensor.front_door', 'state': 'on'},
        {'when': NOW - 150, 'entity_id': 'event.living_button', 'state': '2026-11-15T17:58:00.000-08:00',
         'attributes': {'event_type': 'multi_press_2'}},
        {'when': NOW - 120, 'entity_id': 'automation.evening', 'name': 'Evening lights', 'message': 'triggered',
         'source': 'state of event.living_button', 'domain': 'automation'},
        {'when': NOW - 100, 'entity_id': 'media_player.kitchen', 'state': 'idle'},
        {'when': NOW - 90, 'entity_id': 'light.office_desk', 'state': 'unavailable'},
    ]
    seen = client.get('/app/api/activity').json()
    rows = [(e['name'], e['room'], e['what'], e['cause'], e['kind']) for e in seen['events']]
    assert rows == [
        ('Evening lights', '', 'ran, after Living room button', '', 'routines'),
        ('Living room button', 'Living room', 'pressed twice', '', 'buttons'),
        ('Front door', '', 'opened', '', 'security'),
        ('Porch light', 'Porch', 'off', 'Evening lights', 'lights'),
        ('Ceiling', 'Kitchen', 'on', 'Alex', 'lights'),
    ]
    everything = client.get('/app/api/activity?kinds=lights,music,other').json()['events']
    assert {(e['name'], e['what'], e['kind']) for e in everything} >= {
        ('Kitchen speaker', 'stopped', 'other'), ('Desk lamp', 'went offline', 'other')}
    assert seen['status']['ha']['trouble'] == '' and not seen['status']['apple']['off']


def test_readings_pick_up_where_they_left_off_and_never_repeat_a_row(house, monkeypatch):
    world, claw = house
    world.logbook = [{'when': NOW - 300, 'entity_id': 'light.kitchen_ceiling', 'state': 'on'}]
    asked = []
    real = broker.ask
    monkeypatch.setattr(activity.broker, 'ask', lambda *c, **k: asked.append(c[0]) or real(*c, **k))
    activity.collect()
    first = len(asked)
    assert first >= activity.FIRST_DAYS * 24 * 3600 // activity.WINDOW      # a week back, a few hours at a time
    activity.collect()
    assert len(asked) == first + 1 and set(asked[-1]) == {'type', 'start_time', 'end_time'}
    assert len(client.get('/app/api/activity').json()['events']) == 1
    assert claw.since[-1] > claw.since[0]


def test_hubs_own_change_shows_once_with_who_asked_and_undo(house):
    world, _ = house
    assert client.post('/app/api/device/light.office_desk', json={'on': True}, headers=OWNER).json()['ok']
    world.logbook = [{'when': time.time(), 'entity_id': 'light.office_desk', 'state': 'on', 'context_user_id': 'u-alex'}]
    events = client.get('/app/api/activity').json()['events']
    assert len(events) == 1 and events[0]['source'] == 'hub' and events[0]['undo'] and events[0]['batch']
    assert 'from the app' in events[0]['cause']
    assert client.post('/app/api/undo/' + events[0]['batch'], json={}, headers=OWNER).json()['ok']


def test_apple_home_rows_and_a_device_both_report_shows_once(house):
    world, claw = house
    world.logbook = [{'when': NOW - 60, 'entity_id': 'light.porch_light', 'state': 'on'}]
    claw.found = [
        {'type': 'characteristic_change', 'timestamp': _iso(NOW - 50), 'characteristic': 'power', 'value': 'true',
         'previous_value': 'false', 'service_type': 'lightbulb', 'home': {'id': 'home-1'},
         'accessory': {'id': 'a1', 'name': 'Porch light', 'room': 'Porch'}},
        {'type': 'characteristic_change', 'timestamp': _iso(NOW - 40), 'characteristic': 'lock_current_state', 'value': '1',
         'service_type': 'lock', 'home': {'id': 'home-1'}, 'accessory': {'id': 'a2', 'name': 'Side door lock', 'room': 'Side'}},
        {'type': 'characteristic_change', 'timestamp': _iso(NOW - 30), 'characteristic': 'motion_detected', 'value': 'true',
         'service_type': 'sensor', 'home': {'id': 'home-1'}, 'accessory': {'id': 'a3', 'name': 'Hall sensor', 'room': 'Hall'}},
        {'type': 'characteristic_change', 'timestamp': _iso(NOW - 20), 'characteristic': 'power', 'value': 'true',
         'service_type': 'lightbulb', 'home': {'id': 'another-home'}, 'accessory': {'id': 'a4', 'name': 'Cabin lamp', 'room': 'Cabin'}},
        {'type': 'homes_updated', 'timestamp': _iso(NOW - 10)},
    ]
    events = client.get('/app/api/activity').json()['events']
    assert [(e['source'], e['name'], e['what'], e['also']) for e in events] == [
        ('apple', 'Side door lock', 'locked', ''), ('ha', 'Porch light', 'on', 'Apple Home')]
    motion = client.get('/app/api/activity?kinds=motion&sources=apple').json()['events']
    assert [(e['name'], e['what']) for e in motion] == [('Hall sensor', 'motion')]


def test_a_source_that_is_away_or_switched_off_says_so_and_the_rest_still_shows(house):
    world, claw = house
    claw.logging = False
    world.logbook = [{'when': NOW - 60, 'entity_id': 'light.porch_light', 'state': 'on'}]
    status = client.get('/app/api/activity').json()['status']
    assert status['apple']['off'] and status['apple']['trouble'] == ''
    world.down, claw.down = True, True
    activity.collect()
    seen = activity.picture(fresh=False)
    assert seen['status']['ha']['trouble'] and 'HomeClaw' in seen['status']['apple']['trouble']
    assert [e['name'] for e in seen['events']] == ['Porch light']


def test_old_rows_are_let_go_after_ninety_days(house):
    with activity._db() as conn:
        activity._keep(conn, 'ha', [{'key': 'old', 'ts': NOW - 91 * 86400, 'source': 'ha', 'entity': 'light.x', 'name': 'X',
                                     'room': '', 'kind': 'lights', 'what': 'on', 'cause': ''}], NOW - 60)
    activity.collect()
    with activity._db() as conn:
        assert conn.execute("select count(*) from house_activity where key = 'old'").fetchone()[0] == 0


def test_hub_only_reads_the_logbook_and_homeclaw(monkeypatch):
    with pytest.raises(ValueError):
        broker.ask({'type': 'logbook/get_events', 'start_time': 'x', 'entity_ids': ['lock.front']})
    ran = []
    monkeypatch.setattr(apple.CLI.__class__, 'is_file', lambda self: True)
    monkeypatch.setattr(subprocess, 'run', lambda args, **k: ran.append(args) or subprocess.CompletedProcess(args, 0, b'{"events": []}', b''))
    with pytest.raises(ValueError):
        apple._run('set', 'Front door', 'lock_target_state', '0')
    assert apple.events(NOW) == [] and ran[0][1:4] == ['events', '--since', _iso(NOW)] and ran[0][-1] == '--json'
