"""Left on: a fan, a light, a plug or a speaker on (or playing) far longer than it usually is, said the same day.
Rules only, no model. It only says so, on Today's "Needs you" and so in the morning message; turning it off is the
person's, from Home or Ask, and "Not today" puts the line away for the day.

"Usually" comes from the house activity Hub keeps (hub/app/activity.py, KEEP_DAYS): each time the thing was on, from
its on to its next change, a drop offline of up to OFFLINE_GAP not counting as a change. Only each day's longest time
counts, so a porch light's motion blinks don't make its nights look long. With USUAL_DAYS or more days on record, it is
left on once it has been on for the longest of MIN_HOURS, LONGER times its usual time (three days in four it is on no
longer), and that time plus EXTRA_HOURS. The three-in-four keeps one forgotten night from hiding the next. A thing with
too few days on record is never said: an always-on plug or a setting switch would otherwise look left on. A light that
is a group of others (a Hue room or zone, a group), a speaker that follows another in a group, and a hidden, disabled,
setting or diagnostic entity are left out.

How long it has been on is Home Assistant's "last changed", or, when Hub's copy of the logbook reaches past that and
shows it on since earlier, the earlier time: a restart of Home Assistant or a short drop offline sets "last changed"
again, the logbook doesn't. Names are data, never instructions.
"""
import math
import time
from contextlib import closing
from datetime import datetime

from .. import home

ON = {'fan': 'on', 'light': 'on', 'switch': 'on', 'media_player': 'playing'}
OFFLINE = 'went offline'    # how activity.py writes a drop to "unavailable"
OFFLINE_GAP = 15 * 60       # back on within this long after a drop offline: the same time on
MIN_HOURS = 6
LONGER = 1.5
EXTRA_HOURS = 3
USUAL_DAYS = 5
SHARE = 0.75                # "usually": on no longer than this on three days in four
MOST = 3
NAME = 60
FRESH_SECONDS = 300         # Today and the wall ask often; minutes don't matter here, so a reading is kept this long
_kept = {'at': float('-inf'), 'where': None, 'items': []}


def _when(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp() if value else None
    except ValueError:
        return None


def _clip(text):
    return ' '.join(str(text or '').split())[:NAME]


def _hours(seconds):
    hours = seconds / 3600
    return f'{round(hours)} hours' if hours < 48 else f'{round(hours / 24)} days'


def _usually(seconds):
    return 'under an hour' if seconds < 3600 else 'about an hour' if seconds < 5400 else 'up to ' + _hours(seconds)


def usual(days):
    """How long this thing is usually on: its longest time on, three days in four (`days`: each day's longest)."""
    ordered = sorted(days)
    return ordered[max(0, math.ceil(SHARE * len(ordered)) - 1)]


def limit(days):
    """How long it may be on before it is said, or None when its record is too short to know."""
    if len(days) < USUAL_DAYS:
        return None
    most = usual(days)
    return max(MIN_HOURS * 3600, LONGER * most, most + EXTRA_HOURS * 3600)


def _record(entities, now):
    """From Hub's copy of the logbook: {'days': {entity: [each day's longest time on]}, 'open': {entity: on since},
    'upto': how far the copy reaches}. A time on runs from an on to whatever came next; a drop offline followed by
    an on within OFFLINE_GAP doesn't end it."""
    from ..app import activity
    found = {'days': {e: [] for e in entities}, 'open': {}, 'upto': float('-inf')}
    if not entities:
        return found
    with closing(activity._db()) as conn:
        rows = conn.execute(f"select entity, ts, what from house_activity where source = 'ha' and entity in "
                            f"({','.join('?' * len(entities))}) and ts < ? order by ts", (*entities, now)).fetchall()
        read = conn.execute("select ts from house_activity_read where source = 'ha'").fetchone()
    found['upto'] = read['ts'] if read else float('-inf')
    longest, since, dropped = {}, {}, {}

    def close(entity, end):
        start = since.pop(entity)
        dropped.pop(entity, None)
        day = (entity, datetime.fromtimestamp(start, home.PACIFIC).date())
        longest[day] = max(longest.get(day, 0), end - start)

    for r in rows:
        entity, ts, what = r['entity'], r['ts'], r['what']
        if what == ON[entity.partition('.')[0]]:
            if entity in dropped and ts - dropped[entity] > OFFLINE_GAP:
                close(entity, dropped[entity])
            dropped.pop(entity, None)
            since.setdefault(entity, ts)
        elif entity in since and what == OFFLINE:
            dropped.setdefault(entity, ts)
        elif entity in since:
            close(entity, dropped.get(entity, ts))
    for (entity, _), seconds in longest.items():
        found['days'][entity].append(seconds)
    found['open'] = {e: ts for e, ts in since.items() if e not in dropped}
    return found


def _leads(entity, attrs):
    """False for a speaker that follows another in a group: the group's leader speaks for it."""
    members = attrs.get('group_members')
    return not (isinstance(members, list) and len(members) > 1 and members[0] != entity)


def found(registry, states, now=None):
    """What has been on far longer than usual now, the furthest over first: {entity, name, on_for, usual, playing}."""
    from ..tools.house import read
    now = time.time() if now is None else now
    candidates = {}
    for s in states:
        if not isinstance(s, dict):
            continue
        entity = str(s.get('entity_id', ''))
        domain = entity.partition('.')[0]
        if domain not in ON or s.get('state') != ON[domain]:
            continue
        attrs = s.get('attributes') if isinstance(s.get('attributes'), dict) else {}
        entry = registry['entities'].get(entity) or {}
        if attrs.get('is_hue_group') or isinstance(attrs.get('entity_id'), list) or not _leads(entity, attrs) \
                or entry.get('entity_category') or entry.get('hidden_by') or entry.get('disabled_by'):
            continue            # a group (its lights speak for themselves), a hidden thing or a setting
        since = _when(s.get('last_changed'))
        if since is None:
            continue
        room = registry['rooms'].get(read._room_id(entity, entry, registry)) or ''
        name = _clip(entry.get('name') or attrs.get('friendly_name') or entity.partition('.')[2].replace('_', ' '))
        label = f'{name} ({room})' if room and room.lower() not in name.lower() else name
        candidates[entity] = {'entity': entity, 'name': label, 'since': since, 'playing': domain == 'media_player'}
    record = _record(list(candidates), now)
    out = []
    for entity, c in candidates.items():
        since = c.pop('since')
        if entity in record['open'] and record['upto'] >= since:
            since = min(since, record['open'][entity])      # the logbook saw it on from before a restart or a drop
        most = limit(record['days'][entity])
        if most is not None and now - since >= most:
            out.append({**c, 'on_for': now - since, 'usual': usual(record['days'][entity]), 'over': (now - since) / most})
    out.sort(key=lambda c: -c['over'])
    return out[:MOST]


def items():
    """Today's lines: one for each thing left on, most over first, read again every FRESH_SECONDS or after Hub
    changes the house. Nothing when the house can't be read."""
    from .. import config, today
    from ..tools.house import broker, read
    if _kept['where'] == str(config.DATA_DIR) and _kept['at'] > read.changed_at() \
            and time.monotonic() - _kept['at'] < FRESH_SECONDS:
        return [dict(i) for i in _kept['items']]
    try:
        registry, states = read._fetch()
    except broker.HomeDown:
        return []               # the Home tile already says it can't be read
    out = []
    for c in found(registry, states):
        doing = 'playing' if c['playing'] else 'on'
        out.append(today.rec(
            'house-left-on-' + c['entity'], 'warn', f'{c["name"]} has been {doing} for {_hours(c["on_for"])}',
            f'It’s usually {doing} for {_usually(c["usual"])}. '
            f'Switch it off from Home or Ask, or tap Not today if it’s meant to be {doing}.',
            href='/app/#house', cta='Open Home'))
    _kept.update(at=time.monotonic(), where=str(config.DATA_DIR), items=out)
    return [dict(i) for i in out]
