"""Home: Home Assistant read through the HA broker. A fake broker and made-up devices only."""
import io
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from hub import home
from hub.engine import catalog
from hub.main import app
from hub.tools.house import broker, read

AREAS = [{'area_id': 'kitchen', 'name': 'Kitchen'}, {'area_id': 'living', 'name': 'Living room'}]
DEVICES = [{'id': 'dev-lamp', 'area_id': 'living'}]
ENTITIES = [
    {'entity_id': 'light.counter', 'area_id': 'kitchen'},
    {'entity_id': 'light.floor_lamp', 'device_id': 'dev-lamp'},          # room from its device
    {'entity_id': 'switch.kettle', 'area_id': 'kitchen'},
    {'entity_id': 'switch.lamp_child_lock', 'device_id': 'dev-lamp', 'entity_category': 'config'},
    {'entity_id': 'light.hidden', 'area_id': 'kitchen', 'hidden_by': 'user'},
    {'entity_id': 'media_player.living_room', 'platform': 'music_assistant', 'area_id': 'living'},
    {'entity_id': 'media_player.kitchen', 'platform': 'music_assistant', 'area_id': 'kitchen'},
    {'entity_id': 'media_player.living_room_sonos', 'platform': 'sonos'},
]


def state(entity_id, value, **attrs):
    return {'entity_id': entity_id, 'state': value, 'attributes': attrs}


STATES = [
    state('light.counter', 'on', friendly_name='Counter lights', brightness=128),
    state('light.floor_lamp', 'off', friendly_name='Floor lamp'),
    state('light.porch', 'unavailable', friendly_name='Porch light'),
    state('light.hidden', 'on', friendly_name='Hidden light'),
    state('switch.kettle', 'on', friendly_name='Kettle'),
    state('switch.lamp_child_lock', 'on', friendly_name='Child lock'),
    state('media_player.living_room', 'playing', friendly_name='Living room', media_title='Song A',
          media_artist='Band B', volume_level=0.15, group_members=['media_player.living_room', 'media_player.kitchen']),
    state('media_player.kitchen', 'idle', friendly_name='Kitchen speaker', volume_level=0.2),
    state('media_player.living_room_sonos', 'playing', friendly_name='Sonos twin'),
    state('climate.hallway', 'heat', friendly_name='Thermostat', current_temperature=67.6, temperature=70),
    state('lock.front_door', 'locked', friendly_name='Front door'),
    state('cover.garage_door', 'closed', friendly_name='Garage door', device_class='garage'),
    state('cover.blinds', 'open', friendly_name='Blinds', device_class='blind'),
    state('alarm_control_panel.house', 'disarmed', friendly_name='Alarm'),
    state('camera.porch', 'idle', friendly_name='Porch camera'),
    state('person.alex', 'home', friendly_name='Alex'),
    state('automation.morning', 'on', friendly_name='Morning'),
]


@pytest.fixture
def fake(monkeypatch):
    """A broker that answers reads from the synthetic house, and records what Hub asked."""
    asked = []
    world = {'states': [dict(s, attributes=dict(s['attributes'])) for s in STATES]}
    answers = {'config/area_registry/list': lambda: AREAS, 'config/device_registry/list': lambda: DEVICES,
               'config/entity_registry/list': lambda: ENTITIES, 'get_states': lambda: world['states']}

    def exchange(message, timeout):
        asked.append(message)
        return [answers[c['type']]() for c in message['commands']]
    monkeypatch.setattr(broker, '_exchange', exchange)
    world['asked'] = asked
    return world


def set_state(world, entity_id, value):
    next(s for s in world['states'] if s['entity_id'] == entity_id)['state'] = value
    read.clear_cache()


def down(monkeypatch):
    def refuse(message, timeout):
        raise broker.HomeDown('The Home Assistant broker on this Mac isn’t answering.')
    monkeypatch.setattr(broker, '_exchange', refuse)


# ---- The broker client ----

def test_only_reads_ever_leave_hub(monkeypatch):
    monkeypatch.setattr(broker, '_exchange', lambda *a: pytest.fail('nothing may be sent'))
    for command in ('call_service', 'execute_script', 'config/entity_registry/update', 'auth/long_lived_access_token'):
        with pytest.raises(ValueError):
            broker.read(command)
    with pytest.raises(ValueError):
        broker.read()


class FakeSocket:
    reply = b''
    sent = []

    def __init__(self, *args):
        pass

    def settimeout(self, value):
        pass

    def connect(self, path):
        pass

    def sendall(self, data):
        FakeSocket.sent.append(data)

    def makefile(self, mode):
        return io.BytesIO(FakeSocket.reply)

    def close(self):
        pass


def test_broker_exchange_sends_one_line_and_reads_one_line(monkeypatch):
    monkeypatch.setattr(broker.socket, 'socket', FakeSocket)
    FakeSocket.sent, FakeSocket.reply = [], json.dumps({'ok': True, 'result': [[], []]}).encode() + b'\n'
    assert broker.read('get_states', 'config/area_registry/list') == [[], []]
    assert json.loads(FakeSocket.sent[0]) == {'op': 'ws', 'commands': [{'type': 'get_states'}, {'type': 'config/area_registry/list'}]}


@pytest.mark.parametrize('reply', [b'', b'not json\n', b'{"ok": false, "error": "Token rejected"}\n', b'{"ok": true, "result": {}}\n'])
def test_a_bad_or_refused_answer_is_home_down_with_a_safe_message(monkeypatch, reply):
    monkeypatch.setattr(broker.socket, 'socket', FakeSocket)
    FakeSocket.reply = reply
    with pytest.raises(broker.HomeDown) as error:
        broker.read('get_states')
    assert 'Token' not in str(error.value)


def test_no_broker_is_home_down(monkeypatch):
    def missing(self, path):
        raise FileNotFoundError(path)
    monkeypatch.setattr(broker.socket.socket, 'connect', missing)
    with pytest.raises(broker.HomeDown):
        broker.read('get_states')


# ---- Reading the house ----

def test_snapshot_keeps_only_allowed_devices_in_their_rooms(fake):
    found = read.snapshot()
    names = {d['name'] for r in found['rooms'] for d in r['devices']}
    assert names == {'Counter lights', 'Kettle', 'Floor lamp', 'Porch light'}         # no hidden or config entities
    assert [r['name'] for r in found['rooms']] == ['Kitchen', 'Living room', 'Other']
    assert [d['name'] for d in found['speakers']] == ['Kitchen speaker', 'Living room']  # Music Assistant only
    assert [d['name'] for d in found['security']] == ['Alarm', 'Front door', 'Garage door']  # blinds aren't a garage
    everything = json.dumps(found, default=str)
    for never in ('Porch camera', 'Alex', 'Morning', 'Sonos twin', 'Blinds'):
        assert never not in everything
    assert found['lights_on'] == 1


def test_snapshot_words(fake):
    found = read.snapshot()
    by = {d['id']: d for d in [d for r in found['rooms'] for d in r['devices']] + found['speakers'] + found['climate']}
    assert (by['light.counter']['label'], by['light.counter']['detail']) == ('On', '50%')
    assert by['light.porch']['label'] == 'Offline' and by['light.porch']['offline']
    assert by['media_player.living_room']['detail'] == 'Song A – Band B · volume 15%'
    assert by['media_player.living_room']['group'] == ['Kitchen speaker']
    assert (by['climate.hallway']['label'], by['climate.hallway']['detail']) == ('Heating', 'Now 68° · set to 70°')


def test_snapshot_is_cached_briefly_and_registries_longer(fake, monkeypatch):
    read.snapshot()
    read.snapshot()
    assert len(fake['asked']) == 1
    monkeypatch.setattr(read, 'STATE_SECONDS', -1)
    read.snapshot()
    assert fake['asked'][-1]['commands'] == [{'type': 'get_states'}]


def test_attention_at_night_and_any_hour(fake):
    day, late = datetime(2026, 1, 5, 14, tzinfo=home.PACIFIC), datetime(2026, 1, 5, 23, tzinfo=home.PACIFIC)
    assert read.attention(read.snapshot(), late) == []
    set_state(fake, 'lock.front_door', 'unlocked')
    set_state(fake, 'cover.garage_door', 'open')
    assert read.attention(read.snapshot(), day) == []
    assert [w for _, _, w in read.attention(read.snapshot(), late)] == ['Front door is unlocked', 'Garage door is open']
    set_state(fake, 'alarm_control_panel.house', 'triggered')
    set_state(fake, 'lock.front_door', 'jammed')
    found = read.attention(read.snapshot(), day)
    assert [(t, w) for t, _, w in found] == [('bad', 'Alarm: going off'), ('warn', 'Front door is jammed')]


# ---- The page, the tile and Today ----

def test_home_page_shows_rooms_and_says_it_is_read_only(fake):
    page = TestClient(app).get('/house').text
    for text in ('Doors and alarm', 'Thermostat', 'Speakers', 'Kitchen', 'Counter lights', 'Song A – Band B', 'Read-only for now'):
        assert text in page
    assert 'Porch camera' not in page and '<form' not in page.split('<main')[1]


def test_home_page_when_the_broker_is_down(monkeypatch):
    down(monkeypatch)
    page = TestClient(app).get('/house')
    assert page.status_code == 200 and 'isn’t answering' in page.text and 'nothing was changed' in page.text


def test_tile_and_today_items(fake, monkeypatch):
    tile = home.house()
    assert (tile['tone'], tile['headline']) == ('ok', '1 light on')
    assert 'All locked and closed' in tile['detail'] and 'Thermostat 68° · set to 70°' in tile['detail']
    monkeypatch.setattr(read, 'night', lambda now=None: True)
    set_state(fake, 'lock.front_door', 'unlocked')
    tile = home.house()
    assert (tile['tone'], tile['headline']) == ('warn', 'Front door is unlocked')
    [item] = catalog.house_items()
    assert (item['key'], item['href'], item['tone']) == ('house-lock.front_door', '/house', 'warn')


def test_tile_and_items_when_the_broker_is_down(monkeypatch):
    down(monkeypatch)
    assert home.house()['tone'] == 'idle'
    assert catalog.house_items() == []


# ---- What Ask Hub may look up ----

def test_home_lookup(fake):
    lookups = {l.slug: l for l in catalog.lookups()}
    found = lookups['home_status'].run()
    assert found['rooms'][0] == {'room': 'Kitchen', 'on': ['Counter lights', 'Kettle']}
    assert found['doors_and_alarm'][1] == {'name': 'Front door', 'state': 'Locked'}
    room = lookups['home_status'].run(room='living room')
    assert room['devices'] == [{'name': 'Floor lamp', 'state': 'Off'}]
    assert 'Rooms: Kitchen' in lookups['home_status'].run(room='Attic')['error']
    music = lookups['music_now'].run()['speakers']
    assert music[1] == {'name': 'Living room', 'state': 'Playing', 'detail': 'Song A – Band B · volume 15%',
                        'grouped_with': ['Kitchen speaker']}
    assert lookups['home_status'].page == '/house'


def test_lookups_scrub_names_and_fail_soft(fake, monkeypatch):
    next(s for s in fake['states'] if s['entity_id'] == 'switch.kettle')['attributes']['friendly_name'] = 'Kettle sam@example.com'
    read.clear_cache()
    assert 'Kettle [address]' in catalog.home_lookup()['rooms'][0]['on']
    down(monkeypatch)
    read.clear_cache()
    assert catalog.home_lookup() == catalog.UNAVAILABLE and catalog.music_lookup() == catalog.UNAVAILABLE
