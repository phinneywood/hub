"""Talks to the HA broker over its owner-only Unix socket. Never reaches the network itself.

The broker (home-ops `ha-maintenance/ha_broker.py`) is the one holder of the owner's Home Assistant
administrator token; Hub never sees it. The broker passes on whatever it is sent, so the fence is here. There
are two doors, and nothing else leaves Hub:

- **The everyday door**: the reads in READS and ASKS and the actions in WRITES. Lights, power and speakers: what a
  tap in the app or a plain request to Ask needs.
- **The setup door** (owner decision, 2026-11-15: Ask may change anything in Home Assistant): the named commands in
  SETUP_READS and SETUP_WRITES, the scene, automation and script configs in CONFIGS, and services in
  SERVICE_DOMAINS, and reloading an integration. Used only by admin.py, which records every change with the way
  back and asks for a tap first where its rules say so. Still shut: tokens and users, backups, add-ons and the
  system itself, adding, removing or disabling integrations, removing devices, and services that run code or
  reach other machines.

Every request's data is checked before it is sent.
"""
import json
import re
import socket

from ... import config

READS = ('config/area_registry/list', 'config/device_registry/list', 'config/entity_registry/list',
         'config/label_registry/list', 'config/floor_registry/list', 'get_states')
# Reads that take arguments, with the only keys each may carry: what an automation does, to say what a
# button's presses do, and Home Assistant's logbook for a stretch of time, for the Activity screen. Still read-only.
ASKS = {'search/related': {'item_type', 'item_id'}, 'automation/config': {'entity_id'},
        'logbook/get_events': {'start_time', 'end_time'}}
# (domain, service) -> the data keys it may carry besides entity_id. Lights, power and speakers only;
# locks, the garage, the alarm, the thermostat and automations are not here. A light takes at most one colour.
COLOURS = {'rgb_color', 'hs_color', 'xy_color', 'color_temp_kelvin'}
KELVIN = (2000, 6500)
WRITES = {
    ('light', 'turn_on'): {'brightness_pct', *COLOURS}, ('light', 'turn_off'): set(),
    ('switch', 'turn_on'): set(), ('switch', 'turn_off'): set(),
    ('fan', 'turn_on'): set(), ('fan', 'turn_off'): set(),
    ('scene', 'turn_on'): set(),
    ('media_player', 'media_play'): set(), ('media_player', 'media_pause'): set(),
    ('media_player', 'media_next_track'): set(), ('media_player', 'media_previous_track'): set(),
    ('media_player', 'volume_set'): {'volume_level'},
    ('media_player', 'join'): {'group_members'}, ('media_player', 'unjoin'): set(),
    ('music_assistant', 'play_media'): {'media_id', 'media_type', 'enqueue'},
}
ENTITY = re.compile(r'^(light|switch|fan|scene|media_player)\.[a-z0-9_]{1,120}$')
MEDIA_TYPES = {'track', 'album', 'artist', 'playlist', 'radio'}
# A whole room in one request: lights, plugs and fans may be switched (and lights dimmed) several at a time.
TOGETHER = ('light', 'switch', 'fan')
MOST = 40
# Something concrete Music Assistant can play: its own address for one item, never words. The provider part is
# "library" or a provider instance, which Music Assistant names <domain>--<random>, such as apple_music--Ab3dE6fG;
# the id is the provider's own and may hold dots, dashes or slashes. A description sent as media_id makes Music
# Assistant guess, and usually nothing plays.
MEDIA_URI = re.compile(r'^[a-z][a-z0-9_]{0,40}(?:--[A-Za-z0-9_]{1,40})?://(?P<kind>track|album|artist|playlist|radio)/'
                       r'[^\s"\'<>\\]{1,240}$')
LIMIT = 16 * 1024 * 1024      # one reply line; the broker caps Home Assistant's own answers at 8 MB each
# The kinds of device Hub listens to for changes: what the Home screen shows. The broker allows no others.
LISTEN = ('light', 'switch', 'fan', 'scene', 'media_player', 'binary_sensor', 'alarm_control_panel', 'lock', 'cover',
          'climate', 'weather')
LISTEN_QUIET = 50             # the broker sends a beat at least every 20 seconds; this long without one and the line is dead
TIMEOUT = 8


class HomeDown(Exception):
    """Home Assistant couldn't be read through the broker. The message is safe to show."""


class NotSent(HomeDown):
    """The broker couldn't be reached, so nothing was sent: a change certainly didn't happen."""


class Rejected(HomeDown):
    """Home Assistant answered and said no, so nothing was changed. `code` is its own short reason
    ("not_found", "invalid_format", "HTTP 400"): letters, digits and dashes only, safe to show."""

    def __init__(self, code):
        super().__init__(f'Home Assistant said no ({code}).')
        self.code = code


# How the broker words a refusal by Home Assistant itself; anything else it says means the outcome is unknown.
_REJECTED = re.compile(r'^Home Assistant rejected the request: ([A-Za-z0-9_-]{1,80})$')
_STATUS = re.compile(r'^Home Assistant returned HTTP (4\d\d);')


def _exchange(message, timeout):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(config.HA_BROKER_SOCKET))
    except OSError:
        sock.close()
        raise NotSent('The Home Assistant broker on this Mac isn’t answering.') from None
    try:
        sock.sendall(json.dumps(message, separators=(',', ':')).encode() + b'\n')
        raw = sock.makefile('rb').readline(LIMIT + 1)
    except OSError:
        raise HomeDown('The Home Assistant broker on this Mac isn’t answering.') from None
    finally:
        sock.close()
    if not raw or len(raw) > LIMIT:
        raise HomeDown('The Home Assistant broker gave an answer Hub couldn’t read.')
    try:
        reply = json.loads(raw)
    except ValueError:
        raise HomeDown('The Home Assistant broker gave an answer Hub couldn’t read.') from None
    if not isinstance(reply, dict) or not reply.get('ok'):
        said = str(reply.get('error') or '') if isinstance(reply, dict) else ''
        refused, status = _REJECTED.match(said), _STATUS.match(said)
        if refused or status:
            raise Rejected(refused.group(1) if refused else 'HTTP ' + status.group(1))
        raise HomeDown('Home Assistant couldn’t be read just now.')
    return reply.get('result')


def listen(domains=LISTEN):
    """The house as it changes: yields the entity id of each device of these kinds when it changes (which one,
    never what to), and None for each beat, for as long as the broker keeps the line open. Read-only.
    Raises NotSent when the broker isn't there, HomeDown when it says no (an older broker has no such
    operation) or the line goes quiet. Closing the generator hangs up."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(LISTEN_QUIET)
        try:
            sock.connect(str(config.HA_BROKER_SOCKET))
        except OSError:
            raise NotSent('The Home Assistant broker on this Mac isn’t answering.') from None
        sock.sendall(json.dumps({'op': 'listen', 'domains': list(domains)}, separators=(',', ':')).encode() + b'\n')
        lines = sock.makefile('rb')
        first = True
        while True:
            raw = lines.readline(64 * 1024)
            try:
                message = json.loads(raw) if raw else None
            except ValueError:
                message = None
            if not isinstance(message, dict) or message.get('ok') is False or (first and not message.get('ok')):
                raise HomeDown('The Home Assistant broker isn’t passing changes on.')
            if not first:
                yield str(message['changed'])[:200] if isinstance(message.get('changed'), str) else None
            first = False
    except OSError:
        raise HomeDown('The Home Assistant broker isn’t passing changes on.') from None
    finally:
        sock.close()


def read(*types, timeout=TIMEOUT):
    """Results of read-only WebSocket commands, in order, over one connection."""
    if not types or any(t not in READS for t in types):
        raise ValueError('Hub only reads from Home Assistant here.')
    results = _exchange({'op': 'ws', 'commands': [{'type': t} for t in types]}, timeout)
    if not isinstance(results, list) or len(results) != len(types):
        raise HomeDown('The Home Assistant broker gave an answer Hub couldn’t read.')
    return results


def ask(*commands, timeout=TIMEOUT):
    """Results of read-only WebSocket commands that take arguments (ASKS), in order, over one connection."""
    if not commands or any(not isinstance(c, dict) or c.get('type') not in ASKS
                           or set(c) - {'type'} - ASKS[c['type']] for c in commands):
        raise ValueError('Hub only reads from Home Assistant here.')
    results = _exchange({'op': 'ws', 'commands': list(commands)}, timeout)
    if not isinstance(results, list) or len(results) != len(commands):
        raise HomeDown('The Home Assistant broker gave an answer Hub couldn’t read.')
    return results


def _numbers(value, bounds):
    return isinstance(value, list) and len(value) == len(bounds) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and low <= v <= high for v, (low, high) in zip(value, bounds))


def _colour(data):
    """A light's colour: one of rgb_color, hs_color, xy_color or color_temp_kelvin, each within bounds."""
    keys = COLOURS & set(data)
    if len(keys) > 1:
        raise ValueError('One colour at a time.')
    if 'rgb_color' in data and not (_numbers(data['rgb_color'], [(0, 255)] * 3)
                                    and all(isinstance(v, int) for v in data['rgb_color'])):
        raise ValueError('A colour is three numbers from 0 to 255.')
    if 'hs_color' in data and not _numbers(data['hs_color'], [(0, 360), (0, 100)]):
        raise ValueError('A colour is a hue from 0 to 360 and a saturation from 0 to 100.')
    if 'xy_color' in data and not _numbers(data['xy_color'], [(0, 1), (0, 1)]):
        raise ValueError('A colour is two numbers from 0 to 1.')
    kelvin = data.get('color_temp_kelvin', KELVIN[0])
    if not (isinstance(kelvin, int) and not isinstance(kelvin, bool) and KELVIN[0] <= kelvin <= KELVIN[1]):
        raise ValueError(f'A white is {KELVIN[0]} to {KELVIN[1]} K.')


def _checked(domain, service, data):
    allowed = WRITES.get((domain, service))
    if allowed is None or not isinstance(data, dict) or set(data) - allowed - {'entity_id'}:
        raise ValueError('Hub may not do that in Home Assistant.')
    kind = 'media_player' if domain == 'music_assistant' else domain
    named = data.get('entity_id')
    several = isinstance(named, list) and domain in TOGETHER and 1 <= len(named) <= MOST \
        and all(isinstance(e, str) for e in named) and len(set(named)) == len(named) and not COLOURS & set(data)
    for entity in named if several else [named]:
        if not isinstance(entity, str) or not ENTITY.fullmatch(entity) or entity.partition('.')[0] != kind:
            raise ValueError('Hub may not do that in Home Assistant.')
    if 'brightness_pct' in data and not (isinstance(data['brightness_pct'], int) and 1 <= data['brightness_pct'] <= 100):
        raise ValueError('Brightness must be 1 to 100.')
    _colour(data)
    if 'volume_level' in data and not (isinstance(data['volume_level'], (int, float)) and 0 <= data['volume_level'] <= 1):
        raise ValueError('Volume must be 0 to 1.')
    members = data.get('group_members', [])
    if not isinstance(members, list) or len(members) > 12 or any(
            not isinstance(m, str) or not ENTITY.fullmatch(m) or not m.startswith('media_player.') for m in members):
        raise ValueError('Group members must be speakers.')
    if domain == 'music_assistant' and not (isinstance(data.get('media_id'), str) and data.get('media_type') in MEDIA_TYPES
                                            and data.get('enqueue') in ('replace', 'add', 'next')):
        raise ValueError('Say what to play and what kind of thing it is.')
    if domain == 'music_assistant' and not playable(data['media_id'], data['media_type']):
        raise ValueError('Hub plays only a playlist, album, artist, song or station it found, never a description.')
    return data


def playable(uri, kind):
    """Whether `uri` is a Music Assistant address of that kind: something it found, never a description."""
    found = MEDIA_URI.fullmatch(str(uri or ''))
    return bool(found) and found.group('kind') == kind


def act(domain, service, data, timeout=TIMEOUT):
    """Ask Home Assistant to do one allowed thing. NotSent: certainly not done. HomeDown: outcome unknown."""
    data = _checked(domain, service, data)
    return _exchange({'op': 'request', 'method': 'POST', 'path': f'/api/services/{domain}/{service}', 'body': data}, timeout)


# ---- the setup door: how the house is set up, and devices beyond lights and speakers. Used only by admin.py ----

ANY_ENTITY = re.compile(r'^[a-z0-9_]{1,40}\.[a-z0-9_]{1,120}$')
ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')                   # a room, floor, label, device, helper or config id
NAME = re.compile(r'^[a-z0-9_]{1,60}$')                     # a domain or a service
ICON = re.compile(r'^mdi:[a-z0-9-]{1,60}$')
TEXT = 200                                                  # a name
CONFIG_BYTES = 64 * 1024                                    # one scene, automation or script
DATA_BYTES = 16 * 1024                                      # one service call's data
DEPTH = 14
# Helpers Ask may make, and the fields each may be given besides its name and icon.
HELPERS = {
    'input_boolean': {'initial'}, 'input_number': {'min', 'max', 'step', 'mode', 'unit_of_measurement', 'initial'},
    'input_select': {'options', 'initial'}, 'input_text': {'min', 'max', 'pattern', 'mode', 'initial'},
    'input_datetime': {'has_date', 'has_time', 'initial'}, 'input_button': set(),
    'timer': {'duration', 'restore'}, 'counter': {'initial', 'step', 'minimum', 'maximum', 'restore'},
}
# Reads that take arguments: one thing's own settings, what refers to it, whether a routine is well formed.
SETUP_READS = {
    'config/entity_registry/get': {'entity_id'}, 'search/related': {'item_type', 'item_id'},
    'automation/config': {'entity_id'}, 'script/config': {'entity_id'},
    'validate_config': {'triggers', 'conditions', 'actions'}, 'get_config': set(), 'config_entries/get': set(),
    **{f'{helper}/list': set() for helper in HELPERS},
}
_REGISTRY = {'name', 'icon', 'labels', 'aliases'}
SETUP_WRITES = {
    'config/entity_registry/update': {'entity_id', 'name', 'icon', 'area_id', 'hidden_by', 'disabled_by', 'labels', 'aliases'},
    'config/device_registry/update': {'device_id', 'name_by_user', 'area_id', 'labels', 'disabled_by'},
    'config/area_registry/create': {*_REGISTRY, 'floor_id'}, 'config/area_registry/update': {*_REGISTRY, 'area_id', 'floor_id'},
    'config/area_registry/delete': {'area_id'},
    'config/floor_registry/create': {'name', 'icon', 'aliases', 'level'},
    'config/floor_registry/update': {'floor_id', 'name', 'icon', 'aliases', 'level'},
    'config/floor_registry/delete': {'floor_id'},
    'config/label_registry/create': {'name', 'icon', 'color', 'description'},
    'config/label_registry/update': {'label_id', 'name', 'icon', 'color', 'description'},
    'config/label_registry/delete': {'label_id'},
    # The house's own name and units. Never where it is: that stays in Home Assistant.
    'config/core/update': {'location_name', 'unit_system', 'time_zone', 'currency', 'country', 'language'},
    **{f'{helper}/create': {'name', 'icon', *fields} for helper, fields in HELPERS.items()},
    **{f'{helper}/update': {f'{helper}_id', 'name', 'icon', *fields} for helper, fields in HELPERS.items()},
    **{f'{helper}/delete': {f'{helper}_id'} for helper in HELPERS},
}
CONFIGS = ('automation', 'scene', 'script')
# Services Ask may call when the owner asks, by domain. Not here, so never from Hub: anything that runs code or
# reaches other machines (shell commands, scripts in other languages, web requests, MQTT), radios and pairing,
# updates and firmware, backups, add-ons, the recorder, cameras, people and where they are.
SERVICE_DOMAINS = frozenset((
    'light', 'switch', 'fan', 'cover', 'climate', 'humidifier', 'water_heater', 'media_player', 'vacuum', 'lawn_mower', 'lock',
    'alarm_control_panel', 'valve', 'siren', 'remote', 'scene', 'script', 'automation', 'input_boolean', 'input_number',
    'input_select', 'input_text', 'input_datetime', 'input_button', 'timer', 'counter', 'number', 'select', 'button', 'text',
    'time', 'date', 'datetime', 'notify', 'tts', 'persistent_notification', 'homeassistant'))
# Domains where only some services are open. Restarting is the most Hub may do to Home Assistant itself.
ONLY = {'homeassistant': {'turn_on', 'turn_off', 'toggle', 'update_entity', 'reload_all', 'check_config', 'restart'}}
# Playing goes through the everyday door alone, which checks that it is something Music Assistant found.
SHUT = {('media_player', 'play_media')}
TELLS = re.compile(r'^(mobile_app_[a-z0-9_]{1,60}|persistent_notification)$')    # notify.<this>: a household phone, or Home Assistant's own list
TARGETS = ('entity_id', 'device_id', 'area_id', 'floor_id', 'label_id')


def _plain(value, most, depth=0):
    """Whether `value` is plain data (text, numbers, lists, dicts with text keys) of at most `most` bytes."""
    def ok(v, d):
        if v is None or isinstance(v, (bool, int, float, str)):
            return True
        if d >= DEPTH:
            return False
        if isinstance(v, list):
            return all(ok(x, d + 1) for x in v)
        return isinstance(v, dict) and all(isinstance(k, str) and ok(x, d + 1) for k, x in v.items())
    try:
        return ok(value, depth) and len(json.dumps(value, allow_nan=False)) <= most
    except (TypeError, ValueError):
        return False


def _is(pattern, value, empty=False):
    return (empty and value is None) or (isinstance(value, str) and bool(pattern.fullmatch(value)))


def _words(value, most=TEXT, empty=False):
    return (empty and value is None) or (isinstance(value, str) and 0 < len(value) <= most and '\n' not in value)


def _list_of(pattern, value, most=40):
    return isinstance(value, list) and len(value) <= most and all(_is(pattern, v) for v in value)


def _setup_checked(command):
    """One setup command with every field looked at, or ValueError. The refusal is the same for every reason:
    which field failed is a detail for the code that built it, never for a model."""
    no = ValueError('Hub may not do that in Home Assistant.')
    kind = command.get('type') if isinstance(command, dict) else None
    allowed = SETUP_WRITES.get(kind)
    if allowed is None or set(command) - allowed - {'type'}:
        raise no
    checks = {
        'entity_id': lambda v: _is(ANY_ENTITY, v), 'device_id': lambda v: _is(ID, v),
        'area_id': lambda v: _is(ID, v, empty=not kind.startswith('config/area_registry')),
        'floor_id': lambda v: _is(ID, v, empty=not kind.startswith('config/floor_registry')), 'label_id': lambda v: _is(ID, v),
        'name': lambda v: _words(v, empty=kind == 'config/entity_registry/update'), 'name_by_user': lambda v: _words(v, empty=True),
        'icon': lambda v: _is(ICON, v, empty=True), 'labels': lambda v: _list_of(ID, v),
        'aliases': lambda v: isinstance(v, list) and len(v) <= 12 and all(_words(a) for a in v),
        'hidden_by': lambda v: v in (None, 'user'), 'disabled_by': lambda v: v in (None, 'user'),
        'level': lambda v: v is None or (isinstance(v, int) and not isinstance(v, bool) and -9 <= v <= 99),
        'color': lambda v: _words(v, 30, empty=True), 'description': lambda v: _words(v, 400, empty=True),
    }
    for key, value in command.items():
        if key == 'type':
            continue
        if key in checks and kind.startswith('config/') and kind != 'config/core/update':
            if not checks[key](value):
                raise no
        elif key.endswith('_id') and key.removesuffix('_id') in HELPERS:
            if not _is(ID, value):
                raise no
        elif key in ('name', 'icon'):                       # a helper's own
            if not (checks[key](value) and value is not None):
                raise no
        elif not _plain(value, 2000):                       # a helper's fields, or the house's name and units
            raise no
    needs = {'update': 1, 'delete': 1, 'create': 0}[kind.rpartition('/')[2]] if kind != 'config/core/update' else 0
    if needs and not any(key.endswith('_id') for key in command):
        raise no
    if kind.endswith('/create') and not _words(command.get('name')):
        raise no
    return command


def setup_read(*commands, timeout=TIMEOUT):
    """Results of the setup door's read-only commands, in order, over one connection."""
    if not commands or any(not isinstance(c, dict) or c.get('type') not in SETUP_READS or set(c) - {'type'} - SETUP_READS[c['type']]
                           or not _plain(c, CONFIG_BYTES) for c in commands):
        raise ValueError('Hub only reads from Home Assistant here.')
    results = _exchange({'op': 'ws', 'commands': list(commands)}, timeout)
    if not isinstance(results, list) or len(results) != len(commands):
        raise HomeDown('The Home Assistant broker gave an answer Hub couldn’t read.')
    return results


def setup_write(command, timeout=TIMEOUT):
    """One change to a registry, a helper or the house's own name and units; returns Home Assistant's answer.
    Rejected: it said no and nothing changed. NotSent: certainly not done. HomeDown: outcome unknown."""
    return _exchange({'op': 'ws', 'command': _setup_checked(command)}, timeout)


def _config_path(kind, config_id):
    if kind not in CONFIGS or not _is(ID, config_id):
        raise ValueError('Hub may not do that in Home Assistant.')
    return f'/api/config/{kind}/config/{config_id}'


def setup_config(kind, config_id, timeout=TIMEOUT):
    """One scene, automation or script as Home Assistant keeps it, or None when it keeps none by that id (one
    written by hand in its files has no id and can't be read or changed from here)."""
    try:
        found = _exchange({'op': 'request', 'method': 'GET', 'path': _config_path(kind, config_id)}, timeout)
    except Rejected:
        return None
    return found if isinstance(found, dict) else None


def setup_save(kind, config_id, body, timeout=TIMEOUT):
    """Write one scene, automation or script, or with `body` None remove it. Home Assistant checks it and takes it
    up at once. Raises as setup_write does."""
    path = _config_path(kind, config_id)
    if body is None:
        return _exchange({'op': 'request', 'method': 'DELETE', 'path': path}, timeout)
    if not isinstance(body, dict) or not body or not _plain(body, CONFIG_BYTES):
        raise ValueError('Hub may not do that in Home Assistant.')
    return _exchange({'op': 'request', 'method': 'POST', 'path': path, 'body': body}, timeout)


ENTRY = re.compile(r'^[A-Za-z0-9]{8,64}$')                     # an integration's entry id


def setup_reload(entry_id, timeout=30):
    """Reload one integration, as its own Reload button does: it reconnects to its devices and nothing about how
    it is set up changes. Removing, disabling and adding integrations stay in Home Assistant. Raises as
    setup_write does."""
    if not _is(ENTRY, entry_id):
        raise ValueError('Hub may not do that in Home Assistant.')
    return _exchange({'op': 'request', 'method': 'POST', 'path': f'/api/config/config_entries/entry/{entry_id}/reload'}, timeout)


def service_open(domain, service):
    """Whether a service is behind the setup door at all. A name that isn't plain letters (a template) never is."""
    if not _is(NAME, domain) or not _is(NAME, service) or domain not in SERVICE_DOMAINS or (domain, service) in SHUT:
        return False
    if domain in ONLY:
        return service in ONLY[domain]
    return bool(TELLS.fullmatch(service)) if domain == 'notify' else True


def setup_call(domain, service, data, timeout=TIMEOUT):
    """One service call for the owner: a thermostat, a fan's speed, a lock, a helper's value, a routine run now.
    Raises as setup_write does."""
    if not service_open(domain, service) or not isinstance(data, dict) or not _plain(data, DATA_BYTES):
        raise ValueError('Hub may not do that in Home Assistant.')
    for key in TARGETS:
        named = data.get(key, [])
        pattern = ANY_ENTITY if key == 'entity_id' else ID
        if not (_is(pattern, named) or _list_of(pattern, named, MOST)):
            raise ValueError('Hub may not do that in Home Assistant.')
    return _exchange({'op': 'request', 'method': 'POST', 'path': f'/api/services/{domain}/{service}', 'body': data}, timeout)
