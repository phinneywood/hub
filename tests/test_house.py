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
        if message['op'] == 'request':
            domain, service = message['path'].removeprefix('/api/services/').split('/')
            data = message['body']
            item = next((s for s in world['states'] if s['entity_id'] == data['entity_id']), None)
            if item and service in ('turn_on', 'turn_off'):
                item['state'] = service.removeprefix('turn_')
                if 'brightness_pct' in data:
                    item['attributes']['brightness'] = round(data['brightness_pct'] * 255 / 100)
            elif item and service == 'media_pause':
                item['state'] = 'paused'
            elif item and service in ('media_play', 'play_media'):
                item['state'] = 'playing'
            elif item and service == 'volume_set':
                item['attributes']['volume_level'] = data['volume_level']
            return []
        return [answers[c['type']]() for c in message['commands']]
    monkeypatch.setattr(broker, '_exchange', exchange)
    monkeypatch.setattr(control, 'CHECK_WAIT', 0)
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

def test_house_opens_home_assistant_when_its_address_is_set(monkeypatch):
    from hub import config
    client = TestClient(app)
    assert client.get('/house', follow_redirects=False).headers['location'] == '/house/changes'
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'home': {'assistant_url': 'https://ha.example:8123',
                                                                         'music_url': 'javascript:alert(1)'}}))
    assert client.get('/house', follow_redirects=False).headers['location'] == 'https://ha.example:8123'
    assert read.links() == {'assistant_url': 'https://ha.example:8123'}          # only web addresses


def test_tile_and_today_items(fake, monkeypatch):
    tile = home.house()
    assert (tile['tone'], tile['headline']) == ('ok', '1 light on')
    assert [a['label'] for a in tile['acts']] == ['All lights off', 'Pause Living room']
    assert 'All locked and closed' in tile['detail'] and 'Thermostat 68° · set to 70°' in tile['detail']
    monkeypatch.setattr(read, 'night', lambda now=None: True)
    set_state(fake, 'lock.front_door', 'unlocked')
    tile = home.house()
    assert (tile['tone'], tile['headline']) == ('warn', 'Front door is unlocked')
    [item] = catalog.house_items()
    assert (item['key'], item['href'], item['tone']) == ('house-lock.front_door', '/house/changes', 'warn')


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


# ---- Changing the house ----

from hub.tools.house import control  # noqa: E402

SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}


def sent(world):
    return [(m['path'].removeprefix('/api/services/'), m['body']) for m in world['asked'] if m['op'] == 'request']


def test_the_broker_client_checks_every_change():
    for domain, service, data in (('lock', 'unlock', {'entity_id': 'lock.front_door'}),
                                  ('light', 'turn_on', {'entity_id': 'lock.front_door'}),
                                  ('light', 'turn_on', {'entity_id': 'light.counter', 'brightness_pct': 150}),
                                  ('light', 'turn_on', {'entity_id': 'light.counter', 'flash': 'long'}),
                                  ('media_player', 'join', {'entity_id': 'media_player.a', 'group_members': ['light.x']}),
                                  ('music_assistant', 'play_media', {'entity_id': 'media_player.a', 'media_id': 'x', 'media_type': 'podcast', 'enqueue': 'replace'}),
                                  ('music_assistant', 'play_media', {'entity_id': 'media_player.a', 'media_id': 'soft music for reading',
                                                                     'media_type': 'playlist', 'enqueue': 'replace'}),
                                  ('music_assistant', 'play_media', {'entity_id': 'media_player.a', 'media_id': 'library://album/3',
                                                                     'media_type': 'playlist', 'enqueue': 'replace'}),
                                  ('climate', 'set_temperature', {'entity_id': 'climate.hallway'}),
                                  ('automation', 'trigger', {'entity_id': 'automation.morning'})):
        with pytest.raises(ValueError):
            broker.act(domain, service, data)


def test_a_change_is_recorded_and_undone(fake):
    result = control.change(['light.counter', 'light.floor_lamp'], 'off', login='me@example.com', via='ask')
    assert result['done'] == ['Counter lights off', 'Floor lamp off'] and result['undo']
    assert sent(fake) == [('light/turn_off', {'entity_id': 'light.counter'}), ('light/turn_off', {'entity_id': 'light.floor_lamp'})]
    back = control.undo(result['batch'], login='me@example.com', via='tap')
    assert back['done'] == ['Counter lights put back', 'Floor lamp put back']
    assert sent(fake)[2:] == [('light/turn_on', {'entity_id': 'light.counter', 'brightness_pct': 50}),
                              ('light/turn_off', {'entity_id': 'light.floor_lamp'})]
    with pytest.raises(control.Refused):
        control.undo(result['batch'], login='me@example.com', via='tap')     # once only
    [latest, first] = control.recent()
    assert latest['via'] == 'undo' and first['undone'] and not first['undo']


def test_only_kept_devices_and_their_actions(fake):
    for targets, action in ((['lock.front_door'], 'off'), (['camera.porch'], 'off'), (['light.hidden'], 'off'),
                            (['media_player.living_room_sonos'], 'pause'), (['switch.kettle'], 'brightness')):
        with pytest.raises(control.Refused):
            control.change(targets, action, login='me@example.com', via='ask')
    assert sent(fake) == []


def test_music_starts_quietly_from_silence_and_undo_restores(fake):
    result = control.change(['media_player.kitchen'], 'play_media', login='me@example.com', via='ask', query='library://playlist/3', kind='playlist', label='calm piano')
    assert result['done'] == ['Kitchen speaker playing “calm piano”']
    assert sent(fake) == [('media_player/volume_set', {'entity_id': 'media_player.kitchen', 'volume_level': 0.15}),
                          ('music_assistant/play_media', {'entity_id': 'media_player.kitchen', 'media_id': 'library://playlist/3',
                                                          'media_type': 'playlist', 'enqueue': 'replace'})]
    control.undo(result['batch'], login='me@example.com', via='tap')
    assert sent(fake)[2:] == [('media_player/media_pause', {'entity_id': 'media_player.kitchen'}),
                              ('media_player/volume_set', {'entity_id': 'media_player.kitchen', 'volume_level': 0.2})]
    count = len(sent(fake))
    already = control.change(['media_player.living_room'], 'play_media', login='me@example.com', via='ask', query='apple_music://artist/b1', kind='artist', label='Band B')
    assert already['done'] and [p for p, _ in sent(fake)[count:]] == ['music_assistant/play_media']   # already playing: volume left alone


def test_an_unanswered_change_is_unknown_and_an_unreachable_broker_is_not_done(fake, monkeypatch):
    def lost(domain, service, data, timeout=None):
        raise broker.HomeDown('The Home Assistant broker gave an answer Hub couldn’t read.')
    monkeypatch.setattr(broker, 'act', lost)
    result = control.change(['switch.kettle'], 'off', login='me@example.com', via='ask')
    assert result['done'] == [] and result['unknown'] == ['Kettle off'] and result['undo']
    def missing(domain, service, data, timeout=None):
        raise broker.NotSent('The Home Assistant broker on this Mac isn’t answering.')
    monkeypatch.setattr(broker, 'act', missing)
    result = control.change(['switch.kettle'], 'off', login='me@example.com', via='ask')
    assert result['not_sent'] == ['Kettle off'] and not result['undo']


def test_at_most_thirty_changes_a_minute(fake, monkeypatch):
    monkeypatch.setattr(control, '_recent', type(control._recent)())
    monkeypatch.setattr(control, 'PER_MINUTE', 3)
    control.change(['light.counter', 'switch.kettle'], 'off', login='me@example.com', via='ask')
    with pytest.raises(control.Refused):
        control.change(['light.floor_lamp', 'switch.kettle'], 'on', login='me@example.com', via='ask')


def test_resolve_names_rooms_and_all_lights(fake):
    found = read.snapshot()
    assert control.resolve('counter lights', read.POWER, found) == ['light.counter']
    assert control.resolve('kitchen lights', ('light',), found) == ['light.counter']
    assert sorted(control.resolve('all lights', ('light',), found)) == ['light.counter', 'light.floor_lamp', 'light.porch']
    assert control.resolve('kettle', read.POWER, found) == ['switch.kettle']
    with pytest.raises(control.Refused) as error:
        control.resolve('garage', read.POWER, found)
    assert 'Which one?' in str(error.value)


def test_today_buttons_need_a_signed_in_tap_and_show_undo(fake, monkeypatch):
    monkeypatch.setattr(control, '_recent', type(control._recent)())
    client = TestClient(app)
    assert client.post('/house/do', data={'target': 'lights-on', 'action': 'off'}, headers={'sec-fetch-site': 'same-origin'}).status_code == 403
    assert client.post('/house/do', data={'target': 'lights-on', 'action': 'off'},
                       headers={'origin': 'https://elsewhere.example', 'tailscale-user-login': 'me@example.com'}).status_code == 403
    reply = client.post('/house/do', data={'target': 'lights-on', 'action': 'off'}, headers=SIGNED_IN, follow_redirects=False)
    assert reply.status_code == 303 and reply.headers['location'].startswith('/today?home=')
    assert sent(fake) == [('light/turn_off', {'entity_id': 'light.counter'})]
    page = client.get(reply.headers['location'], headers=SIGNED_IN).text
    assert 'Counter lights off.' in page and '/house/undo/' in page
    batch = reply.headers['location'].split('=')[1]
    assert client.post(f'/house/undo/{batch}', headers=SIGNED_IN, follow_redirects=False).status_code == 303
    assert sent(fake)[-1] == ('light/turn_on', {'entity_id': 'light.counter', 'brightness_pct': 50})
    changes = client.get('/house/changes', headers=SIGNED_IN).text
    assert 'Counter lights off' in changes and 'undone' in changes
    refused = client.post('/house/do', data={'target': 'lock.front_door', 'action': 'off'}, headers=SIGNED_IN)
    assert refused.status_code == 200 and 'Hub can’t control that' in refused.text


def test_ask_controls(fake, monkeypatch):
    from hub.engine import state
    monkeypatch.setattr(control, '_recent', type(control._recent)())
    assert catalog.home_control(None, 'kettle', 'off')['refused'].startswith('Changes to the house need')
    assert catalog.home_control('me@example.com', 'kettle', 'off') == {
        'done': ['Kettle off'], 'undo': 'The owner can undo this for a day on the Home changes page.'}
    assert catalog.home_control('me@example.com', 'counter lights', 'brightness', 30)['done'] == ['Counter lights at 30%']
    assert 'Which one?' in catalog.home_control('me@example.com', 'front door', 'off')['refused']
    assert 'already play together' in catalog.play_music('me@example.com', 'living room', 'group', with_speakers=['kitchen speaker'])['refused']
    fake['states'][6]['attributes']['group_members'] = []                   # the living room plays on its own
    found = catalog.play_music('me@example.com', 'living room', 'group', with_speakers=['kitchen speaker'])
    assert found['done'] == ['Kitchen speaker joined Living room at 15%']   # an idle speaker joins at the group's volume
    assert sent(fake)[-2:] == [('media_player/volume_set', {'entity_id': 'media_player.kitchen', 'volume_level': 0.15}),
                               ('media_player/join', {'entity_id': 'media_player.living_room', 'group_members': ['media_player.kitchen']})]
    state.set_trust(catalog.action('house.control'), 'suggest')
    assert 'Suggest only' in catalog.home_control('me@example.com', 'kettle', 'on')['refused']
