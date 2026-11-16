"""A made-up week of errands for the errands tests and the app's demo server: sorted mail, the bodies the Gmail
helper would give for it, a class guide with to-dos, a calendar, sent mail, and a stand-in for Luna that answers the
way the real one is asked to. Every date is counted from the moment the week is seen from, so the demo works on any
day. Every person, business, number, address and link here is invented, and nothing reaches Gmail, the calendar, a
model or a shop: the readers themselves are replaced.
"""
import json
import re
import time
from contextlib import closing
from datetime import datetime, timedelta
from types import SimpleNamespace

from hub import bridge, home, luna, store as hub_store
from hub.app.errands import picture, read, store, watch
from hub.tools.calendar import read as calendar
from hub.tools.gmail import triage
from preschool import publication

NOW = datetime(2026, 11, 16, 7, 30, tzinfo=home.PACIFIC)          # a Monday morning
HOUR = 3600
QUIET = 'robin@example.org'           # the owner switched AI off for this sender
HOST = 'jamie@example.com'            # the party's host, who is waiting for an answer
DENTIST = 'office@harbordental.example'
PHONE = '(555) 555-0142'


def ago(hours, now=NOW):
    return now.timestamp() - hours * HOUR


def on(now, days):
    return (now + timedelta(days=days)).date()


def written(day):
    """'Saturday, November 21': a date as an email writes it."""
    return f'{day:%A}, {day:%B} {day.day}'


def party_day(now):
    """The party: the first Saturday at least three days off, so that its reply-by Thursday is still ahead."""
    ahead = (5 - now.weekday()) % 7
    return on(now, ahead + 7 if ahead < 3 else ahead)


def mail(now):
    """The emails, each (id, sender, name, kind, state, hours ago, subject, text, links)."""
    party, bill, water, coffee = party_day(now), on(now, 12), on(now, 14), on(now, 2)
    reply_by = party - timedelta(days=2)
    month = (now.replace(day=1) - timedelta(days=1)).strftime('%B')
    zone = datetime(coffee.year, coffee.month, coffee.day, 9, tzinfo=home.PACIFIC).strftime('%Z')
    return [
        ('e-party', HOST, 'Jamie Chen', 'needs', 'open', 45, 'Jordan’s birthday party',
         f"Hi there!\n\nJordan is turning five and we're having a party at Pine Park on {written(party)} from 2-4pm. "
         f"There will be pizza and far too much cake.\n\nPlease let me know by {written(reply_by)} if you can make it!\n\n"
         f"Jamie\n\nOn {on(now, -4):%a}, Jamie Chen <{HOST}> wrote:\n> Save the date!", []),
        ('e-bill', 'billing@news.lakesidepower.example', 'Lakeside Power', 'record', 'filed', 20, 'Your Lakeside Power bill is ready',
         f'Your bill for {month} is ready.\n\nAmount due: $84.20\nDue date: {bill:%B} {bill.day}, {bill.year}\n\n'
         'Pay now: https://pay.lakesidepower.example/bill?acct=1001\n\nQuestions? Call us at (555) 555-0101.',
         [{'label': 'Pay now', 'url': 'https://pay.lakesidepower.example/bill?acct=1001'},
          {'label': 'View your bill', 'url': 'https://click.mailer.example/track?u=1'},
          {'label': 'Unsubscribe', 'url': 'https://lakesidepower.example/unsubscribe'}]),
        ('e-dentist', DENTIST, 'Harbor Dental', 'needs', 'open', 1, 'Time for your cleaning',
         'Hello,\n\nOur records show you are due for your six-month cleaning. Reply with a few times that work for you, '
         f'or call us at {PHONE} to book.\n\nHarbor Dental', []),
        ('e-water', 'billing@water.example', 'Example Water', 'record', 'open', 26, 'Your water bill is ready',
         f'Your bill of $41.00 will be paid automatically on {water:%B} {water.day} from the account on file. '
         'No action is needed.', []),
        ('e-invite', 'calendar-notification@google.com', 'Sam Lee (Google Calendar)', 'calendar', 'open', 30,
         f'Invitation: Parent coffee @ {coffee:%a %b} {coffee.day}, {coffee.year} 9am - 10am ({zone})',
         f'Sam Lee has invited you to Parent coffee\n{coffee:%A %b} {coffee.day}, {coffee.year} 9am – 10am (Pacific Time)\n'
         'Where: Corner Cafe\nGoing? Yes - Maybe - No',
         [{'label': 'Yes', 'url': 'https://calendar.google.com/calendar/event?action=RESPOND&eid=abc&rst=1'},
          {'label': 'No', 'url': 'https://calendar.google.com/calendar/event?action=RESPOND&eid=abc&rst=2'},
          {'label': 'Maybe', 'url': 'https://calendar.google.com/calendar/event?action=RESPOND&eid=abc&rst=3'}]),
        ('e-news', 'club@gardenclub.example', 'Garden Club', 'needs', 'open', 10, 'This week in the garden',
         'This week in the garden club: the bulbs are in and the compost is turning. See you at the next meeting.', []),
        ('e-quiet', QUIET, 'Robin Park', 'needs', 'open', 5, 'Quick question', 'Can you send me that recipe?', []),
        ('e-receipt', 'orders@shop.example', 'Example Shop', 'record', 'filed', 12, 'Your receipt from Example Shop',
         'Thanks for your order of $23.50.', []),
    ]


def school(now):
    """The class guide's next days: two to-dos and a class date, as the reviewed guide gives them."""
    def row(key, kind, title, days, details, start=None):
        return {'_keys': [f'source:{key}'], 'kind': kind, 'title': title, 'date': on(now, days).isoformat(), 'start_time': start,
                'end_time': None, 'location': '', 'status': 'confirmed', 'time_note': '', 'details': details}
    carving = on(now, 4)
    return [row('pumpkin', 'action', 'Bring a small pumpkin', 4, f'Each child brings one small pumpkin for carving on {carving:%A}.'),
            row('photos', 'action', 'Return the photo order form', 2, 'Fill in the order form and put it in the blue folder.'),
            row('photo-day', 'event', 'Photo day', 3, '', start='09:00')]


def answers(now):
    """What the stand-in for Luna says about each item, by words of its subject or title. '{times}' in a reply is
    filled with the free times Luna was given."""
    party, bill, water, coffee, carving, form = party_day(now), on(now, 12), on(now, 14), on(now, 2), on(now, 4), on(now, 2)
    reply_by = party - timedelta(days=2)
    month = (now.replace(day=1) - timedelta(days=1)).strftime('%B')
    none = {'kind': 'none', 'title': '', 'why': '', 'due': None, 'amount': None, 'autopay': False, 'event': None, 'quote': '',
            'choices': []}

    def choice(label, reply=None, link=None, calendar=False, call=None, buy=()):
        return {'label': label, 'reply': reply, 'link': link, 'calendar': calendar, 'call': call, 'buy': list(buy)}

    return {
        'birthday party': {
            'kind': 'rsvp', 'title': 'RSVP to Jordan’s birthday party',
            'why': f'Jamie asks by {reply_by:%A} whether you can come on {party:%A}, {party:%b} {party.day}, 2–4 pm at Pine Park.',
            'due': reply_by.isoformat(), 'amount': None, 'autopay': False,
            'event': {'title': 'Jordan’s birthday party', 'date': party.isoformat(), 'start': '14:00', 'end': '16:00',
                      'place': 'Pine Park'},
            'quote': f'Please let me know by {written(reply_by)} if you can make it!',
            'choices': [choice('Yes, we’ll come', 'Hi Jamie!\n\nWe’d love to come to Jordan’s party. See you at Pine Park!\n\n'
                               'Thanks!', calendar=True),
                        choice('Can’t make it', 'Hi Jamie,\n\nThank you so much for inviting us! Sadly we can’t make it. '
                               'We hope Jordan has a wonderful birthday.\n\nThanks!')]},
        'power bill': {
            'kind': 'pay', 'title': 'Pay the Lakeside Power bill', 'why': f'{month}’s bill of $84.20 is due {bill:%B} {bill.day}.',
            'due': bill.isoformat(), 'amount': 84.2, 'autopay': False, 'event': None, 'quote': 'Amount due: $84.20',
            'choices': [choice('Pay the bill', link='L1')]},
        'cleaning': {
            'kind': 'book', 'title': 'Book a dental cleaning', 'why': 'Harbor Dental says your six-month cleaning is due.',
            'due': None, 'amount': None, 'autopay': False, 'event': None,
            'quote': f'Reply with a few times that work for you, or call us at {PHONE} to book.',
            'choices': [choice('Offer some times', 'Hello,\n\nI’d like to book a cleaning. {times} would work well.\n\nThanks!'),
                        choice('Call to book', call=PHONE)]},
        'water bill': {
            'kind': 'pay', 'title': 'Water bill pays itself', 'why': f'$41.00 is paid automatically on {water:%B} {water.day}.',
            'due': water.isoformat(), 'amount': 41, 'autopay': True, 'event': None, 'quote': '', 'choices': []},
        'parent coffee': {
            'kind': 'rsvp', 'title': 'Answer Sam’s invitation to parent coffee', 'why': f'{coffee:%A} at 9 am at Corner Cafe.',
            'due': coffee.isoformat(), 'amount': None, 'autopay': False,
            'event': {'title': 'Parent coffee', 'date': coffee.isoformat(), 'start': '09:00', 'end': '10:00', 'place': 'Corner Cafe'},
            'quote': '', 'choices': [choice('Yes', link='L1'), choice('No', link='L2')]},
        'garden': none,
        'pumpkin': {
            'kind': 'buy', 'title': f'Get a small pumpkin for {carving:%A}', 'why': f'Each child brings one for carving on {carving:%A}.',
            'due': carving.isoformat(), 'amount': None, 'autopay': False, 'event': None, 'quote': '',
            'choices': [choice('Add to the list', buy=['small pumpkin'])]},
        'photo order form': {
            'kind': 'sign', 'title': 'Return the photo order form', 'why': f'It goes back in the blue folder by {form:%A}.',
            'due': form.isoformat(), 'amount': None, 'autopay': False, 'event': None, 'quote': '', 'choices': []},
    }


class Morning:
    """What the made-up week holds, seen from `now`. Change a list and the next read or look sees it."""

    def __init__(self, now=NOW):
        self.now = now
        self.mail = mail(now)
        self.messages = {m[0]: {'id': m[0], 'from': f'{m[2]} <{m[1]}>', 'subject': m[6], 'text': m[7], 'links': list(m[8]),
                                'recipients': {'to': 'me@example.com'}} for m in self.mail}
        self.school = school(now)
        self.answers = answers(now)
        self.read, self.asked, self.searches = [], [], []
        self.sent = []                  # [(to, epoch seconds)]: what the owner sent
        self.days = {}                  # 'YYYY-MM-DD' -> [calendar events as the helper gives them]
        tomorrow = now + timedelta(days=1)
        self.week = [{'title': 'Standup', 'day': tomorrow.date(), 'last_day': tomorrow.date(),
                      'start': tomorrow.replace(hour=9, minute=0, second=0, microsecond=0),
                      'end': tomorrow.replace(hour=10, minute=0, second=0, microsecond=0),
                      'all_day': False, 'location': '', 'busy': True}]
        self.down = False

    def sort(self):
        """The week's mail as Hub's sort left it."""
        with closing(triage.db()) as conn, conn:
            for message_id, sender, name, kind, state, hours, subject, _, _ in self.mail:
                conn.execute('insert or replace into triage_messages(id, thread_id, sender, name, subject, kind, how, checked,'
                             ' received, seen_at, sorted_day, state) values (?,?,?,?,?,?,?,?,?,?,?,?)',
                             (message_id, 't-' + message_id, sender, name, subject, kind, 'rules', 1, ago(hours, self.now),
                              ago(hours, self.now) + 60, self.now.date().isoformat(), state))

    def replied(self, to, at=None):
        """The owner sends a reply from their mail app, now by errands' clock unless `at` is given."""
        self.sent.append((to, store.now() if at is None else at))

    def saved(self, title, day, start):
        """The owner saves an event in their calendar: `day` a date, `start` 'HH:MM'."""
        hour, minute = (int(part) for part in start.split(':'))
        begins = datetime(day.year, day.month, day.day, hour, minute, tzinfo=home.PACIFIC)
        self.days.setdefault(day.isoformat(), []).append(
            {'summary': title, 'status': 'confirmed', 'start': {'dateTime': begins.isoformat()},
             'end': {'dateTime': (begins + timedelta(hours=1)).isoformat()}})

    # ---- the Gmail and calendar helper ----
    def bridge(self, path, ttl=60, timeout=120, **params):
        if self.down:
            raise bridge.BridgeError('Gmail bridge is unreachable')
        if path == '/gmail/message':
            self.read.append(params['message_id'])
            if params['message_id'] not in self.messages:
                raise bridge.BridgeError('not found', status=404)
            return self.messages[params['message_id']]
        if path == '/gmail/profile':
            return {'mailbox': 'me@example.com'}
        if path == '/gmail/search':
            self.searches.append(params['query'])
            to = re.search(r'to:(\S+)', params['query']).group(1)
            return {'messages': [{'id': f's{n}', 'thread_id': f't{n}', 'date': datetime.fromtimestamp(at, home.PACIFIC).strftime(
                '%a, %d %b %Y %H:%M:%S %z')} for n, (who, at) in enumerate(self.sent) if who == to]}
        if path == '/calendar/events':
            day = datetime.fromisoformat(params['start']).date()
            return {'events': list(self.days.get(day.isoformat(), []))}
        raise bridge.BridgeError('Path not allowed')

    # ---- Luna, as asked in draft.py ----
    def luna(self, request):
        self.asked.append(request)
        data = json.loads(request['messages'][1]['content'].removeprefix('<data>').removesuffix('</data>'))
        times = ' or '.join(data.get('free_times')[:2]) or 'Any weekday morning'
        items = []
        for item in data['items']:
            said = (item.get('subject') or item.get('title') or '').lower()
            answer = next((a for words, a in self.answers.items() if words in said), None)
            if answer is not None:
                answer = json.loads(json.dumps(answer).replace('{times}', times))
                items.append({'ref': item['ref'], **answer})
        return {'items': items}, {'tokens_in': 1200, 'tokens_out': 400, 'cost': 0.002}


def install(patch, now=NOW, quiet=True):
    """Put the made-up week where Hub's readers are, for a test. `patch` is pytest's monkeypatch."""
    morning = Morning(now)
    patch.setattr(home, 'today', lambda: now.date())
    patch.setattr(store, 'now', lambda: now.timestamp())
    patch.setattr(bridge, 'get', morning.bridge)
    patch.setattr(luna, 'configured', lambda: True)
    patch.setattr(calendar, 'week', lambda moment=None: list(morning.week))
    patch.setattr(publication, 'latest', lambda: {'payload': {'events': [dict(row) for row in morning.school]}})
    morning.sort()
    if quiet:
        hub_store.set_ai_blocked(QUIET, True)
    return morning


def demo(patch, now):
    """The made-up week for the demo server, read once as the morning job would. Only what errands read through is
    replaced (the mail helper and the model), so the rest of the demo keeps its own made-up day: the class guide's
    to-dos are read from the demo's own guide (add `Morning.school` to it), and free times from its own calendar.
    Errands' clock starts at `now` and runs on from there. Returns the Morning."""
    morning = Morning(now)
    helper = SimpleNamespace(get=morning.bridge, BridgeError=bridge.BridgeError)
    started = time.time()
    patch.setattr(store, 'now', lambda: now.timestamp() + time.time() - started)
    patch.setattr(read, 'bridge', helper)
    patch.setattr(watch, 'bridge', helper)
    patch.setattr(read, 'luna', SimpleNamespace(configured=lambda: True, call=morning.luna, clip=luna.clip))
    patch.setattr(picture, 'luna', SimpleNamespace(configured=lambda: True, clip=luna.clip))
    again(morning)
    return morning


def quiet():
    """A week with nothing to do: every errand is put away as one that needed nothing."""
    with closing(store.db()) as conn, conn:
        conn.execute("update errands set state='skipped'")


def again(morning):
    """The demo's errands as the first read left them: everything else about them is forgotten."""
    with closing(store.db()) as conn, conn:
        conn.execute('delete from errands')
        conn.execute('delete from errands_tried')
        conn.execute('delete from errands_settings')
    with closing(hub_store.db()) as conn, conn:
        conn.execute('delete from engine_runs where kind=?', (read.KIND,))
    morning.sent.clear()
    morning.days.clear()
    morning.sort()
    hub_store.set_ai_blocked(QUIET, True)
    read.run()
