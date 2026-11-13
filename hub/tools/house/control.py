"""Changing the house: lights, plugs, fans, scenes and speakers, through the HA broker, with a record and Undo.

Only devices the read side keeps (read.py) can be changed, and only with the actions below; the broker
client checks every request again (broker.WRITES). Each change is recorded with who asked, from where, and
the state before, so Undo can put it back. A change whose answer never came back is recorded as unknown,
never as done. Locks, the garage, the alarm, the thermostat and automations are not here.
"""
import json
import threading
import time
import uuid
from collections import deque

from ... import store
from . import broker, read

PER_MINUTE = 30
UNDO_SECONDS = 24 * 3600
QUIET = 0.15                 # playback that starts from silence starts here
POWER = read.POWER
MEDIA_KINDS = tuple(sorted(broker.MEDIA_TYPES))
ACTIONS = {
    'light': ('on', 'off', 'brightness'), 'switch': ('on', 'off'), 'fan': ('on', 'off'), 'scene': ('activate',),
    'media_player': ('play', 'pause', 'next', 'previous', 'volume', 'play_media', 'group', 'ungroup'),
}
SCHEMA = """
create table if not exists house_changes(
  id integer primary key autoincrement, batch text not null, ts real not null, login text, via text not null,
  entity text not null, name text not null, action text not null, words text not null, before text not null,
  outcome text not null, undone integer not null default 0);
create index if not exists house_changes_batch on house_changes(batch);
"""

_lock = threading.Lock()
_recent = deque()


class Refused(Exception):
    """Hub won't make this change. The message is safe to show and nothing was sent."""


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _spend(count):
    now = time.monotonic()
    with _lock:
        while _recent and now - _recent[0] > 60:
            _recent.popleft()
        if len(_recent) + count > PER_MINUTE:
            raise Refused(f'Hub has made {PER_MINUTE} changes in the last minute. Try again in a moment.')
        _recent.extend([now] * count)


def devices(found):
    """Everything Hub may change, by entity id."""
    rows = [d for r in found['rooms'] for d in r['devices']] + found['speakers'] + found['scenes']
    return {d['id']: d for d in rows}


def _steps(d, action, value=None, query=None, kind=None, members=(), label=None, alone=False, soft=False):
    """(steps, words): the broker requests for one device, and what was done in plain words."""
    e, name = d['id'], d['name']
    if action not in ACTIONS.get(d['kind'], ()):
        raise Refused(f'Hub can’t do “{action}” with {name}.')
    if action in ('on', 'off'):
        data = {'entity_id': e}
        if action == 'on' and value is not None and d['kind'] == 'light':
            data['brightness_pct'] = _level(value)
        return [(d['kind'], f'turn_{action}', data)], f'{name} {action}' + (f' at {data["brightness_pct"]}%' if 'brightness_pct' in data else '')
    if action == 'brightness':
        if value is None:
            raise Refused('Say how bright, from 1 to 100.')
        return [('light', 'turn_on', {'entity_id': e, 'brightness_pct': _level(value)})], f'{name} at {_level(value)}%'
    if action == 'activate':
        return [('scene', 'turn_on', {'entity_id': e})], f'{name} scene on'
    simple = {'play': ('media_play', 'playing'), 'pause': ('media_pause', 'paused'),
              'next': ('media_next_track', 'next track'), 'previous': ('media_previous_track', 'previous track')}
    if action in simple:
        service, word = simple[action]
        return [('media_player', service, {'entity_id': e})], f'{name} {word}'
    if action == 'volume':
        if value is None:
            raise Refused('Say the volume, from 0 to 100.')
        level = max(0, min(100, int(value)))
        return [('media_player', 'volume_set', {'entity_id': e, 'volume_level': level / 100})], f'{name} volume {level}%'
    if action == 'play_media':
        query = ' '.join(str(query or '').split())[:200]
        if not query or kind not in broker.MEDIA_TYPES:
            raise Refused('Say what to play, and whether it is a track, album, artist, playlist or radio station.')
        steps, start = [], d['state'] not in ('playing', 'paused', 'buffering')
        split = alone and bool(d['members'])
        if split:                   # play only here: out of its group first, and Undo puts it back
            steps.append(('media_player', 'unjoin', {'entity_id': e}))
        level = None
        if value is not None or soft or start:
            level = QUIET if value is None else max(0, min(100, int(value))) / 100
            steps.append(('media_player', 'volume_set', {'entity_id': e, 'volume_level': level}))
        steps.append(('music_assistant', 'play_media', {'entity_id': e, 'media_id': query, 'media_type': kind, 'enqueue': 'replace'}))
        words = f'{name}{" on its own" if split else ""} playing “{_clip(label) or query}”'   # a name, not a library address
        return steps, words + (f' at {round(level * 100)}%' if level is not None and (soft or value is not None) else '')
    if action == 'group':
        others = [m for m in members if m != e]
        if not others:
            raise Refused('Say which speakers to group with it.')
        return [('media_player', 'join', {'entity_id': e, 'group_members': others})], f'{name} grouped'
    return [('media_player', 'unjoin', {'entity_id': e})], f'{name} on its own'


def _clip(text):
    return ' '.join(str(text or '').split())[:200]


def _level(value):
    try:
        return max(1, min(100, int(value)))
    except (TypeError, ValueError):
        raise Refused('Brightness is a number from 1 to 100.') from None


def _inverse(entity, kind, action, before):
    """The steps that put one change back, or None when there is nothing to put back."""
    state = before.get('state')
    if kind in POWER:
        if state == 'off':
            return [(kind, 'turn_off', {'entity_id': entity})]
        if state == 'on':
            data = {'entity_id': entity}
            if kind == 'light' and before.get('level'):
                data['brightness_pct'] = max(1, min(100, int(before['level'])))
            return [(kind, 'turn_on', data)]
        return None
    if kind != 'media_player' or action in ('next', 'previous'):
        return None
    steps = []
    if action in ('play', 'pause', 'play_media'):
        steps.append(('media_player', 'media_play' if state == 'playing' else 'media_pause', {'entity_id': entity}))
    if action in ('volume', 'play_media') and isinstance(before.get('volume'), (int, float)):
        steps.append(('media_player', 'volume_set', {'entity_id': entity, 'volume_level': float(before['volume'])}))
    if action == 'play_media' and before.get('alone') and before.get('members'):
        steps.append(('media_player', 'join', {'entity_id': entity, 'group_members': list(before['members'])}))
    if action == 'group':
        steps.append(('media_player', 'unjoin', {'entity_id': entity}))
    if action == 'ungroup' and before.get('members'):
        steps.append(('media_player', 'join', {'entity_id': entity, 'group_members': list(before['members'])}))
    return steps or None


def _run(steps):
    """'done', 'not_sent' (certainly nothing happened) or 'unknown'."""
    for n, (domain, service, data) in enumerate(steps):
        try:
            broker.act(domain, service, data)
        except broker.NotSent:
            return 'not_sent' if n == 0 else 'unknown'
        except broker.HomeDown:
            return 'unknown'
        except ValueError:
            return 'not_sent' if n == 0 else 'unknown'
    return 'done'


def _report(batch, rows):
    done = [r['words'] for r in rows if r['outcome'] == 'done']
    unknown = [r['words'] for r in rows if r['outcome'] == 'unknown']
    failed = [r['words'] for r in rows if r['outcome'] == 'not_sent']
    return {'batch': batch, 'done': done, 'unknown': unknown, 'not_sent': failed,
            'undo': any(r['undoable'] and r['outcome'] != 'not_sent' for r in rows)}


def change(targets, action, *, login, via, value=None, query=None, kind=None, members=(), label=None, alone=False, soft=False):
    """Make one kind of change to one or more devices (entity ids). Returns what happened, per device."""
    if not targets:
        raise Refused('Nothing to change.')
    try:
        found = devices(read.snapshot(fresh=True))
    except broker.HomeDown as error:
        raise Refused(str(error)) from None
    chosen = []
    for entity in dict.fromkeys(targets):
        if entity not in found:
            raise Refused('Hub can’t control that. It changes lights, plugs, fans, scenes and speakers only.')
        chosen.append(found[entity])
    plans = [(d, *_steps(d, action, value, query, kind, members, label, alone, soft)) for d in chosen]
    _spend(len(plans))
    batch, now, rows = uuid.uuid4().hex[:12], time.time(), []
    for d, steps, words in plans:
        before = {'state': d['state'], 'level': d['level'], 'volume': d['volume'], 'members': d['members'],
                  'alone': bool(alone and action == 'play_media' and d['members'])}
        outcome = _run(steps)
        rows.append({'entity': d['id'], 'name': d['name'], 'words': words, 'outcome': outcome,
                     'undoable': _inverse(d['id'], d['kind'], action, before) is not None})
        with _db() as conn:
            conn.execute('insert into house_changes(batch, ts, login, via, entity, name, action, words, before, outcome) '
                         'values (?,?,?,?,?,?,?,?,?,?)', (batch, now, login, via, d['id'], d['name'], action, words,
                                                          json.dumps(before), outcome))
    read.forget_states()
    return _report(batch, rows)


def _rows(batch):
    with _db() as conn:
        return [dict(r) for r in conn.execute('select * from house_changes where batch = ? order by id', (batch,))]


def summary(batch):
    """What one batch did, for a banner, or None."""
    rows = _rows(str(batch or '')[:12])
    if not rows:
        return None
    for r in rows:
        r['undoable'] = (_inverse(r['entity'], r['entity'].partition('.')[0], r['action'], json.loads(r['before'])) is not None
                         and not r['undone'] and r['via'] != 'undo' and time.time() - r['ts'] < UNDO_SECONDS)
    found = _report(rows[0]['batch'], rows)
    found['undone'] = all(r['undone'] for r in rows)
    return found


def undo(batch, *, login, via):
    """Put a batch back. Returns what happened, as change() does."""
    rows = [r for r in _rows(str(batch or '')[:12]) if not r['undone'] and r['via'] != 'undo' and r['outcome'] != 'not_sent']
    if not rows:
        raise Refused('There is nothing left to undo there.')
    if time.time() - rows[0]['ts'] > UNDO_SECONDS:
        raise Refused('That change is more than a day old. Change it back in Home Assistant.')
    plans = [(r, _inverse(r['entity'], r['entity'].partition('.')[0], r['action'], json.loads(r['before']))) for r in rows]
    plans = [(r, steps) for r, steps in plans if steps]
    if not plans:
        raise Refused('That can’t be undone from Hub.')
    _spend(len(plans))
    new, now, out = uuid.uuid4().hex[:12], time.time(), []
    for r, steps in plans:
        outcome = _run(steps)
        words = f'{r["name"]} put back'
        out.append({'words': words, 'outcome': outcome, 'undoable': False})
        with _db() as conn:
            conn.execute('insert into house_changes(batch, ts, login, via, entity, name, action, words, before, outcome) '
                         'values (?,?,?,?,?,?,?,?,?,?)', (new, now, login, 'undo', r['entity'], r['name'], 'undo', words, '{}', outcome))
            if outcome != 'not_sent':
                conn.execute('update house_changes set undone = 1 where id = ?', (r['id'],))
    read.forget_states()
    return _report(new, out)


def recent(limit=30):
    """The latest changes, newest first, one entry per batch, for the changes page."""
    with _db() as conn:
        batches = [r['batch'] for r in conn.execute(
            'select batch, max(id) as last from house_changes group by batch order by last desc limit ?', (limit,))]
        whens = {r['batch']: (r['ts'], r['login'], r['via']) for r in conn.execute(
            'select batch, min(ts) as ts, login, via from house_changes group by batch')}
    return [{**summary(b), 'ts': whens[b][0], 'login': whens[b][1], 'via': whens[b][2]} for b in batches]


def resolve(target, kinds, found=None):
    """Entity ids for what the owner named: a device, a room (its lights) or "all lights". Raises Refused."""
    found = found if found is not None else read.snapshot()
    pool = [d for d in devices(found).values() if d['kind'] in kinds]
    wanted = ' '.join(str(target or '').lower().split())
    exact = [d['id'] for d in pool if wanted in (d['name'].lower(), d['id'])]
    if exact:
        return exact
    if 'light' in kinds:
        if wanted in ('all lights', 'all the lights', 'every light', 'lights'):
            return [d['id'] for d in pool if d['kind'] == 'light']
        room = next((r for r in found['rooms'] if r['name'].lower() == wanted.removesuffix(' lights').strip()), None)
        if room:
            lights = [d['id'] for d in room['devices'] if d['kind'] == 'light']
            if lights:
                return lights
    near = [d['name'] for d in pool if wanted and wanted in d['name'].lower()]
    if len(near) == 1:
        return [d['id'] for d in pool if d['name'] == near[0]]
    names = near or [d['name'] for d in pool]
    raise Refused(f'Which one? {", ".join(names[:12])}' if names else 'Hub found nothing like that to control.')
