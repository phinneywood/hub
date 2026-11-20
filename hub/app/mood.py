"""The palette the app wears today, chosen from what is going on in the household's life (the owner's ask, 2026-11-20).

Four palettes (web/app/src/app.css): Dusk, the everyday one; Evergreen for time off; Clay for a celebration; Graphite,
only ever chosen by hand. It is decided once a day (the first time the calendar can be read; again after Hub restarts, which gives the same
answer unless the calendar changed meanwhile), for the whole household, so it reads as meaningful and
never flickers: the same calendar gives the same palette on every phone, all day. The app wears it when it opens and
when it comes back to the front, never while someone is looking (web/app/src/palette.ts). The one live rule, Clay
while a light show is on, is the app's own, since it already knows the moment a show starts and ends.

Highest wins:
  1. Clay: a birthday, a party, guests or a holiday on today's calendar, or a holiday Hub knows by date.
  2. Evergreen: while the trip is on, on a day without school, or at the weekend.
  3. Dusk otherwise.

Read only from what Hub already has (the calendar, the trip, the class guide). Every source fails soft: one that
can't be read just doesn't count, and with nothing readable it is Dusk.
"""
import re
from datetime import date, datetime, timedelta

from .. import home

DUSK, EVERGREEN, CLAY = 'dusk', 'evergreen', 'clay'
NAMES = {DUSK: 'Dusk', EVERGREEN: 'Evergreen', CLAY: 'Clay'}
# Words in a calendar title that make it a celebration at home. Whole words (not a part of "third-party"), any case.
GATHERING = re.compile(r"(?<![\w-])(birthday|bday|b-day|birthday party|dinner party|house party|housewarming|baby shower|bridal shower|"
                       r"wedding anniversary|potluck|friendsgiving|thanksgiving dinner|christmas dinner|christmas eve|christmas day|"
                       r"hanukkah|chanukah|diwali|lunar new year|passover seder|eid|guests over|guests for dinner|hosting dinner)(?![\w-])", re.I)
# A title that is an errand about a celebration ("Buy birthday present"), or a sad one, is not one.
NOT_GATHERING = re.compile(r"^\s*(buy|order|get|pick up|return|shop|shopping|rsvp|wrap|send|mail|book|plan)\b|celebration of life|memorial|funeral|"
                           r"\bwork anniversary\b|\bshopping\b", re.I)
# A class date that means no school that day.
NO_SCHOOL = re.compile(r'\b(no school|school closed|school is closed|no class(es)?|teacher work day|in-service day|school holiday|'
                       r'(winter|spring|fall|thanksgiving|mid-winter|midwinter) break)\b', re.I)
NOT_NO_SCHOOL = re.compile(r'\b(last day before|before)\b', re.I)
LONGEST = 48        # the most of a title said in the reason


def _nth(year, month, weekday, n):
    """The nth `weekday` (Monday 0) of a month."""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def holiday(day):
    """A holiday kept at home that Hub knows by its date, or ''."""
    fixed = {(1, 1): "New Year's Day", (7, 4): 'the Fourth of July', (10, 31): 'Halloween', (12, 24): 'Christmas Eve',
             (12, 25): 'Christmas', (12, 31): "New Year's Eve"}
    if (day.month, day.day) in fixed:
        return fixed[(day.month, day.day)]
    if day == _nth(day.year, 11, 3, 4):
        return 'Thanksgiving'
    return ''


def _short(title):
    title = ' '.join(title.split())
    return title if len(title) <= LONGEST else title[:LONGEST - 1].rstrip() + '…'


def pick(day, titles=(), trip=None, school=()):
    """The palette for `day` and the few words of why: {'palette', 'name', 'why'}.

    `titles`: today's calendar event titles. `trip`: (first day, last day) of the trip, or None. `school`: today's
    class-guide titles."""
    for title in titles:
        if GATHERING.search(title or '') and not NOT_GATHERING.search(title or ''):
            return {'palette': CLAY, 'name': NAMES[CLAY], 'why': f'for {_short(title)}', 'calendar': True}
    known = holiday(day)
    if known:
        return {'palette': CLAY, 'name': NAMES[CLAY], 'why': f'for {known}'}
    if trip and trip[0] <= day <= trip[1]:
        return {'palette': EVERGREEN, 'name': NAMES[EVERGREEN], 'why': 'while you’re away'}
    if any(NO_SCHOOL.search(title or '') and not NOT_NO_SCHOOL.search(title or '') for title in school):
        return {'palette': EVERGREEN, 'name': NAMES[EVERGREEN], 'why': 'for a day without school'}
    if day.weekday() >= 5:
        return {'palette': EVERGREEN, 'name': NAMES[EVERGREEN], 'why': 'for the weekend'}
    return {'palette': DUSK, 'name': NAMES[DUSK], 'why': 'for an ordinary day'}


def _titles(day):
    from ..tools.calendar import read
    now = datetime.combine(day, datetime.min.time(), home.PACIFIC)
    return [e['title'] for e in read.week(now) if e['day'] <= day <= e['last_day']]


def _trip():
    from ..tools.trip import store
    data = store.load()
    return date.fromisoformat(data['start']), date.fromisoformat(data['end'])


def _school(day):
    from preschool import publication
    current = publication.latest()
    if not current:
        return []
    return [row.get('title', '') for row in current['payload'].get('events', []) if row.get('date') == day.isoformat()
            and row.get('status') != 'cancelled']


def _soft(part, otherwise):
    try:
        return part()
    except Exception:  # noqa: BLE001 - a source that can't be read just doesn't count
        return otherwise


_kept = {}      # the day's palette, once decided: {date: picked}


def today(now=None, theirs=True):
    """Today's palette for the household, decided once a day (the first time the calendar can be read) and kept.

    `theirs`: whether the person asking may see the calendar it came from. Anyone else gets the same palette with
    the event's name left out of the reason."""
    day = (now or datetime.now(home.PACIFIC)).date()
    found = _kept.get(day)
    if found is None:
        titles = _soft(lambda: _titles(day), None)
        found = pick(day, titles or [], _soft(_trip, None), _soft(lambda: _school(day), []))
        if titles is not None:          # a calendar that couldn't be read is asked again next time
            _kept.clear()
            _kept[day] = found
    if found.get('calendar') and not theirs:
        found = {**found, 'why': 'for a celebration today'}
    return {key: found[key] for key in ('palette', 'name', 'why')}
