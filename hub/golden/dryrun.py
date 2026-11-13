"""The dry-run layer: Hub's real checks run, the intended change is written down, and nothing is sent.

Every way Ask can change something leaves Hub through one of three doors, and this layer stands in each:

  Home Assistant   broker._exchange, after broker.act has run its own allowlist check (broker._checked)
  Music Assistant  music._send, after music._call has checked the command against READS and WRITES
  Kitchen          kitchen client.call, for anything but a read

Reads go through untouched, so a golden run sees the real house, library and lists. A change that passes
Hub's checks is recorded in `Ledger.changes` and answered as if it had worked; one that fails them is
refused exactly as it would be live. Home Assistant reads are then shown with the recorded changes laid
over them, so a second turn ("play it in the kitchen as well") sees the music the first turn started.
Undo records (house_changes, music_changes) go to a scratch database, never Hub's own, so a dry run leaves
nothing on the Home changes or Playlists pages.

`COVERED` names every Ask tool that can change something. A test fails if the catalog gains a control or a
hand-off this layer doesn't stand in front of, so a new capability can't skip the dry run by accident.
"""
import copy
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

COVERED = {'home_control': 'home', 'play_music': 'home', 'edit_playlist': 'music', 'kitchen': 'kitchen'}
DRY_ID = -1                     # the message number a held-back Kitchen hand-off is given


class Ledger:
    """What a run would have changed, in order, with the turn each came from."""

    def __init__(self):
        self.changes, self.turn, self._overlay, self._lock = [], 0, {}, threading.Lock()

    def add(self, to, what, data):
        with self._lock:
            self.changes.append({'turn': self.turn, 'to': to, 'what': what, 'data': copy.deepcopy(data)})

    # ---- Home Assistant: lay recorded changes over what is read ----

    def _apply(self, domain, service, data):
        entity = data.get('entity_id')
        with self._lock:
            state = self._overlay.setdefault(entity, {'attributes': {}})
            if service in ('turn_on', 'turn_off') and domain in ('light', 'switch', 'fan'):
                state['state'] = 'on' if service == 'turn_on' else 'off'
                if 'brightness_pct' in data:
                    state['attributes']['brightness'] = round(data['brightness_pct'] * 255 / 100)
            elif service == 'media_play' or (domain == 'music_assistant' and service == 'play_media'):
                state['state'] = 'playing'
            elif service == 'media_pause':
                state['state'] = 'paused'
            elif service == 'volume_set':
                state['attributes']['volume_level'] = data['volume_level']
            elif service == 'join':
                group = [entity, *data['group_members']]
                for member in group:
                    self._overlay.setdefault(member, {'attributes': {}})['attributes']['group_members'] = list(group)
            elif service == 'unjoin':
                state['attributes']['group_members'] = [entity]
                for other, seen in self._overlay.items():
                    members = seen['attributes'].get('group_members')
                    if other != entity and members and entity in members:
                        seen['attributes']['group_members'] = [m for m in members if m != entity]

    def laid_over(self, states):
        """Home Assistant's states as they would be after the recorded changes."""
        with self._lock:
            overlay = copy.deepcopy(self._overlay)
        if not overlay or not isinstance(states, list):
            return states
        out = []
        for item in states:
            change = overlay.get(item.get('entity_id')) if isinstance(item, dict) else None
            if change:
                item = copy.deepcopy(item)
                item['state'] = change.get('state', item.get('state'))
                attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
                item['attributes'] = {**attrs, **change['attributes']}
            elif isinstance(item, dict):
                left = [e for e, c in overlay.items() if c['attributes'].get('group_members') == [e]]
                members = (item.get('attributes') or {}).get('group_members')
                if members and any(e in members for e in left):
                    item = copy.deepcopy(item)
                    item['attributes']['group_members'] = [m for m in members if m not in left]
            out.append(item)
        return out


def _scratch_db(folder):
    def db():
        conn = sqlite3.connect(folder / 'changes.sqlite3', timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    return db


@contextmanager
def layer(enabled=True):
    """Stand in Hub's three doors for the length of the block. Yields the Ledger. With enabled=False (live
    writes, for scenarios marked harmless and only when the owner asks) changes really happen and are
    recorded in Hub's own change log, with Undo; the Ledger stays empty."""
    from ..tools.house import broker, control, music
    from ..tools.kitchen import client
    ledger = Ledger()
    if not enabled:
        yield ledger
        return
    real_exchange, real_send, real_call = broker._exchange, music._send, client.call
    saved = {'broker': real_exchange, 'music_send': real_send, 'settle': music._settle, 'kitchen': real_call,
             'control_db': control._db, 'music_db': music._db}

    def exchange(message, timeout):
        if message.get('op') == 'ws':
            results = real_exchange(message, timeout)
            types = [c.get('type') for c in message.get('commands', ())]
            if isinstance(results, list) and 'get_states' in types:
                results = list(results)
                at = types.index('get_states')
                results[at] = ledger.laid_over(results[at])
            return results
        if message.get('op') == 'request' and message.get('method') == 'POST':
            parts = str(message.get('path', '')).strip('/').split('/')
            if len(parts) != 4 or parts[:2] != ['api', 'services']:
                raise broker.NotSent('The dry run refused a request it doesn’t know.')
            domain, service = parts[2:]
            ledger.add('home', f'{domain}/{service}', message['body'])
            ledger._apply(domain, service, message['body'])
            return []
        raise broker.NotSent('The dry run refused a request it doesn’t know.')

    def send(command, args, timeout):
        if command in music.READS:
            return real_send(command, args, timeout)
        if command not in music.WRITES:
            raise music.NotSent('The dry run refused a command it doesn’t know.')
        ledger.add('music', command, args)
        if command == 'music/playlists/create_playlist':
            return {'item_id': f'dry-run-{len(ledger.changes)}', 'name': args.get('name')}
        return {'id': 'dry-run'}

    def kitchen(method, path, body=None, timeout=10):
        if method == 'GET':
            if path.startswith(f'/messages?after={DRY_ID}'):
                return 200, {'messages': []}       # the held-back message has no answer
            return real_call(method, path, body, timeout)
        ledger.add('kitchen', f'{method} {path.split("?")[0]}', body)
        return 200, {'id': DRY_ID}

    with tempfile.TemporaryDirectory(prefix='hub-golden-') as folder:
        broker._exchange, music._send, client.call = exchange, send, kitchen
        music._settle = lambda playlist_id, want: 'done'     # nothing was sent, so there is nothing to read back
        control._db = _with_schema(_scratch_db(Path(folder)), control.SCHEMA)
        music._db = _with_schema(_scratch_db(Path(folder)), music.SCHEMA)
        try:
            yield ledger
        finally:
            broker._exchange, music._send, music._settle, client.call = (
                saved['broker'], saved['music_send'], saved['settle'], saved['kitchen'])
            control._db, music._db = saved['control_db'], saved['music_db']


def _with_schema(db, schema):
    def opened():
        conn = db()
        conn.executescript(schema)
        return conn
    return opened
