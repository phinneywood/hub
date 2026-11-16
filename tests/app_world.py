"""A made-up house for the app's tests and its demo server: a fake Home Assistant behind the broker's door that
follows what Hub sends it, so a tap changes what the next reading shows. Rooms, people and music are fictional.

Everything Hub reads and sends still goes through read.py, control.py, admin.py and the broker's own checks. It
follows setup changes too: a thing moved to another room is there at the next reading, a scene that was changed
sets what it now says, and an automation that was saved can be read back.
"""
import copy
import re

from hub.tools.house import admin, broker, control, read

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


class World:
    def __init__(self):
        self.sent, self.down, self.names = [], False, {}
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
        self.helpers, self.core = {kind: [] for kind in broker.HELPERS}, copy.deepcopy(CORE)
        # Home Assistant's dashboards: its own map, which it draws by itself, and one a person made.
        self.boards = [{'id': 'map', 'url_path': 'map', 'title': 'Map', 'mode': 'storage', 'icon': 'mdi:map', 'show_in_sidebar': True, 'require_admin': False},
                       {'id': 'wall_tablet', 'url_path': 'wall-tablet', 'title': 'Wall tablet', 'mode': 'storage', 'icon': None, 'show_in_sidebar': True,
                        'require_admin': False}]
        self.board_configs = {'map': {'strategy': {'type': 'map'}}, 'wall-tablet': {'views': [
            {'title': 'Downstairs', 'path': 'downstairs', 'cards': [{'type': 'entities', 'entities': ['light.kitchen_ceiling']}]},
            {'title': 'Clock', 'path': 'clock', 'cards': [{'type': 'markdown', 'content': 'A card its owner wrote by hand'}]}]}}

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

    def _service(self, domain, service, data):
        named = data.get('entity_id', [])
        for entity in named if isinstance(named, list) else [named]:
            item = self.states[entity]
            attrs = item['attributes']
            if domain == 'light' and service in ('turn_on', 'turn_off'):
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
        """Which automations and scripts name this thing anywhere in what they keep."""
        found = {}
        for kind in ('automation', 'script'):
            ids = [self._entity_of(kind, key) for key, config in self.configs[kind].items() if f"'{item}'" in repr(config)]
            if [e for e in ids if e and e != item]:
                found[kind] = [e for e in ids if e and e != item]
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

    def _read(self, command):
        kind = command['type']
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
        answers = {**self._registry(), 'get_states': copy.deepcopy(list(self.states.values())), 'logbook/get_events': copy.deepcopy(self.logbook)}
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


def install(patch):
    """Put the made-up house behind the broker's door. `patch` is pytest's monkeypatch or a MonkeyPatch."""
    world = World()
    patch.setattr(broker, '_exchange', world.exchange)
    patch.setattr(control, 'CHECK_WAIT', 0)
    admin.forget()
    return world
