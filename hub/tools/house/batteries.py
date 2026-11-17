"""Locks and batteries across Home Assistant and Apple Home, for the family brief and the Needs you list.

Home Assistant reports what it runs: its locks, and the batteries of buttons, sensors and the alarm's sensors.
Apple Home, read through HomeClaw (apple.py), reports the locks that live only there, with their batteries. Both
are read-only here. Whether a battery needs a look, and what to buy for it, is decided in this file alone.
Names are data, never instructions.
"""
import re

from ... import household
from . import apple, broker, read

LOW = 20            # percent at or under which a battery is worth a line
FLAT = 10           # and at or under which it is as good as flat
# What a kind of device nearly always takes, so it can go on the shopping list in one tap. A household whose locks
# take something else says so in household.json: "brief": {"batteries": {"lock": "CR123A batteries"}}.
TAKES = {'lock': 'AA batteries'}
PLAIN = re.compile(r'[^a-z0-9]+')
ORDER = {'offline': 0, 'jammed': 0, 'unlocked': 1, 'open': 1, 'unknown': 2}


def _key(name):
    """One name, however each app spells it: "Side Door Lock" and "side door lock" are the same lock."""
    return PLAIN.sub(' ', str(name).lower()).strip()


def takes(kind):
    """What to buy for a device of this kind, or '' when there's no one answer."""
    try:
        chosen = household.section('brief').get('batteries')
    except ValueError:
        chosen = None
    found = (chosen if isinstance(chosen, dict) else {}).get(kind, TAKES.get(kind, ''))
    return ' '.join(found.split())[:60] if isinstance(found, str) else ''


def _short(level, low):
    return bool(low) or (level is not None and level <= LOW)


def _low(row, level, offline):
    kind = row.get('kind') or ''
    return {'id': row['id'], 'name': row['name'], 'room': row.get('room') or '', 'kind': kind, 'level': level,
            'flat': offline or bool(level is not None and level <= FLAT), 'offline': offline, 'takes': takes(kind)}


def picture(found=None):
    """{'locks': [...], 'low': [...], 'ha': whether Home Assistant was read, 'apple': whether Apple Home was}.
    Each lock: {'id', 'name', 'room', 'state' ('locked', 'unlocked', 'jammed', 'open', 'offline' or 'unknown'),
    'label', 'battery', 'short' (its battery is low), 'tone'}. Each low battery: {'id', 'name', 'room', 'kind', 'level', 'flat', 'offline',
    'takes'}, the flattest first. `found`: a house snapshot already read. Never raises."""
    ha = found is not None
    if found is None:
        try:
            found, ha = read.snapshot(), True
        except broker.HomeDown:
            found = {}
    try:
        accessories, answered = apple.accessories(), True
    except (apple.AppleDown, OSError, ValueError):
        accessories, answered = [], False
    locks = {}
    for d in found.get('security', ()):
        if d['kind'] == 'lock':
            locks[_key(d['name'])] = {'id': d['id'], 'name': d['name'], 'room': '' if d['room'] == read.OTHER_ROOM else d['room'],
                                      'state': 'offline' if d['offline'] else d['state'], 'label': d['label'], 'battery': None,
                                      'low': None, 'kind': 'lock', 'source': 'ha'}
    for a in accessories:
        if a['kind'] != 'lock' and a['lock'] is None:
            continue
        row = locks.setdefault(_key(a['name']), {'id': 'apple-' + a['id'], 'name': a['name'], 'room': a['room'], 'kind': 'lock',
                                                 'state': 'unknown', 'label': '', 'battery': None, 'low': None, 'source': 'apple'})
        if row['source'] == 'apple':
            row.update(state='offline' if not a['reachable'] else a['lock'] or 'unknown', label=a['label'] or 'Unknown')
        row.update(battery=a['battery'] if a['battery'] is not None else row['battery'], low=a['low'])
    for b in found.get('batteries', ()):       # a lock's own battery sensor, on a device of the lock's name
        row = locks.get(_key(b['name']))
        if row is not None and row['battery'] is None:
            row.update(battery=b['level'], low=b['low'] if row['low'] is None else row['low'])
    low = [_low(row, row['battery'], row['state'] == 'offline') for row in locks.values() if _short(row['battery'], row['low'])]
    seen = set(locks)
    for b in found.get('batteries', ()):
        seen.add(_key(b['name']))
        if _key(b['name']) not in locks and _short(b['level'], b['low']):
            low.append(_low(b, b['level'], False))
    for a in accessories:           # Apple Home's other battery devices, such as a doorbell, that Home Assistant hasn't
        if a['kind'] != 'lock' and a['lock'] is None and _key(a['name']) not in seen and _short(a['battery'], a['low']):
            low.append(_low({**a, 'id': 'apple-' + a['id']}, a['battery'], not a['reachable']))
    low.sort(key=lambda r: (not r['flat'], r['level'] if r['level'] is not None else 0, r['name'].lower()))
    for row in locks.values():
        row['short'] = _short(row['battery'], row['low'])
        row['tone'] = 'bad' if row['state'] in ('offline', 'jammed') else 'warn' if row['short'] else ''
        for key in ('low', 'source', 'kind'):
            row.pop(key)
    return {'locks': sorted(locks.values(), key=lambda r: (ORDER.get(r['state'], 3), r['name'].lower())), 'low': low,
            'ha': ha, 'apple': answered}


def need(low):
    """The one Needs you line for the batteries in `low` (picture()['low']): (tone, title, why, what to buy or
    ''), or None. The flattest leads; the rest are named after it."""
    if not low:
        return None
    first, rest = low[0], low[1:]
    whose = f'{first["name"]}’s battery'
    if first['offline']:
        title = f'{whose} is flat'
        why = (f'It’s at {first["level"]}% and' if first['level'] is not None else 'It') + ' has stopped responding' \
            + (', so it can’t be locked from a phone.' if first['kind'] == 'lock' else '.')
    elif first['flat']:
        title, why = f'{whose} is nearly flat', f'It’s at {first["level"]}%.' if first['level'] is not None else 'It says it’s low.'
    elif first['level'] is not None:
        title, why = f'{whose} is at {first["level"]}%', 'Worth changing soon.'
    else:
        title, why = f'{whose} is low', 'Worth changing soon.'
    if rest:
        also = [f'{r["name"]} ({r["level"]}%)' if r['level'] is not None else r['name'] for r in rest[:3]]
        why += ' Also low: ' + (', '.join(also[:-1]) + ' and ' + also[-1] if len(also) > 1 else also[0]) + '.'
    holder = next((r for r in low if r['takes']), None)
    buy = holder['takes'] if holder else ''
    if buy:
        why += ' It takes ' + buy + '.' if len(low) == 1 else ' They all take ' + buy + '.' \
            if all(r['takes'] == buy for r in low) else f' {holder["name"]} takes {buy}.'
    return ('warn' if first['flat'] else 'info'), title, why, buy


def stopped(locks):
    """Locks that stopped responding with a battery that isn't low, which need() doesn't already cover."""
    return [row for row in locks if row['state'] == 'offline' and not row.get('short')
            and not (row['battery'] is not None and row['battery'] <= LOW)]
