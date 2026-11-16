"""The Today screen's picture, as plain data for the app (web/app): what needs a person, then the day.

Built from what Hub already keeps and nothing else: the same "Needs you" list as the older Today page
(hub/today.py), the calendar week, the reviewed class guide, the trip, and Kitchen's waiting cart. No model is
called and nothing is changed by reading it. Every part fails soft: a source that can't be read is left out,
and when that is a fault the owner should know about, the list itself says so.

Three things differ from the older page, all the owner's call (mail is "only what needs me"):
- Each email that needs the owner is its own line, with who it is from and the next step, instead of a count.
- Hub's own housekeeping offers (filing sorted mail, archive rules, planned changes) are not things that need
  a person. They are told in a quiet line under the day, still one tap from their page.
- What has its own place on the screen is not repeated in the list: a class date is under school, two events
  at once are marked on the day itself, and the trip has its own line.
"""
from datetime import date, datetime, timedelta

from .. import home, today

MOST = today.MAX_ITEMS
EMAILS = 3                      # emails shown one by one; any more are counted
SCHOOL_DAYS = 7
# Hub's offers about its own mail filing: told, not asked.
HOUSEKEEPING = ('gmail-rules', 'gmail-plan', today.GMAIL_NEXT, today.GMAIL_FILED)
# Shown where they belong on the screen, not in the list as well.
ELSEWHERE = ('preschool-', 'calendar-clash-', 'trip-', 'tile-kitchen')
MAIL = 'mail:'                  # a need that is one email: "mail:<message id>"
CART = 'kitchen-cart'


def clock():
    return datetime.now(home.PACIFIC)


def _need(item):
    return {'key': item['key'], 'title': item['title'], 'why': item['why'], 'tone': item['tone'],
            'open': {'href': item['href'], 'label': item['cta'] or 'Open'},
            'tick': 'Not this week' if item['key'].startswith(today.WEEK_KEYS) else 'Not today'}


def _emails():
    """Each email that needs the owner, whoever has waited longest first: who it is from, and the next step."""
    from ..tools.gmail import triage
    seen = triage.picture()
    rows = []
    for row in seen['needs']:
        waited = f'Waiting {row["days"]} days. ' if row['days'] >= triage.WAITING_DAYS else ''
        step = (row.get('step') or row.get('subject') or '').strip()
        rows.append({'key': MAIL + row['id'], 'title': f'Email from {row["who"]}', 'why': (waited + step).strip(),
                     'tone': 'warn' if waited else 'info', 'open': {'href': '/gmail', 'label': 'Open'}, 'tick': 'Done'})
    return rows


def _cart():
    """What Kitchen needs from a person: an order or a cart plan that waits for a tap, and things on the list
    that need a product picked. One line, the most pressing."""
    from ..tools.kitchen import client
    waiting = {'tone': 'warn', 'open': {'href': '/kitchen', 'label': 'Review'}, 'tick': 'Not today'}
    try:
        status, seen = client.call('GET', '/kitchen', timeout=2)
        if status == 200 and isinstance(seen.get('items'), list):
            picks = sum(i.get('state') in ('pick', 'unavailable') for i in seen['items'])
            if (seen.get('order') or {}).get('status') == 'pending':
                return [{'key': CART, 'title': 'Place the grocery order', 'why': 'Checkout is ready. Nothing is ordered until you tap.', **waiting}]
            if seen.get('plan'):
                return [{'key': CART, 'title': 'Approve the grocery cart', 'why': 'Kitchen has a cart ready. Nothing is ordered until you approve it.',
                         **waiting}]
            if picks:
                return [{'key': CART, 'title': 'Pick a product for the list' if picks == 1 else f'Pick products for {picks} things on the list',
                         'why': 'Hub hasn’t bought these before. One tap each, and it will know next time.', **dict(waiting, tone='info')}]
            return []
        _, data = client.call('GET', '/messages?after=999999999', timeout=2)       # an older Kitchen: plans and busy only
    except client.KitchenDown:
        return []
    if not any(p.get('status') == 'pending' for p in (data.get('plans') or {}).values()):
        return []
    return [{'key': CART, 'title': 'Approve the grocery cart', 'why': 'Kitchen has a cart ready. Nothing is ordered until you approve it.', **waiting}]


def _list():
    """(needs, how many more can wait, the quiet lines) from Hub's list, the emails and the cart."""
    from .. import store
    from ..tools import TOOLS
    jobs = today.start()
    found = today.gather(jobs, home.tiles(TOOLS))
    hidden = store.snoozed(home.today().isoformat())
    needs, quiet = [], []
    for item in found:
        if item['key'].startswith(HOUSEKEEPING):
            quiet.append({'text': item['title'] + '.', 'open': {'href': item['href'], 'label': item['cta'] or 'See'}})
        elif item['key'] == 'gmail-needs':
            try:
                emails = _emails()
            except Exception:  # noqa: BLE001 - the count Hub's list already had is better than nothing
                needs.append(_need(item))
                continue
            needs += emails[:EMAILS]
            if len(emails) > EMAILS:
                more = len(emails) - EMAILS
                quiet.append({'text': f'{more} more email{"s need" if more != 1 else " needs"} you.', 'open': {'href': '/gmail', 'label': 'Mail'}})
        elif not item['key'].startswith(ELSEWHERE):
            needs.append(_need(item))
    try:
        needs += [n for n in _cart() if n['key'] not in hidden]
    except Exception:  # noqa: BLE001
        pass
    needs.sort(key=lambda n: today.RANK.get(n['tone'], 9))
    return needs[:MOST], max(0, len(needs) - MOST), quiet


def _clock(moment):
    return moment.strftime('%-I:%M'), moment.strftime('%p').lower()


def _day(events, day, now):
    """One day's events as rows: all-day ones first, then by the clock. Today leaves out what is over."""
    from ..tools.calendar import read
    clashes = {}
    for first, second in read.overlaps(events, {day}):
        clashes.setdefault(id(second), f'Overlaps {first["title"]} at {" ".join(_clock(first["start"]))}')
    rows, upcoming = [], False
    for event in sorted((e for e in events if e['day'] <= day <= e['last_day']), key=lambda e: (not e['all_day'], e['start'] or now)):
        if event['all_day']:
            rows.append({'time': 'All day', 'half': '', 'title': event['title'], 'note': event['location'], 'warn': False, 'next': False})
            continue
        if day == now.date() and event['end'] <= now:
            continue
        going = event['start'] <= now < event['end']
        time, half = ('Now', '') if going and day == now.date() else _clock(event['start']) if event['start'].date() == day else ('Still', '')
        first = day == now.date() and not upcoming
        upcoming = upcoming or first
        rows.append({'time': time, 'half': half, 'title': event['title'], 'note': clashes.get(id(event)) or event['location'],
                     'warn': id(event) in clashes, 'next': first})
    return rows


def _calendar(now):
    """(what is left of today, tomorrow), or ([], []) when the calendar can't be read."""
    from ..tools.calendar import read
    events = read.week(now)
    return _day(events, now.date(), now), _day(events, now.date() + timedelta(days=1), now)


def _school(day):
    """The reviewed class guide's dates for the next week."""
    from preschool import publication
    current = publication.latest()
    if not current:
        return []
    last = (day + timedelta(days=SCHOOL_DAYS - 1)).isoformat()
    rows = []
    for row in sorted(current['payload'].get('events', []), key=lambda r: (r.get('date', ''), r.get('start_time') or '')):
        if not day.isoformat() <= row.get('date', '') <= last:
            continue
        on = date.fromisoformat(row['date'])
        hour = home.clock_label(row.get('start_time')).replace(' AM', ' am').replace(' PM', ' pm')
        note = ', '.join(part for part in (hour, row.get('location') or '') if part)
        cancelled = row.get('status') == 'cancelled'
        rows.append({'day': 'Today' if on == day else on.strftime('%a'), 'title': row.get('title', 'Class date'),
                     'note': 'Cancelled' if cancelled else note, 'struck': cancelled})
    return rows


def _trip(day):
    """The trip, from four months before it until it ends."""
    from ..tools.trip import store
    data = store.load()
    start, end = date.fromisoformat(data['start']), date.fromisoformat(data['end'])
    if day > end or (start - day).days > today.TRIP_HORIZON_DAYS:
        return None
    title = data.get('title', 'Trip').split(' · ')[0]
    if day >= start:
        return {'title': title, 'when': f'Day {(day - start).days + 1} of {(end - start).days + 1}', 'note': '',
                'open': {'href': '/trip', 'label': 'Itinerary'}}
    days = (start - day).days
    chosen = bool(data.get('selected_flight'))
    return {'title': title, 'when': f'In {days} day{"s" * (days != 1)}', 'note': '' if chosen else 'Flights aren’t chosen yet',
            'open': {'href': '/trip', 'label': 'Open' if chosen else 'Compare'}}


def _soft(part, otherwise):
    try:
        return part()
    except Exception:  # noqa: BLE001 - one source that can't be read must not take the screen with it
        return otherwise


def picture(now=None):
    """The whole Today screen. Never raises."""
    now = now or clock()
    needs, more, quiet = _soft(_list, ([], 0, []))
    left, tomorrow = _soft(lambda: _calendar(now), ([], []))
    return {'ok': True, 'at': now.isoformat(timespec='seconds'), 'needs': needs, 'more': more, 'today': left, 'tomorrow': tomorrow,
            'school': _soft(lambda: _school(now.date()), []), 'trip': _soft(lambda: _trip(now.date()), None), 'quiet': quiet}


def tick(key):
    """A tap on a need's circle: an email is marked as handled, anything else is hidden for today (or a week).
    What to tell the owner, or None when there is nothing to tick."""
    from ..tools.gmail import triage
    key = str(key)[:200]
    if key.startswith(MAIL):
        return 'Marked as handled' if triage.done(key.removeprefix(MAIL)) else None
    today.hide(key)
    return 'Hidden for a week' if key.startswith(today.WEEK_KEYS) else 'Hidden until tomorrow'


def untick(key):
    """Put a ticked need back."""
    from ..tools.gmail import triage
    key = str(key)[:200]
    if key.startswith(MAIL):
        return triage.reopen(key.removeprefix(MAIL))
    today.show(key)
    return True
