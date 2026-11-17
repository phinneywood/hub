"""The family brief: what Today and the wall show around the list, from what Hub can read and nothing else.

The weather (Home Assistant's forecast), the house (its locks, alarm, garage and doors left open, from Home Assistant
and Apple Home), the overnight note from the house's caretaker when one is installed, and a line or two that Ask's
model writes to join them up (ask/note.py, only while it is on). The list, the calendar, school and the trip are
today.py's. Every part fails soft and on time: a source that is slow or can't be read is left out, never waited on.

What the wall shows is for anyone in the kitchen, so it is the household's only (the owner's decision, 2026-11-16):
the weather, the house, the overnight note and the household's own needs. Calendars, school, mail and the written
note stay on each person's phone. `family()` is the one door to the wall's picture.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

DEADLINE_SECONDS = 2.0
LINE = 200
# Which needs are the household's, shown on the wall too. Anything else stays on the phones and is only counted.
FAMILY = ('house-', 'kitchen-cart', 'trip-')
# The parts of Today's picture the wall draws. The wall's picture carries these and nothing else, so a part added to
# Today later reaches the wall only once it is added here (with whatever of it is the household's).
WALL = ('ok', 'at', 'part', 'weather', 'weather_says', 'house', 'overnight')
# What the note's model may hear of the list: the house's and the trip's own needs. Never Kitchen's (the cart), mail's
# or Hub's own upkeep.
NOTED = ('house-', 'trip-')
OVERNIGHT_TONES = ('fixed', 'by_itself', 'needs_you', 'info')
_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='hub-brief')


def part(now):
    """'morning' (5 to 11), 'day' (to 5 pm), 'evening' (to 10 pm) or 'night'."""
    hour = now.hour
    return 'morning' if 5 <= hour < 11 else 'day' if 11 <= hour < 17 else 'evening' if 17 <= hour < 22 else 'night'


def _clip(text, most=LINE):
    return ' '.join(str(text or '').split())[:most]


def is_family(key):
    return str(key).startswith(FAMILY)


# ---- the house: locks, the alarm, the garage, doors left open ----

def _house(now):
    """{'rows': [{'kind', 'name', 'state', 'note', 'tone', 'low'}], 'says': a sentence} or None when nothing could be
    read. `tone` is about the state (a lock that stopped responding, a door open at night); `low`, the battery."""
    from ..tools.house import batteries, broker, read
    try:
        found = read.snapshot()
    except broker.HomeDown:
        found = None
    locks = batteries.picture(found if found is not None else {})
    if found is None and not locks['apple']:
        return None
    late = read.night(now)
    rows = [{'kind': 'lock', 'name': lock['name'],
             'state': 'Not responding' if lock['state'] == 'offline' else lock['label'] or lock['state'].capitalize(),
             'note': f'Battery {lock["battery"]}%' if lock['battery'] is not None else '',
             'tone': 'bad' if lock['tone'] == 'bad' else 'warn' if late and lock['state'] in ('unlocked', 'open') else '',
             'low': lock['short']} for lock in locks['locks']]
    for d in (found or {}).get('security', ()):
        if d['kind'] == 'alarm_control_panel':
            rows.append({'kind': 'alarm', 'name': d['name'], 'state': d['label'], 'note': '', 'low': False,
                         'tone': 'bad' if d['state'] in ('triggered', 'pending') else ''})
        elif d['kind'] == 'cover':
            rows.append({'kind': 'garage', 'name': d['name'], 'state': d['label'], 'note': '', 'low': False,
                         'tone': 'warn' if d['open'] and late else ''})
    for name in (found or {}).get('openings', {}).get('open', ()):
        rows.append({'kind': 'open', 'name': name, 'state': 'Open', 'note': '', 'tone': 'warn' if late else '', 'low': False})
    return {'rows': rows, 'says': house_says(rows)} if rows else None


def house_says(rows):
    """"Every lock is locked and the alarm is on." / "The side door lock isn’t responding." One sentence."""
    locks = [r for r in rows if r['kind'] == 'lock']
    stuck = [r for r in locks if r['state'] in ('Not responding', 'Jammed', 'Unknown')]
    unlocked = [r for r in locks if r['state'] == 'Unlocked']
    opened = [r['name'] for r in rows if r['kind'] in ('open', 'garage') and r['state'] in ('Open', 'Opening')]
    alarm = next((r for r in rows if r['kind'] == 'alarm'), None)
    parts = []
    if stuck:
        parts.append(f'{stuck[0]["name"]} {"isn’t responding" if stuck[0]["state"] != "Jammed" else "is jammed"}'
                     + (f', and {len(stuck) - 1} more' if len(stuck) > 1 else ''))
    if unlocked:
        parts.append(f'{_names([r["name"] for r in unlocked])} {"is" if len(unlocked) == 1 else "are"} unlocked')
    if opened:
        parts.append(f'{_names(opened)} {"is" if len(opened) == 1 else "are"} open')
    if not parts and locks and all(r['state'] == 'Locked' for r in locks):
        parts.append('Every lock is locked' if len(locks) > 1 else f'{locks[0]["name"]} is locked')
    if alarm and alarm['state'].startswith(('Armed', 'Going off', 'About')):
        parts.append(f'the alarm is {alarm["state"].lower()}')
    elif alarm and alarm['state'] == 'Disarmed' and not stuck and not unlocked:
        parts.append('the alarm is off')
    if not parts:
        return ''
    said = ', '.join(parts[:-1]) + (' and ' if len(parts) > 1 else '') + parts[-1]
    return said[:1].upper() + said[1:] + '.'


def _names(names):
    names = list(names)
    return ', '.join(names[:-1]) + ' and ' + names[-1] if len(names) > 1 else names[0]


# ---- the overnight note, from the caretaker when one is installed ----

def _overnight():
    """The caretaker's note on the night ({'head', 'lines': [{'text', 'tone'}], 'needs'}), or None. The caretaker is
    a separate part of Hub; without it there is no overnight note."""
    try:
        from .. import caretaker
    except ImportError:
        return None
    found = caretaker.note()
    if not isinstance(found, dict):
        return None
    lines = []
    for line in found.get('lines') or ():
        text = _clip(line.get('text') or line.get('words')) if isinstance(line, dict) else ''
        if text:
            lines.append({'text': text, 'tone': line.get('tone') if line.get('tone') in OVERNIGHT_TONES else 'info'})
    head = _clip(found.get('headline') or found.get('head')) or (lines[0]['text'] if lines else '')
    if not head:
        return None
    needs = found.get('needs')
    return {'head': head, 'lines': lines[:6],
            'needs': needs if isinstance(needs, int) and not isinstance(needs, bool) else sum(r['tone'] == 'needs_you' for r in lines)}


# ---- the note Ask's model writes, from what the wall would show ----

def facts(found):
    """What the note is written from: the weather, the person's calendar, school, the house and the house's own needs,
    without the minute-by-minute (the temperature now, the hourly strip), so the note is written again only when
    something that matters changes. No mail, nothing of Kitchen's."""
    weather = found.get('weather') or {}
    day = weather.get('tomorrow' if found['part'] in ('evening', 'night') else 'today') or {}
    return {
        'when': f'{found["part"]}, {datetime.fromisoformat(found["at"]).strftime("%A %-d %B")}',
        'weather': {k: day.get(k) for k in ('words', 'high', 'low', 'rain', 'wear')} if day else None,
        'today': [f'{s["time"]} {s["half"]} {s["title"]}'.replace('  ', ' ') for s in found['today']][:8],
        'tomorrow': [f'{s["time"]} {s["half"]} {s["title"]}'.replace('  ', ' ') for s in found['tomorrow']][:8],
        'school': [f'{s["day"]}: {s["title"]}' + (' (cancelled)' if s['struck'] else '') for s in found['school']][:6],
        'house': [f'{r["name"]}: {r["state"]}' + (f' ({r["note"]})' if r['note'] else '') for r in (found.get('house') or {}).get('rows', [])
                  if r['tone'] or r['low'] or r['kind'] == 'lock'][:8],
        'needs': [n['title'] for n in found['needs'] if str(n['key']).startswith(NOTED)][:6],
    }


def _note(found):
    from .ask import note
    if found['part'] == 'night':
        return None
    return note.current(facts(found))


# ---- all of it ----

def start(now):
    """Begin reading the brief's own parts side by side, on threads of their own so the list's checks don't hold
    them up; pair with finish()."""
    from ..tools.house import weather
    return {'weather': _pool.submit(weather.picture, now), 'house': _pool.submit(_house, now), 'overnight': _pool.submit(_overnight),
            'by': time.monotonic() + DEADLINE_SECONDS}


def finish(jobs, now):
    """{'part', 'weather', 'weather_says', 'house', 'overnight'}: each part that was read in time, else None.
    Never raises."""
    from ..tools.house import weather
    found = {'part': part(now)}
    for name in ('weather', 'house', 'overnight'):
        job = jobs[name]
        try:
            found[name] = job.result(timeout=max(0.0, jobs['by'] - time.monotonic()))
        except Exception:  # noqa: BLE001 - one part that is slow or can't be read must not take the brief with it
            found[name] = None
    found['weather_says'] = weather.says(found['weather'], found['part']) if found['weather'] else ''
    return found


def note(found):
    """The written note for a finished picture, {'text', 'at'} or None. Never raises."""
    try:
        return _note(found)
    except Exception:  # noqa: BLE001
        return None


def family(found):
    """The picture as the wall shows it: the parts it draws and the household's own needs. The rest of the household's
    list is counted; nobody's calendar, school dates, mail or note is on it."""
    needs = [n for n in found['needs'] if is_family(n['key'])]
    away = len(found['needs']) - len(needs) + found['more']
    return {**{key: found.get(key) for key in WALL}, 'needs': needs, 'more': 0, 'elsewhere': away, 'quiet': [], 'trip': None,
            'today': [], 'tomorrow': [], 'school': [], 'note': None, 'mine': False, 'wall': True}


# ---- said aloud: the brief as a few sentences, for a Shortcut or a speaker ----

TIME = re.compile(r'^\d{1,2}:\d{2}$')


def said_time(slot):
    """A calendar row's time as it is said: "9:30 am", "7 pm", "noon"."""
    hour, _, minute = slot['time'].partition(':')
    if minute == '00':
        return 'noon' if hour == '12' and slot['half'] == 'pm' else 'midnight' if hour == '12' else f'{hour} {slot["half"]}'
    return f'{slot["time"]} {slot["half"]}'


def spoken(found):
    """The brief as a few plain sentences: the weather, what's first, school, the house, and how much needs you."""
    said = []
    hello = {'morning': 'Good morning.', 'day': 'Good afternoon.', 'evening': 'Good evening.', 'night': ''}[found['part']]
    if hello:
        said.append(hello)
    if found.get('weather_says'):
        said.append(found['weather_says'])
        wear = ((found['weather'] or {}).get('tomorrow' if found['part'] in ('evening', 'night') else 'today') or {}).get('wear')
        if wear:
            said.append(f'{wear}.')
    upcoming = [s for s in found['today'] if s['time'] not in ('All day', 'Still')]
    if upcoming and found['part'] != 'night':
        first = upcoming[0]
        said.append(f'{first["title"]} is on now.' if first['time'] == 'Now' else f'First up: {first["title"]} at {said_time(first)}.')
    elif found['tomorrow']:
        first = next((s for s in found['tomorrow'] if TIME.match(s['time'])), None)
        if first:
            said.append(f'Tomorrow starts with {first["title"]} at {said_time(first)}.')
    on = datetime.fromisoformat(found['at']).date()
    on = on if found['part'] in ('morning', 'day') else on + timedelta(days=1)
    school = [s for s in found['school'] if s.get('date') == on.isoformat() and not s['struck']]
    if school:
        said.append(f'At school {"today" if found["part"] in ("morning", "day") else "tomorrow"}: {_names([s["title"] for s in school])}.')
    if found.get('note'):
        said.append(found['note']['text'])
    house = found.get('house') or {}
    if any(r['tone'] for r in house.get('rows', ())) and house.get('says'):
        said.append(house['says'])
    count = len(found['needs']) + found['more']
    said.append('Nothing needs you.' if not count else 'One thing needs you.' if count == 1 else f'{count} things need you.')
    return ' '.join(' '.join(s.split()) for s in said)
