"""Talks to the HA broker over its owner-only Unix socket. Never reaches the network itself.

The broker (home-ops `ha-maintenance/ha_broker.py`) is the one holder of the owner's Home Assistant
administrator token; Hub never sees it. The broker passes on whatever it is sent, so the fence is here:
only the reads in READS and the actions in WRITES ever leave Hub, and an action's data is checked first.
"""
import json
import re
import socket

from ... import config

READS = ('config/area_registry/list', 'config/device_registry/list', 'config/entity_registry/list',
         'config/label_registry/list', 'config/floor_registry/list', 'get_states')
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
