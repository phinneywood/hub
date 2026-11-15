"""A made-up house for the app's tests and its demo server: a fake Home Assistant behind the broker's door that
follows what Hub sends it, so a tap changes what the next reading shows. Rooms, people and music are fictional.

Everything Hub reads and sends still goes through read.py, control.py and the broker's own checks.
"""
import copy

from hub.tools.house import broker, control, read

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
        out[entity] = {'entity_id': entity, 'state': 'unknown', 'attributes': {'friendly_name': name}}
    for entity, name, kind, state in SENSORS:
        out[entity] = {'entity_id': entity, 'state': state, 'attributes': {'friendly_name': name, 'device_class': kind}}
    out['media_player.study_laptop'] = {'entity_id': 'media_player.study_laptop', 'state': 'unavailable',        # asleep, and no room's speaker
                                        'attributes': {'friendly_name': 'A laptop'}}
    out[BUTTON[0]] = {'entity_id': BUTTON[0], 'state': '2026-11-15T17:58:00.000-08:00',
                      'attributes': {'friendly_name': BUTTON[2], 'device_class': 'button', 'event_type': 'multi_press_1'}}
    out['alarm_control_panel.house'] = {'entity_id': 'alarm_control_panel.house', 'state': 'disarmed', 'attributes': {'friendly_name': 'Alarm'}}
    out['weather.home'] = {'entity_id': 'weather.home', 'state': 'rainy', 'attributes': {'friendly_name': 'Home', 'temperature': 52}}
    return out


class World:
    def __init__(self):
        self.states, self.sent, self.down, self.names = _states(), [], False, {}

    def _registry(self):
        rooms = {e: a for e, a, *_ in [*LIGHTS, *SWITCHES, *SPEAKERS, BUTTON]}
        entries = [{'entity_id': e, 'area_id': rooms.get(e), **({'platform': 'music_assistant'} if e.startswith('media_player.') else {})}
                   for e in self.states]
        return {'config/area_registry/list': [{'area_id': a, 'name': n, 'floor_id': f} for a, n, f in AREAS],
                'config/device_registry/list': [], 'config/entity_registry/list': entries, 'config/label_registry/list': [],
                'config/floor_registry/list': copy.deepcopy(FLOORS)}

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

    def _service(self, domain, service, data):
        named = data['entity_id']
        for entity in named if isinstance(named, list) else [named]:
            item = self.states[entity]
            if domain == 'light' and service in ('turn_on', 'turn_off'):
                self._light(entity, service == 'turn_on', data.get('brightness_pct'))
            elif service in ('turn_on', 'turn_off') and domain in ('switch', 'fan'):
                item['state'] = service.removeprefix('turn_')
            elif domain == 'scene':
                for light, level in SCENES[entity][1].items():
                    self._light(light, level > 0, level or None)
            elif service in ('media_play', 'media_pause') or (domain, service) == ('music_assistant', 'play_media'):
                state = 'paused' if service == 'media_pause' else 'playing'
                for member in self._group(entity):
                    self.states[member]['state'] = state
                    if service == 'play_media':
                        album = self.names.get(data['media_id'], '')
                        self.states[member]['attributes'].update(media_title='First track', media_artist='', media_album_name=album)
            elif service == 'volume_set':
                item['attributes']['volume_level'] = data['volume_level']
            elif service == 'join':
                group = self._group(entity) + [m for m in data['group_members'] if m not in self._group(entity)]
                for member in group:
                    leader = self.states[entity]
                    self.states[member]['state'] = leader['state']
                    self.states[member]['attributes'].update(group_members=list(group), **{k: leader['attributes'].get(k, '') for k in TRACK})
            elif service == 'unjoin':
                for member in self._group(entity):
                    self.states[member]['attributes']['group_members'] = [m for m in self._group(member) if m != entity]
                item['attributes']['group_members'], item['state'] = [], 'idle'

    def exchange(self, message, timeout):
        if self.down:
            raise broker.HomeDown('The Home Assistant broker on this Mac isn’t answering.')
        if message['op'] == 'request':
            domain, service = message['path'].removeprefix('/api/services/').split('/')
            self.sent.append((f'{domain}/{service}', copy.deepcopy(message['body'])))
            self._service(domain, service, message['body'])
            return []
        answers = {**self._registry(), 'get_states': copy.deepcopy(list(self.states.values())),
                   'automation/config': {'config': {'alias': 'Living room button', 'description': BUTTON_SAYS}}}
        return [({'automation': ['automation.living_button']} if c['item_id'] == BUTTON[0] else {})
                if c['type'] == 'search/related' else answers[c['type']] for c in message['commands']]


def install(patch):
    """Put the made-up house behind the broker's door. `patch` is pytest's monkeypatch or a MonkeyPatch."""
    world = World()
    patch.setattr(broker, '_exchange', world.exchange)
    patch.setattr(control, 'CHECK_WAIT', 0)
    read.clear_cache()
    return world
