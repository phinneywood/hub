"""The house's caretaker, as Hub sees it.

The caretaker is a program of its own on this Mac (the home-ops repository's `caretaker/`). It watches Home
Assistant, the network, the devices, Hub and its helpers, fixes a fenced few things itself, and keeps a folder
(HUB_CARETAKER_DIR):

- `status.json`, rewritten every minute: each system's state and line, what needs the owner, the
  make-it-sturdier advice;
- `caretaker.db`, its journal (SQLite): incidents, the steps taken, the night's events, the passes it made.

Hub only reads these, the journal read-only. The one thing it writes is a request note in the folder's
`queue/` (Try again, Done, Ignore, Pause, Resume, a drill), after checking it here. The caretaker checks it
again, answers it in its journal and deletes it. Nothing here reaches the house or starts anything.
"""
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from . import config, home

STALE = timedelta(minutes=5)     # a caretaker that hasn't looked for longer isn't watching
HISTORY_DAYS = 7
DOS = ('fix', 'done', 'ignore', 'pause', 'resume', 'drill')
WORST = ('needs_you', 'fixing', 'watching', 'unknown', 'ok', 'off')
REQUEST_ID = re.compile(r'[A-Za-z0-9_-]{6,64}')
INCIDENT = re.compile(r'(?:care-)?(\d{1,9})')
NOTE_TONES = ('fixed', 'by_itself', 'info')


class Refused(Exception):
    """A request Hub won't pass on. The message says why, in words for the owner."""


def _utc(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec='seconds')


def _parse(text):
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _now():
    return datetime.now(timezone.utc)


def clock(moment):
    """'3:12 AM', in the house's time."""
    local = moment.astimezone(home.PACIFIC)
    return f'{local.hour % 12 or 12}:{local.minute:02d} {"AM" if local.hour < 12 else "PM"}'


def _when(moment, now):
    local, today = moment.astimezone(home.PACIFIC), now.astimezone(home.PACIFIC).date()
    if local.date() == today:
        return clock(moment)
    if local.date() == today - timedelta(days=1):
        return 'yesterday ' + clock(moment)
    return f'{local:%a} {local.day} {local:%b} ' + clock(moment)


def folder():
    return config.CARETAKER_DIR


def set_up():
    return (folder() / 'status.json').exists() or (folder() / 'caretaker.db').exists()


def status():
    """The caretaker's last status.json, or None when there is none to read."""
    try:
        data = json.loads((folder() / 'status.json').read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('schema_version') == 1 else None


def _db():
    path = folder() / 'caretaker.db'
    if not path.is_file():
        return None
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(sql, *args):
    conn = _db()
    if conn is None:
        return []
    with closing(conn):
        try:
            return conn.execute(sql, args).fetchall()
        except sqlite3.Error:
            return []


def alive(now=None):
    """(watching, words): whether the caretaker looked in the last five minutes, said plainly either way."""
    now = now or _now()
    found = status()
    if found is None:
        return False, ('The caretaker isn’t set up on this Mac yet.' if not set_up()
                       else 'The caretaker’s status can’t be read.')
    beat = _parse(found.get('heartbeat'))
    if beat is None:
        return False, 'The caretaker hasn’t looked yet.'
    if now - beat > STALE:
        return False, f'The caretaker hasn’t looked since {_when(beat, now)}.'
    return True, f'The caretaker looked at {clock(beat)}.'


# ---- what needs the owner, the morning note ----

def needs(now=None):
    """Open needs-you items that belong on Today now: not a drill, not health-screen-only (a battery), and not
    held for the morning. Read from the journal, which is the truth even when status.json is old."""
    now = now or _now()
    rows = _rows("select * from incidents where status = 'needs_you' and today = 1 and drill is null "
                 "and (today_from is null or today_from <= ?) order by id", _utc(now))
    return [{'id': r['id'], 'title': r['title'], 'words': r['needs'] or r['title'], 'system': r['system'],
             'ask': r['ask'], 'urgent': bool(r['urgent']), 'since': r['first_seen']} for r in rows]


def _window(now):
    """(start, end) of the night the morning note covers: 9 PM until now, until noon. None from noon to 9 PM."""
    local = now.astimezone(home.PACIFIC)
    if 12 <= local.hour < 21:
        return None
    day = local.date() if local.hour >= 21 else local.date() - timedelta(days=1)
    return datetime(day.year, day.month, day.day, 21, 0, tzinfo=home.PACIFIC), now


def _things(n):
    return f'{n} thing{"s" * (n != 1)}'


def headline(fixed, by_itself, needing):
    if fixed:
        tail = f'; {needing} need{"s" if needing == 1 else ""} you' if needing else ''
        return f'I fixed {_things(fixed)} overnight{tail}.'
    if needing:
        tail = f'; {_things(by_itself)} came back by {"itself" if by_itself == 1 else "themselves"}' if by_itself else ''
        return f'{_things(needing)} need{"s" if needing == 1 else ""} you{tail}.'
    if by_itself:
        return f'A quiet night: {_things(by_itself)} came back by {"itself" if by_itself == 1 else "themselves"}.'
    return 'A quiet night.'


def _night(start, end):
    return [{'tone': r['tone'], 'text': r['text']}
            for r in _rows('select tone, text from events where at >= ? and at <= ? and drill is null and tone in (?, ?, ?) '
                           'order by at, id', _utc(start), _utc(end), *NOTE_TONES)]


def note(now=None):
    """The night in a headline and a few lines, for the top of Today until noon (the morning brief shows it).

    {"at", "headline", "lines": [{"tone": "fixed|by_itself|info", "text"}], "needs": n}, or None from noon to
    9 PM, or when the caretaker wasn't watching. Never a needs-you line; `needs` counts what is on Today.
    The caretaker's own caretaker/note.py says the same from the same journal."""
    now = now or _now()
    span = _window(now)
    if span is None:
        return None
    start, _ = span
    if not _rows('select 1 from looks where at >= ? limit 1', _utc(start)):
        return None
    lines = _night(start, now)
    waiting = len(needs(now))
    fixed = sum(1 for line in lines if line['tone'] == 'fixed')
    by_itself = sum(1 for line in lines if line['tone'] == 'by_itself')
    return {'at': _utc(now), 'headline': headline(fixed, by_itself, waiting), 'lines': lines, 'needs': waiting}


def top(now=None):
    """What System and the top button need: is it watching, and how many things need the owner."""
    now = now or _now()
    watching, words = alive(now)
    return {'set_up': set_up(), 'alive': watching, 'looked': words, 'needs': len(needs(now)) if set_up() else 0}


# ---- the health screen ----

def _day(moment):
    return moment.astimezone(home.PACIFIC).date()


def _history(now):
    """For each of the last seven days: was the caretaker watching, and what happened, per system and overall."""
    first = (now.astimezone(home.PACIFIC) - timedelta(days=HISTORY_DAYS - 1)).date()
    since = datetime(first.year, first.month, first.day, tzinfo=home.PACIFIC)
    days = [first + timedelta(days=i) for i in range(HISTORY_DAYS)]
    watched = {_day(_parse(r['at'])) for r in _rows('select at from looks where at >= ?', _utc(since))}
    incidents = _rows('select * from incidents where drill is null and (closed_at is null or closed_at >= ?) '
                      'and first_seen <= ? order by first_seen', _utc(since), _utc(now))
    events = _rows('select at, tone from events where drill is null and at >= ?', _utc(since))
    week = []
    for day in days:
        tones = [e['tone'] for e in events if _day(_parse(e['at'])) == day]
        week.append({'day': day.isoformat(), 'watched': day in watched, 'fixed': tones.count('fixed'),
                     'by_itself': tones.count('by_itself'), 'needs_you': tones.count('needs_you')})
    by_system = {}
    for inc in incidents:
        start = _parse(inc['first_seen'])
        end = _parse(inc['closed_at']) or now
        by_system.setdefault(inc['system'], []).append({
            'id': inc['id'], 'title': inc['title'], 'from': _when(start, now),
            'to': (clock(end) if _day(end) == _day(start) else _when(end, now)) if inc['closed_at'] else None,
            'outcome': inc['outcome'] or inc['status'], 'start': start, 'end': end})
    return days, watched, week, by_system


def _dots(days, watched, mine):
    """One dot a day for a system: what was the worst thing that day."""
    out = []
    for day in days:
        worst = 'ok' if day in watched else 'unknown'
        for inc in mine:
            if _day(inc['start']) <= day <= _day(inc['end']):
                state = 'needs_you' if inc['outcome'] in ('needs_you', 'done', 'ignored') else 'fixing'
                worst = min(worst, state, key=WORST.index)
        out.append({'day': day.isoformat(), 'state': worst})
    return out


def _drill(found):
    """The drill playing now (or last), as its incidents and steps say, for the screen to follow."""
    playing = (found or {}).get('drill')
    if not playing:
        return None
    rows = _rows('select * from incidents where drill = ? order by id desc limit 3', playing.get('scenario', ''))
    steps = []
    for inc in reversed(rows):
        steps += [{'at': clock(_parse(s['at'])), 'status': s['status'], 'words': s['words']}
                  for s in _rows('select * from steps where incident = ? order by id', inc['id'])]
    last = rows[0] if rows else None
    return {'scenario': playing.get('scenario'), 'words': playing.get('words'), 'minute': playing.get('minute'),
            'of': playing.get('of'), 'done': bool(playing.get('done')),
            'title': last['title'] if last else None, 'outcome': (last['outcome'] or last['status']) if last else None,
            'steps': steps}


def picture(now=None):
    """The House health screen as data. Reads the caretaker's folder; calls nothing."""
    now = now or _now()
    if not set_up():
        return {'set_up': False, 'alive': False, 'headline': 'The caretaker isn’t set up on this Mac yet.',
                'sub': 'When it runs, this screen shows what it watches, what it fixed and what needs you.'}
    found = status() or {}
    watching, looked = alive(now)
    waiting = needs(now)
    fixable = {n.get('id'): n for n in found.get('needs', []) if isinstance(n, dict)}
    open_needs = []
    for r in _rows("select * from incidents where status = 'needs_you' and drill is null order by id"):
        extra = fixable.get(r['id'], {})
        held = bool(r['today_from'] and _parse(r['today_from']) > now)
        open_needs.append({'id': r['id'], 'title': r['title'], 'words': r['needs'] or r['title'], 'cause': r['cause'],
                           'ask': r['ask'], 'fixable': bool(extra.get('fixable')), 'today': bool(r['today']),
                           'held': held, 'urgent': bool(r['urgent']), 'since': _when(_parse(r['first_seen']), now)})
    days, watched, week, by_system = _history(now)
    systems = []
    for system in found.get('systems', []):
        mine = by_system.get(system.get('id'), [])
        systems.append({'id': system.get('id'), 'name': system.get('name'), 'state': system.get('state'),
                        'line': system.get('line'), 'days': _dots(days, watched, mine),
                        'history': [{k: v for k, v in inc.items() if k not in ('start', 'end')} for inc in reversed(mine)]})
    span = _window(now)
    start = span[0] if span else datetime.combine(now.astimezone(home.PACIFIC).date() - timedelta(days=1),
                                                  datetime.min.time(), home.PACIFIC).replace(hour=21)
    end = now if span else start + timedelta(hours=15)
    night = _night(start, end)
    fixed = sum(1 for line in night if line['tone'] == 'fixed')
    by_itself = sum(1 for line in night if line['tone'] == 'by_itself')
    held = [n for n in open_needs if n['held']]
    if not watching:
        says = looked
    elif waiting:
        says = f'{_things(len(waiting)).capitalize().replace("1 thing", "One thing")} need{"s" if len(waiting) == 1 else ""} you.'
    elif held:
        says = 'All quiet for now; ' + ('one thing waits' if len(held) == 1 else f'{len(held)} things wait') + ' for the morning.'
    else:
        says = 'All quiet.'
    mode = found.get('mode')
    paused = bool(found.get('paused'))
    until = found.get('paused_until')
    sub = [looked if watching else 'It may have stopped; the Mac’s screen or Termius will say why.']
    if paused:
        sub.append('Paused until you resume.' if until == 'forever' or not _parse(until) else f'Paused until {_when(_parse(until), now)}.')
    elif mode == 'watch':
        sub.append('Watching only: it says what it would fix and leaves it to you.')
    elif mode == 'fix':
        sub.append('It fixes what it safely can and tells you the rest.')
    answers = [{'id': r['id'], 'do': r['do'], 'status': r['status'], 'words': r['words']}
               for r in _rows("select * from requests where id like 'hub-%' order by received_at desc limit 10")]
    return {
        'set_up': True, 'alive': watching, 'headline': says, 'sub': ' '.join(sub), 'mode': mode, 'paused': paused,
        'needs': open_needs, 'today': len(waiting),
        'night': {'from': clock(start), 'headline': headline(fixed, by_itself, len(waiting)) if night or span else None,
                  'lines': night},
        'systems': systems, 'week': week,
        'sturdier': [s for s in found.get('sturdier', []) if isinstance(s, dict)],
        'batteries': found.get('batteries', []), 'ignoring': found.get('ignoring', []),
        'drills': found.get('drills', []), 'drill': _drill(found), 'answers': answers,
    }


# ---- asking the caretaker ----

def ask(do, target, login, now=None):
    """Check a request from the owner's tap and leave it in the caretaker's queue. Returns words for the toast.
    The caretaker checks it again before doing anything, and answers it in its journal."""
    now = now or _now()
    if do not in DOS:
        raise Refused('The caretaker doesn’t do that.')
    queue = folder() / 'queue'
    if not queue.is_dir():
        raise Refused('The caretaker isn’t set up on this Mac yet.')
    target = str(target or '').strip()[:200]
    said = _check(do, target)
    note = {'id': 'hub-' + secrets.token_hex(6), 'do': do, 'target': target, 'by': str(login or '')[:100], 'at': _utc(now)}
    scratch = queue / f'.{note["id"]}.tmp'
    fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(note, handle)
    os.replace(scratch, queue / f'{note["id"]}.json')
    return said, note['id']


def _check(do, target):
    if do == 'resume':
        return 'Asked the caretaker to start fixing again.'
    if do == 'pause':
        if target not in ('', 'forever') and not (target.isdigit() and 1 <= int(target) <= 7 * 24 * 60):
            raise Refused('Pause for a number of minutes, or until you resume.')
        return 'Asked the caretaker to pause.'
    if do == 'drill':
        names = [d.get('name') for d in (status() or {}).get('drills', []) if isinstance(d, dict)]
        if target not in names:
            raise Refused('There is no drill called that.')
        return 'Starting the drill. It plays on a made-up house; nothing real is touched.'
    if target.startswith('sturdier:'):
        if do not in ('done', 'ignore'):
            raise Refused('A suggestion can be marked done or set aside.')
        key = target.split(':', 1)[1]
        if not _rows('select 1 from sturdier where key = ? and gone_at is null', key):
            raise Refused('That suggestion isn’t open.')
        return 'Noted; the caretaker will check whether it helped.' if do == 'done' else 'It won’t suggest that again.'
    found = INCIDENT.fullmatch(target)
    rows = _rows('select * from incidents where id = ?', int(found.group(1))) if found else []
    if not rows or rows[0]['status'] == 'closed' or rows[0]['drill']:
        raise Refused('That isn’t open any more.')
    inc = rows[0]
    if do == 'fix':
        fixable = {n.get('id'): n.get('fixable') for n in (status() or {}).get('needs', []) if isinstance(n, dict)}
        if inc['ask'] == 'ha.restart':
            return 'Asked the caretaker to restart Home Assistant.'
        if not fixable.get(inc['id']):
            raise Refused('The caretaker can’t fix that one; it needs you.')
        return 'Asked the caretaker to try again.'
    if do == 'done':
        return 'Marked as sorted.'
    return 'The caretaker will leave it alone from now on.'
