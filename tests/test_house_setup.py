"""Changing how the house is set up (hub/tools/house/admin.py and plans.py), on the made-up house.

Every change here goes through the real planning, the real rule for what waits for a tap, the broker's own checks
and the real record; only Home Assistant itself is made up (tests/app_world.py), and it follows what it is sent.
"""
import json
import time

import pytest

from hub.tools.house import admin, broker, control, plans, read
from tests import app_world

ME = 'me@example.com'


@pytest.fixture
def house(monkeypatch):
    control._taps.clear()
    control._recent.clear()
    return app_world.install(monkeypatch)


def plan(what, /, **args):
    return plans.PLANS[what](admin.world(fresh=True), **args)


def do(what, /, **args):
    """Plan one change and make it, as Ask does when nothing about it needs a tap, or as the owner's Approve does
    for a scene, automation or script (which only ever waits on its before-and-after card for that reason)."""
    changes = plan(what, **args)
    assert admin.guard(changes) is None, admin.guard(changes)
    return admin.carry_out(changes, login=ME, via='ask')


def rooms_of(*entities):
    found = read.snapshot(fresh=True)
    where = {d['id']: r['name'] for r in found['rooms'] for d in r['devices']}
    return [where.get(e) for e in entities]


# ---- the fence ----

def test_the_setup_door_lets_through_only_what_it_names(house):
    for command in ({'type': 'auth/long_lived_access_token', 'client_name': 'x', 'lifespan': 3650},
                    {'type': 'config/entity_registry/update', 'entity_id': 'light.kitchen_ceiling', 'new_entity_id': 'light.other'},
                    {'type': 'config/entity_registry/remove', 'entity_id': 'light.kitchen_ceiling'},
                    {'type': 'config/device_registry/remove_config_entry', 'device_id': 'dev-floor-lamp'},
                    {'type': 'config_entries/remove', 'entry_id': 'abc'}, {'type': 'backup/restore', 'backup_id': 'abc'},
                    {'type': 'supervisor/api', 'endpoint': '/host/shutdown', 'method': 'post'},
                    {'type': 'config/core/update', 'latitude': 1.0}, {'type': 'config/area_registry/delete', 'area_id': '../x'}):
        with pytest.raises(ValueError):
            broker.setup_write(command)
    for domain, service in (('shell_command', 'anything'), ('homeassistant', 'stop'), ('hassio', 'host_shutdown'), ('update', 'install'),
                            ('notify', 'notify'), ('media_player', 'play_media'), ('python_script', 'x'), ('mqtt', 'publish'),
                            ('script', '{{ states("input_text.x") }}'), ('recorder', 'purge')):
        with pytest.raises(ValueError):
            broker.setup_call(domain, service, {})
    with pytest.raises(ValueError):
        broker.setup_save('lovelace', 'x', {'a': 1})
    with pytest.raises(ValueError):
        broker.setup_save('automation', '../../x', {'a': 1})
    with pytest.raises(ValueError):
        broker.setup_call('lock', 'lock', {'entity_id': 'lock.front_door; rm'})
    assert house.setup == [] and house.sent == []


def test_home_assistants_own_no_is_told_apart_from_a_house_that_is_away(monkeypatch):
    replies = iter([{'ok': False, 'error': 'Home Assistant rejected the request: not_found'},
                    {'ok': False, 'error': 'Home Assistant returned HTTP 400; response body omitted.'},
                    {'ok': False, 'error': 'Home Assistant returned HTTP 502; response body omitted.'},
                    {'ok': False, 'error': 'Operation failed; credential-bearing diagnostics were suppressed.'}])

    class Line:
        def __init__(self, *a):
            pass

        def settimeout(self, t):
            pass

        def connect(self, path):
            pass

        def sendall(self, data):
            pass

        def makefile(self, mode):
            import io
            return io.BytesIO(json.dumps(next(replies)).encode() + b'\n')

        def close(self):
            pass

    monkeypatch.setattr(broker.socket, 'socket', Line)
    for code in ('not_found', 'HTTP 400'):
        with pytest.raises(broker.Rejected) as said:
            broker.setup_write({'type': 'config/area_registry/delete', 'area_id': 'attic'})
        assert said.value.code == code and code in str(said.value)
    for _ in range(2):
        with pytest.raises(broker.HomeDown) as said:
            broker.setup_write({'type': 'config/area_registry/delete', 'area_id': 'attic'})
        assert not isinstance(said.value, broker.Rejected)


# ---- finding ----

def test_a_thing_is_found_by_its_name_its_room_or_what_it_is(house):
    w = admin.world()
    one = lambda said, kinds=None: [t['id'] for t in admin.match(said, w, kinds)]     # noqa: E731
    assert one('floor lamp') == ['light.living_floor_lamp']
    assert one('the Living room floor lamp') == ['light.living_floor_lamp']
    assert one('kitchen ceiling') == ['light.kitchen_ceiling'] and one('ceiling in the kitchen') == ['light.kitchen_ceiling']
    assert one('thermostat') == ['climate.hallway'] and one('garage door') == ['cover.garage_door']
    assert one('bedroom fan') == ['fan.bedroom_fan'] and one('light.robin_night') == ['light.robin_night']
    assert one('kitchen lights') == ['light.kitchen_cabinets', 'light.kitchen_ceiling', 'light.kitchen_pendants']
    assert one('kitchen', kinds=('light',)) == ['light.kitchen_cabinets', 'light.kitchen_ceiling', 'light.kitchen_pendants']
    assert one('porch at sunset') == ['automation.porch_at_sunset'] and one('goodnight') == ['script.goodnight']
    with pytest.raises(admin.Refused, match=r'Which one\? Ceiling \(Entry\), Ceiling \(Guest room\)'):
        one('ceiling')
    with pytest.raises(admin.Refused, match='nothing called “hot tub”'):
        one('hot tub')
    assert admin.room('the living room', w)['id'] == 'living' and admin.room('Robin’s room', w)['id'] == 'robin'
    with pytest.raises(admin.Refused, match='no room called “Attic”. The rooms are: Bath, Bedroom'):
        admin.room('Attic', w)


def test_what_a_model_is_shown_leaves_out_where_people_are_and_anything_secret(house):
    house.states['person.alex'] = {'entity_id': 'person.alex', 'state': 'not_home', 'attributes': {
        'friendly_name': 'Alex', 'latitude': 47.1, 'longitude': -122.1, 'gps_accuracy': 10, 'user_id': 'u-alex'}}
    house.states['media_player.living']['attributes'].update(entity_picture='/api/media_player_proxy/x?token=abc', access_token='abc')
    admin.forget()
    assert admin.find('alex')['found'] == [{'id': 'person.alex', 'name': 'Alex', 'kind': 'person'}]
    assert admin.detail('person.alex') == {'id': 'person.alex', 'name': 'Alex', 'kind': 'person'}
    shown = admin.detail('media_player.living')
    assert shown['state'] == 'playing' and 'volume_level' in shown['attributes']
    assert not {'entity_picture', 'access_token'} & set(shown['attributes']) and 'token' not in json.dumps(shown)
    # A song's title is written by strangers: a model about to change the house isn't shown it here.
    assert 'Paper Lanterns' not in json.dumps(shown) and 'media_title' not in shown['attributes']
    assert admin.find('thermostat')['found'] == [{'id': 'climate.hallway', 'name': 'Thermostat', 'kind': 'climate', 'room': 'Hallway', 'state': 'heat'}]
    assert [f['name'] for f in admin.find(kind='automation')['found']] == ['Living room button', 'Porch at sunset']
    assert {'name': 'Kitchen', 'kind': 'room', 'id': 'kitchen', 'floor': 'Downstairs'} in admin.find(kind='room')['found']
    routine = admin.detail('porch at sunset')
    assert routine['config']['triggers'] == [{'trigger': 'sun', 'event': 'sunset'}]
    assert admin.detail('dinner')['used_by'] == ['Living room button']
    assert [t['id'] for t in admin.detail('Hallway')['things']] == ['climate.hallway', 'light.hall_ceiling']


def test_a_model_reads_home_assistants_moments_on_the_households_clock(house):
    # A scene's state is when it last ran, and an automation says when it last fired, both in UTC.
    house.states['scene.dinner']['state'] = '2026-01-05T19:09:55.123456+00:00'
    house.states['automation.porch_at_sunset']['attributes']['last_triggered'] = '2026-07-04T03:30:00+00:00'
    admin.forget()
    assert admin.detail('dinner')['state'] == 'Jan 5, 11:09 AM'
    assert {'name': 'Dinner', 'state': 'Jan 5, 11:09 AM'}.items() <= next(
        f for f in admin.find(kind='scene')['found'] if f['name'] == 'Dinner').items()
    assert admin.detail('porch at sunset')['attributes']['last_triggered'] == 'Jul 3, 8:30 PM'
    assert admin.detail('thermostat')['state'] == 'heat'


# ---- where things are and what they are called ----

def test_a_light_moves_to_another_room_and_undo_puts_it_back(house):
    result = do('arrange', things=['reading lamp'], room='bedroom')
    assert result['done'] == ['Reading lamp moved from Living room to Bedroom'] and result['undo']
    assert rooms_of('light.living_reading') == ['Bedroom']
    assert house.setup == [{'type': 'config/entity_registry/update', 'entity_id': 'light.living_reading', 'area_id': 'bedroom'}]
    assert admin.undo(result['batch'], login=ME, via='app')['done'] == ['Reading lamp put back']
    assert rooms_of('light.living_reading') == ['Living room']
    with pytest.raises(admin.Refused, match='nothing left to undo'):
        admin.undo(result['batch'], login=ME, via='app')


def test_a_lamp_that_is_its_own_device_takes_the_device_with_it(house):
    result = do('arrange', things=['floor lamp'], room='Office')
    assert result['done'] == ['Floor lamp moved from Living room to Office']
    assert house.setup == [{'type': 'config/device_registry/update', 'device_id': 'dev-floor-lamp', 'area_id': 'office'}]
    assert rooms_of('light.living_floor_lamp') == ['Office']
    assert admin.world(fresh=True)['things']['sensor.floor_lamp_signal']['area'] == 'office'       # its signal reading came along
    admin.undo(result['batch'], login=ME, via='app')
    assert rooms_of('light.living_floor_lamp') == ['Living room']


def test_one_of_two_things_on_a_device_moves_alone(house):
    house.devices.append({'id': 'dev-relay', 'name': 'Two-way relay', 'name_by_user': None, 'area_id': 'hall', 'labels': []})
    for entity in ('light.hall_ceiling', 'light.guest_ceiling'):
        house.entries[entity].update(device_id='dev-relay')
    do('arrange', things=['guest room ceiling'], room='bath')
    assert house.setup == [{'type': 'config/entity_registry/update', 'entity_id': 'light.guest_ceiling', 'area_id': 'bath'}]
    assert rooms_of('light.guest_ceiling', 'light.hall_ceiling') == ['Bath', 'Hallway']


def test_several_things_move_together_and_one_already_there_is_left(house):
    result = do('arrange', things=['dining lights', 'reading lamp'], room='living room')
    assert result['done'] == ['Pendant moved from Dining to Living room', 'Sideboard lamp moved from Dining to Living room']
    with pytest.raises(admin.Already, match='Reading lamp is already in Living room.'):
        plan('arrange', things=['reading lamp'], room='living room')
    assert do('arrange', things=['reading lamp'], room='none')['done'] == ['Reading lamp taken out of Living room']


def test_a_rename_reaches_the_device_when_the_thing_is_the_device(house):
    result = do('arrange', things=['floor lamp'], name='Arc lamp')
    assert result['done'] == ['Floor lamp renamed Arc lamp']
    assert house.setup == [{'type': 'config/device_registry/update', 'device_id': 'dev-floor-lamp', 'name_by_user': 'Arc lamp'}]
    result = do('arrange', things=['reading lamp'], name='Corner lamp')
    assert house.entries['light.living_reading']['name'] == 'Corner lamp'
    assert 'Corner lamp' in [d['name'] for r in read.snapshot(fresh=True)['rooms'] for d in r['devices']]
    admin.undo(result['batch'], login=ME, via='app')
    assert house.entries['light.living_reading']['name'] is None
    with pytest.raises(admin.Refused, match='A name is for one thing'):
        plan('arrange', things=['kitchen lights'], name='Bright')


def test_a_thing_can_be_hidden_and_shown_again(house):
    assert do('arrange', things=['shelf lights'], hidden=True)['done'] == ['Shelf lights hidden']
    assert rooms_of('light.living_shelves') == [None]
    assert do('arrange', things=['light.living_shelves'], hidden=False)['done'] == ['Shelf lights shown again']
    assert rooms_of('light.living_shelves') == ['Living room']


# ---- rooms, floors and groups ----

def test_a_room_is_added_renamed_and_put_on_a_floor(house):
    result = do('place', kind='room', do='add', name='Sun room', floor='upstairs')
    assert result['done'] == ['Room “Sun room” added on Upstairs']
    assert do('arrange', things=['reading lamp'], room='sun room')['done'] == ['Reading lamp moved from Living room to Sun room']
    assert do('place', kind='room', do='rename', name='Sun room', new_name='Conservatory')['done'] == ['Room Sun room renamed Conservatory']
    assert do('place', kind='room', do='set', name='Conservatory', floor='Downstairs')['done'] == ['Conservatory moved to Downstairs']
    assert [a for a in house.areas if a['area_id'] == 'sun_room'][0] | {} == {
        'area_id': 'sun_room', 'name': 'Conservatory', 'floor_id': 'down', 'labels': [], 'aliases': [], 'icon': None}
    with pytest.raises(admin.Already):
        plan('place', kind='room', do='add', name='kitchen')
    admin.undo(result['batch'], login=ME, via='app')                    # taking the new room away again
    assert 'sun_room' not in [a['area_id'] for a in house.areas]


def test_removing_a_room_waits_for_a_tap_and_undo_brings_it_back_with_what_was_in_it(house):
    changes = plan('place', kind='room', do='remove', name='Dining')
    assert admin.guard(changes) == 'delete' and changes[0]['words'] == 'Room “Dining” removed; its 2 things are in no room'
    result = admin.carry_out(changes, login=ME, via='app')
    assert rooms_of('light.dining_pendant') == ['Other'] and 'dining' not in [a['area_id'] for a in house.areas]
    admin.undo(result['batch'], login=ME, via='app')
    assert rooms_of('light.dining_pendant', 'light.dining_sideboard') == ['Dining', 'Dining']
    assert [a['floor_id'] for a in house.areas if a['name'] == 'Dining'] == ['down']


def test_rooms_are_grouped_and_the_group_reaches_them(house):
    result = do('place', kind='group', do='add', name='Open plan', rooms=['kitchen', 'living room'])
    assert result['done'] == ['Group “Open plan” made of Kitchen and Living room']
    assert control.rooms_named('open plan', read.snapshot(fresh=True)) == ['Kitchen', 'Living room']
    changed = do('place', kind='group', do='set', name='open plan', rooms=['kitchen', 'dining'])
    assert changed['done'] == ['Dining added to Open plan', 'Living room taken out of Open plan']
    # The entry holds the front door's lock, and a routine can work a group of rooms: putting it in one, or taking
    # it out, waits for a tap.
    joining = plan('place', kind='group', do='add', name='open plan', rooms=['entry'])
    assert admin.guard(joining) == 'security'
    assert admin.carry_out(joining, login=ME, via='app')['done'] == ['Entry added to Open plan']
    leaving = plan('place', kind='group', do='remove', name='open plan', rooms=['entry'])
    assert admin.guard(leaving) == 'security'
    assert admin.carry_out(leaving, login=ME, via='app')['done'] == ['Entry taken out of Open plan']
    with pytest.raises(admin.Already, match='Entry is already out of Open plan.'):
        plan('place', kind='group', do='remove', name='open plan', rooms=['entry'])
    removal = plan('place', kind='group', do='remove', name='Open plan')
    assert admin.guard(removal) == 'delete'
    gone = admin.carry_out(removal, login=ME, via='app')
    assert control.rooms_named('open plan', read.snapshot(fresh=True)) == []
    admin.undo(gone['batch'], login=ME, via='app')
    assert sorted(control.rooms_named('open plan', read.snapshot(fresh=True))) == ['Dining', 'Kitchen']


def test_a_floor_is_added_and_removed(house):
    assert do('place', kind='floor', do='add', name='Basement', level=-1)['done'] == ['Floor “Basement” added']
    assert do('place', kind='room', do='set', name='office', floor='basement')['done'] == ['Office moved to Basement']
    removal = plan('place', kind='floor', do='remove', name='Basement')
    assert admin.guard(removal) == 'delete'
    gone = admin.carry_out(removal, login=ME, via='app')
    assert [a['floor_id'] for a in house.areas if a['area_id'] == 'office'] == [None]
    admin.undo(gone['batch'], login=ME, via='app')
    assert [a['floor_id'] for a in house.areas if a['area_id'] == 'office'] == ['basement']


# ---- scenes ----

def test_a_scene_is_changed_and_then_sets_what_it_now_says(house):
    result = do('scene', do='save', name='Dinner', set=[{'thing': 'island pendants', 'level': 30}, {'thing': 'floor lamp', 'on': False}])
    assert result['done'] == ['Scene “Dinner” changed: Island pendants at 30% and Floor lamp off']
    control.change(['scene.dinner'], 'activate', login=ME, via='app')
    lights = {d['id']: d for r in read.snapshot(fresh=True)['rooms'] for d in r['devices']}
    assert lights['light.kitchen_pendants']['level'] == 30 and not lights['light.living_floor_lamp']['on']
    assert lights['light.kitchen_ceiling']['level'] == 80               # what the request didn't name is as it was
    admin.undo(result['batch'], login=ME, via='app')
    assert house.configs['scene']['dinner']['entities']['light.kitchen_pendants'] == {'state': 'on', 'brightness': 178}


def test_a_new_scene_keeps_rooms_as_they_are_now(house):
    result = do('scene', do='save', name='Reading', capture=['living room'], set=[{'thing': 'kitchen', 'on': False}])
    assert result['done'] == ['Scene “Reading” saved: living room as it is now, Under the cabinets off, Ceiling (Kitchen) off and Island pendants off']
    (key, kept), = [(k, v) for k, v in house.configs['scene'].items() if v['name'] == 'Reading']
    assert kept['entities']['light.living_floor_lamp'] == {'state': 'on', 'brightness': 115}
    assert kept['entities']['light.living_shelves'] == {'state': 'off'} and kept['entities']['light.kitchen_ceiling'] == {'state': 'off'}
    assert 'Reading' in [s['name'] for s in read.snapshot(fresh=True)['scenes']]
    admin.undo(result['batch'], login=ME, via='app')
    assert key not in house.configs['scene'] and 'Reading' not in [s['name'] for s in read.snapshot(fresh=True)['scenes']]


def test_a_colour_in_a_scene_is_one_the_lamp_can_show(house):
    do('scene', do='save', name='Movie night', set=[{'thing': 'floor lamp', 'level': 20, 'colour': 'warm white'}])
    assert house.configs['scene']['movie_night']['entities']['light.living_floor_lamp'] == {'state': 'on', 'brightness': 51, 'color_temp_kelvin': 2700}
    with pytest.raises(admin.Refused, match='only dims'):
        plan('scene', do='save', name='Movie night', set=[{'thing': 'reading lamp', 'colour': 'red'}])


def test_removing_a_scene_waits_says_what_uses_it_and_can_be_undone(house):
    changes = plan('scene', do='remove', name='dinner')
    assert admin.guard(changes) == 'delete' and changes[0]['words'] == 'Scene “Dinner” removed; Living room button use it'
    gone = admin.carry_out(changes, login=ME, via='app')
    assert 'scene.dinner' not in house.states
    admin.undo(gone['batch'], login=ME, via='app')
    assert house.configs['scene']['dinner']['name'] == 'Dinner' and 'scene.dinner' in house.states


def test_a_scene_changed_again_elsewhere_is_not_put_back_over_that(house):
    result = do('scene', do='save', name='Dinner', set=[{'thing': 'pendant', 'level': 10}])
    house.configs['scene']['dinner']['entities']['light.dining_pendant'] = {'state': 'on', 'brightness': 200}     # by hand, in Home Assistant
    with pytest.raises(admin.Refused, match='Dinner was changed again after Hub changed it'):
        admin.undo(result['batch'], login=ME, via='app')
    assert house.configs['scene']['dinner']['entities']['light.dining_pendant']['brightness'] == 200


def test_a_scene_written_by_hand_is_run_but_not_changed(house):
    house.states['scene.old'] = {'entity_id': 'scene.old', 'state': 'unknown', 'attributes': {'friendly_name': 'Old scene'}}
    with pytest.raises(admin.Refused, match='Old scene was written by hand'):
        plan('scene', do='save', name='old scene', set=[{'thing': 'pendant', 'level': 10}])


# ---- automations and scripts ----

EVENING = {'alias': 'Hall light in the evening', 'description': 'The hallway light comes on at 6:45 pm.', 'mode': 'single',
           'triggers': [{'trigger': 'time', 'at': '18:45:00'}], 'conditions': [],
           'actions': [{'action': 'light.turn_on', 'target': {'entity_id': 'light.hall_ceiling'}, 'data': {'brightness_pct': 30}}]}


def test_an_automation_is_made_read_back_changed_and_undone(house):
    result = do('routine', kind='automation', do='save', name='Hall light in the evening', config=EVENING)
    assert result['done'] == ['Automation “Hall light in the evening” made: at 6:45 pm, Ceiling (Hallway) at 30%']
    (key, kept), = [(k, v) for k, v in house.configs['automation'].items() if v['alias'] == EVENING['alias']]
    assert kept == {'id': key, **EVENING}
    assert admin.detail('hall light in the evening')['config']['triggers'] == EVENING['triggers']
    later = {**EVENING, 'triggers': [{'trigger': 'sun', 'event': 'sunset', 'offset': '-00:30:00'}]}
    changed = do('routine', kind='automation', do='save', name='hall light in the evening', config=later)
    assert changed['done'] == ['Automation “Hall light in the evening” changed: 30 minutes before sunset, Ceiling (Hallway) at 30%']
    admin.undo(changed['batch'], login=ME, via='app')
    assert house.configs['automation'][key]['triggers'] == EVENING['triggers']
    admin.undo(result['batch'], login=ME, via='app')
    assert key not in house.configs['automation'] and 'automation.hall_light_in_the_evening' not in house.states


def test_home_assistants_own_check_decides_whether_a_routine_can_be_read(house):
    with pytest.raises(admin.Refused, match=r"can’t read its triggers: extra keys not allowed @ data\[0\]\['nonsense'\]"):
        plan('routine', kind='automation', do='save', name='Odd', config={**EVENING, 'triggers': [{'trigger': 'time', 'nonsense': 1}]})
    with pytest.raises(admin.Refused, match='needs triggers and actions'):
        plan('routine', kind='automation', do='save', name='Odd', config={'alias': 'Odd', 'actions': EVENING['actions']})
    assert house.setup == []


def test_a_routine_may_name_devices_and_rooms_as_the_house_does(house):
    named = {**EVENING, 'alias': 'Lights when we come in', 'triggers': [{'trigger': 'state', 'entity_id': 'front door lock', 'to': 'unlocked'}],
             'actions': [{'action': 'light.turn_on', 'target': {'entity_id': ['hallway ceiling', 'light.entry_ceiling'], 'area_id': 'Living room'}},
                         {'action': 'scene.turn_on', 'target': {'entity_id': 'Dinner'}}, {'scene': 'movie night'}]}
    do('routine', kind='automation', do='save', name='Lights when we come in', config=named)
    (kept,) = [c for c in house.configs['automation'].values() if c['alias'] == 'Lights when we come in']
    assert kept['triggers'][0]['entity_id'] == 'lock.front_door'
    assert kept['actions'][0]['target'] == {'entity_id': ['light.hall_ceiling', 'light.entry_ceiling'], 'area_id': 'living'}
    assert kept['actions'][1]['target'] == {'entity_id': 'scene.dinner'} and kept['actions'][2] == {'scene': 'scene.movie_night'}
    with pytest.raises(admin.Refused, match=r'Which one\? Ceiling \(Entry\)'):
        plan('routine', kind='automation', do='save', name='Odd', config={**EVENING, 'actions': [
            {'action': 'light.turn_on', 'target': {'entity_id': 'ceiling'}}]})
    with pytest.raises(admin.Refused, match='no room called “Attic”'):
        plan('routine', kind='automation', do='save', name='Odd', config={**EVENING, 'actions': [
            {'action': 'light.turn_off', 'target': {'area_id': 'Attic'}}]})


def test_a_routine_must_name_devices_that_exist(house):
    guessed = {**EVENING, 'actions': [{'action': 'light.turn_on', 'target': {'entity_id': ['light.hallway_light', 'light.hall_ceiling']}}]}
    with pytest.raises(admin.Refused, match='There is no light.hallway_light in Home Assistant. Look it up with find'):
        plan('routine', kind='automation', do='save', name='Hall light in the evening', config=guessed)
    watching = {**EVENING, 'triggers': [{'trigger': 'state', 'entity_id': 'binary_sensor.no_such_door', 'to': 'on'}]}
    with pytest.raises(admin.Refused, match='no binary_sensor.no_such_door'):
        plan('routine', kind='automation', do='save', name='Hall light in the evening', config=watching)
    assert house.setup == []


def test_a_routine_may_only_call_what_the_setup_door_lets_through(house):
    for action in ('shell_command.tidy', 'homeassistant.stop', 'hassio.addon_stop', 'notify.notify', '{{ states("input_text.what") }}'):
        with pytest.raises(admin.Refused, match='Nothing was saved'):
            plan('routine', kind='automation', do='save', name='Odd', config={**EVENING, 'actions': [{'action': action}]})
    house.configs['automation']['a-porch']['actions'].append({'action': 'shell_command.tidy'})      # the owner's own, made in Home Assistant
    kept = {**house.configs['automation']['a-porch'], 'description': 'On at sunset, then tidy.'}
    assert do('routine', kind='automation', do='save', name='Porch at sunset', config=kept)['done']    # is left in when the rest is changed


def test_a_routine_that_unlocks_or_opens_waits_for_a_tap(house):
    unlocks = {**EVENING, 'alias': 'Let us in', 'actions': [{'action': 'lock.unlock', 'target': {'entity_id': 'lock.front_door'}}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='Let us in', config=unlocks)) == 'security'
    garage = {**EVENING, 'alias': 'Air the garage', 'actions': [{'action': 'cover.open_cover', 'target': {'entity_id': 'cover.garage_door'}}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='Air the garage', config=garage)) == 'security'
    blind = {**EVENING, 'alias': 'Morning blind', 'actions': [{'action': 'cover.open_cover', 'target': {'entity_id': 'cover.office_blind'}}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='Morning blind', config=blind)) is None
    locks = {**EVENING, 'alias': 'Lock up', 'actions': [{'action': 'lock.lock', 'target': {'entity_id': 'lock.front_door'}}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='Lock up', config=locks)) is None
    watches = {**EVENING, 'alias': 'Hall light when unlocked', 'triggers': [{'trigger': 'state', 'entity_id': 'lock.front_door', 'to': 'unlocked'}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='Hall light when unlocked', config=watches)) is None


def _owner_made(house):
    """Things the owner made in Home Assistant that open the house: a script that unlocks, a scene that holds the
    lock unlocked, and a script kept in Home Assistant's own files, which Hub can't read."""
    house.configs['script']['let_them_in'] = {'alias': 'Let them in', 'sequence': [{'action': 'lock.unlock', 'target': {'entity_id': 'lock.front_door'}}]}
    house.states['script.let_them_in'] = {'entity_id': 'script.let_them_in', 'state': 'off', 'attributes': {'friendly_name': 'Let them in'}}
    house.configs['scene']['arriving'] = {'id': 'arriving', 'name': 'Arriving', 'entities': {
        'light.entry_ceiling': {'state': 'on'}, 'lock.front_door': {'state': 'unlocked'}}}
    house.states['scene.arriving'] = {'entity_id': 'scene.arriving', 'state': 'unknown', 'attributes': {
        'friendly_name': 'Arriving', 'id': 'arriving', 'entity_id': ['light.entry_ceiling', 'lock.front_door']}}
    house.states['script.by_hand'] = {'entity_id': 'script.by_hand', 'state': 'off', 'attributes': {'friendly_name': 'By hand'}}


def test_a_routine_that_opens_the_house_through_something_else_waits_too(house):
    _owner_made(house)
    asks = lambda actions: admin.guard(plan('routine', kind='automation', do='save', name='At three', config={**EVENING, 'alias': 'At three', 'actions': actions}))  # noqa: E731
    lock = {'entity_id': 'lock.front_door'}
    for opening in ([{'action': 'script.turn_on', 'target': {'entity_id': 'script.let_them_in'}}],      # another routine that unlocks
                    [{'action': 'script.let_them_in'}],                                                   # the same, called by its name
                    [{'action': 'scene.turn_on', 'target': {'entity_id': 'scene.arriving'}}],            # a scene that holds the lock unlocked
                    [{'scene': 'scene.arriving'}],
                    [{'device_id': 'dev-front-door', 'domain': 'lock', 'type': 'unlock', **lock}],        # a device's own action, no service named
                    [{'action': 'scene.apply', 'data': {'entities': {'lock.front_door': 'unlocked'}}}],   # a scene made on the spot
                    [{'action': 'cover.open_cover', 'target': {'area_id': 'office'}}],                   # a whole room: Hub can't list what opens
                    [{'action': 'homeassistant.turn_on', 'target': {'entity_id': 'script.let_them_in'}}],
                    [{'if': [{'condition': 'state', 'entity_id': 'light.entry_ceiling', 'state': 'on'}],     # deep inside a choice
                      'then': [{'repeat': {'count': 2, 'sequence': [{'action': 'lock.unlock', 'target': lock}]}}]}]):
        assert asks(opening) == 'security', opening
    for plain in ([{'action': 'scene.turn_on', 'target': {'entity_id': 'scene.dinner'}}], [{'scene': 'scene.dinner'}],
                  [{'action': 'script.turn_on', 'target': {'entity_id': 'script.goodnight'}}],            # it locks, which is fine
                  [{'device_id': 'dev-front-door', 'domain': 'lock', 'type': 'lock', **lock}],
                  [{'action': 'scene.apply', 'data': {'entities': {'light.entry_ceiling': 'on'}}}],
                  [{'if': [{'condition': 'device', 'device_id': 'dev-front-door', 'domain': 'lock', 'type': 'is_unlocked', **lock}],
                    'then': [{'action': 'lock.lock', 'target': lock}]}]):
        assert asks(plain) is None, plain
    # What Hub can't read waits too, and the card says that, not that it touches a lock.
    blueprint = {'alias': 'From a blueprint', 'use_blueprint': {'path': 'x/y.yaml', 'input': {'door': 'lock.front_door'}}}
    assert admin.guard(plan('routine', kind='automation', do='save', name='From a blueprint', config=blueprint)) == 'unread'
    assert asks([{'action': 'script.turn_on', 'target': {'entity_id': 'script.by_hand'}}]) == 'unread'      # kept in Home Assistant's own files
    assert asks([{'action': 'script.turn_on', 'target': {'area_id': 'entry'}}]) == 'unread'                # every script in a room: Hub can't list them
    assert asks([{'action': 'script.turn_on', 'target': {'entity_id': 'script.by_hand'}},
                 {'action': 'lock.unlock', 'target': lock}]) == 'security'                                 # one that plainly opens says so
    with pytest.raises(admin.Refused, match='works a camera device'):
        plan('routine', kind='automation', do='save', name='Snap', config={**EVENING, 'actions': [
            {'device_id': 'dev-cam', 'domain': 'camera', 'type': 'snapshot'}]})


def test_running_something_that_opens_the_house_waits_however_it_is_asked(house):
    _owner_made(house)
    assert admin.guard(plan('routine', kind='script', do='run', name='let them in')) == 'security'
    assert admin.guard(plan('routine', kind='script', do='run', name='by hand')) == 'unread'
    assert admin.guard(plan('routine', kind='script', do='run', name='goodnight')) is None
    assert admin.guard(plan('act', action='script.turn_on', things=['let them in'])) == 'security'
    assert admin.guard(plan('act', action='script.let_them_in')) == 'security'
    assert admin.guard(plan('act', action='homeassistant.turn_on', things=['script.let_them_in'])) == 'security'
    assert admin.guard(plan('act', action='script.turn_on', things=['by hand'])) == 'unread'
    assert admin.guard(plan('act', action='scene.turn_on', things=['arriving'])) == 'security'
    assert admin.guard(plan('act', action='scene.turn_on', things=['dinner'])) is None
    assert admin.guard(plan('act', action='automation.trigger', things=['porch at sunset'])) is None
    wrapper = {**EVENING, 'alias': 'Open up at dawn', 'actions': [{'action': 'script.let_them_in'}]}
    saved = plan('routine', kind='automation', do='save', name='Open up at dawn', config=wrapper)
    admin.carry_out(saved, login=ME, via='app')                     # the owner tapped the card
    assert admin.guard(plan('routine', kind='automation', do='run', name='open up at dawn')) == 'security'
    assert admin.guard(plan('act', action='automation.trigger', things=['open up at dawn'])) == 'security'
    for made_on_the_spot in ('scene.apply', 'scene.create'):
        with pytest.raises(admin.Refused, match='scene_setup'):
            plan('act', action=made_on_the_spot, data={'entities': {'lock.front_door': 'unlocked'}})
    assert house.sent == [] and house.states['lock.front_door']['state'] == 'locked'


def test_a_device_action_with_nothing_named_means_the_only_one_of_its_kind(house):
    assert do('act', action='climate.set_temperature', data={'temperature': 70})['done'] == ['Thermostat set to 70°']
    with pytest.raises(admin.Refused, match='Which one\\? Ceiling fan \\(Bedroom\\), Ceiling fan \\(Office\\)'):
        plan('act', action='fan.turn_on')
    with pytest.raises(admin.Refused, match='There is no vacuum'):
        plan('act', action='vacuum.start')


def test_a_newer_home_assistant_that_no_longer_says_how_a_thing_is_named(house):
    """Since 2026 the registry's list has no has_entity_name: a thing named after its device shows in its name."""
    areas = [{'area_id': 'guest', 'name': 'Guest room', 'floor_id': None, 'labels': [], 'aliases': []}]
    devices = [{'id': 'dev-fan', 'name': 'Guest fan', 'name_by_user': None, 'area_id': 'guest', 'labels': []}]
    entry = lambda e, original=None: {'entity_id': e, 'device_id': 'dev-fan', 'area_id': None, 'name': None, 'original_name': original,   # noqa: E731
                                      'hidden_by': None, 'disabled_by': None, 'entity_category': None, 'labels': []}
    entities = [entry('fan.guest_fan'), entry('light.guest_fan'), entry('switch.guest_fan_whoosh', 'Whoosh'), entry('switch.guest_fan_old', 'Old style')]
    state = lambda e, name: {'entity_id': e, 'state': 'off', 'attributes': {'friendly_name': name}}      # noqa: E731
    states = [state('fan.guest_fan', 'Guest fan'), state('light.guest_fan', 'Guest fan'), state('switch.guest_fan_whoosh', 'Guest fan Whoosh'),
              state('switch.guest_fan_old', 'Old style')]
    w = admin._world(areas, devices, entities, [], [], states)
    assert [(t['id'], t['is_device'], t['named_alone']) for t in w['things'].values()] == [
        ('fan.guest_fan', True, False), ('light.guest_fan', True, False), ('switch.guest_fan_old', False, True),
        ('switch.guest_fan_whoosh', False, False)]
    # The fan and its light are one device under one name: "the guest fan" is the fan, and moving it moves the device.
    assert [t['id'] for t in admin.match('guest fan', w)] == ['fan.guest_fan']
    assert [t['id'] for t in admin.match('guest fan light', w)] == ['light.guest_fan']
    areas.append({'area_id': 'office', 'name': 'Office', 'floor_id': None, 'labels': [], 'aliases': []})
    w = admin._world(areas, devices, entities, [], [], states)
    (moved,) = plans.arrange(w, ['guest fan'], room='office')
    assert moved['steps'] == [{'ws': {'type': 'config/device_registry/update', 'device_id': 'dev-fan', 'area_id': 'office'}}]
    (renamed,) = plans.arrange(w, ['guest fan'], name='Quiet fan')
    assert renamed['steps'] == [{'ws': {'type': 'config/device_registry/update', 'device_id': 'dev-fan', 'name_by_user': 'Quiet fan'}}]
    (feature,) = plans.arrange(w, ['guest fan whoosh'], name='Breeze')
    assert feature['steps'] == [{'ws': {'type': 'config/entity_registry/update', 'entity_id': 'switch.guest_fan_whoosh', 'name': 'Breeze'}}]
    # Two different devices with one name are still asked about, and told apart by what they are.
    devices.append({'id': 'dev-lamp', 'name': 'Guest fan', 'name_by_user': None, 'area_id': 'guest', 'labels': []})
    entities.append({**entry('light.other'), 'device_id': 'dev-lamp'})
    states.append(state('light.other', 'Guest fan'))
    with pytest.raises(admin.Refused, match='Which one\\? Guest fan \\(Guest room\\), the fan, Guest fan \\(Guest room\\), the light, Guest fan'):
        admin.match('guest fan', admin._world(areas, devices, entities, [], [], states))


def test_a_card_that_could_not_be_carried_out_goes_on_waiting(house):
    card = admin.wait('Remove Dining?', plan('place', kind='room', do='remove', name='dining'), 'place',
                      {'kind': 'room', 'do': 'remove', 'name': 'dining'}, login=ME)
    control._taps.extend([time.monotonic()] * control.TAPS_PER_MINUTE)       # a minute with too many taps already
    with pytest.raises(admin.Refused, match='changes in the last minute'):
        admin.confirm(card['id'], login=ME)
    assert admin.waiting(card['id'])['status'] == 'waiting'
    control._taps.clear()
    assert admin.confirm(card['id'], login=ME)['done'] == ['Room “Dining” removed; its 2 things are in no room']


def test_every_change_to_what_the_house_does_by_itself_waits_for_approve(house):
    """Owner decision (2026-11-20): Ask proposes each change to a scene, automation or script on a card with its
    before and after, and only Approve makes it. Running one is not a change to it. A reason of the house's own
    safety still decides the card's words."""
    config = {'alias': 'Porch light at dusk', 'description': 'The porch light comes on at 6 pm.', 'mode': 'single',
              'triggers': [{'trigger': 'time', 'at': '18:00:00'}], 'actions': [{'action': 'light.turn_on', 'target': {'entity_id': 'light.hall_ceiling'}}]}
    assert admin.asks(plan('scene', do='save', name='Dinner', set=[{'thing': 'island pendants', 'level': 30}])) == 'review'
    assert admin.asks(plan('scene', do='save', name='Reading', capture=['living room'])) == 'review'
    assert admin.asks(plan('scene', do='rename', name='Dinner', new_name='Supper')) == 'review'
    assert admin.asks(plan('routine', kind='automation', do='save', name='x', config=config)) == 'review'
    assert admin.asks(plan('routine', kind='automation', do='off', name='porch at sunset')) == 'review'
    assert admin.asks(plan('routine', kind='automation', do='run', name='porch at sunset')) is None
    assert admin.asks(plan('routine', kind='automation', do='remove', name='porch at sunset')) == 'delete'
    assert admin.asks(plan('arrange', things=['reading lamp'], room='bedroom')) is None
    assert admin.asks(plan('routine', kind='script', do='rename', name='goodnight', new_name='Lights down')) in ('review', 'security')
    assert admin.asks(plan('act', action='automation.turn_off', things=['porch at sunset'])) == 'review'
    unlocks = dict(config, actions=[{'action': 'lock.unlock', 'target': {'entity_id': 'lock.front_door'}}])
    assert admin.asks(plan('routine', kind='automation', do='save', name='x', config=unlocks)) == 'security'    # the house's safety first
    card = admin.wait('Change scene Dinner?', plan('scene', do='save', name='Dinner', set=[{'thing': 'island pendants', 'level': 30}]),
                      'scene', {}, login=ME)
    assert (card['do'], card['diff']) == ('Approve', [{'name': 'Island pendants', 'before': '70%', 'after': '30%'}])


def test_make_this_the_new_scene_keeps_its_own_lights_as_they_are_now(house):
    house.states['light.kitchen_pendants']['attributes']['brightness'] = 128
    (c,) = plan('scene', do='save', name='Dinner', capture=['everything in it'])
    assert c['words'] == 'Scene “Dinner” changed: everything in it as it is now'
    assert set(c['captured']) == set(house.configs['scene']['dinner']['entities'])          # its own lights, nothing more
    assert {'name': 'Island pendants', 'before': '70%', 'after': '50%'} in c['diff']
    frozen = dict(c['captured'], **{'light.kitchen_pendants': {'state': 'on', 'brightness': 128}})
    house.states['light.kitchen_pendants']['attributes']['brightness'] = 10               # moved before the tap
    (again,) = plan('scene', do='save', name='Dinner', capture=['everything in it'], frozen=frozen)
    assert admin.fingerprint([again]) == admin.fingerprint([c])
    with pytest.raises(admin.Refused):
        plan('scene', do='save', name='Sunrise', capture=['everything in it'])           # a new scene has nothing of its own


def test_an_automation_is_switched_off_and_on_run_and_removed(house):
    off = do('routine', kind='automation', do='off', name='porch at sunset')
    assert off['done'] == ['Automation “Porch at sunset” switched off'] and house.states['automation.porch_at_sunset']['state'] == 'off'
    with pytest.raises(admin.Already):
        plan('routine', kind='automation', do='off', name='porch at sunset')
    admin.undo(off['batch'], login=ME, via='app')
    assert house.states['automation.porch_at_sunset']['state'] == 'on'
    ran = do('routine', kind='automation', do='run', name='Porch at sunset')
    assert ran['done'] == ['Automation “Porch at sunset” run'] and not ran['undo']
    assert house.sent[-1] == ('automation/trigger', {'entity_id': 'automation.porch_at_sunset', 'skip_condition': True})
    removal = plan('routine', kind='automation', do='remove', name='Porch at sunset')
    assert admin.guard(removal) == 'delete'
    gone = admin.carry_out(removal, login=ME, via='app')
    assert 'a-porch' not in house.configs['automation']
    admin.undo(gone['batch'], login=ME, via='app')
    assert house.configs['automation']['a-porch']['alias'] == 'Porch at sunset'


def test_a_script_is_made_run_and_renamed(house):
    made = do('routine', kind='script', do='save', name='Movie time', config={
        'sequence': [{'action': 'scene.turn_on', 'target': {'entity_id': 'scene.movie_night'}}], 'mode': 'single'})
    assert made['done'] == ['Script “Movie time” made: Movie night on']
    assert house.configs['script']['movie_time']['alias'] == 'Movie time'
    assert do('routine', kind='script', do='run', name='movie time')['done'] == ['Script “Movie time” run']
    assert house.sent[-1] == ('script/turn_on', {'entity_id': 'script.movie_time'})
    assert do('arrange', things=['movie time'], name='Film time')['done'] == ['Script Movie time renamed Film time']
    assert house.configs['script']['movie_time']['alias'] == 'Film time'
    with pytest.raises(admin.Refused, match='no on or off'):
        plan('routine', kind='script', do='off', name='goodnight')


def test_a_thing_is_disabled_on_a_card_and_enabled_at_once(house):
    off = plan('arrange', things=['coffee maker'], enabled=False)
    assert admin.guard(off) == 'disable' and [c['words'] for c in off] == ['Coffee maker disabled']
    done = admin.carry_out(off, login=ME, via='app')
    assert house.entries['switch.coffee_maker']['disabled_by'] == 'user'
    with pytest.raises(admin.Already):
        plan('arrange', things=['coffee maker'], enabled=False)
    on = do('arrange', things=['coffee maker'], enabled=True)             # enabling needs no card
    assert on['done'] == ['Coffee maker enabled again; it appears within a minute'] and house.entries['switch.coffee_maker']['disabled_by'] is None
    admin.undo(on['batch'], login=ME, via='app')
    assert house.entries['switch.coffee_maker']['disabled_by'] == 'user'
    admin.undo(done['batch'], login=ME, via='app')
    assert house.entries['switch.coffee_maker']['disabled_by'] is None
    # A lamp that is its own device is disabled as a device; what uses a thing is said on the card.
    (lamp,) = plan('arrange', things=['floor lamp'], enabled=False)
    assert lamp['steps'] == [{'ws': {'type': 'config/device_registry/update', 'device_id': 'dev-floor-lamp', 'disabled_by': 'user'}}]
    (porch,) = plan('arrange', things=['porch light'], enabled=False)
    assert porch['words'] == 'Porch light disabled; Porch at sunset use it'


def test_a_thing_is_given_an_icon(house):
    done = do('arrange', things=['reading lamp'], icon='mdi:floor-lamp')
    assert done['done'] == ['Reading lamp given the icon floor lamp'] and house.entries['light.living_reading']['icon'] == 'mdi:floor-lamp'
    assert do('arrange', things=['reading lamp'], icon='Desk Lamp')['done'] == ['Reading lamp given the icon desk lamp']
    admin.undo(done['batch'], login=ME, via='app')
    assert house.entries['light.living_reading']['icon'] is None
    for odd in ('https://example.com/x.png', 'mdi:<script>', ''):
        with pytest.raises(admin.Refused, match='An icon is one of'):
            plan('arrange', things=['reading lamp'], icon=odd)


def test_an_integration_is_reloaded_and_nothing_else_about_it_is_touched(house):
    found = admin.find(kind='integration')['found']
    assert {'name': 'Upstairs bridge', 'kind': 'integration', 'of': 'hue', 'state': 'trying to connect'} in found
    assert not any('@' in json.dumps(row) for row in found)                 # an account's address is never shown to a model
    assert do('integration', name='upstairs bridge')['done'] == ['Upstairs bridge reloaded (hue)']
    assert house.setup[-1] == ('reload', 'entry0002bridge')
    both = do('integration', name='hue')                                    # by what it is: every one of that kind
    assert both['done'] == ['Kitchen bridge reloaded (hue)', 'Upstairs bridge reloaded (hue)'] and not both['undo']
    assert do('integration', name='reload the Music Assistant integration')['done'] == ['Music Assistant reloaded']
    with pytest.raises(admin.Refused, match='There is no integration called “zigbee”. There are: google, hue, music assistant, sun'):
        plan('integration', name='zigbee')
    with pytest.raises(admin.Refused, match='only be reloaded by restarting'):
        plan('integration', name='sun')
    for entry in ('../../core/restart', 'entry0001bridge/../x', '', 'x' * 80):
        with pytest.raises(ValueError):
            broker.setup_reload(entry)
    house.refuse = 'reload'
    said = admin.carry_out(plan('integration', name='kitchen bridge'), login=ME, via='ask')
    assert said['done'] == [] and said['not_sent'] == ['Kitchen bridge reloaded (hue)'] and said['why'] == 'HTTP 404'


def test_a_dashboard_is_made_from_rooms_and_things_and_undone(house):
    made = do('dashboard', do='add', name='Upstairs', rooms=['bedroom', 'robin'])
    assert made['done'] == ['Dashboard “Upstairs” made, showing Bedroom and Robin’s room']
    assert [b['url_path'] for b in house.boards] == ['map', 'wall-tablet', 'hub-upstairs']
    views = house.board_configs['hub-upstairs']['views']
    assert [v['title'] for v in views] == ['Bedroom', 'Robin’s room'] and views[0]['type'] == 'sections'
    cards = views[0]['sections'][0]['cards']
    assert cards[0] == {'type': 'heading', 'heading': 'Bedroom'}
    assert [c['entity'] for c in cards[1:]] == ['light.bedroom_left', 'light.bedroom_right', 'fan.bedroom_fan', 'media_player.bedroom']
    assert {c['type'] for c in cards[1:]} == {'tile'}                                   # Home Assistant's own cards, and nothing a model wrote
    assert {'name': 'Upstairs', 'kind': 'dashboard', 'views': ['Bedroom', 'Robin’s room']} in admin.find(kind='dashboard')['found']
    assert {'name': 'Map', 'kind': 'dashboard', 'made_by': 'Home Assistant itself'} in admin.find(kind='dashboard')['found']
    with pytest.raises(admin.Already):
        plan('dashboard', do='add', name='upstairs', rooms=['bedroom'])
    admin.undo(made['batch'], login=ME, via='app')
    assert [b['url_path'] for b in house.boards] == ['map', 'wall-tablet'] and 'hub-upstairs' not in house.board_configs
    things = do('dashboard', do='add', name='Evening', things=['reading lamp', 'thermostat', 'front door lock'], view='Evening things')
    assert things['done'] == ['Dashboard “Evening” made, showing Evening things']
    assert [c.get('entity') for c in house.board_configs['hub-evening']['views'][0]['sections'][0]['cards'][1:]] == [
        'light.living_reading', 'climate.hallway', 'lock.front_door']
    with pytest.raises(admin.Refused, match='Say what the dashboard shows'):
        plan('dashboard', do='add', name='Empty')


def test_a_view_is_added_to_a_dashboard_its_owner_made_and_their_own_cards_are_kept(house):
    before = house.board_configs['wall-tablet']
    added = do('dashboard', do='add_view', name='wall tablet', rooms=['office'])
    assert added['done'] == ['View “Office” added to dashboard “Wall tablet”']
    now = house.board_configs['wall-tablet']
    assert now['views'][:2] == before['views'] and now['views'][2]['title'] == 'Office'      # what the owner wrote by hand is left as it was
    with pytest.raises(admin.Already):
        plan('dashboard', do='add_view', name='Wall tablet', rooms=['office'])
    gone = plan('dashboard', do='remove_view', name='Wall tablet', view='clock')
    assert admin.guard(gone) == 'delete' and gone[0]['words'] == 'View “Clock” taken out of dashboard “Wall tablet”'
    taken = admin.carry_out(gone, login=ME, via='app')
    assert [v['title'] for v in house.board_configs['wall-tablet']['views']] == ['Downstairs', 'Office']
    admin.undo(taken['batch'], login=ME, via='app')
    assert [v['title'] for v in house.board_configs['wall-tablet']['views']] == ['Downstairs', 'Clock', 'Office']
    # Changed again in Home Assistant since: Undo leaves it alone and says so.
    house.board_configs['wall-tablet']['views'].append({'title': 'Added by hand', 'cards': []})
    with pytest.raises(admin.Refused, match='was changed again after Hub changed it'):
        admin.undo(added['batch'], login=ME, via='app')
    with pytest.raises(admin.Refused, match='Which view\\? Wall tablet has: Downstairs, Clock, Office, Added by hand'):
        plan('dashboard', do='remove_view', name='Wall tablet', view='garden')


def test_a_dashboard_is_renamed_and_removed_and_home_assistants_own_are_left_alone(house):
    assert do('dashboard', do='rename', name='Wall tablet', new_name='Hall tablet')['done'] == ['Dashboard Wall tablet renamed Hall tablet']
    removal = plan('dashboard', do='remove', name='Hall tablet')
    assert admin.guard(removal) == 'delete'
    gone = admin.carry_out(removal, login=ME, via='app')
    assert [b['title'] for b in house.boards] == ['Map']
    admin.undo(gone['batch'], login=ME, via='app')
    assert [b['title'] for b in house.boards] == ['Map', 'Hall tablet'] and len(house.board_configs['wall-tablet']['views']) == 2
    for change in ({'do': 'rename', 'new_name': 'Atlas'}, {'do': 'remove'}, {'do': 'add_view', 'rooms': ['office']}):
        with pytest.raises(admin.Refused, match='one of Home Assistant’s own dashboards'):
            plan('dashboard', name='map', **change)
    with pytest.raises(admin.Refused, match='There is no dashboard called “Garden”. There are: Hall tablet, Map'):
        plan('dashboard', do='remove', name='Garden')
    # The fence: only a dashboard a person made (a hyphen in its address), only the fields Hub knows, only plain data.
    for odd in ({'type': 'lovelace/config/save', 'url_path': 'map', 'config': {'views': []}},
                {'type': 'lovelace/config/save', 'url_path': None, 'config': {'views': []}},
                {'type': 'lovelace/config/save', 'url_path': 'wall-tablet', 'config': {}},
                {'type': 'lovelace/config/save', 'url_path': 'wall-tablet', 'config': {'views': ['x' * 300_000]}},
                {'type': 'lovelace/dashboards/create', 'url_path': 'hub-x', 'title': 'X', 'mode': 'yaml'},
                {'type': 'lovelace/dashboards/create', 'url_path': '../x-y', 'title': 'X', 'mode': 'storage'},
                {'type': 'lovelace/dashboards/update', 'dashboard_id': 'wall_tablet', 'url_path': 'other-path'},
                {'type': 'lovelace/dashboards/delete'}, {'type': 'lovelace/resources/create', 'url': 'https://example.com/x.js', 'res_type': 'module'}):
        with pytest.raises(ValueError):
            broker.setup_dashboard(odd)


# ---- devices beyond lights and speakers ----

def test_the_thermostat_is_set_and_put_back(house):
    result = do('act', action='climate.set_temperature', things=['thermostat'], data={'temperature': 70})
    assert result['done'] == ['Thermostat set to 70°'] and house.states['climate.hallway']['attributes']['temperature'] == 70
    admin.undo(result['batch'], login=ME, via='app')
    assert house.states['climate.hallway']['attributes']['temperature'] == 68 and house.states['climate.hallway']['state'] == 'heat'
    off = do('act', action='climate.set_hvac_mode', things=['thermostat'], data={'hvac_mode': 'off'})
    assert off['done'] == ['Thermostat set to off']
    admin.undo(off['batch'], login=ME, via='app')
    assert house.states['climate.hallway']['state'] == 'heat'


def test_locking_happens_at_once_and_unlocking_waits_for_a_tap(house):
    house.states['lock.front_door']['state'] = 'unlocked'
    locked = do('act', action='lock.lock', things=['front door lock'])
    assert locked['done'] == ['Front door lock locked'] and house.states['lock.front_door']['state'] == 'locked'
    with pytest.raises(admin.Already, match='Front door lock is already locked.'):
        plan('act', action='lock.lock', things=['front door lock'])
    unlocking = plan('act', action='lock.unlock', things=['front door lock'])
    assert admin.guard(unlocking) == 'unlock' and house.states['lock.front_door']['state'] == 'locked'
    assert admin.guard(plan('act', action='cover.open_cover', things=['garage door'])) == 'unlock'
    assert admin.guard(plan('act', action='cover.close_cover', things=['blind'])) is None
    assert admin.guard(plan('act', action='homeassistant.restart')) == 'restart'
    with pytest.raises(admin.Refused, match='own action'):
        plan('act', action='homeassistant.turn_on', things=['front door lock'])


def test_a_fan_a_blind_and_a_plug_are_worked_and_put_back(house):
    fan = do('act', action='fan.set_percentage', things=['bedroom fan'], data={'percentage': 40})
    assert fan['done'] == ['Ceiling fan (Bedroom) at 40%'] and house.states['fan.bedroom_fan']['state'] == 'on'
    admin.undo(fan['batch'], login=ME, via='app')
    assert house.states['fan.bedroom_fan']['state'] == 'off'
    blind = do('act', action='cover.set_cover_position', things=['blind'], data={'position': 20})
    assert blind['done'] == ['Blind at 20%']
    admin.undo(blind['batch'], login=ME, via='app')
    assert house.states['cover.office_blind']['attributes']['current_position'] == 100
    plug = do('act', action='switch.turn_on', things=['coffee maker'])
    assert plug['done'] == ['Coffee maker on']
    admin.undo(plug['batch'], login=ME, via='app')
    assert house.states['switch.coffee_maker']['state'] == 'off'


def test_what_the_setup_door_keeps_shut_is_refused_before_anything_is_looked_up(house):
    for action in ('shell_command.tidy', 'hassio.host_reboot', 'homeassistant.stop', 'update.install', 'media_player.play_media', 'nonsense'):
        with pytest.raises(admin.Refused, match='can be done in Home Assistant'):
            plan('act', action=action, things=['thermostat'])
    assert house.sent == []


# ---- helpers and the house's own settings ----

def test_a_helper_is_added_changed_and_removed(house):
    made = do('helper', do='add', kind='input_boolean', name='Guests staying')
    assert made['done'] == ['Helper “Guests staying” added (switch)'] and 'input_boolean.guests_staying' in house.states
    number = do('helper', do='add', kind='input_number', name='Wake light', fields={'min': 0, 'max': 100, 'step': 5})
    changed = do('helper', do='change', kind='input_number', name='wake light', fields={'max': 50})
    assert changed['done'] == ['Helper “Wake light” changed: max']
    admin.undo(changed['batch'], login=ME, via='app')
    assert house.helpers['input_number'][0]['max'] == 100
    with pytest.raises(admin.Refused, match='input_number takes only'):
        plan('helper', do='change', kind='input_number', name='wake light', fields={'colour': 'red'})
    removal = plan('helper', do='remove', kind='input_boolean', name='Guests staying')
    assert admin.guard(removal) == 'delete'
    admin.undo(number['batch'], login=ME, via='app')
    assert house.helpers['input_number'] == []


def test_the_houses_own_settings_wait_for_a_tap(house):
    changes = plan('house', fields={'time_zone': 'America/New_York', 'unit_system': 'metric'})
    assert admin.guard(changes) == 'house' and changes[0]['words'] == 'The house’s time zone set to America/New_York and units set to metric'
    done = admin.carry_out(changes, login=ME, via='app')
    assert house.core['time_zone'] == 'America/New_York' and house.core['unit_system']['temperature'] == '°C'
    admin.undo(done['batch'], login=ME, via='app')
    assert house.core['time_zone'] == 'America/Los_Angeles' and house.core['unit_system']['temperature'] == '°F'
    with pytest.raises(admin.Refused):
        plan('house', fields={'latitude': '1'})


# ---- waiting for a tap, the record, and when things go wrong ----

def test_a_card_waits_and_the_tap_plans_again_from_the_house_as_it_is(house):
    args = {'kind': 'room', 'do': 'remove', 'name': 'Dining'}
    card = admin.wait('Remove the room Dining?', plan('place', **args), 'place', args, login=ME)
    assert card == {'kind': 'confirm', 'id': card['id'], 'title': 'Remove the room Dining?', 'do': 'Remove',
                    'lines': ['Room “Dining” removed; its 2 things are in no room'], 'why': admin.WHY['delete']}
    assert house.setup == [] and admin.waiting(card['id'])['status'] == 'waiting'
    result = admin.confirm(card['id'], login=ME)
    assert result['done'] == ['Room “Dining” removed; its 2 things are in no room'] and admin.waiting(card['id'])['status'] == 'done'
    assert admin.waiting(card['id'])['batch'] == result['batch']
    with pytest.raises(admin.Refused, match='already done'):
        admin.confirm(card['id'], login=ME)


def test_a_card_is_not_carried_out_if_the_house_changed_under_it_or_it_waited_too_long(house):
    args = {'kind': 'room', 'do': 'remove', 'name': 'Dining'}
    card = admin.wait('Remove the room Dining?', plan('place', **args), 'place', args, login=ME)
    do('arrange', things=['reading lamp'], room='dining')              # now three things would be left without a room
    with pytest.raises(admin.Refused, match='changed since that card was made'):
        admin.confirm(card['id'], login=ME)
    assert 'dining' in [a['area_id'] for a in house.areas]
    old = admin.wait('Remove the room Dining?', plan('place', **args), 'place', args, login=ME)
    with admin._db() as conn:
        conn.execute('update house_setup_waiting set ts = ? where id = ?', (time.time() - admin.WAIT_SECONDS - 5, old['id']))
    with pytest.raises(admin.Refused, match='waited more than an hour'):
        admin.confirm(old['id'], login=ME)
    newer = admin.wait('Remove the room Dining?', plan('place', **args), 'place', args, login=ME)
    again = admin.wait('Remove the room Entry?', plan('place', kind='room', do='remove', name='Entry'), 'place',
                       {'kind': 'room', 'do': 'remove', 'name': 'Entry'}, login=ME)
    assert admin.waiting(newer['id'])['status'] == 'replaced' and admin.waiting(again['id'])['status'] == 'waiting'


def test_more_than_ten_things_in_one_request_wait_for_a_tap(house):
    assert admin.guard(plan('arrange', things=['kitchen lights', 'living room lights', 'dining lights'], room='office')) is None
    wide = plan('arrange', things=['light.kitchen_ceiling', 'light.kitchen_pendants', 'light.kitchen_cabinets', 'light.living_reading',
                                   'light.living_shelves', 'light.dining_pendant', 'light.dining_sideboard', 'light.entry_ceiling',
                                   'light.bedroom_left', 'light.bedroom_right', 'light.robin_night'], room='office')
    assert len(wide) == 11 and admin.guard(wide) == 'many'


def test_when_home_assistant_says_no_nothing_is_recorded_as_done(house):
    house.refuse = 'config/entity_registry/update'
    result = admin.carry_out(plan('arrange', things=['reading lamp'], room='bedroom'), login=ME, via='ask')
    assert result['done'] == [] and result['not_sent'] == ['Reading lamp moved from Living room to Bedroom'] and result['why'] == 'invalid_format'
    assert not result['undo'] and rooms_of('light.living_reading') == ['Living room']
    house.refuse, house.down = None, True
    with pytest.raises(broker.HomeDown):
        plan('arrange', things=['reading lamp'], room='bedroom')


def test_the_record_lists_each_request_newest_first_with_who_asked(house):
    do('arrange', things=['reading lamp'], room='bedroom')
    second = do('act', action='climate.set_temperature', things=['thermostat'], data={'temperature': 66})
    admin.undo(second['batch'], login=ME, via='app')
    rows = admin.recent()
    assert [(r['via'], r['done'], r['undo'], r['undone']) for r in rows] == [
        ('undo', ['Thermostat put back'], False, False), ('ask', ['Thermostat set to 66°'], False, True),
        ('ask', ['Reading lamp moved from Living room to Bedroom'], True, False)]
    assert rows[1]['kinds'] == ['climate'] and rows[2]['kinds'] == ['place'] and all(r['batch'].startswith('s') for r in rows)


def test_setup_changes_count_against_the_same_limit_as_other_changes(house):
    for n in range(control.PER_MINUTE):
        control._spend(1)
    with pytest.raises(admin.Refused, match='changes in the last minute'):
        admin.carry_out(plan('arrange', things=['reading lamp'], room='bedroom'), login=ME, via='ask')
    assert house.setup == []


# ---- the third pass: what guards the house, what a helper sets off, and routines that keep it shut ----

def _guards(house):
    """Things a real house has beside its locks and alarm: a switch on the alarm's own integration, a relay
    called Gate, a button on the garage opener's device, a helper that an automation of the owner's unlocks on."""
    def thing(entity, name, platform=None, device=None, state='off', **attrs):
        house.states[entity] = {'entity_id': entity, 'state': state, 'attributes': {'friendly_name': name, **attrs}}
        house.entries[entity] = {'entity_id': entity, 'area_id': None, 'device_id': device, 'name': None, 'icon': None, 'labels': [],
                                 'hidden_by': None, 'disabled_by': None, 'platform': platform, 'original_name': name if device else None}
    house.entries['alarm_control_panel.house'] = {'entity_id': 'alarm_control_panel.house', 'area_id': None, 'device_id': None, 'name': None,
                                                  'labels': [], 'hidden_by': None, 'disabled_by': None, 'platform': 'acme_alarm'}
    house.devices.append({'id': 'dev-garage', 'name': 'Garage opener', 'name_by_user': None, 'area_id': None, 'labels': []})
    house.entries['cover.garage_door'] = dict(house.entries['cover.garage_door'], device_id='dev-garage')
    thing('switch.arriving_home', 'Arriving home', platform='acme_alarm')
    thing('switch.gate_relay', 'Gate', platform='shelly')
    thing('button.opener_toggle', 'Toggle', device='dev-garage', state='unknown')
    thing('switch.garage_light', 'Garage light', platform='shelly')
    thing('input_boolean.guests', 'Guests are coming')
    thing('input_boolean.movie', 'Movie mode')
    house.configs['automation']['a-guests'] = {'id': 'a-guests', 'alias': 'Let guests in', 'triggers': [
        {'trigger': 'state', 'entity_id': 'input_boolean.guests', 'to': 'on'}], 'conditions': [],
        'actions': [{'action': 'lock.unlock', 'target': {'entity_id': 'lock.front_door'}}]}
    house.configs['automation']['a-movie'] = {'id': 'a-movie', 'alias': 'Movie lights', 'triggers': [
        {'trigger': 'state', 'entity_id': 'input_boolean.movie', 'to': 'on'}], 'conditions': [],
        'actions': [{'action': 'scene.turn_on', 'target': {'entity_id': 'scene.movie_night'}}]}
    house.configs['automation']['a-lockup'] = {'id': 'a-lockup', 'alias': 'Lock up at ten', 'triggers': [{'trigger': 'time', 'at': '22:00:00'}],
                                               'conditions': [], 'actions': [{'action': 'lock.lock', 'target': {'entity_id': 'lock.front_door'}},
                                                                             {'action': 'alarm_control_panel.alarm_arm_home', 'target': {'entity_id': 'alarm_control_panel.house'}}]}
    for key, name in (('a-guests', 'Let guests in'), ('a-movie', 'Movie lights'), ('a-lockup', 'Lock up at ten')):
        entity = 'automation.' + app_world.slug(name)
        house.states[entity] = {'entity_id': entity, 'state': 'on', 'attributes': {'friendly_name': name, 'id': key}}
    admin.forget()


def test_what_belongs_to_a_lock_or_the_alarm_waits_for_a_tap_like_them(house):
    _guards(house)
    w = admin.world(fresh=True)
    assert {t['id'] for t in w['things'].values() if t['guards']} == {'switch.arriving_home', 'switch.gate_relay', 'button.opener_toggle'}
    assert admin.guard(plan('act', action='switch.turn_on', things=['arriving home'])) == 'security'       # on the alarm's own integration
    house.states['switch.arriving_home']['state'] = 'on'
    assert admin.guard(plan('act', action='switch.turn_off', things=['arriving home'])) == 'security'      # either way: it is the alarm's
    house.states['switch.arriving_home']['state'] = 'off'
    assert admin.guard(plan('act', action='switch.turn_on', things=['gate'])) == 'security'               # a relay named for what it opens
    assert admin.guard(plan('act', action='button.press', things=['button.opener_toggle'])) == 'security'  # on the garage opener's own device
    assert admin.guard(plan('act', action='homeassistant.turn_on', things=['switch.arriving_home'])) == 'security'
    assert admin.guard(plan('act', action='switch.turn_on', things=['garage light'])) is None            # a light in the garage is a light
    assert admin.guard(plan('act', action='switch.turn_on', things=['coffee maker'])) is None
    routine = {**EVENING, 'alias': 'Home early', 'actions': [{'action': 'switch.turn_on', 'target': {'entity_id': 'switch.arriving_home'}}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='Home early', config=routine)) == 'security'
    with pytest.raises(admin.Refused, match='belongs to the locks or the alarm'):
        plan('scene', do='save', name='Arrive', set=[{'thing': 'arriving home', 'on': True}])
    assert house.sent == []


def test_a_helper_is_not_a_way_round_the_tap(house):
    _guards(house)
    assert admin.guard(plan('act', action='input_boolean.turn_on', things=['guests are coming'])) == 'security'    # the owner's automation unlocks on it
    assert admin.guard(plan('act', action='homeassistant.turn_on', things=['input_boolean.guests'])) == 'security'
    assert admin.guard(plan('act', action='input_boolean.turn_on', things=['movie mode'])) is None                # this one only sets lights
    wrapper = {**EVENING, 'alias': 'Sneaky', 'actions': [{'action': 'input_boolean.turn_on', 'target': {'entity_id': 'input_boolean.guests'}}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='Sneaky', config=wrapper)) == 'security'
    house.states['automation.let_guests_in']['state'] = 'off'               # switched off, it sets nothing off
    assert admin.guard(plan('act', action='input_boolean.turn_on', things=['guests are coming'])) is None
    # An automation that sets its own helper never sends Hub round in circles.
    house.states['automation.let_guests_in']['state'] = 'on'
    house.configs['automation']['a-guests']['actions'].append({'action': 'input_boolean.turn_off', 'target': {'entity_id': 'input_boolean.guests'}})
    assert admin.guard(plan('act', action='input_boolean.turn_on', things=['guests are coming'])) == 'security'


def test_switching_off_or_writing_over_what_locks_up_waits_for_a_tap(house):
    _guards(house)
    assert admin.guard(plan('routine', kind='automation', do='off', name='lock up at ten')) == 'security'
    assert admin.guard(plan('act', action='automation.turn_off', things=['lock up at ten'])) == 'security'
    assert admin.guard(plan('act', action='homeassistant.turn_off', things=['automation.lock_up_at_ten'])) == 'security'
    assert admin.guard(plan('act', action='automation.toggle', things=['lock up at ten'])) == 'security'
    assert admin.guard(plan('routine', kind='automation', do='off', name='porch at sunset')) is None
    lights_only = {'alias': 'Lock up at ten', 'triggers': [{'trigger': 'time', 'at': '22:00:00'}], 'conditions': [],
                   'actions': [{'action': 'light.turn_off', 'target': {'entity_id': 'light.entry_ceiling'}}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='lock up at ten', config=lights_only)) == 'security'
    stops_it = {**EVENING, 'alias': 'Stay open', 'actions': [{'action': 'automation.turn_off', 'target': {'entity_id': 'automation.lock_up_at_ten'}}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='Stay open', config=stops_it)) == 'security'
    # Switching on one that only locks is fine; switching on one that unlocks sets an unlocking going.
    for name in ('lock_up_at_ten', 'let_guests_in'):
        house.states[f'automation.{name}']['state'] = 'off'
    assert admin.guard(plan('routine', kind='automation', do='on', name='lock up at ten')) is None
    assert admin.guard(plan('routine', kind='automation', do='on', name='let guests in')) == 'security'
    assert admin.guard(plan('act', action='automation.turn_on', things=['let guests in'])) == 'security'
    assert house.sent == []


def test_hiding_what_watches_a_way_in_waits_for_a_tap(house):
    _guards(house)
    for watched in ('front door', 'front door lock', 'alarm', 'gate'):
        hidden = plan('arrange', things=[watched], hidden=True)
        assert admin.guard(hidden) == 'hide', watched
    assert admin.guard(plan('arrange', things=['reading lamp'], hidden=True)) is None
    done = admin.carry_out(plan('arrange', things=['front door'], hidden=True), login=ME, via='app')
    assert admin.guard(plan('arrange', things=['binary_sensor.front_door'], hidden=False)) is None      # showing it again needs no tap
    admin.undo(done['batch'], login=ME, via='app')
    assert house.entries['binary_sensor.front_door']['hidden_by'] is None


def test_an_opening_is_found_however_deep_a_routine_hides_it(house):
    """Hundreds of made-up routines: an unlock, a disarm or a garage opening wrapped at random in choices, repeats,
    branches and sequences is always found, and the same routines without it never wait."""
    import random
    _owner_made(house)
    w = admin.world(fresh=True)
    rng = random.Random(7)
    plain = [{'action': 'light.turn_on', 'target': {'entity_id': 'light.entry_ceiling'}}, {'delay': '00:00:05'},
             {'action': 'scene.turn_on', 'target': {'entity_id': 'scene.dinner'}}, {'action': 'lock.lock', 'target': {'entity_id': 'lock.front_door'}},
             {'action': 'cover.close_cover', 'target': {'entity_id': 'cover.garage_door'}}, {'action': 'cover.open_cover', 'target': {'entity_id': 'cover.office_blind'}},
             {'action': 'alarm_control_panel.alarm_arm_home', 'target': {'entity_id': 'alarm_control_panel.house'}},
             {'variables': {'x': 1}}, {'action': 'notify.persistent_notification', 'data': {'message': 'unlock the door'}}]
    opening = [{'action': 'lock.unlock', 'target': {'entity_id': 'lock.front_door'}}, {'service': 'lock.open', 'entity_id': 'lock.front_door'},
               {'action': 'cover.open_cover', 'data': {'entity_id': ['cover.office_blind', 'cover.garage_door']}},
               {'action': 'alarm_control_panel.alarm_disarm', 'target': {'entity_id': 'alarm_control_panel.house'}, 'data': {'code': '0000'}},
               {'action': 'cover.toggle', 'target': {'area_id': 'entry'}}, {'scene': 'scene.arriving'}, {'action': 'script.let_them_in'},
               {'action': 'homeassistant.toggle', 'target': {'entity_id': 'cover.garage_door'}},
               {'device_id': 'dev-x', 'domain': 'alarm_control_panel', 'type': 'disarm', 'entity_id': 'alarm_control_panel.house'},
               {'action': 'scene.create', 'data': {'scene_id': 'before', 'snapshot_entities': ['lock.front_door']}}]

    def wrap(steps, depth):
        if depth == 0:
            return steps
        how = rng.choice(('if', 'choose', 'repeat', 'parallel', 'sequence', 'else', 'default'))
        cond = [{'condition': 'state', 'entity_id': 'light.entry_ceiling', 'state': 'on'}]
        inner = wrap(steps, depth - 1)
        return [{'if': cond, 'then': inner} if how == 'if' else {'if': cond, 'then': [rng.choice(plain)], 'else': inner} if how == 'else'
                else {'choose': [{'conditions': cond, 'sequence': inner}]} if how == 'choose'
                else {'choose': [{'conditions': cond, 'sequence': [rng.choice(plain)]}], 'default': inner} if how == 'default'
                else {'repeat': {'count': 2, 'sequence': inner}} if how == 'repeat' else {'parallel': inner} if how == 'parallel' else {'sequence': inner}]

    for n in range(300):
        before, after = [rng.choice(plain) for _ in range(rng.randint(0, 3))], [rng.choice(plain) for _ in range(rng.randint(0, 3))]
        kind = rng.choice(('automation', 'script'))
        body = lambda steps: {'alias': 'x', 'triggers': [{'trigger': 'time', 'at': '03:00:00'}], 'actions': steps} if kind == 'automation' else {'alias': 'x', 'sequence': steps}  # noqa: E731
        hidden = wrap([rng.choice(opening)], rng.randint(0, 4))
        assert plans.opens(kind, body(before + hidden + after), w) == 'security', (n, hidden)
        assert plans.opens(kind, body(before + wrap([rng.choice(plain)], rng.randint(0, 4)) + after), w) is None, n


def test_four_more_ways_round_are_shut(house):
    _guards(house)
    # A new name for what guards the house: part of how Hub knows a relay is the gate is that it is called Gate.
    for guard in ('gate', 'front door lock', 'garage door'):
        assert admin.guard(plan('arrange', things=[guard], name='Hall lamp')) == 'security', guard
    assert admin.guard(plan('arrange', things=['reading lamp'], name='Corner lamp')) is None
    # A routine that was disabled in Home Assistant comes back to life when it is enabled.
    house.entries['automation.let_guests_in'] = {'entity_id': 'automation.let_guests_in', 'area_id': None, 'device_id': None, 'name': None,
                                                 'labels': [], 'hidden_by': None, 'disabled_by': 'user'}
    assert admin.guard(plan('arrange', things=['automation.let_guests_in'], enabled=True)) == 'unread'
    house.entries['automation.let_guests_in']['disabled_by'] = None
    # A lock's own settings on the lock's device: its codes, its auto-lock.
    house.devices.append({'id': 'dev-lock', 'name': 'Front door lock', 'name_by_user': None, 'area_id': 'entry', 'labels': []})
    house.entries['lock.front_door'] = dict(house.entries['lock.front_door'], device_id='dev-lock')
    for entity, name in (('text.front_door_code_1', 'Code 1'), ('switch.front_door_auto_lock', 'Auto lock')):
        house.states[entity] = {'entity_id': entity, 'state': 'unknown', 'attributes': {'friendly_name': 'Front door lock ' + name}}
        house.entries[entity] = {'entity_id': entity, 'area_id': None, 'device_id': 'dev-lock', 'name': None, 'labels': [], 'hidden_by': None,
                                 'disabled_by': None, 'original_name': name, 'entity_category': 'config'}
    assert admin.guard(plan('act', action='text.set_value', things=['text.front_door_code_1'], data={'value': '1234'})) == 'security'
    assert admin.guard(plan('act', action='switch.turn_off', things=['switch.front_door_auto_lock'])) == 'security'
    # A scene that sets a helper which an automation unlocks on.
    house.configs['scene']['guest_mode'] = {'id': 'guest_mode', 'name': 'Guest mode', 'entities': {
        'light.entry_ceiling': {'state': 'on'}, 'input_boolean.guests': {'state': 'on'}}}
    house.states['scene.guest_mode'] = {'entity_id': 'scene.guest_mode', 'state': 'unknown', 'attributes': {
        'friendly_name': 'Guest mode', 'id': 'guest_mode', 'entity_id': ['light.entry_ceiling', 'input_boolean.guests']}}
    house.configs['scene']['coffee'] = {'id': 'coffee', 'name': 'Coffee', 'entities': {'switch.coffee_maker': {'state': 'on'}}}
    house.states['scene.coffee'] = {'entity_id': 'scene.coffee', 'state': 'unknown', 'attributes': {
        'friendly_name': 'Coffee', 'id': 'coffee', 'entity_id': ['switch.coffee_maker']}}
    assert admin.guard(plan('act', action='scene.turn_on', things=['guest mode'])) == 'security'
    assert admin.guard(plan('act', action='scene.turn_on', things=['coffee'])) is None
    assert house.sent == []


# ---- a third read, by a stranger: the rule is one, whatever Hub can't read as safe waits for a tap ----

def _thing(house, entity, name, platform=None, state='off', **attrs):
    house.states[entity] = {'entity_id': entity, 'state': state, 'attributes': {'friendly_name': name, **attrs}}
    house.entries[entity] = {'entity_id': entity, 'area_id': None, 'device_id': None, 'name': None, 'icon': None, 'labels': [],
                             'hidden_by': None, 'disabled_by': None, 'platform': platform, 'original_name': None}


def _automation(house, key, name, triggers, actions, conditions=()):
    house.configs['automation'][key] = {'id': key, 'alias': name, 'triggers': triggers, 'conditions': list(conditions), 'actions': actions}
    entity = 'automation.' + app_world.slug(name)
    house.states[entity] = {'entity_id': entity, 'state': 'on', 'attributes': {'friendly_name': name, 'id': key}}
    admin.forget()
    return entity


UNLOCK = [{'action': 'lock.unlock', 'target': {'entity_id': 'lock.front_door'}}]


def test_an_old_service_template_is_read_like_any_call_and_one_worked_out_at_run_time_waits(house):
    quiet = {'alias': 'Quiet one', 'sequence': [{'service_template': 'lock.unlock', 'data_template': {'entity_id': 'lock.front_door'}}]}
    assert admin.guard(plan('routine', kind='script', do='save', name='Quiet one', config=quiet)) == 'security'
    timed = {**EVENING, 'alias': 'At three', 'actions': [{'service_template': 'cover.open_cover', 'entity_id': 'cover.garage_door'}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='At three', config=timed)) == 'security'
    with pytest.raises(admin.Refused, match='names each action plainly'):
        plan('routine', kind='script', do='save', name='Odd one', config={'alias': 'Odd one', 'sequence': [{'service_template': '{{ what }}'}]})
    # One of the owner's own, whose action is worked out when it runs: Hub can't read it, so running it waits.
    house.configs['script']['do_it'] = {'alias': 'Do it', 'sequence': [{'action': '{{ what }}', 'target': {'entity_id': '{{ which }}'}}]}
    house.states['script.do_it'] = {'entity_id': 'script.do_it', 'state': 'off', 'attributes': {'friendly_name': 'Do it'}}
    admin.forget()
    assert admin.guard(plan('act', action='script.turn_on', things=['do it'], data={'variables': {'what': 'lock.unlock'}})) == 'unread'
    assert admin.guard(plan('routine', kind='script', do='run', name='do it')) == 'unread'
    assert house.sent == []


def test_a_step_hub_cant_read_waits(house):
    _thing(house, 'remote.rf_bridge', 'RF bridge', state='on')
    admin.forget()
    for step in ({'event': 'open_sesame', 'event_data': {'door': 'front'}},                # an event another routine may act on
                 {'something_new': {'door': 'front'}},                                     # a kind of step Hub doesn't know
                 {'action': 'remote.send_command', 'target': {'entity_id': 'remote.rf_bridge'}, 'data': {'command': 'open'}}):
        body = {'alias': 'Ring', 'sequence': [{'delay': '00:00:05'}, {'choose': [{'conditions': [], 'sequence': [step]}]}]}
        assert admin.guard(plan('routine', kind='script', do='save', name='Ring', config=body)) == 'unread', step
    # A code learned from a remote may open the gate as well as change the channel.
    assert admin.guard(plan('act', action='remote.send_command', things=['rf bridge'], data={'command': 'open'})) == 'unread'
    quiet = {'alias': 'Quiet', 'sequence': [{'delay': '00:00:05'}, {'wait_template': '{{ true }}'}, {'variables': {'n': 1}}, {'stop': 'done'}]}
    assert admin.guard(plan('routine', kind='script', do='save', name='Quiet', config=quiet)) is None


def test_a_devices_own_action_is_read_by_its_device(house):
    _guards(house)
    script = lambda step: admin.guard(plan('routine', kind='script', do='save', name='Press it', config={'alias': 'Press it', 'sequence': [step]}))  # noqa: E731
    assert script({'device_id': 'dev-garage', 'domain': 'button', 'type': 'press', 'entity_id': 'button.opener_toggle'}) == 'security'
    assert script({'device_id': 'dev-garage', 'domain': 'cover', 'type': 'close', 'entity_id': 'cover.garage_door'}) is None
    assert script({'device_id': 'dev-nowhere', 'domain': 'switch', 'type': 'turn_on', 'entity_id': 'switch.garage_light'}) == 'unread'
    assert script({'device_id': 'dev-floor-lamp', 'domain': 'light', 'type': 'turn_on', 'entity_id': 'light.living_floor_lamp'}) is None
    assert house.setup == []


def test_a_helper_is_looked_into_wherever_an_automation_reads_it(house):
    _guards(house)
    guests = lambda: admin.guard(plan('act', action='input_boolean.turn_on', things=['guests are coming']))      # noqa: E731
    automation = house.configs['automation']['a-guests']
    # Read in a condition, not a trigger: at the door with guests coming, it unlocks.
    automation.update(triggers=[{'trigger': 'state', 'entity_id': 'binary_sensor.front_door', 'to': 'on'}],
                      conditions=[{'condition': 'state', 'entity_id': 'input_boolean.guests', 'state': 'on'}])
    admin.forget()
    assert guests() == 'security'
    # Read in a template, which Home Assistant's own search doesn't index: so every automation is read.
    automation.update(triggers=[{'trigger': 'template', 'value_template': "{{ is_state('input_boolean.guests', 'on') }}"}], conditions=[])
    admin.forget()
    assert 'automation.let_guests_in' not in house._related('input_boolean.guests').get('automation', [])
    assert guests() == 'security'
    # Made from a blueprint whose inputs name it: what the blueprint does with it is Home Assistant's to know.
    house.configs['automation']['a-guests'] = {'id': 'a-guests', 'alias': 'Let guests in', 'use_blueprint': {
        'path': 'made_up/unlock_on_switch.yaml', 'input': {'switch': 'input_boolean.guests', 'lock': 'lock.front_door'}}}
    admin.forget()
    assert guests() == 'unread'
    house.configs['automation']['a-guests']['use_blueprint']['input']['switch'] = 'input_boolean.movie'
    admin.forget()
    assert guests() is None                     # one whose inputs don't name it isn't set off by it
    # Written in Home Assistant's own files: read all the same.
    del house.configs['automation']['a-guests']
    house.states['automation.let_guests_in']['attributes'].pop('id')
    house.by_hand['automation.let_guests_in'] = {'alias': 'Let guests in', 'triggers': [
        {'trigger': 'state', 'entity_id': 'input_boolean.guests', 'to': 'on'}], 'conditions': [], 'actions': UNLOCK}
    admin.forget()
    assert guests() == 'security'
    # One that can't be read at all, or Home Assistant not answering, is not a yes.
    del house.by_hand['automation.let_guests_in']
    admin.forget()
    assert guests() == 'unread'
    asked = broker.setup_read
    try:
        broker.setup_read = lambda *commands, timeout=None: (_ for _ in ()).throw(broker.HomeDown('away'))
        w = admin.world(fresh=True)
        assert plans._sets_off(w['things']['input_boolean.guests'], w) == 'unread'
    finally:
        broker.setup_read = asked


def test_what_home_assistants_own_search_misses_is_read_all_the_same(house):
    """Its search knows the entities under entity_id. These three read a thing elsewhere, and each would open the
    house when Ask works that thing."""
    _guards(house)
    _thing(house, 'input_datetime.wake', 'Wake time', state='07:00:00', has_date=False, has_time=True)
    house.configs['automation']['a-guests']['triggers'] = [{'trigger': 'template', 'value_template': "{{ is_state('input_boolean.guests', 'on') }}"}]
    _automation(house, 'a-night', 'At three', [{'trigger': 'time', 'at': '03:00:00'}], UNLOCK,
                conditions=[{'condition': 'template', 'value_template': "{{ is_state('input_boolean.movie', 'on') }}"}])
    _automation(house, 'a-wake', 'Up and about', [{'trigger': 'time', 'at': 'input_datetime.wake'}],
                [{'action': 'alarm_control_panel.alarm_disarm', 'target': {'entity_id': 'alarm_control_panel.house'}}])
    for opening, item in (('let_guests_in', 'input_boolean.guests'), ('at_three', 'input_boolean.movie'), ('up_and_about', 'input_datetime.wake')):
        assert 'automation.' + opening not in house._related(item).get('automation', [])
    assert admin.guard(plan('act', action='input_boolean.turn_on', things=['guests are coming'])) == 'security'
    assert admin.guard(plan('act', action='input_boolean.turn_on', things=['movie mode'])) == 'security'
    assert admin.guard(plan('act', action='input_datetime.set_datetime', things=['wake time'], data={'time': '06:30:00'})) == 'security'
    assert house.sent == []


def test_a_scene_that_switches_an_automation_is_read_like_switching_it(house):
    _guards(house)
    house.states['automation.let_guests_in']['state'] = 'off'
    _automation(house, 'a-watch', 'Garage left open', [{'trigger': 'state', 'entity_id': 'cover.garage_door', 'to': 'open', 'for': '00:10:00'}],
                [{'action': 'notify.persistent_notification', 'data': {'message': 'The garage is open'}}])
    _automation(house, 'a-hall', 'Hall light at the door', [{'trigger': 'state', 'entity_id': 'binary_sensor.front_door', 'to': 'on'}],
                [{'action': 'light.turn_on', 'target': {'entity_id': 'light.hall_ceiling'}}])
    applied = lambda name, entities: admin.guard(plan('routine', kind='script', do='save', name=name, config={      # noqa: E731
        'alias': name, 'sequence': [{'action': 'scene.apply', 'data': {'entities': entities}}]}))
    assert applied('Welcome', {'automation.let_guests_in': 'on'}) == 'security'                 # on: what it does is set going
    assert applied('Hush', {'automation.garage_left_open': 'off'}) == 'security'                # off: nobody is told
    assert applied('Calm', {'automation.hall_light_at_the_door': 'on'}) is None
    # An owner's scene that holds one.
    house.configs['scene']['welcome'] = {'id': 'welcome', 'name': 'Welcome', 'entities': {
        'automation.let_guests_in': {'state': 'on'}, 'light.entry_ceiling': {'state': 'on'}}}
    house.states['scene.welcome'] = {'entity_id': 'scene.welcome', 'state': 'unknown', 'attributes': {
        'friendly_name': 'Welcome', 'id': 'welcome', 'entity_id': ['automation.let_guests_in', 'light.entry_ceiling']}}
    admin.forget()
    assert admin.guard(plan('act', action='scene.turn_on', things=['welcome'])) == 'security'


def test_switching_off_what_watches_a_way_in_waits_inside_a_routine_too(house):
    _automation(house, 'a-watch', 'Garage left open', [{'trigger': 'state', 'entity_id': 'cover.garage_door', 'to': 'open', 'for': '00:10:00'}],
                [{'action': 'notify.persistent_notification', 'data': {'message': 'The garage is open'}}])
    for call in ('automation.turn_off', 'homeassistant.turn_off'):
        body = {'alias': 'Hush', 'sequence': [{'action': call, 'target': {'entity_id': 'automation.garage_left_open'}}]}
        assert admin.guard(plan('routine', kind='script', do='save', name='Hush', config=body)) == 'security', call


def test_everything_of_every_kind_at_once_is_more_than_hub_can_read(house):
    every = {'alias': 'All out', 'sequence': [{'action': 'homeassistant.turn_off', 'target': {'entity_id': 'all'}}]}
    assert admin.guard(plan('routine', kind='script', do='save', name='All out', config=every)) == 'unread'
    lights = {'alias': 'All dark', 'sequence': [{'action': 'light.turn_off', 'target': {'entity_id': 'all'}}]}
    assert admin.guard(plan('routine', kind='script', do='save', name='All dark', config=lights)) is None


def test_an_automation_that_only_works_a_lock_is_not_set_off_by_it(house):
    _automation(house, 'a-wake', 'Open up at seven', [{'trigger': 'time', 'at': '07:00:00'}], UNLOCK)
    house.states['lock.front_door']['state'] = 'unlocked'
    admin.forget()
    assert admin.guard(plan('act', action='lock.lock', things=['front door lock'])) is None      # locking stays at once


def test_a_light_or_a_speaker_that_sets_off_an_opening_automation_waits_too(house):
    _automation(house, 'a-porch', 'Porch welcome', [{'trigger': 'state', 'entity_id': 'light.porch_light', 'to': 'off'}], UNLOCK)
    assert admin.guard(plan('act', action='light.turn_off', things=['porch light'])) == 'security'
    body = {**EVENING, 'alias': 'Porch dark', 'actions': [{'action': 'light.turn_off', 'target': {'area_id': 'porch'}}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='Porch dark', config=body)) == 'security'   # by its room, too
    house.configs['scene']['out'] = {'id': 'out', 'name': 'Out', 'entities': {'light.porch_light': {'state': 'off'}}}
    house.states['scene.out'] = {'entity_id': 'scene.out', 'state': 'unknown', 'attributes': {'friendly_name': 'Out', 'id': 'out',
                                                                                             'entity_id': ['light.porch_light']}}
    admin.forget()
    assert admin.guard(plan('act', action='scene.turn_on', things=['out'])) == 'security'
    assert admin.guard(plan('act', action='light.turn_on', things=['path lights'])) is None


def test_a_helpers_settings_are_looked_into_like_working_it(house):
    house.helpers['input_select'] = [{'id': 'house_mode', 'name': 'House mode', 'options': ['Home', 'Guest', 'Away']}]
    _thing(house, 'input_select.house_mode', 'House mode', state='Home', options=['Home', 'Guest', 'Away'])
    _automation(house, 'a-mode', 'Guest arrival', [{'trigger': 'state', 'entity_id': 'input_select.house_mode', 'to': 'Guest'}], UNLOCK)
    # Without "Home", Home Assistant sets the first choice left, Guest, which unlocks.
    assert admin.guard(plan('helper', do='change', kind='input_select', name='House mode', fields={'options': ['Guest', 'Away']})) == 'security'
    assert admin.guard(plan('helper', do='change', kind='input_select', name='House mode', new_name='Home or away')) is None


def test_a_way_in_that_changes_rooms_waits(house):
    assert admin.guard(plan('arrange', things=['garage door'], room='office')) == 'security'
    assert admin.guard(plan('arrange', things=['front door lock'], room='guest room')) == 'security'
    assert admin.guard(plan('place', kind='room', do='set', name='entry', floor='upstairs')) == 'security'      # the entry holds the lock
    assert admin.guard(plan('place', kind='group', do='add', name='Morning', rooms=['office', 'entry'])) == 'security'
    assert admin.guard(plan('arrange', things=['reading lamp'], room='office')) is None
    assert admin.guard(plan('place', kind='room', do='set', name='office', floor='upstairs')) is None
    assert admin.guard(plan('place', kind='group', do='add', name='Morning', rooms=['office', 'kitchen'])) is None


def test_a_group_is_read_by_what_it_holds(house):
    _owner_made(house)
    _thing(house, 'group.ways_in', 'Ways in', state='closed', entity_id=['cover.garage_door', 'script.let_them_in'])
    _thing(house, 'group.lamps', 'Lamps', state='off', entity_id=['light.living_floor_lamp'])
    _thing(house, 'cover.all_blinds', 'All blinds', state='closed', entity_id=['cover.office_blind'])
    admin.forget()
    assert admin.guard(plan('act', action='homeassistant.toggle', things=['group.ways_in'])) == 'security'
    assert admin.guard(plan('act', action='homeassistant.turn_on', things=['ways in'])) == 'security'
    dawn = {**EVENING, 'alias': 'Dawn', 'actions': [{'action': 'homeassistant.toggle', 'target': {'entity_id': 'group.ways_in'}}]}
    assert admin.guard(plan('routine', kind='automation', do='save', name='Dawn', config=dawn)) == 'security'
    assert admin.guard(plan('act', action='homeassistant.turn_on', things=['group.lamps'])) is None
    assert admin.guard(plan('act', action='cover.open_cover', things=['all blinds'])) is None      # a group of blinds is blinds


def test_a_cover_that_doesnt_say_what_it_is_waits_like_the_garage(house):
    _thing(house, 'cover.side', 'Side', state='closed')
    _thing(house, 'cover.back_window', 'Back window', state='closed', device_class='window')
    admin.forget()
    assert admin.guard(plan('act', action='cover.open_cover', things=['side'])) == 'unlock'
    assert admin.guard(plan('act', action='cover.open_cover', things=['back window'])) == 'unlock'
    house.states['cover.office_blind']['state'] = 'closed'
    admin.forget()
    assert admin.guard(plan('act', action='cover.open_cover', things=['blind'])) is None


def test_a_thing_made_from_a_template_waits(house):
    _thing(house, 'switch.open_sesame', 'Open sesame', platform='template')
    admin.forget()
    assert admin.guard(plan('act', action='switch.turn_on', things=['open sesame'])) == 'unread'      # it runs whatever its owner wrote


def test_a_new_name_for_what_opens_the_house_waits(house):
    _owner_made(house)
    assert admin.guard(plan('scene', do='rename', name='arriving', new_name='Dinner two')) == 'security'
    assert admin.guard(plan('arrange', things=['scene.arriving'], name='Dinner two')) == 'security'
    assert admin.guard(plan('routine', kind='script', do='rename', name='let them in', new_name='Porch lights')) == 'security'


def test_switching_off_what_tells_the_owner_a_way_in_is_open_waits(house):
    _automation(house, 'a-watch', 'Garage left open', [{'trigger': 'state', 'entity_id': 'cover.garage_door', 'to': 'open', 'for': '00:10:00'}],
                [{'action': 'notify.persistent_notification', 'data': {'message': 'The garage is open'}}])
    assert admin.guard(plan('routine', kind='automation', do='off', name='garage left open')) == 'security'
    _automation(house, 'a-hall', 'Hall light at the door', [{'trigger': 'state', 'entity_id': 'binary_sensor.front_door', 'to': 'on'}],
                [{'action': 'light.turn_on', 'target': {'entity_id': 'light.hall_ceiling'}}])
    assert admin.guard(plan('routine', kind='automation', do='off', name='hall light at the door')) is None


def test_a_routine_card_says_what_opens_first(house):
    body = {**EVENING, 'alias': 'Evening', 'actions': [
        {'action': 'light.turn_on', 'target': {'entity_id': 'light.hall_ceiling'}}, {'action': 'light.turn_on', 'target': {'entity_id': 'light.entry_ceiling'}},
        {'action': 'light.turn_on', 'target': {'entity_id': 'light.porch_light'}}, *UNLOCK]}
    made = plan('routine', kind='automation', do='save', name='Evening', config=body)
    assert admin.guard(made) == 'security' and made[0]['words'].startswith('Automation “Evening” made: at 6:45 pm, Front door lock unlocked')


def test_a_card_does_only_what_it_showed(house):
    body = {**EVENING, 'alias': 'Morning air', 'actions': [{'action': 'cover.open_cover', 'target': {'area_id': 'Office'}}]}
    args = dict(kind='automation', do='save', name='Morning air', config=body)
    card = admin.wait('Save Morning air?', plan('routine', **args), 'routine', args, login=ME)
    # Only names change, at once: Office is now Snug, and the porch is called Office. The card would read the same.
    do('place', kind='room', do='rename', name='office', new_name='Snug')
    do('place', kind='room', do='rename', name='porch', new_name='Office')
    with pytest.raises(admin.Refused, match='house has changed'):
        admin.confirm(card['id'], login=ME)
    assert not any(c.get('alias') == 'Morning air' for c in house.configs['automation'].values())
    # A new routine's id comes from the clock: a card made a moment ago is still the same plan.
    args = dict(kind='automation', do='save', name='At three', config={**EVENING, 'alias': 'At three', 'actions': UNLOCK})
    card = admin.wait('Save At three?', plan('routine', **args), 'routine', args, login=ME)
    time.sleep(0.01)
    assert admin.confirm(card['id'], login=ME)['done'] == ['Automation “At three” made: at 6:45 pm, Front door lock unlocked']


def test_undo_is_never_a_way_round_the_tap(house):
    house.states['lock.front_door']['state'] = 'unlocked'
    admin.forget()
    locked = do('act', action='lock.lock', things=['front door lock'])
    answer = admin.undo(locked['batch'], login=ME, via='app')
    card = answer['card']
    assert (card['title'], card['lines'], card['why']) == ('Put back Front door lock?', ['Front door lock unlocked'], admin.WHY['unlock'])
    assert house.sent[-1] == ('lock/lock', {'entity_id': 'lock.front_door'})          # nothing sent on the Undo tap
    assert admin.confirm(card['id'], login=ME)['done'] == ['Front door lock put back']    # the owner's tap on the card
    assert house.sent[-1] == ('lock/unlock', {'entity_id': 'lock.front_door'})
    with pytest.raises(admin.Refused, match='nothing left to undo'):
        admin.undo(locked['batch'], login=ME, via='app')
    # Disabled, a routine that unlocks is enabled again only by a tap; and so is one brought back as it was.
    _guards(house)
    off = admin.carry_out(plan('arrange', things=['let guests in'], enabled=False), login=ME, via='app')
    assert admin.undo(off['batch'], login=ME, via='app')['card']['why'] == admin.WHY['unread']
    changed = admin.carry_out(plan('routine', kind='automation', do='save', name='let guests in', config={
        **house.configs['automation']['a-guests'], 'actions': [{'action': 'light.turn_on', 'target': {'entity_id': 'light.entry_ceiling'}}]}),
        login=ME, via='app')
    assert admin.undo(changed['batch'], login=ME, via='app')['card']['why'] == admin.WHY['security']
    # Everyday changes are put back at once, as before.
    renamed = do('arrange', things=['reading lamp'], name='Book lamp')
    assert admin.undo(renamed['batch'], login=ME, via='app')['done'] == ['Book lamp put back']
