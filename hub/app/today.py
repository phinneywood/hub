"""The Today screen's picture, as plain data for the app (web/app): the family brief, what needs a person, then
the day. The wall in the kitchen (the app's #wall) shows the same picture, kept to what is the household's.

Built from what Hub already keeps and reads and nothing else: the same "Needs you" list as the older Today page
(hub/today.py), the calendar week, the reviewed class guide, the trip, Kitchen's waiting cart, and the brief's
own parts (brief.py): the weather, the locks and doors, the overnight note, and a line from Ask's model while
that is on. Nothing is changed by reading it. Every part fails soft: a source that can't be read is left out,
and when that is a fault the owner should know about, the list itself says so.

Errands Hub got ready (hub/app/errands/) are drawn as their own cards, finished with a tap, rather than as a line
in the list; an email or a class to-do with an errand on the screen is not listed again.

Three things differ from the older page, all the owner's call (mail is "only what needs me"):
- Each email that needs the owner is its own line, with who it is from and the next step, instead of a count.
- Hub's own housekeeping offers (filing sorted mail, archive rules, planned changes) are not things that need
  a person. They are told in a quiet line under the day, still one tap from their page.
- What has its own place on the screen is not repeated in the list: a class date is under school, two events
  at once are marked on the day itself, and the trip has its own line.
"""
from datetime import date, datetime, timedelta

from .. import config, home, people, today
from . import brief

MOST = today.MAX_ITEMS
EMAILS = 3                      # emails shown one by one; any more are counted
SCHOOL_DAYS = 7
# Hub's offers about its own mail filing: told, not asked.
HOUSEKEEPING = ('gmail-rules', 'gmail-plan', today.GMAIL_NEXT, today.GMAIL_FILED)
# Shown where they belong on the screen, not in the list as well.
ELSEWHERE = ('preschool-', 'calendar-clash-', 'trip-', 'tile-kitchen', 'errands')
MAIL = 'mail:'                  # a need that is one email: "mail:<message id>"
CART = 'kitchen-cart'


def clock():
    return datetime.now(home.PACIFIC)


def _need(item):
    return {'key': item['key'], 'title': item['title'], 'why': item['why'], 'tone': item['tone'],
            'open': {'href': item['href'], 'label': item['cta'] or 'Open'},
            'tick': 'Not this week' if item['key'].startswith(today.WEEK_KEYS) else 'Not today',
            'add': item.get('add') or None}


def _emails():
    """Each email that needs the owner, whoever has waited longest first: who it is from, and the next step."""
    from ..tools.gmail import triage
    from .errands import store as errands
    seen = triage.picture()
    drafted = {e['message_id'] for e in errands.rows("state in ('ready', 'opened', 'later') and message_id is not null")}
    rows = []
    for row in seen['needs']:
        if row['id'] in drafted:                # an errand for it is on the screen already, ready to finish
            continue
        waited = f'Waiting {row["days"]} days. ' if row['days'] >= triage.WAITING_DAYS else ''
        step = (row.get('step') or row.get('subject') or '').strip()
        rows.append({'key': MAIL + row['id'], 'title': f'Email from {row["who"]}', 'why': (waited + step).strip(),
                     'tone': 'warn' if waited else 'info', 'open': {'href': '/gmail', 'label': 'Open'}, 'tick': 'Done', 'add': None})
    return rows


def _cart():
    """What Kitchen needs from a person: an order or a cart plan that waits for a tap, and things on the list
    that need a product picked. One line, the most pressing."""
    from ..tools.kitchen import client
    waiting = {'tone': 'warn', 'open': {'href': '/kitchen', 'label': 'Review'}, 'tick': 'Not today', 'add': None}
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


def _list(accounts, missed=None):
    """(needs, how many more can wait, the quiet lines) from Hub's list, the emails and the cart: what a person who
    sees these accounts may see (people.py). The emails are the owner's, so they come only with the owner's list.
    `missed`: as today.gather's."""
    from .. import store
    from ..tools import TOOLS
    jobs = today.start()
    found = today.gather(jobs, today.mine(home.tiles(TOOLS), accounts), accounts=accounts, missed=missed)
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
    """The reviewed class guide's dates for the next week. A to-do whose errand is on the screen, ready for a tap, is
    left to its card until it is done."""
    from preschool import publication
    from .errands import read as errands_read, store as errands
    current = publication.latest()
    if not current:
        return []
    last = (day + timedelta(days=SCHOOL_DAYS - 1)).isoformat()
    try:
        carded = {e['key'] for e in errands.rows("state in ('ready', 'opened') and source='school'")}
    except Exception:  # noqa: BLE001 - the class dates are worth more than the tidiness
        carded = set()
    rows = []
    for row in sorted(current['payload'].get('events', []), key=lambda r: (r.get('date', ''), r.get('start_time') or '')):
        if not day.isoformat() <= row.get('date', '') <= last:
            continue
        if carded and errands_read.school_key(row) in carded:
            continue
        on = date.fromisoformat(row['date'])
        hour = home.clock_label(row.get('start_time')).replace(' AM', ' am').replace(' PM', ' pm')
        note = ', '.join(part for part in (hour, row.get('location') or '') if part)
        cancelled = row.get('status') == 'cancelled'
        rows.append({'day': 'Today' if on == day else on.strftime('%a'), 'title': row.get('title', 'Class date'),
                     'note': 'Cancelled' if cancelled else note, 'struck': cancelled, 'date': row['date'],
                     'todo': row.get('kind') == 'action' and not cancelled})
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


def _ready(login):
    """The errands Hub got ready for this person, the most pressing few as cards to finish with a tap
    (hub/app/errands/): their own and the household's."""
    from .errands import picture as errands
    return errands.today_part(login=login)


def _unknown(login):
    """A quiet line for a person Hub lets in but can't yet tell whose mail and calendar are theirs (household.json's
    "people" doesn't name them, or several logins are allowed and it says nothing), so an empty day isn't mistaken
    for a quiet one."""
    if login is None or people.who(login) is not None or str(login).strip().lower() not in config.ALLOWED_LOGINS:
        return []
    return [{'text': 'Hub doesn’t know whose mail and calendar are yours yet, so they aren’t shown. The owner sets this in '
                     'Hub’s settings file.', 'open': None}]


def picture(now=None, wall=False, login=None):
    """The whole Today screen for this login (people.py): the household's things, and the mail and calendar of the
    accounts this person may see. With `wall`, the kitchen wall's: the household's things only, whoever is asking
    (brief.family). Never raises."""
    now = now or clock()
    accounts = frozenset() if wall else _soft(lambda: people.accounts(login), frozenset())
    theirs = people.DEFAULT in accounts                   # the calendar and the note are the owner's, for now
    jobs = _soft(lambda: brief.start(now), None)
    needs, more, quiet = _soft(lambda: _list(accounts), ([], 0, []))
    left, tomorrow = _soft(lambda: _calendar(now), ([], [])) if theirs else ([], [])
    found = {'ok': True, 'at': now.isoformat(timespec='seconds'), 'needs': needs, 'more': more, 'today': left, 'tomorrow': tomorrow,
             'school': _soft(lambda: _school(now.date()), []), 'trip': _soft(lambda: _trip(now.date()), None),
             'quiet': quiet + ([] if wall else _soft(lambda: _unknown(login), [])), 'wall': False, 'elsewhere': 0, 'mine': theirs,
             'ready': {'cards': [], 'more': 0} if wall else _soft(lambda: _ready(login), {'cards': [], 'more': 0})}
    found.update(_soft(lambda: brief.finish(jobs, now), {'part': brief.part(now), 'weather': None, 'weather_says': '', 'house': None,
                                                         'overnight': None}) if jobs else
                 {'part': brief.part(now), 'weather': None, 'weather_says': '', 'house': None, 'overnight': None})
    found['note'] = brief.note(found) if theirs else None
    # A line for each dashboard this person may open (hub/app/boards/). Never on the wall: a dashboard is a person's.
    found['boards'] = [] if wall else _soft(lambda: _boards(login, now.date()), [])
    return brief.family(found) if wall else found


def _boards(login, day):
    from . import boards
    return boards.lines(login, day)


def yours(key, login):
    """Whether this login may tick a need with this key: the household's are everyone's, the rest their account's."""
    whose = today.account_of(key)
    return whose is None or people.sees(login, whose)


def tick(key):
    """A tap on a need's circle: an email is marked as handled, anything else is hidden for today (or a week).
    What to tell the person, or None when there is nothing to tick. A household need is hidden for everyone."""
    from ..tools.gmail import triage
    key = str(key)[:200]
    if key.startswith(MAIL):
        return 'Marked as handled' if triage.done(key.removeprefix(MAIL)) else None
    today.hide(key)
    everyone = ' for everyone' if today.account_of(key) is None else ''
    return f'Hidden{everyone} for a week' if key.startswith(today.WEEK_KEYS) else f'Hidden{everyone} until tomorrow'


def untick(key):
    """Put a ticked need back."""
    from ..tools.gmail import triage
    key = str(key)[:200]
    if key.startswith(MAIL):
        return triage.reopen(key.removeprefix(MAIL))
    today.show(key)
    return True
