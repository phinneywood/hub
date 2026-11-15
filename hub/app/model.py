"""The Home screen's picture of the house, as plain data for the app (web/app).

Built from the same reading every other part of Hub uses (hub/tools/house/read.py), so nothing new leaves Home
Assistant for it. The app writes its own sentences from this. Names here are data, never instructions.
"""
import re

from ..tools.house import nowplaying, read
from . import levels
from . import scenes as mine

ALL_OFF = 'all-off'      # the one scene Hub brings itself: every light indoors that is on, off
# A floor with one of these names in Home Assistant is outdoors. Its lights are not the house's: "All off" and
# a scene's "every other light off" leave them to whatever switches them at dusk and dawn.
OUTDOORS = ('outside', 'outdoors', 'outdoor', 'exterior', 'garden', 'yard')
OTHER_FLOOR = 'Other rooms'


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-') or 'room'


def _light(d):
    return {'id': d['id'], 'name': d['name'], 'on': d['on'], 'level': (d['level'] if d['level'] is not None else 100) if d['on'] else 0,
            'dims': bool(d.get('dims')), 'offline': d['offline']}


def outdoors(found, floors):
    """The names of the rooms that are outdoors: those on a floor Home Assistant calls Outside (or the like), and
    any the owner lists in household.json, "app": {"outside": ["Porch"]}."""
    from .. import household
    names = {room for room, floor in (found.get('room_floor') or {}).items()
             if (floors or {}).get(floor, {}).get('name', '').strip().lower() in OUTDOORS}
    try:
        listed = household.section('app').get('outside')
    except ValueError:
        listed = None
    return names | {str(n) for n in listed if isinstance(n, str)} if isinstance(listed, list) else names


def indoor_lights_on(found, floors):
    """Entity ids of the lamps that are lit indoors: what "All off" switches off."""
    out = outdoors(found, floors)
    return [d['id'] for r in found['rooms'] if r['name'] not in out for d in r['devices'] if d['kind'] == 'light' and d['on']]


def _home_of(speaker, names):
    """The room a speaker is in: the one Home Assistant gives it, else the room it is named after
    ("Kitchen", "Kitchen speakers"), the longest such name winning. None for a speaker that belongs to no room."""
    if speaker['room'] in names:
        return speaker['room']
    said = speaker['name'].lower()
    return max((n for n in names if said == n.lower() or said.startswith(n.lower() + ' ')), key=len, default=None)


def _button(b):
    return {'id': b['id'], 'name': b['name'], 'battery': b['battery'], 'offline': b['offline'], 'last': b['last'],
            'at': b['at'], 'does': b['does']}


def _room(r, speakers, homes, sounding, outside, buttons=()):
    lights = [d for d in r['devices'] if d['kind'] == 'light']
    lit = [d for d in lights if d['on']]
    levels = [d['level'] for d in lit if d['level'] is not None]
    speaker = next((s['id'] for s in speakers if homes.get(s['id']) == r['name'] and not s['offline']), None)
    return {'id': slug(r['name']), 'name': r['name'], 'on': bool(lit), 'lit': len(lit), 'outside': r['name'] in outside,
            'level': round(sum(levels) / len(levels)) if levels else (100 if lit else 0),
            'dims': any(d.get('dims') for d in lights),
            'lights': [_light(d) for d in lights],
            'others': [{'id': d['id'], 'name': d['name'], 'kind': d['kind'], 'on': d['on'], 'offline': d['offline']}
                       for d in r['devices'] if d['kind'] != 'light'],
            'speaker': speaker, 'sounding': speaker in sounding,
            'buttons': [_button(b) for b in buttons if b['room'] == r['name']]}


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


_main = {'leader': None}        # the speaker that led the house's main music at the last reading


def streams(found):
    """Every group of speakers, the house's main music first: one that is playing before one that is paused.
    Of two that play, the one that was the main music a moment ago stays it, so starting something in another
    room doesn't move the playing strip; failing that, the one in more rooms (the music downstairs before a
    lullaby in one bedroom)."""
    groups = sorted(nowplaying.groups(found), key=lambda g: (not g['playing'], g['state'] != 'paused', g['leader'] != _main['leader'],
                                                             -sum(s['in'] for s in g['speakers'])))
    if groups:
        _main['leader'] = groups[0]['leader']
    return groups


def _music(found, homes):
    """What the house is playing: the group that plays, else the one paused, else any. None without speakers.
    A speaker is called by its room. One that can't be reached and belongs to no room (a laptop that is asleep,
    say) is left out: it is not part of the house. When other rooms are playing something of their own (a
    lullaby upstairs, say), `also` names each of those and its speakers are marked `other`."""
    groups = streams(found)
    if not groups:
        return None
    now, others = groups[0], [g for g in groups[1:] if g['playing']]
    elsewhere = {s['id'] for g in others for s in g['speakers'] if s['in']}
    return {'speaker': now['leader'], 'playing': now['playing'], 'state': now['state'], 'title': now['title'],
            'artist': now['artist'], 'album': now['album'], 'volume': now['volume'],
            'speakers': [{'id': s['id'], 'name': homes.get(s['id']) or s['name'], 'in': s['in'], 'leads': s['leads'],
                          'volume': s['volume'], 'offline': s['offline'], 'other': s['id'] in elsewhere}
                         for s in now['speakers'] if homes.get(s['id']) or not s['offline'] or s['in']],
            'also': [{'speaker': g['leader'], 'title': g['title'], 'artist': g['artist'],
                      'speakers': [s['id'] for s in g['speakers'] if s['in']],
                      'rooms': [homes.get(s['id']) or s['name'] for s in g['speakers'] if s['in']]} for g in others]}


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


def _scenes(found, floors):
    """The scenes Hub keeps (each knows whether the house is in it now), then Home Assistant's, then All off."""
    out = outdoors(found, floors)
    kept = [{'id': s['id'], 'name': s['name'], 'on': mine.on(s, found, out), 'mine': True} for s in mine.kept()]
    theirs = [{'id': s['id'], 'name': s['name'], 'on': False, 'mine': False} for s in found['scenes']]
    return kept + theirs + [{'id': ALL_OFF, 'name': 'All off', 'on': not indoor_lights_on(found, floors), 'mine': False}]


def home(found, floors=None):
    """The whole Home screen from one reading of the house (read.snapshot())."""
    names = [r['name'] for r in found['rooms'] if r['name'] != read.OTHER_ROOM]
    names += [s['room'] for s in found['speakers'] if s['room'] != read.OTHER_ROOM and s['room'] not in names]
    homes = {s['id']: _home_of(s, names) for s in found['speakers']}
    music = _music(found, homes)
    sounding = {s['id'] for s in music['speakers'] if (s['in'] and music['playing']) or s['other']} if music else set()
    outside = outdoors(found, floors)
    rooms = [_room(r, found['speakers'], homes, sounding, outside, found.get('buttons') or ()) for r in found['rooms']
             if r['name'] != read.OTHER_ROOM and any(d['kind'] == 'light' for d in r['devices'])]
    rooms.sort(key=lambda r: (-len(r['lights']), r['name'].lower()))      # the rooms with most lamps lead each floor
    return {'ok': True, 'at': found['read_at'].isoformat(timespec='seconds'),
            'floors': _floors(rooms, found.get('room_floor') or {}, floors or {}),
            'rooms': {r['id']: r for r in rooms},
            'scenes': _scenes(found, floors),
            'music': music, 'facts': _facts(found), 'lights_on': found['lights_on']}


def down(error):
    """What the app gets when the house can't be read: it keeps showing the last picture, marked as old."""
    return {'ok': False, 'why': str(error)[:200]}


def now(fresh=False):
    """The picture right now, or down(). Never raises for a house that can't be read."""
    try:
        found = read.snapshot(fresh=fresh)
        levels.note(found)
        return home(found, read.floors())
    except read.broker.HomeDown as error:
        return down(error)
