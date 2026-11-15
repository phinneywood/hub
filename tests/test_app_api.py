"""The app's data and taps (hub/app): the Home screen's picture, the live feed and every change. A made-up house."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from hub import config
from hub.app import feed as feeds
from hub.app import model
from hub.main import app
from hub.tools.house import broker, control, read
from tests import app_world

client = TestClient(app)
SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}


@pytest.fixture
def house(monkeypatch):
    control._taps.clear()
    control._recent.clear()
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


def test_all_off_is_every_lit_lamp_in_as_few_requests_as_it_takes(house):
    assert tap('scene/all-off').json()['ok']
    assert len(house.sent) == 1 and house.sent[0][0] == 'light/turn_off' and len(house.sent[0][1]['entity_id']) == 9
    assert picture()['lights_on'] == 0
    assert tap('scene/all-off').json() == {'error': 'Every light is already off.'}


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


def test_hub_opens_on_the_app_only_once_the_owner_says_so(house):
    assert 'id="app"' not in client.get('/').text
    assert client.get('/app/api/home', headers=SIGNED_IN).json()['initial'] == 'M'
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'app': {'front': True}}))
    assert client.get('/', follow_redirects=False).headers['location'] == '/app/'
    assert '<div id="app">' in client.get('/').text


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


def test_the_live_address_streams_the_picture_then_beats_while_nothing_changes(house, monkeypatch):
    from hub.app import routes
    monkeypatch.setattr(routes, 'BEAT', 0.05)
    with client.stream('GET', '/app/api/live') as stream:
        lines = stream.iter_lines()
        assert next(lines) == 'event: house'
        assert json.loads(next(lines).removeprefix('data: '))['rooms']['kitchen']['on'] is True
        assert stream.headers['content-type'].startswith('text/event-stream')
        assert 'event: beat' in [next(lines) for _ in range(12)]
