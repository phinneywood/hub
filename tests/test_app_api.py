"""The app's data and taps (hub/app): the Home screen's picture, the live feed and every change. A made-up house."""
import asyncio
import json
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from hub import config
from hub.app import feed as feeds
from hub.app import levels, model
from hub.main import app
from hub.tools.house import broker, control, read
from tests import app_world

client = TestClient(app)
REAL_CONNECT = socket.socket.connect      # taken before the suite's guard replaces it; used only for a socket in a temp folder
SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}


@pytest.fixture
def house(monkeypatch):
    control._taps.clear()
    control._recent.clear()
    levels.forget()
    return app_world.install(monkeypatch)


def picture():
    read.clear_cache()
    return client.get('/app/api/home').json()


# ---- The picture ----

def test_the_house_is_laid_out_floor_by_floor(house):
    found = picture()
    assert found['ok'] and found['signed_in'] is False
    assert [f['name'] for f in found['floors']] == ['Downstairs', 'Upstairs', 'Outside']
    assert found['floors'][0]['rooms'] == ['kitchen', 'living-room', 'dining', 'office', 'entry']     # most lamps first
    assert sorted(r for r in found['rooms'] if found['rooms'][r]['on']) == ['dining', 'kitchen', 'living-room', 'porch', 'robin-s-room']


def test_a_room_says_how_lit_it_is_and_what_is_in_it(house):
    kitchen = picture()['rooms']['kitchen']
    assert (kitchen['on'], kitchen['lit'], kitchen['level'], kitchen['dims']) == (True, 3, 80, True)
    assert [light['name'] for light in kitchen['lights']] == ['Ceiling', 'Island pendants', 'Under the cabinets']
    assert kitchen['others'] == [{'id': 'switch.coffee_maker', 'name': 'Coffee maker', 'kind': 'switch', 'on': False, 'offline': False}]
    assert kitchen['speaker'] == 'media_player.kitchen' and kitchen['sounding'] is True
    porch = picture()['rooms']['porch']
    assert (porch['on'], porch['level'], porch['dims']) == (True, 100, False)       # on and no brightness: fully lit
    assert picture()['rooms']['bedroom']['sounding'] is False


def test_music_is_the_group_that_plays_named_by_room(house):
    music = picture()['music']
    assert (music['speaker'], music['playing'], music['title'], music['album']) == ('media_player.living', True, 'Paper Lanterns', 'Evening Jazz')
    assert [(s['name'], s['in']) for s in music['speakers']] == [
        ('Bedroom', False), ('Dining', False), ('Kitchen', True), ('Living room', True), ('Robin’s room', False)]


def test_a_speaker_is_found_by_its_name_and_a_sleeping_laptop_is_not_the_houses(house):
    found = picture()
    assert found['rooms']['dining']['speaker'] == 'media_player.dining'      # Home Assistant gave it no room; its name did
    assert 'A laptop' not in [s['name'] for s in found['music']['speakers']]
    house.states['media_player.kitchen']['state'] = 'unavailable'            # a room's own speaker gone quiet is still said
    assert ('Kitchen', True) in [(s['name'], s['offline']) for s in picture()['music']['speakers']]


def test_facts_are_the_alarm_what_stands_open_and_the_weather(house):
    assert picture()['facts'] == [{'label': 'Alarm', 'value': 'Off', 'tone': ''},
                                  {'label': 'Doors and windows', 'value': 'All shut', 'tone': ''},
                                  {'label': 'Outside', 'value': '52°', 'tone': ''}]
    house.states['binary_sensor.back_door']['state'] = 'on'
    assert picture()['facts'][1] == {'label': 'Doors and windows', 'value': 'Back door', 'tone': 'warn'}
    house.states['binary_sensor.kitchen_window']['state'] = 'on'
    assert picture()['facts'][1]['value'] == '2 open'


def test_scenes_are_home_assistants_plus_all_off(house):
    assert [s['name'] for s in picture()['scenes']] == ['Bedtime', 'Dinner', 'Movie night', 'All off']


def test_a_house_that_cant_be_read_says_so_plainly(house):
    house.down = True
    found = picture()
    assert (found['ok'], found['why'], found['signed_in']) == (False, 'The Home Assistant broker on this Mac isn’t answering.', False)


def test_a_house_without_floors_is_one_unnamed_list(house, monkeypatch):
    monkeypatch.setattr(app_world, 'FLOORS', [])
    found = picture()
    assert [f['name'] for f in found['floors']] == [''] and len(found['floors'][0]['rooms']) == 12


def test_nothing_new_about_the_house_reaches_ask(house):
    """The Home screen's extra facts stay on the Home screen: the lookup shares what it always did."""
    from hub.engine import catalog
    house.states['binary_sensor.back_door']['state'] = 'on'
    read.clear_cache()
    lookups = {l.slug: l for l in catalog.lookups()}
    shared = json.dumps(lookups['home_status'].run())
    assert 'Back door' not in shared and 'binary_sensor' not in shared and '52°' not in shared


# ---- Taps ----

def tap(path, body=None, headers=SIGNED_IN):
    return client.post('/app/api/' + path, json=body or {}, headers=headers)


def test_a_change_needs_a_signed_in_device(house):
    answer = tap('room/kitchen', {'on': False}, headers={'origin': 'http://testserver'})
    assert answer.status_code == 403 and 'phone or laptop' in answer.json()['error']
    assert house.sent == []
    assert tap('room/kitchen', {'on': False}, headers={**SIGNED_IN, 'origin': 'https://elsewhere.example'}).status_code == 403
    assert tap('room/kitchen', {'on': False}, headers={'origin': 'http://testserver', 'tailscale-user-login': 'x@example.com'}).status_code == 403
    assert house.sent == []


def test_a_room_goes_off_in_one_request_and_undo_puts_each_lamp_back(house):
    answer = tap('room/kitchen', {'on': False}).json()
    assert answer['ok'] and answer['batch']
    assert house.sent == [('light/turn_off', {'entity_id': ['light.kitchen_ceiling', 'light.kitchen_pendants', 'light.kitchen_cabinets']})]
    assert picture()['rooms']['kitchen']['on'] is False
    assert tap('undo/' + answer['batch']).json()['ok']
    kitchen = picture()['rooms']['kitchen']
    assert [(light['on'], light['level']) for light in kitchen['lights']] == [(True, 80), (True, 70), (True, 90)]
    assert len(control.recent()) == 2


def test_a_room_comes_on_and_only_its_dark_lamps_are_asked(house):
    assert tap('room/living-room', {'on': True}).json()['ok']
    assert house.sent == [('light/turn_on', {'entity_id': 'light.living_shelves'})]
    assert tap('room/living-room', {'on': True}).json() == {'error': 'Those lights are already like that.'}


def test_a_rooms_level_dims_the_lit_lamps_that_can_dim(house):
    assert tap('room/living-room', {'level': 30}).json()['ok']
    assert house.sent == [('light/turn_on', {'entity_id': ['light.living_floor_lamp', 'light.living_reading'], 'brightness_pct': 30})]
    assert picture()['rooms']['living-room']['level'] == 30
    house.sent.clear()
    assert tap('room/office', {'level': 50}).json()['ok']        # a dark room: every lamp that dims comes on at that level
    assert house.sent == [('light/turn_on', {'entity_id': ['light.office_ceiling', 'light.office_desk'], 'brightness_pct': 50})]
    house.sent.clear()
    assert tap('room/yard', {'level': 40}).json()['ok']          # nothing there dims: a level means on
    assert house.sent == [('light/turn_on', {'entity_id': 'light.yard_path'})]
    assert tap('room/kitchen', {'level': 0}).json()['ok'] and picture()['rooms']['kitchen']['on'] is False


def test_one_lamp_and_one_plug(house):
    assert tap('device/light.kitchen_ceiling', {'level': 20}).json()['ok']
    assert tap('device/switch.coffee_maker', {'on': True}).json()['ok']
    assert tap('device/light.bath_vanity', {'on': True}).json()['ok']
    assert house.sent == [('light/turn_on', {'entity_id': 'light.kitchen_ceiling', 'brightness_pct': 20}),
                          ('switch/turn_on', {'entity_id': 'switch.coffee_maker'}), ('light/turn_on', {'entity_id': 'light.bath_vanity'})]
    assert tap('device/lock.front_door', {'on': False}).status_code == 409 and len(house.sent) == 3
    assert tap('device/light.kitchen_ceiling', {'level': 'bright'}).status_code == 400


def test_all_off_is_every_lamp_lit_indoors_in_one_request(house):
    assert [s for s in picture()['scenes'] if s['id'] == 'all-off'][0]['on'] is False
    assert tap('scene/all-off').json()['ok']
    assert len(house.sent) == 1 and house.sent[0][0] == 'light/turn_off' and len(house.sent[0][1]['entity_id']) == 8
    found = picture()
    # The porch is on a floor Home Assistant calls Outside: its light is left to whatever switches it at dusk.
    assert [r for r in found['rooms'] if found['rooms'][r]['on']] == ['porch'] and found['rooms']['porch']['outside'] is True
    assert [s for s in found['scenes'] if s['id'] == 'all-off'][0]['on'] is True
    assert tap('scene/all-off').json() == {'error': 'Every light indoors is already off.'}


def test_the_owner_can_name_rooms_that_are_outdoors(house, monkeypatch):
    monkeypatch.setattr(app_world, 'FLOORS', [])
    assert not any(r['outside'] for r in picture()['rooms'].values())
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'app': {'outside': ['Porch', 'Yard']}}))
    assert sorted(r for r, room in picture()['rooms'].items() if room['outside']) == ['porch', 'yard']
    assert tap('scene/all-off').json()['ok'] and len(house.sent[0][1]['entity_id']) == 8


def test_a_room_switched_back_on_comes_back_as_bright_as_it_was(house):
    picture()                                       # Hub has seen the kitchen lit: 80, 70 and 90
    assert tap('room/kitchen', {'on': False}).json()['ok']
    house.sent.clear()
    answer = tap('room/kitchen', {'on': True}).json()
    assert answer['ok'] and answer['batch']
    assert sorted(house.sent, key=str) == sorted([
        ('light/turn_on', {'entity_id': 'light.kitchen_ceiling', 'brightness_pct': 80}),
        ('light/turn_on', {'entity_id': 'light.kitchen_pendants', 'brightness_pct': 70}),
        ('light/turn_on', {'entity_id': 'light.kitchen_cabinets', 'brightness_pct': 90})], key=str)
    assert [light['level'] for light in picture()['rooms']['kitchen']['lights']] == [80, 70, 90]
    levels.forget()                                 # and it is still known after Hub restarts
    assert levels.last('light.kitchen_pendants') == 70 and levels.last('light.office_desk') is None


def test_a_home_assistant_scene_runs(house):
    assert tap('scene/scene.movie_night').json()['ok']
    assert house.sent == [('scene/turn_on', {'entity_id': 'scene.movie_night'})]
    assert picture()['rooms']['living-room']['level'] == 15 and picture()['rooms']['kitchen']['on'] is False


def test_music_taps_pause_set_a_volume_and_bring_a_room_in(house):
    assert tap('music/media_player.kitchen', {'action': 'pause'}).json()['ok']
    assert house.sent[-1] == ('media_player/media_pause', {'entity_id': 'media_player.living'})      # through the leader
    assert picture()['music']['playing'] is False
    assert tap('music/media_player.living', {'action': 'play'}).json()['ok']
    assert tap('music/media_player.living', {'action': 'volume', 'target': 'media_player.kitchen', 'value': 12}).json()['ok']
    assert house.sent[-1] == ('media_player/volume_set', {'entity_id': 'media_player.kitchen', 'volume_level': 0.12})
    assert tap('music/media_player.living', {'action': 'add', 'target': 'media_player.dining'}).json()['ok']
    assert [s['name'] for s in picture()['music']['speakers'] if s['in']] == ['Dining', 'Kitchen', 'Living room']
    assert tap('music/media_player.living', {'action': 'remove', 'target': 'media_player.dining'}).json()['ok']
    assert tap('music/media_player.living', {'action': 'louder'}).status_code == 409


def test_a_quiet_room_plays_its_own_music_and_the_rest_plays_on(house):
    house.names = {'library://playlist/7': 'Rainy Day'}
    pick = {'uri': 'library://playlist/7', 'kind': 'playlist', 'name': 'Rainy Day', 'here': True}
    assert tap('play/media_player.bedroom', pick).json()['ok']
    music = picture()['music']
    assert (music['speaker'], music['title'], music['playing']) == ('media_player.living', 'Paper Lanterns', True)   # the strip stays
    assert [(s['speaker'], s['rooms']) for s in music['also']] == [('media_player.bedroom', ['Bedroom'])]
    assert [s['name'] for s in music['speakers'] if s['other']] == ['Bedroom']
    assert house.states['media_player.bedroom']['attributes']['volume_level'] == control.QUIET        # from silence, softly


def test_playing_here_takes_a_room_out_of_the_music_it_shared(house):
    house.names = {'library://playlist/7': 'Rainy Day'}
    pick = {'uri': 'library://playlist/7', 'kind': 'playlist', 'name': 'Rainy Day', 'here': True}
    assert tap('play/media_player.kitchen', pick).json()['ok']
    assert ('media_player/unjoin', {'entity_id': 'media_player.kitchen'}) in house.sent
    music = picture()['music']
    assert [s['name'] for s in music['speakers'] if s['in']] == ['Living room'] and music['title'] == 'Paper Lanterns'
    assert [s['rooms'] for s in music['also']] == [['Kitchen']]


def test_a_change_that_never_reached_the_house_is_not_called_done(house):
    house.down = True
    answer = tap('room/kitchen', {'on': False})
    assert answer.status_code == 409 and 'isn’t answering' in answer.json()['error']


def test_the_broker_fence_takes_a_room_of_lights_but_nothing_else_by_the_list(house):
    assert broker._checked('light', 'turn_off', {'entity_id': ['light.a', 'light.b']})
    assert broker._checked('fan', 'turn_on', {'entity_id': ['fan.a']})
    for domain, service, data in (('media_player', 'media_pause', {'entity_id': ['media_player.a', 'media_player.b']}),
                                  ('scene', 'turn_on', {'entity_id': ['scene.a']}),
                                  ('light', 'turn_off', {'entity_id': ['light.a', 'switch.b']}),
                                  ('light', 'turn_off', {'entity_id': ['light.a', 'light.a']}),
                                  ('light', 'turn_off', {'entity_id': []}),
                                  ('light', 'turn_on', {'entity_id': ['light.a', 'light.b'], 'hs_color': [10, 10]}),
                                  ('light', 'turn_off', {'entity_id': [f'light.l{n}' for n in range(41)]}),
                                  ('light', 'turn_off', {'entity_id': ['light.a', {'x': 1}]})):
        with pytest.raises(ValueError):
            broker._checked(domain, service, data)


# ---- Scenes Hub keeps ----

def test_the_house_as_it_stands_is_kept_as_a_scene_and_run_with_one_undo(house):
    kept = tap('scenes', {'name': 'Dinner time', 'rooms': ['kitchen', 'dining'], 'rest_off': False})
    assert kept.json() == {'ok': True, 'said': 'Dinner time is kept'} and house.sent == []      # keeping changes nothing
    scenes = picture()['scenes']
    assert scenes[0] == {'id': 'hub:1', 'name': 'Dinner time', 'on': True, 'mine': True}
    assert [s['name'] for s in scenes][-1] == 'All off'
    tap('room/kitchen', {'on': False}), tap('device/light.dining_pendant', {'level': 20}), tap('room/office', {'on': True})
    assert picture()['scenes'][0]['on'] is False
    house.sent.clear()
    ran = tap('scene/hub:1').json()
    assert ran['ok'] and ran['batch']
    # Only what differs is asked: three kitchen lamps back to their own levels and one pendant. The sideboard lamp,
    # already right, and the office, not part of the scene, are left alone.
    assert sorted(house.sent, key=str) == sorted([
        ('light/turn_on', {'entity_id': 'light.kitchen_pendants', 'brightness_pct': 70}),
        ('light/turn_on', {'entity_id': 'light.kitchen_ceiling', 'brightness_pct': 80}),
        ('light/turn_on', {'entity_id': 'light.kitchen_cabinets', 'brightness_pct': 90}),
        ('light/turn_on', {'entity_id': 'light.dining_pendant', 'brightness_pct': 60})], key=str)
    found = picture()
    assert found['scenes'][0]['on'] is True and found['rooms']['office']['on'] is True
    assert tap('scene/hub:1').json() == {'error': 'The house is already like that.'}
    assert tap('undo/' + ran['batch']).json()['ok']
    assert picture()['rooms']['kitchen']['on'] is False and picture()['scenes'][0]['on'] is False


def test_a_scene_can_turn_everything_else_off(house):
    tap('room/robin-s-room', {'level': 10})
    assert tap('scenes', {'name': 'Bedtime story', 'rooms': ['robin-s-room', 'hallway'], 'rest_off': True}).json()['ok']
    assert picture()['scenes'][0]['on'] is False                    # other rooms are still lit
    house.sent.clear()
    assert tap('scene/hub:1').json()['ok']
    assert len(house.sent) == 1 and house.sent[0][0] == 'light/turn_off' and len(house.sent[0][1]['entity_id']) == 7
    found = picture()
    assert sorted(r for r in found['rooms'] if found['rooms'][r]['on']) == ['porch', 'robin-s-room']     # outdoors is left alone
    assert found['scenes'][0]['on'] is True


def test_scenes_are_replaced_by_name_forgotten_and_few(house, monkeypatch):
    assert tap('scenes', {'name': 'Evening', 'rooms': ['kitchen']}).json()['ok']
    tap('room/kitchen', {'level': 30})
    assert tap('scenes', {'name': 'Evening', 'rooms': ['kitchen']}).json()['ok']
    scenes = [s for s in picture()['scenes'] if s['mine']]
    assert len(scenes) == 1 and scenes[0]['on'] is True
    assert tap('scenes', {'name': '  ', 'rooms': ['kitchen']}).json() == {'error': 'Give the scene a name.'}
    assert tap('scenes', {'name': 'Nowhere', 'rooms': ['attic']}).json() == {'error': 'Pick at least one room.'}
    assert tap('scenes', {'name': 'Mine', 'rooms': ['kitchen']}, headers={'origin': 'http://testserver'}).status_code == 403
    from hub.app import scenes
    monkeypatch.setattr(scenes, 'MOST', 1)
    assert 'Forget one first' in tap('scenes', {'name': 'Another', 'rooms': ['kitchen']}).json()['error']
    assert tap('scenes/hub:1/forget').json()['ok']
    assert [s for s in picture()['scenes'] if s['mine']] == []
    assert tap('scene/hub:1').json() == {'error': 'That scene isn’t kept any more.'}
    assert tap('scene/hub:x').status_code == 409


def test_taps_have_their_own_limit_and_a_room_counts_once(house, monkeypatch):
    monkeypatch.setattr(control, 'TAPS_PER_MINUTE', 3)
    assert tap('scene/all-off').json()['ok']                         # nine lamps, one tap
    assert tap('room/kitchen', {'on': True}).json()['ok']
    assert tap('device/light.kitchen_ceiling', {'level': 40}).json()['ok']
    assert 'in the last minute' in tap('room/kitchen', {'on': False}).json()['error']
    assert len(control._recent) == 0                                 # Ask's own limit is untouched


# ---- The page and its files ----

def test_the_page_names_its_built_files_by_version_and_they_may_be_kept(house):
    assert client.get('/app', follow_redirects=False).headers['location'] == '/app/'
    page = client.get('/app/')
    assert page.status_code == 200 and '<div id="app">' in page.text and page.headers['cache-control'] == 'no-store'
    assert "default-src 'self'" in page.headers['content-security-policy'] and '<script>' not in page.text
    versions = __import__('hub.app.routes', fromlist=['versions']).versions()
    assert f'/static/app/app.js?v={versions["js"]}' in page.text and f'/static/app/app.css?v={versions["css"]}' in page.text
    assert client.get('/static/app/app.js', params={'v': versions['js']}).headers['cache-control'] == 'public, max-age=31536000, immutable'
    assert client.get('/static/app/app.js').headers['cache-control'] == 'no-store'
    assert client.get('/static/style.css').headers['cache-control'] == 'no-store'
    assert client.get('/static/app/nothing.js', params={'v': '1'}).headers['cache-control'] == 'no-store'


def test_hub_opens_on_the_app_unless_the_owner_turns_it_off(house):
    assert client.get('/', follow_redirects=False).headers['location'] == '/app/'
    assert client.get('/manifest.webmanifest').json()['start_url'] == '/app/'
    assert '<div id="app">' in client.get('/').text
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'app': {'front': False}}))
    assert 'id="ask-root"' in client.get('/').text and client.get('/manifest.webmanifest').json()['start_url'] == '/'
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'app': {'front': True}}))
    assert client.get('/', follow_redirects=False).headers['location'] == '/app/'


# ---- The live feed ----

def test_the_feed_sends_a_picture_once_and_again_only_when_it_changes():
    pictures = iter([{'n': 1}, {'n': 1}, {'n': 2}, {'n': 2}])
    feed = feeds.Feed(read=lambda: next(pictures, {'n': 2}), every=0.02)

    async def listen():
        queue = feed.listen(asyncio.get_running_loop())
        first = await asyncio.wait_for(queue.get(), 2)
        second = await asyncio.wait_for(queue.get(), 2)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), 0.2)
        feed.leave(queue)
        return first, second

    assert asyncio.run(listen()) == ('{"n":1}', '{"n":2}')
    assert feed.last() == '{"n":2}'


def test_a_poke_reads_again_at_once():
    reads = []
    feed = feeds.Feed(read=lambda: reads.append(time.monotonic()) or {'n': len(reads)}, every=30)

    async def listen():
        queue = feed.listen(asyncio.get_running_loop())
        await asyncio.wait_for(queue.get(), 2)
        feed.poke()
        await asyncio.wait_for(queue.get(), 2)
        feed.leave(queue)

    asyncio.run(listen())
    assert len(reads) >= 2 and reads[1] - reads[0] < 2


def test_a_change_the_broker_reports_is_read_at_once_and_the_timer_relaxes(monkeypatch):
    """With the broker passing changes on, the house is read when it changes, not every few seconds."""
    reads, said = [], threading.Event()

    def hear():
        yield None                      # a beat: the line is up
        said.wait(5)
        yield 'light.kitchen_ceiling'   # a wall switch
        threading.Event().wait(5)

    feed = feeds.Feed(read=lambda: reads.append(time.monotonic()) or {'n': len(reads)}, every=0.05, hear=hear, relaxed=30)

    async def listen():
        queue = feed.listen(asyncio.get_running_loop())
        await asyncio.wait_for(queue.get(), 2)
        await asyncio.sleep(0.4)
        quiet = len(reads)              # on the 0.05 s timer this would be many; relaxed, it is the first few
        said.set()
        await asyncio.wait_for(queue.get(), 2)
        live = feed.listening_live()
        feed.leave(queue)
        return quiet, live

    quiet, live = asyncio.run(listen())
    assert live and quiet <= 3 and len(reads) >= quiet + 1


def test_without_a_broker_that_listens_the_house_is_read_on_the_timer():
    def hear():
        raise broker.HomeDown('The Home Assistant broker isn’t passing changes on.')
        yield

    reads = []
    feed = feeds.Feed(read=lambda: reads.append(1) or {'n': len(reads)}, every=0.03, hear=hear, retry=30)

    async def listen():
        queue = feed.listen(asyncio.get_running_loop())
        for _ in range(4):
            await asyncio.wait_for(queue.get(), 2)
        feed.leave(queue)

    asyncio.run(listen())
    assert not feed.listening_live() and len(reads) >= 4


def test_listening_to_the_broker_is_read_only_and_names_devices_not_states(monkeypatch):
    """The line Hub opens asks only to listen, for house devices; what comes back is a name or a beat."""
    import socket as sockets
    import tempfile
    path = tempfile.mkdtemp(prefix='hb', dir='/tmp') + '/b.sock'    # a socket's path must be short; macOS's temp folder isn't
    monkeypatch.setattr(config, 'HA_BROKER_SOCKET', path)
    server = sockets.socket(sockets.AF_UNIX, sockets.SOCK_STREAM)
    try:    # a real socket in a temp folder; the installed-package check forbids listening
        server.bind(path)
    except AssertionError:
        server.close()
        pytest.skip('needs a local socket, which this run does not allow')
    server.listen(1)
    asked = []

    def serve():
        conn, _ = server.accept()
        asked.append(json.loads(conn.makefile('rb').readline()))
        for line in ({'ok': True, 'result': 'listening'}, {'changed': 'light.a'}, {'beat': 1}, {'changed': 'media_player.b'},
                     {'ok': False, 'error': 'Home Assistant closed the connection.'}):
            conn.sendall(json.dumps(line).encode() + b'\n')
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setattr(sockets.socket, 'connect', REAL_CONNECT)
    heard = []
    with pytest.raises(broker.HomeDown):
        for changed in broker.listen():
            heard.append(changed)
    server.close()
    assert heard == ['light.a', None, 'media_player.b']
    assert asked == [{'op': 'listen', 'domains': list(broker.LISTEN)}]
    assert not {'person', 'camera', 'device_tracker'} & set(broker.LISTEN)


def test_a_broker_that_isnt_there_is_a_plain_no(monkeypatch):
    import socket as sockets
    import tempfile
    monkeypatch.setattr(config, 'HA_BROKER_SOCKET', tempfile.mkdtemp(prefix='hb') + '/absent.sock')
    monkeypatch.setattr(sockets.socket, 'connect', REAL_CONNECT)
    try:
        with pytest.raises(broker.NotSent):
            next(broker.listen())
    except AssertionError:      # the installed-package check forbids even trying to connect
        pytest.skip('needs to try a local socket, which this run does not allow')


def test_the_live_address_streams_the_picture_then_beats_while_nothing_changes(house, monkeypatch):
    from hub.app import routes
    monkeypatch.setattr(routes, 'BEAT', 0.05)
    with client.stream('GET', '/app/api/live') as stream:
        lines = stream.iter_lines()
        assert next(lines) == 'event: house'
        assert json.loads(next(lines).removeprefix('data: '))['rooms']['kitchen']['on'] is True
        assert stream.headers['content-type'].startswith('text/event-stream')
        assert 'event: beat' in [next(lines) for _ in range(12)]
