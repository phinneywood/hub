"""Talks to the HA broker over its owner-only Unix socket. Never reaches the network itself.

The broker (home-ops `ha-maintenance/ha_broker.py`) is the one holder of the owner's Home Assistant
administrator token; Hub never sees it. The broker passes on whatever it is sent, so the fence is here:
only the reads in READS and the actions in WRITES ever leave Hub, and an action's data is checked first.
"""
import json
import re
import socket

from ... import config

READS = ('config/area_registry/list', 'config/device_registry/list', 'config/entity_registry/list', 'get_states')
# (domain, service) -> the data keys it may carry besides entity_id. Lights, power and speakers only;
# locks, the garage, the alarm, the thermostat and automations are not here.
WRITES = {
    ('light', 'turn_on'): {'brightness_pct'}, ('light', 'turn_off'): set(),
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
LIMIT = 16 * 1024 * 1024      # one reply line; the broker caps Home Assistant's own answers at 8 MB each
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


def read(*types, timeout=TIMEOUT):
    """Results of read-only WebSocket commands, in order, over one connection."""
    if not types or any(t not in READS for t in types):
        raise ValueError('Hub only reads from Home Assistant here.')
    results = _exchange({'op': 'ws', 'commands': [{'type': t} for t in types]}, timeout)
    if not isinstance(results, list) or len(results) != len(types):
        raise HomeDown('The Home Assistant broker gave an answer Hub couldn’t read.')
    return results


def _checked(domain, service, data):
    allowed = WRITES.get((domain, service))
    if allowed is None or not isinstance(data, dict) or set(data) - allowed - {'entity_id'}:
        raise ValueError('Hub may not do that in Home Assistant.')
    if not isinstance(data.get('entity_id'), str) or not ENTITY.fullmatch(data['entity_id']) \
            or data['entity_id'].partition('.')[0] != ('media_player' if domain == 'music_assistant' else domain):
        raise ValueError('Hub may not do that in Home Assistant.')
    if 'brightness_pct' in data and not (isinstance(data['brightness_pct'], int) and 1 <= data['brightness_pct'] <= 100):
        raise ValueError('Brightness must be 1 to 100.')
    if 'volume_level' in data and not (isinstance(data['volume_level'], (int, float)) and 0 <= data['volume_level'] <= 1):
        raise ValueError('Volume must be 0 to 1.')
    members = data.get('group_members', [])
    if not isinstance(members, list) or len(members) > 12 or any(
            not isinstance(m, str) or not ENTITY.fullmatch(m) or not m.startswith('media_player.') for m in members):
        raise ValueError('Group members must be speakers.')
    if domain == 'music_assistant' and not (isinstance(data.get('media_id'), str) and 0 < len(data['media_id']) <= 200
                                            and data.get('media_type') in MEDIA_TYPES and data.get('enqueue') in ('replace', 'add', 'next')):
        raise ValueError('Say what to play and what kind of thing it is.')
    return data


def act(domain, service, data, timeout=TIMEOUT):
    """Ask Home Assistant to do one allowed thing. NotSent: certainly not done. HomeDown: outcome unknown."""
    data = _checked(domain, service, data)
    return _exchange({'op': 'request', 'method': 'POST', 'path': f'/api/services/{domain}/{service}', 'body': data}, timeout)
