"""The Home screen's picture of the house, as plain data for the app (web/app).

Built from the same reading every other part of Hub uses (hub/tools/house/read.py), so nothing new leaves Home
Assistant for it. The app writes its own sentences from this. Names here are data, never instructions.
"""
import re

from ..tools.house import nowplaying, read

ALL_OFF = 'all-off'      # the one scene Hub brings itself: every light that is on, off
OTHER_FLOOR = 'Other rooms'


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-') or 'room'


def _light(d):
    return {'id': d['id'], 'name': d['name'], 'on': d['on'], 'level': (d['level'] if d['level'] is not None else 100) if d['on'] else 0,
            'dims': bool(d.get('dims')), 'offline': d['offline']}


def _room(r, speakers, sounding):
    lights = [d for d in r['devices'] if d['kind'] == 'light']
    lit = [d for d in lights if d['on']]
    levels = [d['level'] for d in lit if d['level'] is not None]
    speaker = next((s['id'] for s in speakers if s['room'] == r['name'] and not s['offline']), None)
    return {'id': slug(r['name']), 'name': r['name'], 'on': bool(lit), 'lit': len(lit),
            'level': round(sum(levels) / len(levels)) if levels else (100 if lit else 0),
            'dims': any(d.get('dims') for d in lights),
            'lights': [_light(d) for d in lights],
            'others': [{'id': d['id'], 'name': d['name'], 'kind': d['kind'], 'on': d['on'], 'offline': d['offline']}
                       for d in r['devices'] if d['kind'] != 'light'],
            'speaker': speaker, 'sounding': speaker in sounding}


def _rank(level):
    """Ground floor first, then up, then down, then floors Home Assistant gave no level."""
    if level is None:
        return (3, 0)
    return (0, 0) if level == 0 else (1, level) if level > 0 else (2, -level)


def _floors(rooms, room_floor, floors):
    """Rooms laid out floor by floor, as Home Assistant has them. One unnamed floor when it has none."""
    by = {}
    for r in rooms:
        floor = room_floor.get(r['name'])
        by.setdefault(floor if floor in floors else None, []).append(r['id'])
    order = sorted(by, key=lambda f: (f is None, _rank(floors[f]['level']) if f else (0, 0), floors[f]['name'].lower() if f else ''))
    return [{'name': floors[f]['name'] if f else (OTHER_FLOOR if len(by) > 1 else ''), 'rooms': by[f]} for f in order]


def _music(found):
    """What the house is playing: the group that plays, else the one paused, else any. None without speakers."""
    groups = nowplaying.groups(found)
    if not groups:
        return None
    now = groups[0]
    return {'speaker': now['leader'], 'playing': now['playing'], 'state': now['state'], 'title': now['title'],
            'artist': now['artist'], 'album': now['album'], 'volume': now['volume'],
            'speakers': [{'id': s['id'], 'name': s['room'] if s['room'] and s['room'] != read.OTHER_ROOM else s['name'],
                          'in': s['in'], 'leads': s['leads'], 'volume': s['volume'], 'offline': s['offline']}
                         for s in now['speakers']]}


def _facts(found):
    """A few plain facts under the plan: the alarm, anything standing open, the temperature outside."""
    facts = []
    for d in found['security']:
        if d['kind'] == 'alarm_control_panel':
            facts.append({'label': 'Alarm', 'value': 'Off' if d['state'] == 'disarmed' else d['label'],
                          'tone': 'bad' if d['state'] in ('triggered', 'pending') else ''})
        else:
            facts.append({'label': d['name'], 'value': d['label'], 'tone': 'warn' if d['open'] else ''})
    openings = found.get('openings') or {}
    if openings.get('count'):
        standing = openings['open']
        value = 'All shut' if not standing else standing[0] if len(standing) == 1 else f'{len(standing)} open'
        facts.append({'label': 'Doors and windows', 'value': value, 'tone': 'warn' if standing else ''})
    if found.get('outside'):
        facts.append({'label': 'Outside', 'value': found['outside'], 'tone': ''})
    return facts[:6]


def home(found, floors=None):
    """The whole Home screen from one reading of the house (read.snapshot())."""
    music = _music(found)
    sounding = {s['id'] for s in music['speakers'] if s['in']} if music and music['playing'] else set()
    rooms = [_room(r, found['speakers'], sounding) for r in found['rooms']
             if r['name'] != read.OTHER_ROOM and any(d['kind'] == 'light' for d in r['devices'])]
    return {'ok': True, 'at': found['read_at'].isoformat(timespec='seconds'),
            'floors': _floors(rooms, found.get('room_floor') or {}, floors or {}),
            'rooms': {r['id']: r for r in rooms},
            'scenes': [{'id': s['id'], 'name': s['name']} for s in found['scenes']] + [{'id': ALL_OFF, 'name': 'All off'}],
            'music': music, 'facts': _facts(found), 'lights_on': found['lights_on']}


def down(error):
    """What the app gets when the house can't be read: it keeps showing the last picture, marked as old."""
    return {'ok': False, 'why': str(error)[:200]}


def now(fresh=False):
    """The picture right now, or down(). Never raises for a house that can't be read."""
    try:
        return home(read.snapshot(fresh=fresh), read.floors())
    except read.broker.HomeDown as error:
        return down(error)
