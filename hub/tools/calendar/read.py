"""The next week of the owner's primary calendar, read through the helper.

Read-only: Hub uses only the helper's event list. It never opens one event's description or guest
list and has no route that creates or changes events. Event text is untrusted data.
"""
from datetime import date, datetime, time, timedelta

from ... import bridge, home

DAYS = 7
LIMIT = 100
CACHE_SECONDS = 300
TIMEOUT = 15        # seconds; the page and the Today card wait on this read


class SignInNeeded(Exception):
    """The helper's Calendar grant lapsed; only the owner can renew it."""


def _when(value):
    """(datetime in Pacific, or None for an all-day date, the date)."""
    if 'dateTime' in value:
        moment = datetime.fromisoformat(value['dateTime'].replace('Z', '+00:00')).astimezone(home.PACIFIC)
        return moment, moment.date()
    return None, date.fromisoformat(value['date'])


def week(now=None):
    """Events from the start of today for DAYS days, oldest first. Raises SignInNeeded or bridge.BridgeError."""
    now = now or datetime.now(home.PACIFIC)
    start = datetime.combine(now.date(), time(), home.PACIFIC)
    try:
        reply = bridge.get('/calendar/events', ttl=CACHE_SECONDS, timeout=TIMEOUT, start=start.isoformat(),
                           end=(start + timedelta(days=DAYS)).isoformat(), time_zone=home.PACIFIC.key, limit=LIMIT)
    except bridge.BridgeError as error:
        if error.status in (401, 403) or 'authoriz' in str(error).lower():
            raise SignInNeeded() from None
        raise
    events = []
    for raw in reply.get('events', []):
        if raw.get('status') == 'cancelled' or 'start' not in raw or 'end' not in raw:
            continue
        try:
            begins, day = _when(raw['start'])
            ends, last = _when(raw['end'])
        except (KeyError, ValueError):
            continue
        if begins is None:
            last -= timedelta(days=1)                   # an all-day end date is exclusive
        elif ends > begins:
            last = (ends - timedelta(microseconds=1)).date()  # ending at midnight doesn't reach the next day
        events.append({'title': str(raw.get('summary') or '(No title)')[:200], 'day': day, 'last_day': max(day, last),
                       'start': begins, 'end': ends, 'all_day': begins is None,
                       'location': str(raw.get('location') or '')[:200],
                       'busy': raw.get('transparency') != 'transparent'})
    return events


def overlaps(events, days):
    """Pairs of timed, busy events on the given dates that run into each other."""
    timed = sorted((e for e in events if not e['all_day'] and e['busy'] and e['day'] in days), key=lambda e: e['start'])
    found = []
    for i, first in enumerate(timed):
        for second in timed[i + 1:]:
            if second['start'] >= first['end']:
                break
            found.append((first, second))
    return found


def label(event, now=None):
    """'Today 3:30 PM', 'Thu all day' and the like."""
    day = home.day_label(event['day'].isoformat(), (now or datetime.now(home.PACIFIC)).date())
    return f'{day} all day' if event['all_day'] else f'{day} {home.clock_label(event["start"].strftime("%H:%M"))}'
