"""Rooms are Home Assistant's own areas, reached by name or by an area label, and lights take a bounded colour
with Undo. A made-up house and a fake broker only; nothing is switched on or off."""
import pytest

from hub.engine import catalog, recipes
from hub.tools.house import broker, control, read

ME = 'me@example.com'
LABELS = [{'label_id': 'open_plan', 'name': 'Open Plan'}, {'label_id': 'downstairs', 'name': 'Downstairs'}]
AREAS = [{'area_id': 'upper_sun_room', 'name': 'Upper Sun Room', 'labels': ['open_plan']},
         {'area_id': 'reading_nook', 'name': 'Reading Nook', 'labels': ['open_plan']},
         {'area_id': 'kitchen', 'name': 'Kitchen', 'labels': ['downstairs']},
         {'area_id': 'den', 'name': 'Den', 'labels': ['downstairs']}]
DEVICES = [{'id': 'dev-ceiling', 'area_id': 'upper_sun_room'}]
ENTITIES = [{'entity_id': 'light.ceiling', 'device_id': 'dev-ceiling'},            # room from its device
            {'entity_id': 'light.globe', 'area_id': 'upper_sun_room'},
            {'entity_id': 'light.nook_lamp', 'area_id': 'reading_nook'},
            {'entity_id': 'light.kitchen_strip'},                                  # no area: room from its id
            {'entity_id': 'light.den_lamp', 'area_id': 'den'},
            {'entity_id': 'light.nook', 'area_id': 'den'}]                         # a device named like a room's word
COLOUR = ['hs', 'color_temp']


def light(entity_id, name, value, modes, **attrs):
    return {'entity_id': entity_id, 'state': value,
            'attributes': {'friendly_name': name, 'supported_color_modes': modes, **attrs}}


@pytest.fixture
def house(monkeypatch):
    states = [light('light.ceiling', 'Ceiling', 'on', COLOUR, brightness=255, color_mode='color_temp',
                    color_temp_kelvin=2700, hs_color=[30.0, 60.0]),
              light('light.globe', 'Globe', 'on', COLOUR, brightness=128, color_mode='hs', hs_color=[230.0, 100.0]),
              light('light.nook_lamp', 'Nook lamp', 'off', ['color_temp']),
              light('light.kitchen_strip', 'Strip', 'off', COLOUR),
              light('light.den_lamp', 'Den lamp', 'on', ['brightness'], brightness=64, color_mode='brightness'),
              light('light.nook', 'Nook', 'off', ['onoff'])]
    world = {'states': states, 'sent': []}
    answers = {'config/area_registry/list': AREAS, 'config/device_registry/list': DEVICES,
               'config/entity_registry/list': ENTITIES, 'config/label_registry/list': LABELS}

    def exchange(message, timeout):
        if message['op'] == 'request':
            service, data = message['path'].removeprefix('/api/services/'), message['body']
            world['sent'].append((service, data))
            item = next(s for s in states if s['entity_id'] == data['entity_id'])
            item['state'] = 'off' if service.endswith('turn_off') else 'on'
            for key in ('hs_color', 'color_temp_kelvin'):
                if key in data:
                    item['attributes'].update({key: data[key], 'color_mode': 'hs' if key == 'hs_color' else 'color_temp'})
            return []
        return [states if c['type'] == 'get_states' else answers[c['type']] for c in message['commands']]
    monkeypatch.setattr(broker, '_exchange', exchange)
    read.clear_cache()
    yield world
    read.clear_cache()


def names(ids):
    found = control.devices(read.snapshot())
    return sorted(found[e]['name'] for e in ids)


def test_rooms_come_from_areas_devices_and_entity_ids(house):
    rooms = {r['name']: sorted(d['name'] for d in r['devices']) for r in read.snapshot()['rooms']}
    assert rooms == {'Den': ['Den lamp', 'Nook'], 'Kitchen': ['Strip'], 'Reading Nook': ['Nook lamp'],
                     'Upper Sun Room': ['Ceiling', 'Globe']}


@pytest.mark.parametrize('said, lights', [
    ('Upper Sun Room', ['Ceiling', 'Globe']),          # the area's name
    ('sun room', ['Ceiling', 'Globe']),                # words of it
    ('the sun room lights', ['Ceiling', 'Globe']),
    ('open plan', ['Ceiling', 'Globe', 'Nook lamp']),    # a label: every area that has it
    ('downstairs', ['Den lamp', 'Nook', 'Strip']),
    ('kitchen', ['Strip']),
])
def test_a_room_word_reaches_the_areas_it_names(house, said, lights):
    assert names(control.resolve(said, ('light',))) == lights


def test_an_exact_device_name_beats_a_loose_room_and_filler_words_match_nothing(house):
    assert control.resolve('nook', read.POWER) == ['light.nook']                   # not the Reading Nook area
    assert names(control.resolve('reading nook', read.POWER)) == ['Nook lamp']
    with pytest.raises(control.Refused, match='Which one'):
        control.resolve('the room', read.POWER)


def test_home_status_shows_labels_and_reads_a_room_by_its_words(house):
    lookup = {l.slug: l for l in catalog.lookups()}['home_status']
    rooms = {r['room']: r for r in lookup.run()['rooms']}
    assert rooms['Upper Sun Room']['labels'] == ['Open Plan'] and rooms['Kitchen']['labels'] == ['Downstairs']
    room = lookup.run(room='sun room')
    assert room['room'] == 'Upper Sun Room'
    assert [d['can_show'] for d in room['devices']] == ['colours and whites', 'colours and whites']


def test_colour_a_room_skips_lights_that_cant_and_undo_puts_colours_back(house):
    found = catalog.home_control(ME, 'open plan', 'colour', colour='red')
    assert found['done'] == ['Ceiling red', 'Globe red'] and found['undo']          # the white-only lamp is left alone
    assert house['sent'] == [('light/turn_on', {'entity_id': 'light.ceiling', 'hs_color': [0, 100]}),
                             ('light/turn_on', {'entity_id': 'light.globe', 'hs_color': [0, 100]})]
    batch = control.recent()[0]['batch']
    house['sent'].clear()
    control.undo(batch, login=ME, via='tap')
    assert house['sent'] == [
        ('light/turn_on', {'entity_id': 'light.ceiling', 'brightness_pct': 100, 'color_temp_kelvin': 2700}),
        ('light/turn_on', {'entity_id': 'light.globe', 'brightness_pct': 50, 'hs_color': [230.0, 100.0]})]


def test_whites_reach_white_lights_and_an_off_light_goes_back_off(house):
    found = catalog.home_control(ME, 'reading nook', 'colour', colour='Warm white')
    assert found['done'] == ['Nook lamp warm white (2700 K)']
    assert house['sent'] == [('light/turn_on', {'entity_id': 'light.nook_lamp', 'color_temp_kelvin': 2700})]
    house['sent'].clear()
    control.undo(control.recent()[0]['batch'], login=ME, via='tap')
    assert house['sent'] == [('light/turn_off', {'entity_id': 'light.nook_lamp'})]
    house['sent'].clear()
    catalog.home_control(ME, 'Strip', 'colour', colour='3500 k')
    assert house['sent'] == [('light/turn_on', {'entity_id': 'light.kitchen_strip', 'color_temp_kelvin': 3500})]


@pytest.mark.parametrize('device, colour, why', [
    ('Den lamp', 'red', 'only dims'),
    ('Nook lamp', 'blue', 'only warm to cool white'),
    ('Ceiling', 'plaid', 'Say a colour'),
    ('Ceiling', '9000 K', '2000 to 6500'),
    ('Ceiling', None, 'Say a colour'),
])
def test_a_colour_a_light_cant_show_is_refused_and_nothing_is_sent(house, device, colour, why):
    found = catalog.home_control(ME, device, 'colour', colour=colour)
    assert why in found['refused'] and house['sent'] == []


@pytest.mark.parametrize('data', [
    {'rgb_color': [0, 0, 256]}, {'rgb_color': [0, 0]}, {'rgb_color': [0.5, 0, 0]}, {'hs_color': [400, 50]},
    {'xy_color': [0.3, 1.2]}, {'color_temp_kelvin': 1000}, {'color_temp_kelvin': True},
    {'hs_color': [0, 100], 'color_temp_kelvin': 2700}, {'effect': 'colorloop'}, {'flash': 'short'},
])
def test_the_broker_client_bounds_every_colour(house, data):
    with pytest.raises(ValueError):
        broker.act('light', 'turn_on', {'entity_id': 'light.ceiling', **data})
    with pytest.raises(ValueError):
        broker.act('switch', 'turn_on', {'entity_id': 'switch.kettle', 'color_temp_kelvin': 2700})
    assert house['sent'] == []


def test_the_broker_client_lets_bounded_colours_through(house):
    for data in ({'rgb_color': [255, 0, 0]}, {'hs_color': [360, 0]}, {'xy_color': [0.3, 0.3]},
                 {'color_temp_kelvin': 6500, 'brightness_pct': 40}):
        broker.act('light', 'turn_on', {'entity_id': 'light.globe', **data})
    assert len(house['sent']) == 4


def test_a_recipe_can_set_a_rooms_colour(house):
    steps = recipes.check([{'tool': 'home_control', 'args': {'device': 'sun room', 'action': 'colour', 'colour': 'blue'}}])
    assert recipes.describe(steps[0]) == 'sun room blue'
