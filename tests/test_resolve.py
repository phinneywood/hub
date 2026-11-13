"""Naming things in the house: a room wins over a device with the same name (owner, 2026-11-13).
A made-up household: the living room has a light called "Living room" as well as two other lights."""
import pytest

from hub.engine import catalog
from hub.tools.house import broker, control, read
from tests import golden_world

ME = 'me@example.com'
ROOM = {'light.reading_lamp', 'light.living_ceiling', 'light.living_room'}


@pytest.fixture
def house(monkeypatch):
    world = golden_world.install(monkeypatch)
    sent = []
    monkeypatch.setattr(broker, '_exchange', lambda m, t: sent.append(m['body']) or [] if m['op'] == 'request' else world.exchange(m, t))
    return sent


def names(ids):
    found = control.devices(read.snapshot())
    return sorted(found[e]['name'] for e in ids)


@pytest.mark.parametrize('said', ['Living Room', 'living room', 'the living room', 'Living Room lights', 'the living room light'])
def test_a_room_name_means_every_light_in_the_room(house, said):
    assert set(control.resolve(said, read.POWER)) == ROOM
    assert set(control.resolve(said, ('light',))) == ROOM          # brightness, too


def test_a_device_is_reached_by_a_name_no_room_has(house):
    assert names(control.resolve('Reading Lamp', read.POWER)) == ['Reading Lamp']
    assert names(control.resolve('the kitchen pendant', read.POWER)) == ['Kitchen Pendant']
    assert names(control.resolve('coffee maker', read.POWER)) == ['Coffee Maker']
    assert control.resolve('light.living_room', read.POWER) == ['light.living_room']     # and always by its id
    assert names(control.resolve('ceiling', read.POWER)) == ['Ceiling Light']              # one near match
    assert len(control.resolve('all lights', read.POWER)) == 5


def test_an_unknown_name_asks_which_one_and_scenes_go_by_name(house):
    with pytest.raises(control.Refused, match='Which one'):
        control.resolve('garage', read.POWER)
    assert names(control.resolve('Movie Night', ('scene',))) == ['Movie Night']


def test_speakers_follow_the_same_rule(house):
    assert names(control.resolve('Living Room', ('media_player',))) == ['Living Room Speaker']
    assert names(control.resolve('the kitchen', ('media_player',))) == ['Kitchen Speaker']
    assert names(control.resolve('Bedroom Speaker', ('media_player',))) == ['Bedroom Speaker']


def test_turning_off_the_living_room_turns_off_all_of_it(house):
    found = catalog.home_control(ME, 'Living Room', 'off')
    assert {b['entity_id'] for b in house} == ROOM and len(found['done']) == 3
    house.clear()
    catalog.home_control(ME, 'Reading Lamp', 'brightness', brightness_percent=40)
    assert house == [{'entity_id': 'light.reading_lamp', 'brightness_pct': 40}]


def test_play_music_in_a_room_uses_that_rooms_speaker(house):
    found = catalog.play_music(ME, 'living room', 'play_media', what='calm piano', soft=True)
    assert found['done'] and {b['entity_id'] for b in house} == {'media_player.living_room'}
