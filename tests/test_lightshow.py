"""The light show, Hub's side (hub/tools/house/lightshow.py): found by name in a made-up house, its card's picture,
and the only three things Hub may do with it. A made-up house and a made-up show."""
import pytest
from fastapi.testclient import TestClient

from hub.app import levels
from hub.main import app
from hub.tools.house import broker, control, lightshow, read
from tests import app_world

client = TestClient(app)
SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
SHOW = app_world.SHOW
FOUND = 'apple_music://playlist/p.sunday-disco'


@pytest.fixture
def house(monkeypatch):
    control._taps.clear()
    control._recent.clear()
    levels.forget()
    lightshow.forget()
    world = app_world.install(monkeypatch)
    monkeypatch.setattr(lightshow, 'CHECK_WAIT', 0)
    return world


def with_show(world, **kw):
    app_world.add_show(world, **kw)
    lightshow.forget()
    read.clear_cache()


def picture():
    read.clear_cache()
    return client.get('/app/api/home').json()


def sent(world, domain):
    return [body for what, body in world.sent if what.startswith(domain + '/')]


# ---- found by name, and only when it is all there ----

def test_a_house_without_a_show_has_none_and_reads_as_before(house):
    assert read.snapshot(fresh=True)['show'] is None
    assert picture()['show'] is None


def test_the_show_is_found_by_its_names_and_its_start_script(house):
    with_show(house, timing=150)
    show = read.snapshot(fresh=True)['show']
    assert show == {'start': SHOW['start'], 'end': SHOW['end'], 'flag': SHOW['flag'], 'player': SHOW['player'], 'on': False,
                    'timing': {'id': SHOW['timing'], 'value': 150.0, 'min': -1500.0, 'max': 1500.0, 'step': 50.0}}
    assert picture()['show'] is None                  # off: no card


def test_two_scripts_with_one_name_or_a_start_script_that_names_two_speakers_is_no_show(house):
    with_show(house)
    house.states['script.another'] = {'entity_id': 'script.another', 'state': 'off', 'attributes': {'friendly_name': 'Start light show'}}
    assert read.snapshot(fresh=True)['show'] is None
    del house.states['script.another']
    house.configs['script'][SHOW['start'].partition('.')[2]]['sequence'].append(
        {'action': 'music_assistant.play_media', 'target': {'entity_id': 'media_player.kitchen'}})
    lightshow.forget()
    assert read.snapshot(fresh=True)['show'] is None


def test_a_start_script_that_cant_be_read_never_breaks_the_house_read(house, monkeypatch):
    with_show(house)
    def refused(*args, **kw):
        raise broker.HomeDown('Home Assistant couldn’t be read just now.')
    monkeypatch.setattr(broker, 'setup_config', refused)
    found = read.snapshot(fresh=True)
    assert found['show'] is None and found['speakers']


# ---- the card ----

def test_while_a_show_is_on_its_card_has_its_music_and_timing(house):
    with_show(house, on=True, timing=-100)
    show = picture()['show']
    assert show['speaker'] == 'media_player.living' and show['playing'] and show['title'] == 'Paper Lanterns'
    assert show['volume'] is not None and show['timing'] == {'value': -100.0, 'min': -1500.0, 'max': 1500.0, 'step': 50.0}


def test_the_card_ends_the_show_and_moves_its_timing_on_its_steps_within_its_range(house):
    with_show(house, on=True)
    assert client.post('/app/api/show/timing', json={'value': 137}, headers=SIGNED_IN).json()['timing']['value'] == 150
    assert house.states[SHOW['timing']]['state'] == '150.0'
    answer = client.post('/app/api/show/timing', json={'value': 9000}, headers=SIGNED_IN).json()
    assert answer['timing']['value'] == 1500 and answer['said'] == 'Light show lights 1500 ms earlier'
    assert client.post('/app/api/show/timing', json={'value': -50}, headers=SIGNED_IN).json()['said'] == 'Light show lights 50 ms later'
    assert client.post('/app/api/show/timing', json={'value': 'soon'}, headers=SIGNED_IN).json()['error']
    answer = client.post('/app/api/show/end', headers=SIGNED_IN).json()
    assert answer['ok'] and answer['batch'] is None                 # no Undo: ending the show is the way back
    assert house.states[SHOW['flag']]['state'] == 'off' and picture()['show'] is None
    assert client.post('/app/api/show/end', headers=SIGNED_IN).json()['error'] == 'No light show is on.'
    assert client.post('/app/api/show/end', headers={'origin': 'http://testserver'}).status_code in (401, 403)


def test_a_house_without_a_show_refuses_the_card(house):
    assert client.post('/app/api/show/end', headers=SIGNED_IN).json()['error'] == 'Home Assistant has no light show Hub can run.'
    assert not sent(house, 'script')


# ---- starting: only with something Music Assistant found, and only when it plays ----

def test_a_show_starts_with_something_found_and_is_recorded_without_undo(house):
    with_show(house)
    house.names[FOUND] = 'Sunday Disco'
    result = lightshow.start(FOUND, 'playlist', 'Sunday Disco', login='me@example.com', via='ask')
    assert result['done'] == ['Light show with Sunday Disco'] and not result['undo']
    assert sent(house, 'script')[-1] == {'entity_id': SHOW['start'], 'variables': {'media_id': FOUND, 'media_type': 'playlist'}}
    assert house.states[SHOW['flag']]['state'] == 'on'
    assert control.summary(result['batch'])['undo'] is False


def test_a_show_that_came_on_silent_is_never_done(house, monkeypatch):
    with_show(house)
    monkeypatch.setattr(house, '_show', lambda start, variables: None)       # the script ran; nothing came of it
    result = lightshow.start(FOUND, 'playlist', 'Sunday Disco', login='me@example.com', via='ask')
    assert not result['done'] and result['unknown'] and result['silent']


def test_a_show_never_starts_with_words_or_a_station(house):
    with_show(house)
    for uri, kind in (('some disco music', 'playlist'), ('apple_music://radio/r.1', 'radio'), (FOUND, 'track')):
        with pytest.raises(control.Refused):
            lightshow.start(uri, kind, '', login='me@example.com', via='ask')
    assert not sent(house, 'script')


def test_the_broker_lets_a_script_carry_only_something_found_and_a_number_only_a_number():
    ok = {'entity_id': 'script.x', 'variables': {'media_id': FOUND, 'media_type': 'playlist'}}
    assert broker._checked('script', 'turn_on', dict(ok)) and broker._checked('script', 'turn_on', {'entity_id': 'script.x'})
    for bad in ({**ok, 'variables': {'media_id': 'party music', 'media_type': 'playlist'}},
                {**ok, 'variables': {'media_id': FOUND, 'media_type': 'playlist', 'enqueue': 'add'}},
                {**ok, 'variables': {'media_id': 'apple_music://radio/r.1', 'media_type': 'radio'}},
                {**ok, 'entity_id': 'automation.x'}, {**ok, 'data': {}}):
        with pytest.raises(ValueError):
            broker._checked('script', 'turn_on', bad)
    assert broker._checked('input_number', 'set_value', {'entity_id': 'input_number.x', 'value': -150})
    for bad in (True, float('inf'), '150', 10 ** 7):
        with pytest.raises(ValueError):
            broker._checked('input_number', 'set_value', {'entity_id': 'input_number.x', 'value': bad})
    with pytest.raises(ValueError):
        broker._checked('script', 'turn_off', {'entity_id': 'script.x'})


def test_timing_says_where_the_lights_sit():
    assert [lightshow.timing_words(v) for v in (0, 150, -100.0)] == ['in time', '150 ms earlier', '100 ms later']
