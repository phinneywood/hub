"""The house's caretaker, as Hub sees it.

The caretaker is a program of its own on this Mac (the home-ops repository's `caretaker/`). It watches Home
Assistant, the network, the devices, Hub and its helpers, fixes a fenced few things itself, and keeps a folder
(HUB_CARETAKER_DIR):

- `status.json`, rewritten every minute: each system's state and line, what needs the owner, the
  make-it-sturdier advice;
- `caretaker.db`, its journal (SQLite): incidents, the steps taken, the night's events, the passes it made.

Hub only reads these, the journal read-only. The one thing it writes is a request note in the folder's
`queue/` (Try again, Restart Home Assistant, Done, Ignore, Pause, Resume, a drill), after checking it here, and
only while the caretaker is watching. The caretaker checks it again, answers it in its journal and deletes it.
Nothing here reaches the house or starts anything.

Ask is the way in (hub/app/ask): `brief()` rides along with every request, `lines()` is what it reads to answer
a question about the house's health, and `plan()` turns what the owner asked for into one of these notes, or a
card that waits for their tap.
"""
import json
import logging
import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from . import config, home

log = logging.getLogger('hub.caretaker')
STALE = timedelta(minutes=5)     # a caretaker that hasn't looked for longer isn't watching
HISTORY_DAYS = 7
DOS = ('fix', 'restart', 'done', 'ignore', 'pause', 'resume', 'drill')     # restart: Home Assistant, only at a tap
WORST = ('needs_you', 'fixing', 'watching', 'unknown', 'ok', 'off')
REQUEST_ID = re.compile(r'[A-Za-z0-9_-]{6,64}')
INCIDENT = re.compile(r'(?:care-)?(\d{1,9})')
NOTE_TONES = ('fixed', 'by_itself', 'info')


class Refused(Exception):
    """A request Hub won't pass on. The message says why, in words for the owner."""


class Unclear(Exception):
    """Ask's model named nothing open, or more than one thing. The message says what is open, for it to try again."""


class Unreadable(OSError):
    """The caretaker's journal can't be read: damaged, busy too long, or written by a caretaker this Hub doesn't
    follow (a table or a column it expects is missing)."""


UNREADABLE = 'The caretaker’s journal can’t be read, so House health can’t say what needs you.'
# What a journal Hub can't follow raises on the way out: a page shows UNREADABLE instead of failing.
JOURNAL_ERRORS = (Unreadable, IndexError, KeyError, TypeError, ValueError)


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
    """'3:12 AM', in the house's time; '' for a time the journal didn't give."""
    if moment is None:
        return ''
    local = moment.astimezone(home.PACIFIC)
    return f'{local.hour % 12 or 12}:{local.minute:02d} {"AM" if local.hour < 12 else "PM"}'


def _when(moment, now):
    if moment is None:
        return ''
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
    """The journal, opened read-only: Hub never writes a row in it."""
    conn = sqlite3.connect((folder() / 'caretaker.db').as_uri() + '?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(sql, *args):
    """Rows from the journal, opened read-only; [] before the caretaker has written one. Raises Unreadable rather than
    answer [] for a journal it can't read, so a damaged one never reads as "all quiet"."""
    path = folder() / 'caretaker.db'
    if not path.is_file():
        return []
    try:
        with closing(_db()) as conn:
            return conn.execute(sql, args).fetchall()
    except sqlite3.Error as error:
        raise Unreadable(UNREADABLE) from error


def _unreadable(error):
    log.warning('caretaker: the journal could not be read (%s)', type(error).__name__)


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


NOTE_HOURS = (4, 12)      # the note shows from 4 AM until noon, about the night from 9 PM before


def _window(now):
    """(9 PM last night, now) from 4 AM until noon; None otherwise, so a brief never says "A quiet night" at 10 PM."""
    local = now.astimezone(home.PACIFIC)
    if not NOTE_HOURS[0] <= local.hour < NOTE_HOURS[1]:
        return None
    day = local.date() - timedelta(days=1)
    return datetime(day.year, day.month, day.day, 21, 0, tzinfo=home.PACIFIC), now


NUMBERS = ('no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine')


def number(n, capital=False):
    """One to nine as words, as Today says them; 10 and up as figures."""
    text = NUMBERS[n] if 0 <= n <= 9 else str(n)
    return text[:1].upper() + text[1:] if capital else text


def counted(n, word, capital=False):
    return f'{number(n, capital)} {word}{"s" * (n != 1)}'


def _back(n, capital=False):
    return f'{number(n, capital)} came back by {"itself" if n == 1 else "themselves"}.'


def headline(fixed, by_itself, needing):
    """No subject: "Fixed one thing overnight. One needs you." The caretaker's own note says the same."""
    if fixed:
        parts = [f'Fixed {counted(fixed, "thing")} overnight.']
        if needing:
            parts.append(f'{number(needing, True)} need{"s" if needing == 1 else ""} you.')
        elif by_itself:
            parts.append(_back(by_itself, True))
        return ' '.join(parts)
    if needing:
        parts = [f'{counted(needing, "thing", True)} need{"s" if needing == 1 else ""} you.']
        if by_itself:
            parts.append(_back(by_itself, True))
        return ' '.join(parts)
    if by_itself:
        return f'A quiet night. {counted(by_itself, "thing", True)} came back by {"itself" if by_itself == 1 else "themselves"}.'
    return 'A quiet night.'


def _night(start, end):
    return [{'tone': r['tone'], 'text': r['text']}
            for r in _rows('select tone, text from events where at >= ? and at <= ? and drill is null and tone in (?, ?, ?) '
                           'order by at, id', _utc(start), _utc(end), *NOTE_TONES)]


def note(now=None):
    """The night from 9 PM in a headline and a few lines, for the top of Today from 4 AM until noon (the morning
    brief shows it).

    {"at", "headline", "lines": [{"tone": "fixed|by_itself|info", "text"}], "needs": n}, or None outside those
    hours or when the caretaker wasn't watching. Never a needs-you line; `needs` counts what is on Today.
    The caretaker's own caretaker/note.py says the same from the same journal."""
    now = now or _now()
    span = _window(now)
    if span is None:
        return None
    start, _ = span
    try:
        if not _rows('select 1 from looks where at >= ? limit 1', _utc(start)):
            return None
        lines = _night(start, now)
        waiting = len(needs(now))
    except JOURNAL_ERRORS as error:      # the brief goes on without the night
        _unreadable(error)
        return None
    fixed = sum(1 for line in lines if line['tone'] == 'fixed')
    by_itself = sum(1 for line in lines if line['tone'] == 'by_itself')
    return {'at': _utc(now), 'headline': headline(fixed, by_itself, waiting), 'lines': lines, 'needs': waiting}


def top(now=None):
    """What System and the top button need: is it watching, and how many things need the owner. A journal it can't
    read turns the top button amber with UNREADABLE, like a caretaker that stopped looking."""
    now = now or _now()
    watching, words = alive(now)
    if not set_up():
        return {'set_up': False, 'alive': watching, 'looked': words, 'needs': 0}
    try:
        waiting = len(needs(now))
        listed = len(_now_needs(_open_needs(now, status() or {})))
    except JOURNAL_ERRORS as error:
        _unreadable(error)
        return {'set_up': True, 'alive': False, 'looked': UNREADABLE, 'needs': 0, 'look': 0}
    return {'set_up': True, 'alive': watching, 'looked': words, 'needs': waiting, 'look': listed}


# ---- the health screen ----

def _day(moment):
    return moment.astimezone(home.PACIFIC).date() if moment else None


def _history(now):
    """For each of the last seven days: was the caretaker watching, and what happened, per system and overall."""
    first = (now.astimezone(home.PACIFIC) - timedelta(days=HISTORY_DAYS - 1)).date()
    since = datetime(first.year, first.month, first.day, tzinfo=home.PACIFIC)
    days = [first + timedelta(days=i) for i in range(HISTORY_DAYS)]
    watched = {_day(_parse(r['at'])) for r in _rows('select at from looks where at >= ?', _utc(since))}
    incidents = _rows('select * from incidents where drill is null and (closed_at is null or closed_at >= ?) '
                      'and first_seen <= ? order by first_seen', _utc(since), _utc(now))
    events = _rows('select at, tone, text from events where drill is null and at >= ? order by at, id', _utc(since))
    week = []
    for day in days:
        mine = [e for e in events if _day(_parse(e['at'])) == day]
        tones = [e['tone'] for e in mine]
        week.append({'day': day.isoformat(), 'watched': day in watched, 'fixed': tones.count('fixed'),
                     'by_itself': tones.count('by_itself'), 'needs_you': tones.count('needs_you'),
                     'lines': [{'at': clock(_parse(e['at'])), 'tone': e['tone'], 'text': e['text']} for e in mine]})
    by_system = {}
    for inc in incidents:
        start = _parse(inc['first_seen'])
        if start is None:           # a journal row without a time can't be placed on a day
            continue
        end = _parse(inc['closed_at']) or now
        by_system.setdefault(inc['system'], []).append({
            'id': inc['id'], 'title': inc['title'], 'from': _when(start, now),
            'to': (clock(end) if _day(end) == _day(start) else _when(end, now)) if inc['closed_at'] else None,
            'outcome': inc['outcome'] or inc['status'], 'start': start, 'end': end})
    return days, watched, week, by_system


DAY_ORDER = ('needs_you', 'fixed', 'by_itself', 'ok', 'unknown')


def _dots(days, watched, mine):
    """One dot a day for a system: the most it took that day (it needed you, was fixed, or came back by itself)."""
    out = []
    for day in days:
        worst = 'ok' if day in watched else 'unknown'
        for inc in mine:
            if _day(inc['start']) <= day <= _day(inc['end']):
                outcome = inc['outcome']
                state = 'needs_you' if outcome in ('needs_you', 'done', 'ignored') else 'fixed' if outcome == 'fixed' else 'by_itself'
                worst = min(worst, state, key=DAY_ORDER.index)
        out.append({'day': day.isoformat(), 'state': worst})
    return out


def week_words(week):
    """'This week: two fixed, three came back by themselves, one needed you.'"""
    fixed, by_itself, needed = (sum(d[k] for d in week) for k in ('fixed', 'by_itself', 'needs_you'))
    said = [f'{number(fixed)} fixed' if fixed else '',
            f'{number(by_itself)} came back by {"itself" if by_itself == 1 else "themselves"}' if by_itself else '',
            f'{number(needed)} needed you' if needed else '']
    said = [part for part in said if part]
    return 'This week: ' + ', '.join(said) + '.' if said else 'A quiet week.'


DRILL_SHOWN = timedelta(minutes=15)     # a drill that has finished stays in view this long


def _drill(found, now):
    """The drill playing now, or one that finished in the last quarter of an hour, as its incidents and steps say,
    for the screen and Ask's card to follow. None otherwise."""
    playing = (found or {}).get('drill')
    if not isinstance(playing, dict) or not playing:
        return None
    rows = _rows('select * from incidents where drill = ? order by id desc limit 3', str(playing.get('scenario', '')))
    steps, stamps = [], []
    for inc in reversed(rows):
        stamps += [_parse(inc['closed_at']), _parse(inc['first_seen'])]
        for step in _rows('select * from steps where incident = ? order by id', inc['id']):
            stamps.append(_parse(step['at']))
            steps.append({'at': clock(_parse(step['at'])) if _parse(step['at']) else '', 'status': step['status'], 'words': step['words']})
    done = bool(playing.get('done'))
    latest = max((stamp for stamp in stamps if stamp), default=None)
    if done and (latest is None or now - latest > DRILL_SHOWN):
        return None
    last = rows[0] if rows else None
    return {'scenario': playing.get('scenario'), 'words': playing.get('words'), 'minute': playing.get('minute'),
            'of': playing.get('of'), 'done': done,
            'title': last['title'] if last else None, 'outcome': (last['outcome'] or last['status']) if last else None,
            'steps': steps}


def _open_needs(now, found):
    """Every open needs-you item, not a drill: those on Today, those held for the morning, and those for this screen
    only (a battery)."""
    fixable = {n.get('id'): n for n in found.get('needs', []) if isinstance(n, dict)}
    out = []
    for r in _rows("select * from incidents where status = 'needs_you' and drill is null order by id"):
        extra = fixable.get(r['id'], {})
        held = bool(r['today_from']) and (_parse(r['today_from']) or now) > now
        out.append({'id': r['id'], 'title': r['title'], 'words': r['needs'] or r['title'], 'cause': r['cause'],
                    'ask': r['ask'], 'fixable': bool(extra.get('fixable')), 'today': bool(r['today']),
                    'held': held, 'urgent': bool(r['urgent']), 'since': _when(_parse(r['first_seen']), now)})
    return out


def _now_needs(open_needs):
    """What House health lists as needing the owner now: every open item but those held for the morning. Its headline,
    System's and Ask's count these, so a count always matches the list under it."""
    return [n for n in open_needs if not n['held']]


def _says(watching, looked, waiting, held):
    """The screen's headline: is it watching, and what needs the owner now."""
    if not watching:
        return looked
    if waiting:
        return f'{counted(len(waiting), "thing", True)} need{"s" if len(waiting) == 1 else ""} you.'
    if held:
        return f'All quiet for now. {counted(len(held), "thing", True)} wait{"s" if len(held) == 1 else ""} for the morning.'
    return 'All quiet.'


def _until(found, now):
    """'until 3:00 PM' or 'until you resume' while it is paused, else ''."""
    if not found.get('paused'):
        return ''
    until = found.get('paused_until')
    return 'until you resume' if until == 'forever' or not _parse(until) else f'until {_when(_parse(until), now)}'


def _night_of(now, waiting):
    """The night from 9 PM: tonight's after 9 PM, else last night's (to noon once it is past noon)."""
    local = now.astimezone(home.PACIFIC)
    tonight = local.hour >= 21
    day = local.date() if tonight else local.date() - timedelta(days=1)
    start = datetime(day.year, day.month, day.day, 21, 0, tzinfo=home.PACIFIC)
    end = now if tonight or local.hour < 12 else start + timedelta(hours=15)
    lines = _night(start, end)
    fixed = sum(1 for line in lines if line['tone'] == 'fixed')
    by_itself = sum(1 for line in lines if line['tone'] == 'by_itself')
    return {'title': 'Tonight, from 9:00 PM' if tonight else 'Last night', 'headline': headline(fixed, by_itself, waiting),
            'lines': lines}


NOT_SET_UP = {'set_up': False, 'alive': False, 'headline': 'The caretaker isn’t set up on this Mac yet.',
              'sub': 'When it runs, this screen shows what it watches, what it fixed and what needs you.'}
CANT_READ = {'set_up': True, 'alive': False, 'unreadable': True, 'headline': UNREADABLE, 'paused': False, 'needs': [],
             'sub': 'The caretaker may be part way through an update, or its journal is damaged; the Mac’s screen or '
                    'Termius will say why. Nothing it saw is lost.'}


def glance(now=None, found=None):
    """House health as it stands this minute, without the week behind it: the headline, a pause, what needs the
    owner, a drill that is playing, and the caretaker's answers to the last requests. What Ask's cards follow."""
    now = now or _now()
    if not set_up():
        return dict(NOT_SET_UP)
    try:
        return _glance(now, (status() or {}) if found is None else found)
    except JOURNAL_ERRORS as error:
        _unreadable(error)
        return dict(CANT_READ)


def _glance(now, found):
    watching, looked = alive(now)
    waiting = needs(now)
    open_needs = _open_needs(now, found)
    mode = found.get('mode')
    sub = [looked if watching else 'It may have stopped; the Mac’s screen or Termius will say why.']
    if found.get('paused'):
        sub.append(f'Paused {_until(found, now)}: it keeps watching, but fixes nothing.')
    elif mode == 'watch':
        sub.append('Watching only: it says what it would fix and leaves it to you.')
    elif mode == 'fix':
        sub.append('It fixes what it safely can and tells you the rest.')
    answers = [{'id': r['id'], 'do': r['do'], 'status': r['status'], 'words': r['words']}
               for r in _rows("select * from requests where id like 'hub-%' order by received_at desc limit 10")]
    return {'set_up': True, 'alive': watching, 'headline': _says(watching, looked, _now_needs(open_needs), [n for n in open_needs if n['held']]),
            'sub': ' '.join(sub), 'mode': mode, 'paused': bool(found.get('paused')), 'needs': open_needs, 'today': len(waiting),
            'drill': _drill(found, now), 'answers': answers}


def picture(now=None):
    """The House health screen as data. Reads the caretaker's folder; calls nothing."""
    now = now or _now()
    if not set_up():
        return dict(NOT_SET_UP)
    try:
        return _picture(now, status() or {})
    except JOURNAL_ERRORS as error:
        _unreadable(error)
        return dict(CANT_READ)


def _picture(now, found):
    top_part = _glance(now, found)
    days, watched, week, by_system = _history(now)
    systems = []
    for system in found.get('systems', []):
        mine = by_system.get(system.get('id'), [])
        systems.append({'id': system.get('id'), 'name': system.get('name'), 'state': system.get('state'),
                        'line': system.get('line'), 'days': _dots(days, watched, mine),
                        'history': [{k: v for k, v in inc.items() if k not in ('start', 'end')} for inc in reversed(mine)]})
    return {
        **top_part, 'night': _night_of(now, top_part['today']),
        'systems': systems, 'week': week, 'week_says': week_words(week),
        'sturdier': [s for s in found.get('sturdier', []) if isinstance(s, dict)],
        'batteries': found.get('batteries', []), 'ignoring': found.get('ignoring', []),
        'drills': found.get('drills', []),
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
    _watching(now)
    target = str(target or '').strip()[:200]
    try:
        said = _check(do, target)
    except JOURNAL_ERRORS as error:
        _unreadable(error)
        raise Refused(UNREADABLE + ' Nothing was asked.') from error
    note = {'id': 'hub-' + secrets.token_hex(6), 'do': do, 'target': target, 'by': str(login or '')[:100], 'at': _utc(now)}
    _leave(note)
    return said, note['id']


def _watching(now):
    """A request only while the caretaker is looking: one left for a stopped caretaker would be acted on whenever
    it starts again, long after it was asked for."""
    watching, looked = alive(now)
    if not watching:
        raise Refused(f'{looked} It takes no requests until it is looking again.')


def _leave(note):
    """The one write Hub makes in the caretaker's folder: a checked request note, put in its queue whole. The
    golden dry run (hub/golden/dryrun.py) stands here and writes the note down instead."""
    queue = folder() / 'queue'
    scratch = queue / f'.{note["id"]}.tmp'
    fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(note, handle)
    os.replace(scratch, queue / f'{note["id"]}.json')


def _check(do, target):
    if do == 'resume':
        return 'Asked the caretaker to start fixing again.'
    if do == 'pause':
        if target not in ('', 'forever') and not (target.isascii() and target.isdigit() and 1 <= int(target) <= PAUSE_MOST):
            raise Refused('Pause for a number of minutes, or until you resume.')
        return 'Asked the caretaker to pause.'
    if do == 'drill':
        found = status() or {}
        playing = found.get('drill')
        if isinstance(playing, dict) and playing and not playing.get('done'):
            raise Refused('A drill is playing already; House health shows it.')
        names = [d.get('name') for d in found.get('drills', []) if isinstance(d, dict)]
        if target not in names:
            raise Refused('There is no drill called that.')
        return 'Starting the drill. It plays on a made-up house; nothing real is touched.'
    if target.startswith('sturdier:'):
        if do not in ('done', 'ignore'):
            raise Refused('A suggestion can be marked done or set aside.')
        key = target.split(':', 1)[1]
        if not _rows('select 1 from sturdier where key = ? and gone_at is null', key):
            raise Refused('That suggestion isn’t open.')
        return ('Noted; the caretaker will check whether it helped.' if do == 'done'
                else 'Asked the caretaker not to suggest that again.')
    found = INCIDENT.fullmatch(target)
    rows = _rows('select * from incidents where id = ?', int(found.group(1))) if found else []
    if not rows or rows[0]['status'] == 'closed' or rows[0]['drill']:
        raise Refused('That isn’t open any more.')
    inc = rows[0]
    if do == 'restart':             # only for a problem that asks for it, and only from the owner's tap
        if inc['ask'] != 'ha.restart':
            raise Refused('That one doesn’t call for restarting Home Assistant.')
        return 'Asked the caretaker to restart Home Assistant.'
    if do == 'fix':
        fixable = {n.get('id'): n.get('fixable') for n in (status() or {}).get('needs', []) if isinstance(n, dict)}
        if inc['ask'] == 'ha.restart':
            raise Refused('Restarting Home Assistant waits for your tap on Restart.')
        if not fixable.get(inc['id']):
            raise Refused('The caretaker can’t fix that one; it needs you.')
        return 'Asked the caretaker to try again.'
    if do == 'done':
        return 'Asked the caretaker to mark it as sorted.'
    return 'Asked the caretaker to leave it alone from now on.'


# ---- Ask: what goes with every request, what it reads, and what it may ask for ----

LINK = re.compile(r'\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+', re.I)
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
IP = re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b'                    # IPv4, with a port or not
                r'|\b(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{1,4}\b'                   # IPv6 written out (and a hardware address)
                r'|\b(?:[0-9a-f]{1,4}:){1,6}:(?:[0-9a-f]{1,4}\b)?|(?<![\w:])::[0-9a-f]{1,4}\b', re.I)     # IPv6 with ::
# A question plainly about the house's health: it gets House health with it, and its answer ends with Open House health.
ABOUT = re.compile(
    r"\b(house health|caretaker|how(?:’|')?s the house|how is the house|house doing|how(?:’|')?s everything"
    r"|is (?:everything|the house|all) (?:ok|okay|alright|all right|fine|working)|what (?:broke|went wrong|got fixed|was fixed)"
    r"|anything (?:broken|wrong|down)|(?:keeps?|kept) (?:dropping|failing|going (?:off|out)|falling off)"
    r"|sturdier|hue channel|zigbee channel)\b", re.I)
ASKS = ('pause', 'resume', 'try_again', 'done', 'ignore', 'restart_home_assistant', 'drill', 'sturdier_done', 'sturdier_ignore')
PAUSE_MOST = 7 * 24 * 60        # minutes
FILLER = {'the', 'an', 'one', 'that', 'this', 'it', 'its', 'thing', 'problem', 'about', 'of', 'for', 'on', 'my', 'our', 'with',
          'drill', 'suggestion', 'please'}


def _plain(text):
    """Caretaker text as Ask's model may see it: no links, no email addresses, no IP addresses."""
    return IP.sub('[address]', EMAIL.sub('[address]', LINK.sub('[link]', str(text or ''))))


def _end(text):
    text = str(text or '').strip()
    return text if not text or text[-1] in '.!?' else text + '.'


def brief(now=None):
    """One or two plain lines that go with every request to Ask's model while the caretaker is set up: House
    health's headline, a pause, and up to three things that need the owner. [] when it isn't set up."""
    now = now or _now()
    if not set_up():
        return []
    try:
        return _brief(now, status() or {})
    except JOURNAL_ERRORS as error:
        _unreadable(error)
        return ['House health: ' + UNREADABLE]


def _brief(now, found):
    watching, looked = alive(now)
    open_needs = _open_needs(now, found)
    first = 'House health: ' + _says(watching, looked, _now_needs(open_needs), [n for n in open_needs if n['held']])
    until = _until(found, now)
    if until:
        first += f' The caretaker is paused {until}: it keeps watching but fixes nothing.'
    out = [first]
    if open_needs:
        shown = sorted(open_needs, key=lambda n: (n['held'] or not n['today'], n['id']))
        more = len(shown) - 3
        out.append('Needs the owner: ' + '; '.join(
            n['title'] + (' (restarting Home Assistant may clear it)' if n['ask'] == 'ha.restart' else '') for n in shown[:3])
            + (f'; and {number(more)} more' if more > 0 else '') + '.')
    return [_plain(line) for line in out]


def lines(now=None):
    """House health as plain lines for Ask's model: what needs the owner, the night, the systems with trouble, the
    advice to make the house sturdier with its evidence, a pause or watching only, and when it last looked."""
    now = now or _now()
    if not set_up():
        return ['The caretaker isn’t set up on this Mac yet, so there is no House health to read.']
    try:
        return _lines(now, status() or {})
    except JOURNAL_ERRORS as error:
        _unreadable(error)
        return [UNREADABLE]


def _lines(now, found):
    watching, looked = alive(now)
    waiting = needs(now)
    open_needs = _open_needs(now, found)
    out = ['House health: ' + _says(watching, looked, _now_needs(open_needs), [n for n in open_needs if n['held']]),
           looked if watching else 'The caretaker may have stopped; the Mac’s screen or Termius will say why.']
    until, mode = _until(found, now), found.get('mode')
    if until:
        out.append(f'The caretaker is paused {until}: it keeps watching and telling, but fixes nothing.')
    elif mode == 'watch':
        out.append('The caretaker is only watching: it says what it would fix and leaves it to the owner.')
    elif mode == 'fix':
        out.append('The caretaker fixes what it safely can and tells the owner the rest.')
    for n in open_needs:
        out.append(f'Needs the owner, since {n["since"]}: {n["title"]}. {_end(n["words"])}'
                   + (' The caretaker can restart Home Assistant for it at the owner’s tap.' if n['ask'] == 'ha.restart' else '')
                   + (' It goes on Today in the morning.' if n['held'] else ''))
    night = _night_of(now, len(waiting))
    out.append(f'{night["title"]}: {night["headline"]}')
    out += [f'- {line["text"]}' for line in night['lines']]
    systems = [s for s in found.get('systems', []) if isinstance(s, dict)]
    trouble = [s for s in systems if s.get('state') not in ('ok', 'off')]
    out += [f'Trouble now, {s.get("name")}: {_end(s.get("line"))}' for s in trouble]
    if systems and not trouble:
        out.append(f'All {number(len(systems))} systems it watches are fine now.')
    for item in (s for s in found.get('sturdier', []) if isinstance(s, dict)):
        state = item.get('verdict') or ('The owner made this change; the caretaker is checking whether it helped.' if item.get('done_at') else '')
        out.append(f'To make the house sturdier: {item.get("title")}. {_end(item.get("words"))} {_end(state)}'.strip())
    batteries = [b for b in found.get('batteries', []) if isinstance(b, dict)]
    if batteries:
        out.append('Low batteries: ' + '; '.join(f'{b.get("name")} {b.get("level")}%' + ('' if b.get('reachable', True) else ', not responding')
                                                 for b in batteries) + '.')
    out.append(week_words(_history(now)[2]))
    playing = found.get('drill') or {}
    if playing and not playing.get('done'):
        out.append(f'A drill is playing now on a made-up house: {_end(playing.get("words"))}')
    drills = [d for d in found.get('drills', []) if isinstance(d, dict) and d.get('name')]
    if drills:
        out.append('Drills it can play on a made-up house: ' + ' '.join(f'“{d["name"]}”: {_end(d.get("words"))}' for d in drills))
    ignoring = [i.get('name') for i in found.get('ignoring', []) if isinstance(i, dict)]
    if ignoring:
        out.append('Not watched, as the owner asked: ' + ', '.join(ignoring) + '.')
    return [_plain(line) for line in out]


def asked_about(text):
    """Whether the owner's words are plainly about the house's health (while the caretaker is set up), so Ask gives
    its model House health with the request and ends the answer with Open House health."""
    return bool(ABOUT.search(str(text or ''))) and set_up()


def span(minutes):
    """30 → 'half an hour', 60 → 'an hour', 90 → 'an hour and a half', 120 → 'two hours', 1440 → 'a day'."""
    if minutes == 30:
        return 'half an hour'
    if minutes == 90:
        return 'an hour and a half'
    if minutes % 1440 == 0:
        return 'a day' if minutes == 1440 else f'{number(minutes // 1440)} days'
    if minutes % 60 == 0:
        return 'an hour' if minutes == 60 else f'{number(minutes // 60)} hours'
    return 'a minute' if minutes == 1 else f'{number(minutes)} minutes'


def _stem(word):
    return word[:-1] if len(word) > 3 and word.endswith('s') else word


def _words(text):
    return {_stem(w) for w in re.findall(r'[a-z0-9]+', str(text or '').lower()) if len(w) > 1} - FILLER


def _either(names):
    names = [f'“{n}”' for n in names]
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' or ' + names[-1]


def _choose(items, about, words, name, kind):
    """The one item `about` names, by its words: all of them, else the most of them. Raises Unclear."""
    if not items:
        raise Unclear(f'House health has no {kind} open.' if kind != 'drill' else 'The caretaker has no drills to play.')
    asked = _words(about)
    if not asked:
        if len(items) == 1:
            return items[0]
        raise Unclear(f'Which {kind}? {_either([name(i) for i in items])}.')
    whole = [i for i in items if asked <= words(i)]
    if len(whole) == 1:
        return whole[0]
    if not whole:
        scored = sorted(((len(asked & words(i)), -n) for n, i in enumerate(items)), reverse=True)
        if scored[0][0] and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return items[-scored[0][1]]
        raise Unclear(f'House health has no {kind} like that. It has {_either([name(i) for i in items])}.')
    raise Unclear(f'Which {kind}? {_either([name(i) for i in whole])}.')


def plan(do, about=None, minutes=None, now=None):
    """What Ask's caretaker tool asks for: {'do', 'target', 'said'} for a note to leave now, or {'card'} for one that
    waits for the owner's tap (restarting Home Assistant, leaving a problem alone). Raises Unclear when `about`
    names nothing open or more than one thing, and Refused when the caretaker can't be asked that now."""
    now = now or _now()
    if do not in ASKS:
        raise Refused('The caretaker doesn’t do that.')
    if not set_up():
        raise Refused('The caretaker isn’t set up on this Mac yet.')
    _watching(now)
    try:
        return _plan(do, about, minutes, now, status() or {})
    except JOURNAL_ERRORS as error:
        _unreadable(error)
        raise Refused(UNREADABLE + ' Nothing was asked.') from error


def _plan(do, about, minutes, now, found):
    if do == 'pause':
        if minutes is None:
            return {'do': 'pause', 'target': 'forever',
                    'said': 'Asked the caretaker to pause until you resume it. It keeps watching but fixes nothing.'}
        if not isinstance(minutes, int) or not 1 <= minutes <= PAUSE_MOST:
            raise Unclear('Pause for one minute up to seven days, or leave minutes out to pause until resumed.')
        return {'do': 'pause', 'target': str(minutes),
                'said': f'Asked the caretaker to pause for {span(minutes)}. It keeps watching but fixes nothing until then.'}
    if do == 'resume':
        if not found.get('paused'):
            raise Refused('The caretaker isn’t paused.')
        return {'do': 'resume', 'target': '', 'said': 'Asked the caretaker to start fixing again.'}
    if do == 'drill':
        playing = found.get('drill') or {}
        if playing and not playing.get('done'):
            raise Refused('A drill is playing already; House health shows it.')
        drills = [d for d in found.get('drills', []) if isinstance(d, dict) and d.get('name')]
        chosen = _choose(drills, about, lambda d: _words(d['name'].replace('-', ' ') + ' ' + str(d.get('words', ''))),
                         lambda d: d['name'], 'drill')
        words = _end(chosen.get('words'))
        return {'do': 'drill', 'target': chosen['name'],
                'said': f'Starting the drill: {words[:1].lower()}{words[1:]} It plays on a made-up house; nothing real is touched.'}
    if do in ('sturdier_done', 'sturdier_ignore'):
        open_items = [s for s in found.get('sturdier', []) if isinstance(s, dict) and s.get('key') and not s.get('done_at')]
        chosen = _choose(open_items, about, lambda s: _words(f'{s["key"].replace("-", " ")} {s.get("title", "")} {s.get("words", "")}'),
                         lambda s: s.get('title', s['key']), 'suggestion')
        if do == 'sturdier_done':
            return {'do': 'done', 'target': f'sturdier:{chosen["key"]}',
                    'said': f'Told the caretaker you did it: “{chosen.get("title")}”. It will watch whether it helped and say so in House health.'}
        return {'do': 'ignore', 'target': f'sturdier:{chosen["key"]}',
                'said': f'Asked the caretaker not to suggest “{chosen.get("title")}” again.'}
    problems = _open_needs(now, found)
    if do == 'restart_home_assistant':
        problems = [p for p in problems if p['ask'] == 'ha.restart']
        if not problems:
            raise Unclear('Nothing House health shows asks for a restart. To restart Home Assistant anyway, use device with '
                          'homeassistant.restart; it waits for the owner’s tap too.')
    chosen = _choose(problems, about, lambda p: _words(f'{p["title"]} {p["words"]} {p["cause"]}'), lambda p: p['title'], 'problem')
    target = f'care-{chosen["id"]}'
    if do == 'restart_home_assistant' or (do == 'try_again' and chosen['ask'] == 'ha.restart'):
        return {'card': {'kind': 'care', 'do': 'restart', 'target': target, 'title': 'Restart Home Assistant?', 'lines': [chosen['title']],
                         'why': 'Home Assistant is away for a minute or two. The caretaker then looks again.', 'button': 'Restart'}}
    if do == 'ignore':
        return {'card': {'kind': 'care', 'do': 'ignore', 'target': target, 'title': 'Leave this alone from now on?',
                         'lines': [chosen['title']], 'why': 'The caretaker stops watching for it and won’t bring it up again.',
                         'button': 'Leave it alone'}}
    if do == 'try_again':
        if not chosen['fixable']:
            raise Refused(f'The caretaker can’t fix “{chosen["title"]}”; it needs you. House health says what to do.')
        return {'do': 'fix', 'target': target, 'said': f'Asked the caretaker to try again: “{chosen["title"]}”.'}
    return {'do': 'done', 'target': target, 'said': f'Asked the caretaker to mark “{chosen["title"]}” as sorted.'}
