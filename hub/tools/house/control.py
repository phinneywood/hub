"""Changing the house: lights, plugs, fans, scenes and speakers, through the HA broker, with a record and Undo.

Only devices the read side keeps (read.py) can be changed, and only with the actions below; the broker
client checks every request again (broker.WRITES). Each change is recorded with who asked, from where, and
the state before, so Undo can put it back. A change whose answer never came back is recorded as unknown,
never as done. Locks, the garage, the alarm, the thermostat and automations are not here.
"""
import json
import re
import threading
import time
import uuid
from collections import deque

from ... import store
from . import broker, read

PER_MINUTE = 30
UNDO_SECONDS = 24 * 3600
QUIET = 0.15                 # playback that starts from silence starts here
PLAYING = ('playing', 'buffering')
CHECKS, CHECK_WAIT = 4, 1.0  # reading a speaker back after play: Home Assistant accepting it doesn't mean music started
POWER = read.POWER
MEDIA_KINDS = tuple(sorted(broker.MEDIA_TYPES))
ACTIONS = {
    'light': ('on', 'off', 'brightness', 'colour'), 'switch': ('on', 'off'), 'fan': ('on', 'off'), 'scene': ('activate',),
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
    if action == 'colour':
        colour, said = _colour(value, d)
        return [('light', 'turn_on', {'entity_id': e, **colour})], f'{name} {said}'
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
        uri = broker.MEDIA_URI.fullmatch(query)
        if not uri or uri.group(1) != kind:     # broker checks again; this refusal comes before anything is sent
            raise Refused('Hub plays only a playlist, album, artist, song or station it found in the library or Apple Music, '
                          'never a description. Nothing was played.')
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
    if action == 'ungroup':
        return [('media_player', 'unjoin', {'entity_id': e})], f'{name} on its own'
    raise Refused(f'Hub can’t do “{action}” with {name}.')


def _join_steps(leader, d, level=None):
    """(steps, words): one speaker joining the leader's group, so both play the same music in sync. It starts at
    `level` when given, else at the leader's volume when it was silent, so a joining room is never louder."""
    steps = []
    if level is None and d['state'] not in ('playing', 'buffering') and isinstance(leader['volume'], (int, float)):
        level = float(leader['volume'])
    if level is not None:
        steps.append(('media_player', 'volume_set', {'entity_id': d['id'], 'volume_level': level}))
    steps.append(('media_player', 'join', {'entity_id': leader['id'], 'group_members': [d['id']]}))
    return steps, f'{d["name"]} joined {leader["name"]}' + (f' at {round(level * 100)}%' if level is not None else '')


def _clip(text):
    return ' '.join(str(text or '').split())[:200]


def _level(value):
    try:
        return max(1, min(100, int(value)))
    except (TypeError, ValueError):
        raise Refused('Brightness is a number from 1 to 100.') from None


# Colours by name. Whites are colour temperatures; the rest are hue and saturation. Nothing else is accepted.
WHITES = {'candlelight': 2000, 'warm white': 2700, 'warm': 2700, 'soft white': 3000, 'white': 4000,
          'neutral white': 4000, 'cool white': 5000, 'cool': 5000, 'daylight': 6500}
HUES = {'red': (0, 100), 'orange': (30, 100), 'amber': (40, 100), 'yellow': (55, 100), 'lime': (90, 100),
        'green': (120, 100), 'teal': (175, 100), 'cyan': (185, 100), 'blue': (230, 100), 'indigo': (260, 100),
        'purple': (280, 100), 'violet': (290, 90), 'magenta': (310, 100), 'pink': (330, 60)}
KELVIN_WORDS = re.compile(r'^(\d{4})\s*k(elvin)?$')


def colour_names():
    return [*WHITES, *HUES]


def _colour(value, d):
    """({argument: value}, words) for a colour the owner named, checked against what the light can show."""
    said = ' '.join(str(value or '').lower().split()).removesuffix(' light')
    kelvin = KELVIN_WORDS.fullmatch(said)
    if said in WHITES or kelvin:
        number = WHITES.get(said) or int(kelvin.group(1))
        low, high = broker.KELVIN
        if not low <= number <= high:
            raise Refused(f'A white is {low} to {high} K.')
        if not d.get('colours'):
            raise Refused(f'{d["name"]} only dims; it can’t change colour.')
        return {'color_temp_kelvin': number}, f'{said} ({number} K)' if said in WHITES else f'at {number} K'
    if said in HUES:
        if not d.get('colours'):
            raise Refused(f'{d["name"]} only dims; it can’t change colour.')
        if d['colours'] != 'colour':
            raise Refused(f'{d["name"]} can’t show colours, only warm to cool white.')
        return {'hs_color': list(HUES[said])}, said
    raise Refused('Say a colour: ' + ', '.join(colour_names()) + ', or a white such as 2700 K.')


def _shows(value, d):
    try:
        return bool(_colour(value, d))
    except Refused:
        return False


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
            if kind == 'light' and isinstance(before.get('colour'), dict) and len(before['colour']) == 1 \
                    and set(before['colour']) <= broker.COLOURS:
                data.update(before['colour'])
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
    if action == 'group':               # recorded before joins were kept per speaker
        steps.append(('media_player', 'unjoin', {'entity_id': entity}))
    if action == 'join':                # a speaker that joined a group leaves it again; the group plays on
        steps.append(('media_player', 'unjoin', {'entity_id': entity}))
        if isinstance(before.get('volume'), (int, float)):
            steps.append(('media_player', 'volume_set', {'entity_id': entity, 'volume_level': float(before['volume'])}))
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


def _playing(entity):
    """Whether the speaker is playing, read back a few times while Music Assistant starts. False when it can't be read."""
    for n in range(CHECKS):
        if n:
            time.sleep(CHECK_WAIT)
        try:
            now = devices(read.snapshot(fresh=True)).get(entity)
        except broker.HomeDown:
            return False
        if now and now['state'] in PLAYING:
            return True
    return False


def _report(batch, rows):
    done = [r['words'] for r in rows if r['outcome'] == 'done']
    unknown = [r['words'] for r in rows if r['outcome'] == 'unknown']
    failed = [r['words'] for r in rows if r['outcome'] == 'not_sent']
    return {'batch': batch, 'done': done, 'unknown': unknown, 'not_sent': failed,
            'silent': [r['words'] for r in rows if r.get('silent')],
            'undo': any(r['undoable'] and r['outcome'] != 'not_sent' for r in rows)}


def _known(found, entity):
    if entity not in found:
        raise Refused('Hub can’t control that. It changes lights, plugs, fans, scenes and speakers only.')
    return found[entity]


def _joins(leader, others, found, level=None):
    """Plans for speakers joining the leader's group; those already in it are left as they are."""
    plans = []
    for entity in dict.fromkeys(others):
        d = _known(found, entity)
        if d['kind'] != 'media_player':
            raise Refused(f'{d["name"]} isn’t a speaker.')
        if entity != leader['id'] and entity not in leader['members']:
            plans.append((d, 'join', *_join_steps(leader, d, level)))
    return plans


def change(targets, action, *, login, via, value=None, query=None, kind=None, members=(), label=None, alone=False, soft=False,
           together=(), each=None):
    """Make one kind of change to one or more devices (entity ids). Returns what happened, per device.

    `group` joins `members` to the one target's group. `together` (play_media, one target): speakers of the same zone
    that join it once it plays, at the same volume, in the same batch, so one Undo puts all of it back.
    `each` (volume): {entity id: percent} for speakers that each get their own level, as a group slider sets them."""
    if not targets:
        raise Refused('Nothing to change.')
    try:
        found = devices(read.snapshot(fresh=True))
    except broker.HomeDown as error:
        raise Refused(str(error)) from None
    chosen = [_known(found, entity) for entity in dict.fromkeys(targets)]
    if action == 'colour' and len(chosen) > 1:      # a room: the lights that can show it; dim-only ones are left alone
        chosen = [d for d in chosen if _shows(value, d)] or chosen[:1]
    if action == 'group':
        if len(chosen) != 1 or chosen[0]['kind'] != 'media_player':
            raise Refused('Say which speaker the others should join.')
        leader, others = chosen[0], [m for m in members if m != chosen[0]['id']]
        if leader['state'] not in PLAYING:  # a silent leader would silence the music the others are playing
            playing = [found[m] for m in others if m in found and found[m]['state'] in PLAYING]
            if len({frozenset([d['id'], *d['members']]) for d in playing}) > 1:
                raise Refused('More than one of those speakers is playing. Say which music the others should join.')
            if playing:
                leader, others = playing[0], [leader['id'], *(m for m in others if m != playing[0]['id'])]
        plans = _joins(leader, others, found)
        if not plans:
            raise Refused('Say which other speakers to group with it.' if not others
                          else f'Those speakers already play together with {leader["name"]}.')
    else:
        plans = [(d, action, *_steps(d, action, (each or {}).get(d['id'], value), query, kind, members, label, alone, soft))
                 for d in chosen]
    if together and action == 'play_media' and len(chosen) == 1:
        leader = dict(chosen[0], members=[] if alone else chosen[0]['members'])
        level = next((data['volume_level'] for _, service, data in plans[0][2] if service == 'volume_set'), None)
        for entity in leader['members']:             # already grouped: only the volume follows, when one was set
            if entity in together and level is not None:
                plans.append((found[entity], 'volume', *_steps(found[entity], 'volume', round(level * 100))))
        plans += _joins(leader, together, found, level)
    _spend(len(plans))
    batch, now, rows = uuid.uuid4().hex[:12], time.time(), []
    for d, act, steps, words in plans:
        before = {'state': d['state'], 'level': d['level'], 'colour': d.get('colour'), 'volume': d['volume'], 'members': d['members'],
                  'alone': bool(alone and act == 'play_media' and d['members'])}
        outcome, silent = _run(steps), False
        if act in ('play_media', 'play') and outcome != 'not_sent' and not _playing(d['id']):
            outcome, silent = 'unknown', True        # sent, maybe accepted, but nothing plays: never recorded as done
        rows.append({'entity': d['id'], 'name': d['name'], 'words': words, 'outcome': outcome, 'silent': silent,
                     'undoable': _inverse(d['id'], d['kind'], act, before) is not None})
        with _db() as conn:
            conn.execute('insert into house_changes(batch, ts, login, via, entity, name, action, words, before, outcome) '
                         'values (?,?,?,?,?,?,?,?,?,?)', (batch, now, login, via, d['id'], d['name'], act, words,
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
    from ...engine import friction
    friction.undone([r for r, _ in plans], via)
    return _report(new, out)


def recent(limit=30):
    """The latest changes, newest first, one entry per batch, for the changes page."""
    with _db() as conn:
        batches = [r['batch'] for r in conn.execute(
            'select batch, max(id) as last from house_changes group by batch order by last desc limit ?', (limit,))]
        whens = {r['batch']: (r['ts'], r['login'], r['via']) for r in conn.execute(
            'select batch, min(ts) as ts, login, via from house_changes group by batch')}
    return [{**summary(b), 'ts': whens[b][0], 'login': whens[b][1], 'via': whens[b][2]} for b in batches]


ALL_LIGHTS = ('all lights', 'all the lights', 'every light', 'lights')


FILLER = {'the', 'room', 'rooms', 'area', 'light', 'lights', 'in', 'all', 'of'}


def _words(text):
    return set(re.sub(r'[^a-z0-9]+', ' ', str(text).lower()).split())


def rooms_named(wanted, found, loose=False):
    """The rooms (Home Assistant areas) the owner named, by name or by an area label, else []. Exact first: the
    room's name, or a label's, so "upstairs" reaches every area labelled Upstairs. With `loose`, the words they
    said found in a room's name or label ("sun room" for an area named Upper Sun Room), but never by
    filler words alone."""
    wanted = ' '.join(str(wanted or '').lower().split()).removeprefix('the ')
    wanted = wanted.removesuffix(' lights').removesuffix(' light').strip()
    labels = dict(found.get('room_labels') or {})
    for r in found['rooms']:
        labels.setdefault(r['name'], r.get('labels') or [])
    for d in found['speakers']:
        if d.get('room'):
            labels.setdefault(d['room'], [])
    names = [n for n in labels if n != read.OTHER_ROOM]
    exact = [n for n in names if wanted == n.lower()] or [n for n in names if wanted in (x.lower() for x in labels[n])]
    if exact or not loose:
        return exact
    words = _words(wanted)
    if not words - FILLER:
        return []
    return [n for n in names if words <= _words(n) or any(words <= _words(x) for x in labels[n])]


def _in_room(wanted, kinds, found, loose=False):
    """Entity ids for rooms the owner named, or []: their lights, or their speakers (the one named like the room first).
    A room wins over a device with the same name (owner, 2026-11-13): "turn off the living room" means every light
    there, even when one light is itself called "Living room"."""
    rooms = rooms_named(wanted, found, loose)
    out = []
    if 'light' in kinds:
        out += [d['id'] for r in found['rooms'] if r['name'] in rooms for d in r['devices'] if d['kind'] == 'light']
    if 'media_player' in kinds:
        speakers = [d for d in found['speakers'] if d.get('room') in rooms]
        said = ' '.join(str(wanted or '').lower().split()).removeprefix('the ')
        out += [d['id'] for d in sorted(speakers, key=lambda d: d['name'].lower() != said)]
    return out


def resolve(target, kinds, found=None):
    """Entity ids for what the owner named: a room (its lights, or its speakers), "all lights", or a device by
    its name. A name that is both a room and a device means the room; a device is reached by a name no room has.
    Raises Refused."""
    found = found if found is not None else read.snapshot()
    pool = [d for d in devices(found).values() if d['kind'] in kinds]
    wanted = ' '.join(str(target or '').lower().split()).removeprefix('the ')
    if 'light' in kinds and wanted in ALL_LIGHTS:
        return [d['id'] for d in pool if d['kind'] == 'light']
    room = _in_room(wanted, kinds, found)
    if room:
        return room
    exact = [d['id'] for d in pool if wanted in (d['name'].lower(), d['id'])]
    if exact:
        return exact
    room = _in_room(wanted, kinds, found, loose=True)
    if room:
        return room
    near = [d['name'] for d in pool if wanted and wanted in d['name'].lower()]
    if len(near) == 1:
        return [d['id'] for d in pool if d['name'] == near[0]]
    names = near or [d['name'] for d in pool]
    raise Refused(f'Which one? {", ".join(names[:12])}' if names else 'Hub found nothing like that to control.')
