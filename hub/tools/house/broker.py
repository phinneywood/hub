"""Talks to the HA broker over its owner-only Unix socket. Never reaches the network itself.

The broker (home-ops `ha-maintenance/ha_broker.py`) is the one holder of the owner's Home Assistant
administrator token; Hub never sees it. The broker passes on whatever it is sent, so the fence is here:
only the reads in READS ever leave Hub. Changes come later, each with its own entry in the catalog.
"""
import json
import socket

from ... import config

READS = ('config/area_registry/list', 'config/device_registry/list', 'config/entity_registry/list', 'get_states')
LIMIT = 16 * 1024 * 1024      # one reply line; the broker caps Home Assistant's own answers at 8 MB each
TIMEOUT = 8


class HomeDown(Exception):
    """Home Assistant couldn't be read through the broker. The message is safe to show."""


def _exchange(message, timeout):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(config.HA_BROKER_SOCKET))
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
