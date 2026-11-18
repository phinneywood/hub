"""The calendar dashboard: the owner's next seven days, what clashes, and what is on today.

It reads exactly what the older Calendar page read (`hub/tools/calendar/read.week`, through the local helper), and
replaces that page: `/calendar` opens this board. Read-only: event titles, times and places only, never a
description or a guest list. Event text is untrusted data. Ask already answers calendar questions through its own
lookup (`hub/app/ask/private.py`), so `facts` only says where the week stands.

One person's calendar: only a login that may see the main Google account opens it.
"""
from datetime import datetime

from ... import bridge, home, people

ID = 'calendar'
NAME = 'Calendar'
WHAT = 'Your next seven days, and what clashes'
SHOWN_A_DAY = 12


def sees(login):
    return people.sees(login, people.DEFAULT)


def _week(now):
    """(days, clashes, problem): days as (date, label, [events with 'time' and 'clash']), as the older page drew them."""
    from ...tools.calendar import pages, read
    try:
        events = read.week(now)
    except read.SignInNeeded:
        return [], 0, 'signin'
    except bridge.BridgeError:
        return [], 0, 'down'
    pairs = read.overlaps(events, {e['day'] for e in events})
    clashes = {id(e) for pair in pairs for e in pair}
    shown = {}
    for event in events:
        for day in pages.covered(event, now.date()):
            shown.setdefault(day, []).append(dict(event, clash=id(event) in clashes and day == event['day'], time=pages.clock(event, day)))
    days = []
    for day in sorted(shown):
        listed = sorted(shown[day], key=lambda e: (e['time'] != 'All day', e['start'] or now))
        days.append((day, home.day_label(day.isoformat(), now.date()), listed))
    return days, len(pairs), None


def _row(event, label=''):
    where = event['location']
    sub = ' · '.join(part for part in [f'{label} {event["time"]}'.strip() if label else event['time'], where] if part)
    return {'title': event['title'], 'sub': sub + (' · clashes' if event['clash'] else ''), 'tone': 'warn' if event['clash'] else ''}


def picture(login, today):
    now = datetime.now(home.PACIFIC)
    days, clashes, problem = _week(now)
    if problem:
        say = 'Calendar needs signing in again' if problem == 'signin' else 'Can’t read the calendar right now'
        sub = ('In Termius on the Mac mini, run ./phone-setup --calendar-auth from ~/Developer/mac-mini.' if problem == 'signin'
               else 'The Google helper isn’t answering right now.')
        return {'say': say, 'sub': sub, 'blocks': [{'id': 'how', 'title': 'Calendar', 'kind': 'note', 'text': sub}]}
    count = sum(len(events) for _, _, events in days)
    on_today = next((events for day, _, events in days if day == now.date()), [])
    later = [e for e in on_today if e['all_day'] or (e['end'] and e['end'] > now)]
    upcoming = next((e for e in later if not e['all_day']), None)
    say = (f'Next: {upcoming["title"]} at {home.clock_label(upcoming["start"].strftime("%H:%M"))}' if upcoming and upcoming['start'] > now
           else f'Now: {upcoming["title"]}' if upcoming else 'Nothing else on today' if on_today else 'Nothing on today')
    blocks = [
        {'id': 'today', 'title': 'Today', 'kind': 'rows', 'rows': [_row(e) for e in on_today[:SHOWN_A_DAY]], 'empty': 'Nothing on today.'},
        {'id': 'numbers', 'title': 'The week', 'kind': 'tiles', 'tiles': [
            {'value': str(count), 'label': 'events' if count != 1 else 'event'},
            {'value': str(sum(1 for _, _, events in days if events)), 'label': 'busy days'},
            {'value': str(clashes), 'label': 'clashes' if clashes != 1 else 'clash', 'tone': 'warn' if clashes else ''}]},
        {'id': 'week', 'title': 'The next seven days', 'kind': 'rows',
         'rows': [_row(e, label) for day, label, events in days if day != now.date() for e in events[:SHOWN_A_DAY]],
         'empty': 'Nothing else this week.'},
    ]
    return {'say': say, 'sub': f'{count} {"event" if count == 1 else "events"} in the next seven days. Read-only, from Google Calendar.', 'blocks': blocks}


def line(login, today):
    """None: Today already draws the day's calendar and its clashes, so the board adds no line of its own."""
    return None


def facts(login, today):
    days, clashes, problem = _week(datetime.now(home.PACIFIC))
    if problem:
        return {'note': 'Hub can’t read the calendar right now.'}
    return {'events_next_7_days': sum(len(e) for _, _, e in days), 'clashes_next_7_days': clashes}
