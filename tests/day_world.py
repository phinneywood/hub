"""A made-up day for the app's Today tests and its demo server: a calendar, a class guide, two emails that need
someone, a cart waiting in Kitchen, a trip and one line of housekeeping. Every person, place and event is
fictional. Nothing here reads a real calendar, mailbox or service: the readers themselves are replaced.
"""
from datetime import datetime, timedelta

from hub import home, today
from hub.app import today as screen
from hub.engine import catalog
from hub.engine.registry import Source
from hub.tools.calendar import read as calendar
from hub.tools.gmail import triage
from hub.tools.kitchen import client as kitchen
from hub.tools.trip import store as trips
from preschool import publication

NOW = datetime(2026, 11, 14, 18, 10, tzinfo=home.PACIFIC)       # a Saturday evening


def at(day, clock, now=NOW):
    hour, minute = (int(part) for part in clock.split(':'))
    return (now + timedelta(days=day)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def event(title, day, start=None, end=None, where='', busy=True, now=NOW):
    """A calendar event `day` days from now, as Hub's calendar reader gives it. No times: all day."""
    on = (now + timedelta(days=day)).date()
    begins, ends = (at(day, start, now), at(day, end, now)) if start else (None, None)
    return {'title': title, 'day': on, 'last_day': on, 'start': begins, 'end': ends, 'all_day': start is None, 'location': where, 'busy': busy}


def school(title, day, start=None, where=None, status='confirmed', now=NOW):
    return {'date': (now + timedelta(days=day)).date().isoformat(), 'title': title, 'start_time': start, 'location': where, 'status': status}


class Day:
    """What the made-up day holds. Change a list and the next reading shows it."""

    def __init__(self, now=NOW):
        self.now = now
        self.events = [
            event('Lunch with Sam', 0, '12:00', '13:00', now=now),                  # over by now
            event('Dinner at home', 0, '19:00', '20:00', now=now),
            event('Robin’s bedtime', 0, '19:45', '20:15', busy=False, now=now),
            event('Alex away', 1, now=now),
            event('Swim lesson', 1, '09:30', '10:15', 'Community pool', now=now),
            event('Grocery pickup', 1, '11:00', '11:30', 'Greenway Market', now=now),
            event('Dentist', 1, '16:00', '16:30', now=now),
            event('Call with the plumber', 1, '16:00', '17:00', now=now),
        ]
        self.school = [school('Photo day', 2, now=now), school('You bring the snack', 4, '09:00', now=now),
                       school('Garden morning', 5, '10:00', status='cancelled', now=now),
                       school('Family work party', 6, '10:00', 'The garden', now=now), school('Spring concert', 40, '17:00', now=now)]
        self.mail = [
            {'id': 'm1', 'who': 'Maple Street Dental', 'step': 'Confirm Tuesday at 3:00.', 'subject': 'Your appointment', 'days': 1},
            {'id': 'm2', 'who': 'Sam Rivera', 'step': 'Send the signed form back.', 'subject': 'Field trip form', 'days': 4},
        ]
        self.filed = 12                 # emails Hub filed today
        self.sorted = {                 # what the last sort left in the inbox, kind by kind
            'reading': [{'id': f'n{n}', 'who': 'The Weekly Example', 'sender': 'news@example.com', 'subject': f'Issue {n}', 'days': n}
                        for n in (1, 2, 3)],
            'record': [{'id': 'r1', 'who': 'Example Shop', 'sender': 'shop@example.com', 'subject': 'Your receipt', 'days': 0}],
            'noise': [{'id': f'p{n}', 'who': 'Example Deals', 'sender': 'deals@example.com', 'subject': 'This week only', 'days': n}
                      for n in range(5)],
        }
        self.asked = [{'thread_id': 'a1', 'who': 'Alex', 'subject': 'Saturday plans?', 'days': 3}]
        self.cart = True                # a cart plan waits in Kitchen
        self.trip = {'title': 'Lisbon · spring', 'start': (now + timedelta(days=73)).date().isoformat(),
                     'end': (now + timedelta(days=81)).date().isoformat(), 'selected_flight': None}
        self.faults = []                # tiles in trouble, as the home summaries would report them
        self.calendar_down = False

    def week(self, now=None):
        if self.calendar_down:
            raise calendar.SignInNeeded()
        return list(self.events)

    def picture(self, now=None):
        needs = [dict(row, name=row['who'], sender='', kind='needs', state='open') for row in self.mail if row.get('state', 'open') == 'open']
        needs.sort(key=lambda r: r['days'] < triage.WAITING_DAYS)
        kinds = {kind: {'kind': kind, 'name': triage.NAMES[kind], 'open': [dict(row, name=row['who'], kind=kind, state='open')
                                                                       for row in self.sorted.get(kind, [])], 'filed': [], 'new': 0,
                        'filed_today': 0} for kind in triage.FILED + (triage.UNSORTED,)}
        return {'synced_at': self.now.timestamp() - 600, 'needs': needs, 'waiting': [r for r in needs if r['days'] >= triage.WAITING_DAYS], 'odd': [],
                'asked': [dict(a) for a in self.asked], 'kinds': kinds, 'review': 0, 'sorted_today': 0, 'filed_today': self.filed}

    def items(self):
        """Hub's own list, as its sources would give it."""
        found = []
        seen = self.picture()
        if seen['needs']:
            found.append(today.rec('gmail-needs', 'info', f'{len(seen["needs"])} emails need you', 'From two people.', href='/gmail', cta='Open'))
        if self.filed:
            found.append(today.rec(today.GMAIL_FILED, 'info', f'Hub filed {self.filed} emails', 'Receipts and newsletters.', href='/gmail', cta='See'))
        found.append(today.rec('trip-flights', 'info', 'Choose flights', 'Nothing is selected.', href='/trip', cta='Compare flights'))
        found.append(today.rec('preschool-x-Photo day', 'ok', 'Photo day', 'Monday', href='/preschool/', cta='Class guide'))
        return found

    def done(self, message_id):
        row = next((r for r in self.mail if r['id'] == message_id and r.get('state', 'open') == 'open'), None)
        if row:
            row['state'] = 'done'
        return bool(row)

    def reopen(self, message_id):
        row = next((r for r in self.mail if r['id'] == message_id and r.get('state') == 'done'), None)
        if row:
            row['state'] = 'open'
        return bool(row)

    def kitchen(self, method, path, body=None, timeout=10):
        return 200, {'messages': [], 'busy': False, 'plans': {'7': {'status': 'pending'}} if self.cart else {}, 'orders': {}}

    def tiles(self, tools):
        return [home.tile(slug, 'bad', headline, detail, href=href) for slug, headline, detail, href in self.faults]


def install(patch, kitchen_too=True, now=NOW):
    """Put the made-up day where Hub's readers are. `patch` is pytest's monkeypatch or a MonkeyPatch. `now`: the
    moment the day is seen from (ten past six on a Saturday evening unless given)."""
    day = Day(now)
    patch.setattr(screen, 'clock', lambda: now)
    patch.setattr(home, 'today', lambda: now.date())
    patch.setattr(calendar, 'week', day.week)
    patch.setattr(publication, 'latest', lambda: {'payload': {'events': list(day.school)}})
    patch.setattr(triage, 'picture', day.picture)
    patch.setattr(triage, 'done', day.done)
    patch.setattr(triage, 'reopen', day.reopen)
    patch.setattr(trips, 'load', lambda: dict(day.trip))
    patch.setattr(catalog, 'SOURCES', [Source('made-up', 'Made up', '', '', items=(day.items,))])
    patch.setattr(home, 'tiles', day.tiles)
    if kitchen_too:
        patch.setattr(kitchen, 'call', day.kitchen)
    return day
