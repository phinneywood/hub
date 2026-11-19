"""A made-up house for the app's tests and its demo server: a fake Home Assistant behind the broker's door that
follows what Hub sends it, so a tap changes what the next reading shows. Rooms, people and music are fictional.

Everything Hub reads and sends still goes through read.py, control.py, admin.py and the broker's own checks. It
follows setup changes too: a thing moved to another room is there at the next reading, a scene that was changed
sets what it now says, and an automation that was saved can be read back.
"""
import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hub import home
from hub.tools.house import admin, apple, broker, control, read

FLOORS = [{'floor_id': 'down', 'name': 'Downstairs', 'level': 0}, {'floor_id': 'up', 'name': 'Upstairs', 'level': 1},
          {'floor_id': 'out', 'name': 'Outside', 'level': None}]
AREAS = [('kitchen', 'Kitchen', 'down'), ('living', 'Living room', 'down'), ('dining', 'Dining', 'down'),
         ('entry', 'Entry', 'down'), ('office', 'Office', 'down'), ('bedroom', 'Bedroom', 'up'),
         ('robin', 'Robin’s room', 'up'), ('guest', 'Guest room', 'up'), ('hall', 'Hallway', 'up'), ('bath', 'Bath', 'up'),
         ('porch', 'Porch', 'out'), ('yard', 'Yard', 'out')]
DIM, PLAIN = ['brightness'], ['onoff']
# (entity, room, name, brightness percent or 0 for off, what it can do)
LIGHTS = [
    ('light.kitchen_ceiling', 'kitchen', 'Ceiling', 80, DIM), ('light.kitchen_pendants', 'kitchen', 'Island pendants', 70, DIM),
    ('light.kitchen_cabinets', 'kitchen', 'Under the cabinets', 90, DIM),
    ('light.living_floor_lamp', 'living', 'Floor lamp', 45, ['color_temp', 'hs']), ('light.living_reading', 'living', 'Reading lamp', 40, DIM),
    ('light.living_shelves', 'living', 'Shelf lights', 0, DIM),
    ('light.dining_pendant', 'dining', 'Pendant', 60, DIM), ('light.dining_sideboard', 'dining', 'Sideboard lamp', 50, DIM),
    ('light.entry_ceiling', 'entry', 'Ceiling', 0, DIM),
    ('light.office_desk', 'office', 'Desk lamp', 0, DIM), ('light.office_ceiling', 'office', 'Ceiling', 0, DIM),
    ('light.bedroom_left', 'bedroom', 'Bedside, left', 0, DIM), ('light.bedroom_right', 'bedroom', 'Bedside, right', 0, DIM),
    ('light.robin_night', 'robin', 'Night light', 12, DIM), ('light.robin_ceiling', 'robin', 'Ceiling', 0, DIM),
    ('light.guest_ceiling', 'guest', 'Ceiling', 0, DIM), ('light.hall_ceiling', 'hall', 'Ceiling', 0, DIM),
    ('light.bath_vanity', 'bath', 'Vanity', 0, PLAIN),
    ('light.porch_light', 'porch', 'Porch light', 100, PLAIN), ('light.yard_path', 'yard', 'Path lights', 0, PLAIN),
]
SWITCHES = [('switch.coffee_maker', 'kitchen', 'Coffee maker', 'off'), ('fan.office_fan', 'office', 'Ceiling fan', 'off'),
            ('fan.bedroom_fan', 'bedroom', 'Ceiling fan', 'off'), ('switch.robin_sound', 'robin', 'Sound machine', 'on')]
SPEAKERS = [('media_player.kitchen', 'kitchen', 'Kitchen speaker', 0.28), ('media_player.living', 'living', 'Living room speaker', 0.22),
            ('media_player.dining', None, 'Dining', 0.2), ('media_player.bedroom', 'bedroom', 'Bedroom speaker', 0.15),       # no room given: named after it
            ('media_player.robin', 'robin', 'Robin’s speaker', 0.1)]
PLAYING = ('media_player.living', 'media_player.kitchen')       # the living room leads
TRACK = {'media_title': 'Paper Lanterns', 'media_artist': 'The Harbor Trio', 'media_album_name': 'Evening Jazz'}
# What each made-up scene sets: {light: percent}; lights it doesn't name are left alone.
SCENES = {
    'scene.dinner': ('Dinner', {'light.kitchen_ceiling': 80, 'light.kitchen_pendants': 70, 'light.living_floor_lamp': 45,
                                'light.dining_pendant': 60, 'light.dining_sideboard': 50}),
    'scene.bedtime': ('Bedtime', {'light.robin_night': 12, 'light.robin_ceiling': 0, 'light.hall_ceiling': 20,
                                  'light.kitchen_ceiling': 30, 'light.kitchen_pendants': 0, 'light.kitchen_cabinets': 0,
                                  'light.living_floor_lamp': 30, 'light.living_reading': 0, 'light.dining_pendant': 0,
                                  'light.dining_sideboard': 0}),
    'scene.movie_night': ('Movie night', {'light.living_floor_lamp': 15, 'light.living_reading': 0, 'light.kitchen_ceiling': 0,
                                          'light.kitchen_pendants': 0, 'light.kitchen_cabinets': 0, 'light.dining_pendant': 0,
                                          'light.dining_sideboard': 0}),
}
# A button by the living room door; Home Assistant's automation for it says what its presses do.
BUTTON = ('event.living_button', 'living', 'Living room button')
BUTTON_SAYS = 'Single press: next of Dinner, Movie night, Off. Double press: Movie night. Long press: Off.'
SENSORS = [('binary_sensor.front_door', 'Front door', 'door', 'off'), ('binary_sensor.back_door', 'Back door', 'door', 'off'),
           ('binary_sensor.kitchen_window', 'Kitchen window', 'window', 'off')]
# One lamp is a device of its own, as most real ones are: the lamp is the device's main feature and takes its name.
DEVICES = [{'id': 'dev-floor-lamp', 'name': 'Floor lamp', 'name_by_user': None, 'area_id': 'living', 'labels': []}]
ON_DEVICE = {'light.living_floor_lamp': 'dev-floor-lamp', 'sensor.floor_lamp_signal': 'dev-floor-lamp'}
# Beyond lights and speakers: (entity, room, name, state, attributes)
OTHERS = [
    ('climate.hallway', 'hall', 'Thermostat', 'heat', {'current_temperature': 67, 'temperature': 68, 'hvac_modes': ['off', 'heat', 'cool'],
                                                       'preset_mode': 'none', 'fan_mode': 'auto'}),
    ('lock.front_door', 'entry', 'Front door lock', 'locked', {}),
    ('cover.garage_door', None, 'Garage door', 'closed', {'device_class': 'garage'}),
    ('cover.office_blind', 'office', 'Blind', 'open', {'device_class': 'blind', 'current_position': 100}),
    ('sensor.floor_lamp_signal', None, 'Signal', '-52', {'device_class': 'signal_strength'}),
]
# What Home Assistant keeps for its routines: {kind: {id: config}}. The button's automation says what its presses do.
AUTOMATIONS = {
    'a-porch': {'id': 'a-porch', 'alias': 'Porch at sunset', 'description': 'The porch light comes on at sunset.', 'mode': 'single',
                'triggers': [{'trigger': 'sun', 'event': 'sunset'}], 'conditions': [],
                'actions': [{'action': 'light.turn_on', 'target': {'entity_id': 'light.porch_light'}}]},
    'a-button': {'id': 'a-button', 'alias': 'Living room button', 'description': BUTTON_SAYS, 'mode': 'single',
                 'triggers': [{'trigger': 'state', 'entity_id': 'event.living_button'}], 'conditions': [],
                 'actions': [{'action': 'scene.turn_on', 'target': {'entity_id': 'scene.dinner'}}]},
}
SCRIPTS = {'goodnight': {'alias': 'Goodnight', 'mode': 'single', 'sequence': [
    {'action': 'scene.turn_on', 'target': {'entity_id': 'scene.bedtime'}}, {'action': 'lock.lock', 'target': {'entity_id': 'lock.front_door'}}]}}
# A made-up light show, only where it is asked for (the demo, the show's own tests): two scripts and a timing number
# with the names Hub looks for, a switch that says the show is on, and the speaker that plays it.
SHOW = {'start': 'script.party_lights_start', 'end': 'script.party_lights_end', 'timing': 'input_number.party_lights_timing',
        'flag': 'input_boolean.party_lights', 'player': 'media_player.living'}


def _show_parts(world, on, timing):
    s = SHOW
    world.states.update({
        s['start']: {'entity_id': s['start'], 'state': 'off', 'attributes': {'friendly_name': 'Start light show', 'mode': 'restart'}},
        s['end']: {'entity_id': s['end'], 'state': 'off', 'attributes': {'friendly_name': 'End light show', 'mode': 'single'}},
        s['timing']: {'entity_id': s['timing'], 'state': str(float(timing)), 'attributes': {
            'friendly_name': 'Light show timing', 'min': -1500.0, 'max': 1500.0, 'step': 50.0, 'unit_of_measurement': 'ms'}},
        s['flag']: {'entity_id': s['flag'], 'state': 'on' if on else 'off', 'attributes': {'friendly_name': 'Party lights'}}})
    world.configs['script'][s['start'].partition('.')[2]] = {
        'alias': 'Start light show', 'mode': 'restart',
        'fields': {'media_id': {'required': True, 'selector': {'text': {}}}, 'media_type': {'default': 'playlist'}},
        'sequence': [{'if': [{'condition': 'state', 'entity_id': s['flag'], 'state': 'off'}],
                      'then': [{'action': 'scene.create', 'data': {'scene_id': 'party_lights_before', 'snapshot_entities': ['light.living_floor_lamp']}}]},
                     {'action': 'input_boolean.turn_on', 'target': {'entity_id': s['flag']}},
                     {'action': 'music_assistant.play_media', 'target': {'entity_id': s['player']},
                      'data': {'media_id': '{{ media_id }}', 'media_type': '{{ media_type }}', 'enqueue': 'replace'}}]}
    world.configs['script'][s['end'].partition('.')[2]] = {
        'alias': 'End light show', 'mode': 'single',
        'sequence': [{'action': 'media_player.media_pause', 'target': {'entity_id': s['player']}},
                     {'action': 'input_boolean.turn_off', 'target': {'entity_id': s['flag']}}]}


def add_show(world, on=False, timing=0):
    """Give the made-up house its light show, kept through reset(). `on`: a show is already running."""
    world.with_show = True
    _show_parts(world, on, timing)


# The integrations the made-up Home Assistant has set up. One is not connecting; one is titled with an account.
ENTRIES = [
    {'entry_id': 'entry0001bridge', 'domain': 'hue', 'title': 'Kitchen bridge', 'state': 'loaded', 'disabled_by': None, 'supports_unload': True},
    {'entry_id': 'entry0002bridge', 'domain': 'hue', 'title': 'Upstairs bridge', 'state': 'setup_retry', 'disabled_by': None, 'supports_unload': True},
    {'entry_id': 'entry0003calendar', 'domain': 'google', 'title': 'alex@example.com', 'state': 'loaded', 'disabled_by': None, 'supports_unload': True},
    {'entry_id': 'entry0004speakers', 'domain': 'music_assistant', 'title': 'Music Assistant', 'state': 'loaded', 'disabled_by': None, 'supports_unload': True},
    {'entry_id': 'entry0005sun', 'domain': 'sun', 'title': 'Sun', 'state': 'loaded', 'disabled_by': None, 'supports_unload': False},
]
CORE = {'location_name': 'Home', 'time_zone': 'America/Los_Angeles', 'currency': 'USD', 'country': 'US', 'language': 'en',
        'unit_system': {'temperature': '°F', 'length': 'mi'}, 'latitude': 0.0, 'longitude': 0.0}


def slug(name):
    return re.sub(r'[^a-z0-9]+', '_', str(name).lower()).strip('_') or 'thing'


def _scene_configs():
    return {slug(name): {'id': slug(name), 'name': name, 'entities': {
        light: ({'state': 'on', 'brightness': round(level * 255 / 100)} if level else {'state': 'off'}) for light, level in lights.items()}}
        for name, lights in SCENES.values()}


def _states():
    out = {}
    for entity, _, name, level, modes in LIGHTS:
        out[entity] = {'entity_id': entity, 'state': 'on' if level else 'off', 'attributes': {
            'friendly_name': name, 'supported_color_modes': list(modes), **({'brightness': round(level * 255 / 100)} if level else {})}}
    for entity, _, name, state in SWITCHES:
        out[entity] = {'entity_id': entity, 'state': state, 'attributes': {'friendly_name': name}}
    for entity, _, name, volume in SPEAKERS:
        on = entity in PLAYING
        out[entity] = {'entity_id': entity, 'state': 'playing' if on else 'idle', 'attributes': {
            'friendly_name': name, 'volume_level': volume, 'group_members': list(PLAYING) if on else [], **(TRACK if on else {})}}
    for entity, (name, _) in SCENES.items():
        out[entity] = {'entity_id': entity, 'state': 'unknown', 'attributes': {'friendly_name': name, 'id': slug(name)}}
    for entity, _, name, state, attrs in OTHERS:
        out[entity] = {'entity_id': entity, 'state': state, 'attributes': {'friendly_name': name, **attrs}}
    for key, config in AUTOMATIONS.items():
        entity = 'automation.' + slug(config['alias'])
        out[entity] = {'entity_id': entity, 'state': 'on', 'attributes': {'friendly_name': config['alias'], 'id': key}}
    for key, config in SCRIPTS.items():
        out['script.' + key] = {'entity_id': 'script.' + key, 'state': 'off', 'attributes': {'friendly_name': config['alias']}}
    for entity, name, kind, state in SENSORS:
        out[entity] = {'entity_id': entity, 'state': state, 'attributes': {'friendly_name': name, 'device_class': kind}}
    out['media_player.study_laptop'] = {'entity_id': 'media_player.study_laptop', 'state': 'unavailable',        # asleep, and no room's speaker
                                        'attributes': {'friendly_name': 'A laptop'}}
    out[BUTTON[0]] = {'entity_id': BUTTON[0], 'state': '2026-11-15T17:58:00.000-08:00',
                      'attributes': {'friendly_name': BUTTON[2], 'device_class': 'button', 'event_type': 'multi_press_1'}}
    out['alarm_control_panel.house'] = {'entity_id': 'alarm_control_panel.house', 'state': 'disarmed', 'attributes': {'friendly_name': 'Alarm'}}
    out['weather.home'] = {'entity_id': 'weather.home', 'state': 'rainy', 'attributes': {'friendly_name': 'Home', 'temperature': 52}}
    return out


# A made-up forecast for the made-up house, by the hour from the given moment: a grey morning that warms up,
# rain from three to six in the afternoon, a clear evening, and a bright day after. (condition, °F, inches)
WEATHER_DAY = {
    0: ('cloudy', 44, 0), 1: ('cloudy', 43, 0), 2: ('cloudy', 43, 0), 3: ('fog', 42, 0), 4: ('fog', 42, 0), 5: ('fog', 43, 0),
    6: ('cloudy', 44, 0), 7: ('partlycloudy', 45, 0), 8: ('partlycloudy', 46, 0), 9: ('partlycloudy', 48, 0),
    10: ('partlycloudy', 50, 0), 11: ('cloudy', 51, 0), 12: ('cloudy', 52, 0), 13: ('cloudy', 53, 0), 14: ('cloudy', 53, 0),
    15: ('rainy', 51, 0.04), 16: ('pouring', 50, 0.12), 17: ('rainy', 49, 0.05), 18: ('cloudy', 48, 0), 19: ('partlycloudy', 47, 0),
    20: ('clear-night', 46, 0), 21: ('clear-night', 45, 0), 22: ('clear-night', 45, 0), 23: ('clear-night', 44, 0)}
WEATHER_DAYS = [('rainy', 53, 42, 0.21), ('sunny', 56, 41, 0), ('partlycloudy', 54, 43, 0), ('rainy', 50, 44, 0.3),
                ('cloudy', 51, 42, 0.0), ('sunny', 55, 40, 0)]
SUN = ('07:12', '16:31')          # sunrise and sunset, every day


def forecast(kind, now):
    """What weather.get_forecasts answers for the made-up house, seen from `now`."""
    start = now.replace(minute=0, second=0, microsecond=0)
    if kind == 'hourly':
        rows = []
        for n in range(48):
            moment = start + timedelta(hours=n)
            condition, temp, rain = WEATHER_DAY[moment.hour]
            if moment.date() > now.date() and condition in ('rainy', 'pouring'):
                condition, rain = 'partlycloudy', 0            # the day after is dry
            rows.append({'condition': condition, 'datetime': moment.astimezone(timezone.utc).isoformat(), 'temperature': temp + (2 if moment.date() > now.date() else 0),
                         'precipitation': rain, 'humidity': 80, 'wind_speed': 6.2, 'cloud_coverage': 60})
        return rows
    return [{'condition': c, 'datetime': (start.replace(hour=12) + timedelta(days=n)).astimezone(timezone.utc).isoformat(),
             'temperature': high, 'templow': low, 'precipitation': rain, 'uv_index': 1.4, 'humidity': 82, 'wind_speed': 7}
            for n, (c, high, low, rain) in enumerate(WEATHER_DAYS)]


def sun(now):
    """Home Assistant's sun as seen at `now`: when it next rises and sets, in UTC."""
    def at(clock, days=0):
        hour, minute = (int(x) for x in clock.split(':'))
        return (now + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    rises = at(SUN[0]) if now < at(SUN[0]) else at(SUN[0], 1)
    sets = at(SUN[1]) if now < at(SUN[1]) else at(SUN[1], 1)
    return {'entity_id': 'sun.sun', 'state': 'above_horizon' if at(SUN[0]) <= now < at(SUN[1]) else 'below_horizon',
            'attributes': {'friendly_name': 'Sun', 'next_rising': rises.astimezone(timezone.utc).isoformat(),
                           'next_setting': sets.astimezone(timezone.utc).isoformat()}}


# Apple Home's own locks and a doorbell, as HomeClaw's list gives them (every value is text), and one light that
# Home Assistant shares into Apple Home, which Hub leaves to Home Assistant.
ACCESSORIES = [
    {'id': 'A-SIDE', 'name': 'Side door lock', 'room': 'Entry', 'category': 'lock', 'manufacturer': 'Example Locks', 'reachable': False,
     'semantic_type': 'door_lock', 'state': {'battery_level': '1', 'lock_current_state': 'locked', 'lock_target_state': 'unlocked'}},
    {'id': 'A-BACK', 'name': 'Back door lock', 'room': 'Kitchen', 'category': 'lock', 'manufacturer': 'Example Locks', 'reachable': True,
     'semantic_type': 'door_lock', 'state': {'battery_level': '18', 'lock_current_state': 'locked', 'lock_target_state': 'locked'}},
    {'id': 'A-BELL', 'name': 'Doorbell', 'room': 'Porch', 'category': 'video_doorbell', 'manufacturer': 'Example', 'reachable': True,
     'state': {'battery_level': '76', 'input_event': '0'}},
    {'id': 'A-LAMP', 'name': 'Ceiling', 'room': 'Kitchen', 'category': 'lightbulb', 'reachable': True, 'state': {'power': '1'},
     'bridge': {'manufacturer': 'Home Assistant', 'name': 'HASS Bridge'}},
]


class World:
    def __init__(self):
        self.sent, self.down, self.names = [], False, {}
        self.with_show = False   # add_show(): the made-up light show, kept through reset()
        self.clock = None        # what "now" is to the weather and the sun; None: the real time
        self.logbook = []        # what Home Assistant's logbook answers, for the Activity screen
        self.setup = []          # every request that came through the setup door, in order
        self.refuse = None       # a setup command Home Assistant says no to, for tests: its type, or 'config'
        self.reset()

    def reset(self):
        """The house as it started."""
        self.states = _states()
        rooms = {e: a for e, a, *_ in [*LIGHTS, *SWITCHES, *SPEAKERS, BUTTON, *OTHERS]}
        self.entries = {e: {'entity_id': e, 'area_id': None if e in ON_DEVICE else rooms.get(e), 'device_id': ON_DEVICE.get(e),
                            'name': None, 'icon': None, 'labels': [], 'hidden_by': None, 'disabled_by': None,
                            'has_entity_name': e in ON_DEVICE, 'original_name': 'Signal' if e.startswith('sensor.') else None,
                            'entity_category': 'diagnostic' if e.startswith('sensor.') else None,
                            **({'platform': 'music_assistant'} if e.startswith('media_player.') else {})} for e in self.states}
        self.areas = [{'area_id': a, 'name': n, 'floor_id': f, 'labels': [], 'aliases': [], 'icon': None} for a, n, f in AREAS]
        self.floors, self.labels, self.devices = copy.deepcopy(FLOORS), [], copy.deepcopy(DEVICES)
        self.configs = {'scene': _scene_configs(), 'automation': copy.deepcopy(AUTOMATIONS), 'script': copy.deepcopy(SCRIPTS)}
        self.by_hand = {}        # automations written in Home Assistant's own files, by entity: read, never written, from Hub
        self.helpers, self.core = {kind: [] for kind in broker.HELPERS}, copy.deepcopy(CORE)
        # Home Assistant's dashboards: its own map, which it draws by itself, and one a person made.
        self.boards = [{'id': 'map', 'url_path': 'map', 'title': 'Map', 'mode': 'storage', 'icon': 'mdi:map', 'show_in_sidebar': True, 'require_admin': False},
                       {'id': 'wall_tablet', 'url_path': 'wall-tablet', 'title': 'Wall tablet', 'mode': 'storage', 'icon': None, 'show_in_sidebar': True,
                        'require_admin': False}]
        self.board_configs = {'map': {'strategy': {'type': 'map'}}, 'wall-tablet': {'views': [
            {'title': 'Downstairs', 'path': 'downstairs', 'cards': [{'type': 'entities', 'entities': ['light.kitchen_ceiling']}]},
            {'title': 'Clock', 'path': 'clock', 'cards': [{'type': 'markdown', 'content': 'A card its owner wrote by hand'}]}]}}
        if self.with_show:
            _show_parts(self, False, 0)

    def _registry(self):
        for entity in self.states:      # anything that appeared since (a new scene, a helper) is in the registry too
            self.entries.setdefault(entity, {'entity_id': entity, 'area_id': None, 'device_id': None, 'name': None, 'labels': [],
                                             'hidden_by': None, 'disabled_by': None})
        entries = [e for e in self.entries.values() if e['entity_id'] in self.states]
        return {'config/area_registry/list': copy.deepcopy(self.areas), 'config/device_registry/list': copy.deepcopy(self.devices),
                'config/entity_registry/list': copy.deepcopy(entries), 'config/label_registry/list': copy.deepcopy(self.labels),
                'config/floor_registry/list': copy.deepcopy(self.floors)}

    def _light(self, entity, on, level=None):
        item = self.states[entity]
        item['state'] = 'on' if on else 'off'
        if not on:
            item['attributes'].pop('brightness', None)
        elif level is not None and 'onoff' not in item['attributes']['supported_color_modes']:
            item['attributes']['brightness'] = round(level * 255 / 100)
        elif 'onoff' not in item['attributes']['supported_color_modes']:
            item['attributes'].setdefault('brightness', 153)       # back on at 60%

    def _group(self, entity):
        return list(self.states[entity]['attributes'].get('group_members') or [entity])

    def _scene(self, entity):
        """Set what a scene keeps, as Home Assistant has it now."""
        kept = self.configs['scene'].get(self.states[entity]['attributes'].get('id'), {}).get('entities', {})
        for thing, wanted in kept.items():
            if thing.startswith('light.') and thing in self.states:
                level = round(wanted['brightness'] * 100 / 255) if wanted.get('brightness') else None
                self._light(thing, wanted.get('state') == 'on', level)
            elif thing in self.states:
                self.states[thing]['state'] = wanted.get('state', 'on')

    def _show(self, start, variables):
        """What the made-up show's scripts do: the switch on and its speaker playing, or the music paused and it off."""
        self.states[SHOW['flag']]['state'] = 'on' if start else 'off'
        for member in self._group(SHOW['player']):
            self.states[member]['state'] = 'playing' if start else 'paused'
            if start:
                self.states[member]['attributes'].update(media_title='First track', media_artist='',
                                                         media_album_name=self.names.get(variables.get('media_id'), ''))

    def _service(self, domain, service, data):
        named = data.get('entity_id', [])
        for entity in named if isinstance(named, list) else [named]:
            item = self.states[entity]
            attrs = item['attributes']
            if domain == 'script' and self.with_show and entity in (SHOW['start'], SHOW['end']):
                self._show(entity == SHOW['start'], data.get('variables') or {})
            elif domain == 'input_number' and service == 'set_value':
                item['state'] = str(float(data['value']))
            elif domain == 'light' and service in ('turn_on', 'turn_off'):
                self._light(entity, service == 'turn_on', data.get('brightness_pct'))
            elif domain == 'fan' and service == 'set_percentage':
                item['state'], attrs['percentage'] = ('on' if data['percentage'] else 'off'), data['percentage']
            elif domain == 'climate':
                item['state'] = data.get('hvac_mode', 'off' if service == 'turn_off' else item['state'])
                attrs.update({k: data[k] for k in ('temperature', 'preset_mode', 'fan_mode') if k in data})
            elif domain == 'lock':
                item['state'] = {'lock': 'locked', 'unlock': 'unlocked'}.get(service, item['state'])
            elif domain == 'cover':
                item['state'] = {'open_cover': 'open', 'close_cover': 'closed'}.get(service, item['state'])
                if 'current_position' in attrs:
                    attrs['current_position'] = data.get('position', 100 if item['state'] == 'open' else 0)
                    item['state'] = 'open' if attrs['current_position'] else 'closed'
            elif service in ('turn_on', 'turn_off') and domain != 'scene' and item['state'] in ('on', 'off'):
                item['state'] = service.removeprefix('turn_')
            elif domain == 'scene':
                self._scene(entity)
            elif service in ('media_play', 'media_pause') or (domain, service) == ('music_assistant', 'play_media'):
                state = 'paused' if service == 'media_pause' else 'playing'
                for member in self._group(entity):
                    self.states[member]['state'] = state
                    if service == 'play_media':
                        album = self.names.get(data['media_id'], '')
                        self.states[member]['attributes'].update(media_title='First track', media_artist='', media_album_name=album)
            elif service == 'volume_set':
                attrs['volume_level'] = data['volume_level']
            elif service == 'join':
                group = self._group(entity) + [m for m in data['group_members'] if m not in self._group(entity)]
                for member in group:
                    leader = self.states[entity]
                    self.states[member]['state'] = leader['state']
                    self.states[member]['attributes'].update(group_members=list(group), **{k: leader['attributes'].get(k, '') for k in TRACK})
            elif service == 'unjoin':
                for member in self._group(entity):
                    self.states[member]['attributes']['group_members'] = [m for m in self._group(member) if m != entity]
                attrs['group_members'], item['state'] = [], 'idle'

    # ---- the setup door ----

    def _entity_of(self, kind, key):
        """The entity Home Assistant made for one kept scene, automation or script."""
        if kind == 'script':
            return 'script.' + key
        return next((e for e, s in self.states.items() if e.startswith(kind + '.') and s['attributes'].get('id') == key), None)

    def _related(self, item):
        """Which automations and scripts refer to this thing, as Home Assistant's own search indexes them: the
        entities under entity_id in state, numeric_state and event triggers, in conditions, and in what an action
        aims at. Never one read in a template, nor a time trigger's "at"."""
        def named(value, out):
            if isinstance(value, dict):
                for key, inner in value.items():
                    if key in ('entity_id', 'scene'):
                        out.update(e for e in ([inner] if isinstance(inner, str) else inner if isinstance(inner, list) else [])
                                   if isinstance(e, str) and '{' not in e)
                    else:
                        named(inner, out)
            elif isinstance(value, list):
                for inner in value:
                    named(inner, out)
            return out

        def indexed(kind, config):
            if kind == 'script':
                return named(config.get('sequence'), set())
            out = set()
            for trigger in config.get('triggers', config.get('trigger')) or []:
                if isinstance(trigger, dict) and trigger.get('trigger', trigger.get('platform')) in ('state', 'numeric_state'):
                    named({'entity_id': trigger.get('entity_id')}, out)
                elif isinstance(trigger, dict) and trigger.get('trigger', trigger.get('platform')) == 'event':
                    named({'entity_id': (trigger.get('event_data') or {}).get('entity_id')}, out)
            return named([config.get('conditions', config.get('condition')), config.get('actions', config.get('action'))], out)

        found = {}
        for kind in ('automation', 'script'):
            kept = [*((self._entity_of(kind, key), config) for key, config in self.configs[kind].items()),
                    *(self.by_hand.items() if kind == 'automation' else ())]
            ids = [e for e, config in kept if e and e != item and item in indexed(kind, config)]
            if ids:
                found[kind] = ids
        return found

    def _config(self, method, kind, key, body):
        kept = self.configs[kind]
        if method == 'GET':
            if key not in kept:
                raise broker.Rejected('HTTP 404')
            return copy.deepcopy(kept[key])
        self.setup.append((f'{method} {kind}', key, copy.deepcopy(body)))
        if self.refuse == 'config' or (method == 'DELETE' and key not in kept):
            raise broker.Rejected('HTTP 400')
        entity = self._entity_of(kind, key)
        if method == 'DELETE':
            del kept[key]
            self.states.pop(entity, None)
            return {'result': 'ok'}
        kept[key] = copy.deepcopy(body)
        name = body.get('name' if kind == 'scene' else 'alias') or key
        if entity is None or entity not in self.states:
            entity = f'{kind}.{key if kind == "script" else slug(name)}'
            self.states[entity] = {'entity_id': entity, 'state': {'scene': 'unknown', 'automation': 'on', 'script': 'off'}[kind],
                                   'attributes': {} if kind == 'script' else {'id': key}}
        self.states[entity]['attributes']['friendly_name'] = name
        return {'result': 'ok'}

    def _write(self, command):
        """One registry, helper or core change, as Home Assistant answers it."""
        kind = command['type']
        self.setup.append(copy.deepcopy(command))
        if kind == self.refuse:
            raise broker.Rejected('invalid_format')
        fields = {k: v for k, v in command.items() if k != 'type'}
        if kind.startswith('lovelace/'):
            return self._board(kind, fields)
        if kind == 'config/core/update':
            units = fields.pop('unit_system', None)
            self.core.update(fields, **({'unit_system': {'temperature': '°C' if units == 'metric' else '°F'}} if units else {}))
            return {}
        if kind == 'config/entity_registry/update':
            entry = self.entries.get(fields.pop('entity_id'))
            if entry is None:
                raise broker.Rejected('not_found')
            entry.update(fields)
            return {'entity_entry': copy.deepcopy(entry)}
        table, what, verb = kind.removeprefix('config/').rpartition('/')[0], None, kind.rpartition('/')[2]
        if table.endswith('_registry'):
            what = table.removesuffix('_registry')
            rows = {'device': self.devices, 'area': self.areas, 'floor': self.floors, 'label': self.labels}[what]
            key = 'id' if what == 'device' else what + '_id'
            if what == 'device':
                fields['id'] = fields.pop('device_id')
        else:                           # a helper
            rows, key, fields = self.helpers[table], 'id', {('id' if k == table + '_id' else k): v for k, v in fields.items()}
        if verb == 'create':
            made = slug(fields['name'])
            while any(r[key] == made for r in rows):
                made += '_2'
            row = {key: made, **({'labels': [], 'aliases': [], 'icon': None, 'floor_id': None} if what == 'area' else {}), **fields}
            rows.append(row)
            if what is None:
                self.states[f'{table}.{made}'] = {'entity_id': f'{table}.{made}', 'state': 'off' if table == 'input_boolean' else 'unknown',
                                                  'attributes': {'friendly_name': fields['name']}}
            return copy.deepcopy(row)
        row = next((r for r in rows if r[key] == fields.get(key)), None)
        if row is None:
            raise broker.Rejected('not_found')
        if verb == 'update':
            row.update(fields)
            return copy.deepcopy(row)
        rows.remove(row)
        gone = row[key]
        if what == 'area':
            for other in [*self.devices, *self.entries.values()]:
                if other.get('area_id') == gone:
                    other['area_id'] = None
        elif what == 'floor':
            for area in self.areas:
                if area.get('floor_id') == gone:
                    area['floor_id'] = None
        elif what == 'label':
            for other in [*self.areas, *self.devices, *self.entries.values()]:
                other['labels'] = [g for g in other.get('labels') or [] if g != gone]
        elif what is None:
            self.states.pop(f'{table}.{gone}', None)
        return {}

    def _board(self, kind, fields):
        """One change to a dashboard, as Home Assistant answers it."""
        if kind == 'lovelace/dashboards/create':
            if any(b['url_path'] == fields['url_path'] for b in self.boards):
                raise broker.Rejected('home_assistant_error')
            made = {'id': fields['url_path'].replace('-', '_'), 'icon': None, 'show_in_sidebar': True, 'require_admin': False, **fields}
            self.boards.append(made)
            return copy.deepcopy(made)
        if kind.startswith('lovelace/dashboards/'):
            row = next((b for b in self.boards if b['id'] == fields.get('dashboard_id')), None)
            if row is None:
                raise broker.Rejected('not_found')
            if kind.endswith('/delete'):
                self.boards.remove(row)
                self.board_configs.pop(row['url_path'], None)
                return {}
            row.update({k: v for k, v in fields.items() if k != 'dashboard_id'})
            return copy.deepcopy(row)
        if not any(b['url_path'] == fields['url_path'] for b in self.boards):
            raise broker.Rejected('config_not_found')
        if kind == 'lovelace/config/save':
            self.board_configs[fields['url_path']] = copy.deepcopy(fields['config'])
        else:
            self.board_configs.pop(fields['url_path'], None)
        return {}

    def now(self):
        return self.clock() if self.clock else datetime.now(home.PACIFIC)

    def _read(self, command):
        kind = command['type']
        if kind == 'call_service':
            entity = command['target']['entity_id']
            if (command['domain'], command['service']) != ('weather', 'get_forecasts') or not command.get('return_response') \
                    or entity not in self.states:
                raise broker.Rejected('service_validation_error')
            return {'context': {'id': 'x'}, 'response': {entity: {'forecast': forecast(command['service_data']['type'], self.now())}}}
        if kind == 'lovelace/dashboards/list':
            return copy.deepcopy(self.boards)
        if kind == 'lovelace/config':
            if command.get('url_path') not in self.board_configs:
                raise broker.Rejected('config_not_found')
            return copy.deepcopy(self.board_configs[command['url_path']])
        if kind == 'search/related':
            if command['item_id'] == BUTTON[0]:
                return {'automation': ['automation.living_room_button']}
            return self._related(command['item_id'])
        if kind in ('automation/config', 'script/config'):
            entity = command['entity_id']
            key = entity.partition('.')[2] if kind == 'script/config' else self.states.get(entity, {}).get('attributes', {}).get('id')
            if kind == 'automation/config' and key is None:         # one written in Home Assistant's own files
                if entity not in self.by_hand:
                    raise broker.Rejected('not_found')
                return {'config': copy.deepcopy(self.by_hand[entity])}
            return {'config': copy.deepcopy(self.configs[kind.partition('/')[0]].get(key, {}))}
        if kind == 'validate_config':
            return {part: ({'valid': False, 'error': "extra keys not allowed @ data[0]['nonsense']"} if 'nonsense' in repr(value)
                           else {'valid': True, 'error': None}) for part, value in command.items() if part != 'type'}
        if kind == 'get_config':
            return copy.deepcopy(self.core)
        if kind == 'config_entries/get':
            return copy.deepcopy(ENTRIES)
        if kind.endswith('/list') and kind.partition('/')[0] in self.helpers:
            return copy.deepcopy(self.helpers[kind.partition('/')[0]])
        states = [*self.states.values(), *([] if 'sun.sun' in self.states else [sun(self.now())])]
        answers = {**self._registry(), 'get_states': copy.deepcopy(states), 'logbook/get_events': copy.deepcopy(self.logbook)}
        return answers[kind]

    def exchange(self, message, timeout):
        if self.down:
            raise broker.HomeDown('The Home Assistant broker on this Mac isn’t answering.')
        if message['op'] == 'request':
            path = message['path']
            if path.startswith('/api/config/config_entries/entry/'):
                entry = path.removeprefix('/api/config/config_entries/entry/').removesuffix('/reload')
                self.setup.append(('reload', entry))
                if self.refuse == 'reload' or entry not in [e['entry_id'] for e in ENTRIES]:
                    raise broker.Rejected('HTTP 404')
                return {'require_restart': False}
            if path.startswith('/api/config/'):
                kind, _, key = path.removeprefix('/api/config/').partition('/config/')
                return self._config(message['method'], kind, key, message.get('body'))
            domain, service = path.removeprefix('/api/services/').split('/')
            self.sent.append((f'{domain}/{service}', copy.deepcopy(message['body'])))
            self._service(domain, service, message['body'])
            return []
        if 'command' in message:
            return self._write(message['command'])
        return [self._read(c) for c in message['commands']]


def install(patch, accessories=True):
    """Put the made-up house behind the broker's door, and its Apple Home behind HomeClaw's list. `patch` is
    pytest's monkeypatch or a MonkeyPatch. world.accessories can be changed; the next reading shows it."""
    world = World()
    world.accessories = copy.deepcopy(ACCESSORIES) if accessories else None
    patch.setattr(broker, '_exchange', world.exchange)
    patch.setattr(control, 'CHECK_WAIT', 0)
    real = apple._run
    patch.setattr(apple, 'CLI', Path('/nonexistent/homeclaw-cli'))    # never the Mac's own HomeClaw, if it has one

    def homeclaw(*args):
        if args == ('list',) and world.accessories is not None:
            return json.loads(json.dumps(world.accessories))
        return real(*args)                      # Hub's own checks, then "HomeClaw isn't installed on this Mac."
    patch.setattr(apple, '_run', homeclaw)
    admin.forget()
    read.clear_cache()
    return world
