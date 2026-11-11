"""Sorting the inbox, following loose ends, filing sorted mail and the Gmail page.

Synthetic data only. The Gmail helper and both models are fakes; nothing here reaches a mailbox.
"""
import html
import json
import re
import time
from email.utils import formatdate, parseaddr

import pytest
from fastapi.testclient import TestClient

from hub import actions, bridge, home, luna, store, today, worker
from hub.engine import catalog, overnight, sorting, state
from hub.main import app
from hub.tools.gmail import filing, inbox, nightly, triage
from tests.test_app import ORIGIN, PROFILE, fake_get

client = TestClient(app)
NOW = time.time()
DAY = 86400
USAGE = {'tokens_in': 900, 'tokens_out': 120, 'cost': 0.002}
LOCAL = {'sec-fetch-site': 'none'}


class Gmail:
    """A stand-in for the Gmail helper that remembers labels, so filing and undo can be followed."""

    def __init__(self):
        self.now = NOW
        self.messages, self.at, self.texts, self.to = {}, {}, {}, {}
        self.labels = [{'id': 'INBOX', 'name': 'INBOX', 'type': 'system'}]
        self.reads, self.posts, self.changes = [], [], {}
        self.paused = self.down = False
        self.apply_result = self.undo_result = None

    def add(self, message_id, sender, subject, days=0, labels=(), thread=None, snippet='', text='', to='', box='INBOX'):
        """A message `days` old. Added in order, each a minute older than the last, so the first is the newest."""
        self.at[message_id] = NOW - days * DAY - 3600 - 60 * len(self.messages)
        self.messages[message_id] = {
            'id': message_id, 'thread_id': thread or 't-' + message_id, 'labels': [*([box] if box else []), *labels],
            'from': sender, 'subject': subject, 'date': formatdate(self.at[message_id], usegmt=True), 'snippet': snippet}
        self.texts[message_id], self.to[message_id] = text, to

    def sent(self, message_id, subject, days, to, text, thread=None):
        self.add(message_id, 'Me <me@example.com>', subject, days, thread=thread, text=text, to=to, box='SENT')

    def leave_inbox(self, message_id):
        self.messages[message_id]['labels'].remove('INBOX')

    def read_paths(self, path):
        return [params for seen, params in self.reads if seen == path]

    def _search(self, query, limit, token):
        def age(message):
            return (self.now - self.at[message['id']]) / DAY
        everything = self.messages.values()
        if query.startswith('in:sent'):
            found = [m for m in everything if 'SENT' in m['labels'] and age(m) <= 14]
        elif query == 'in:inbox newer_than:7d':
            found = [m for m in everything if 'INBOX' in m['labels'] and age(m) <= 7]
        elif query.startswith('in:inbox older_than:5d newer_than:16d from:('):
            people = query.split('from:(')[1].rstrip(')').split(' OR ')
            found = [m for m in everything if 'INBOX' in m['labels'] and 5 <= age(m) <= 16 and parseaddr(m['from'])[1] in people]
        else:
            return fake_get('/gmail/search')
        found.sort(key=lambda m: -self.at[m['id']])
        start = int(token or 0)
        return {'messages': [dict(m, labels=list(m['labels'])) for m in found[start:start + limit]],
                'next_page_token': str(start + limit) if start + limit < len(found) else ''}

    def get(self, path, ttl=60, **params):
        assert path in bridge.READ_PATHS
        self.reads.append((path, params))
        if self.down:
            raise bridge.BridgeError('The Gmail helper is not answering.')
        if path == '/gmail/profile':
            return {**PROFILE, 'organization_paused': self.paused}
        if path == '/gmail/search':
            assert params['limit'] <= 25
            return self._search(params.get('query', ''), params['limit'], params.get('page_token'))
        if path == '/gmail/message':
            return {'text': self.texts[params['message_id']], 'recipients': {'to': self.to[params['message_id']]}}
        if path == '/gmail/thread':
            found = sorted((m for m in self.messages.values() if m['thread_id'] == params['thread_id']), key=lambda m: self.at[m['id']])
            return {'messages': found, 'total_messages': len(found)}
        if path == '/gmail/labels':
            return {'labels': list(self.labels)}
        return fake_get(path)

    def _relabel(self, ids, add, remove):
        for message_id in ids:
            labels = self.messages[message_id]['labels']
            labels[:] = [name for name in labels if name not in remove] + [name for name in add if name not in labels]

    def post(self, path, body):
        assert path in bridge.WRITE_PATHS
        self.posts.append((path, body))
        if self.down:
            raise bridge.BridgeError('The Gmail helper is not answering.')
        if path == '/gmail/labels/create':
            label = {'id': f'Label_{len(self.labels) + 10}', 'name': body['name'], 'type': 'user'}
            self.labels.append(label)
            return {'id': label['id'], 'name': label['name']}
        result = self.apply_result if path == '/gmail/labels/apply' else self.undo_result
        if isinstance(result, Exception):
            raise result
        if result is not None:
            return result
        if path == '/gmail/labels/apply':
            assert 1 <= len(body['message_ids']) <= 50
            change = f'chg-{len(self.changes) + 1}'
            self.changes[change] = body
            self._relabel(body['message_ids'], body['add_label_ids'], body['remove_label_ids'])
            return {'changed': len(body['message_ids']), 'status': 'applied', 'change_id': change}
        done = self.changes[body['change_id']]
        self._relabel(done['message_ids'], done['remove_label_ids'], done['add_label_ids'])
        return {'undone': True}

    def applied(self):
        return [body for path, body in self.posts if path == '/gmail/labels/apply']


class Models:
    """Luna answers from fixed tables by subject line; Jev says yes to any text that says “please”."""

    def __init__(self, place=None, steps=None):
        self.place, self.steps = place or {}, steps or {}
        self.luna_requests, self.jev_requests = [], []

    def luna(self, request):
        self.luna_requests.append(request)
        name = request['response_format']['json_schema']['name']
        data = json.loads(request['messages'][1]['content'].removeprefix('<data>').removesuffix('</data>'))
        if name == 'sorted_mail':
            items = [{'ref': d['ref'], **dict(zip(('kind', 'person', 'otherwise'), self.place[d['subject']]))}
                     for d in data if d['subject'] in self.place]
        else:
            items = [{'ref': d['ref'], 'step': self.steps.get(d['subject'], '')} for d in data]
        return {'items': items}, USAGE

    def jev(self, url, request, token):
        self.jev_requests.append(request)
        [question] = request['questions']
        score = 0.9 if 'please' in request['state']['body'].lower() else 0.1
        return {'answers': {question: {'type': 'noul', 'noul': score}}, 'provider': 'TypeSafe', 'model': 'typesafe/jev-1.14'}

    def sort(self, **options):
        return triage.sort(self.luna, self.jev, **options)

    def said(self, name):
        return [json.loads(r['messages'][1]['content'][6:-7]) for r in self.luna_requests
                if r['response_format']['json_schema']['name'] == name]


@pytest.fixture(autouse=True)
def gmail(monkeypatch):
    fake = Gmail()
    monkeypatch.setattr(bridge, 'get', fake.get)
    monkeypatch.setattr(bridge, 'post', fake.post)
    inbox._filing.update(t=None, result=None)
    triage._thread['t'] = None
    yield fake
    triage.wait()
    inbox.wait()


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(luna, 'key', lambda: 'synthetic-key')


def week(gmail):
    """One made-up week of inbox mail, newest first, and the models' answers for it."""
    store.set_ai_blocked('private@quiet.example', True)
    gmail.add('m1', 'Dana Lee <dana@friends.example>', 'Lunch on Thursday?', text='Are you free for lunch on Thursday? Please say.')
    gmail.add('m2', 'School Office <office@school.example>', 'Permission slip due Friday',
              text='Please sign and return the slip by Friday. https://school.example/slip?id=123')
    gmail.add('m3', 'Synthetic Shop <orders@shop.example>', 'Action required: confirm your order preferences',
              text='We have refreshed the order preferences page.')
    gmail.add('m4', 'Synthetic Shop <orders@shop.example>', 'Your receipt from Synthetic Shop', snippet='Total $20.00')
    gmail.add('m5', 'Bank <no-reply@bank.example>', 'Your verification code', text='Your code is 000000.')
    gmail.add('m6', 'Weekly Reader <newsletter@reader.example>', 'The weekly digest', labels=['CATEGORY_PROMOTIONS'])
    gmail.add('m7', 'Deals <deals@store.example>', '50% off everything', labels=['CATEGORY_PROMOTIONS'])
    gmail.add('m8', 'Quiet Person <private@quiet.example>', 'A private matter', text='Please keep this between us.')
    gmail.add('m9', 'Bank <alerts@bank.example>', 'Unusual sign-in to your account')
    gmail.add('m10', 'Committee <committee@club.example>', 'Notes from the committee', text='Minutes attached for the file.')
    gmail.add('m11', 'Utility <billing@utility.example>', 'Payment failed for your account', text='Please update the card ending 12345.')
    return Models(place={'Lunch on Thursday?': ('needs', True, 'reading'), 'Notes from the committee': ('needs', False, 'reading')},
                  steps={'Lunch on Thursday?': 'Reply to Dana about Thursday lunch', 'Permission slip due Friday': 'Return the signed slip by Friday',
                         'Payment failed for your account': 'Update the card ending 12345'})


def receipts(gmail, count, days=0):
    for n in range(1, count + 1):
        gmail.add(f'r{n}', 'Synthetic Shop <orders@shop.example>', f'Your receipt for order {n}', days=days, snippet='Total $20.00')


def trust(kind, level):
    assert state.set_trust(catalog.action(filing.ACTIONS[kind]), level)


def kinds():
    return {row['id']: row['kind'] for row in triage._rows('1=1')}


def states():
    return {row['id']: row['state'] for row in triage._rows('1=1')}


def shown(response):
    """The words on a page, without the markup."""
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', response.text)).split())


def said(response):
    assert response.status_code == 303
    return response.headers['location']


def tap(path, **form):
    return said(client.post(path, data=form, headers=ORIGIN, follow_redirects=False))


def today_keys():
    return {item['key']: item for item in today.gmail_triage()}


# --- the fixed rules ---------------------------------------------------------------------------------

@pytest.mark.parametrize('subject, address, labels, snippet, kind, extra', [
    ('Invitation: Team lunch @ Thursday', 'pat@example.net', [], '', 'calendar', {}),
    ('Anything at all', 'calendar-notification@google.com', [], '', 'calendar', {}),
    ('Your password was changed', 'alerts@bank.example', [], '', 'record', {'odd': 'security'}),
    ('Reset your password', 'no-reply@bank.example', [], '', 'expires', {}),
    ('Your verification code', 'no-reply@bank.example', [], '', 'expires', {}),
    ('Big sale this weekend', 'deals@store.example', ['CATEGORY_PROMOTIONS'], '', 'noise', {}),
    ('This week in gardening', 'hello@garden.example', ['CATEGORY_PROMOTIONS'], '', 'reading', {}),
    ('Someone liked your post', 'notify@social.example', ['CATEGORY_SOCIAL'], '', 'noise', {}),
    ('Final notice: 50% off ends tonight', 'deals@store.example', ['CATEGORY_PROMOTIONS'], '', 'noise', {'checked': 1}),
    ('Your order has shipped', 'orders@shop.example', [], '', 'record', {'odd': None}),
    ('Your receipt', 'orders@shop.example', [], 'Total charged $1,450.00', 'record', {'odd': 'charge'}),
    ('Your receipt', 'orders@shop.example', [], 'Total charged $20.00', 'record', {'odd': None}),
    ('Your statement is ready', 'no-reply@bank.example', [], 'Balance $2,300.00', 'record', {'odd': None}),  # a balance isn't a charge
    ('We’re updating our terms', 'legal@app.example', [], '', 'noise', {}),
    ('New topic in the neighbours group', 'group@list.example', ['CATEGORY_FORUMS'], '', 'reading', {}),
    ('Hello there', 'pat@example.net', [], '', 'unsorted', {}),
    ('We’ve received your payment', 'no-reply@bank.example', [], '', 'record', {'odd': None}),
    ('Ordered 1 item: Synthetic lamp', 'orders@shop.example', [], '', 'record', {}),
    ('You shared some account data with Synthetic App', 'no-reply@accounts.example', [], '', 'record', {'odd': None}),
    ('An app’s access to your account was updated', 'no-reply@accounts.example', [], '', 'record', {'odd': None}),
    ('A new benefits statement is available', 'no-reply@insurer.example', [], '', 'record', {}),
    ('We’re updating Synthetic’s Privacy Policy', 'legal@app.example', [], '', 'noise', {}),
    ('Receipt for your Friday appointment', 'desk@salon.example', [], '', 'record', {'checked': 1}),
    ('Confirm your subscription to Synthetic News', 'hello@news.example', [], '', 'expires', {}),
    ('You’ve got 12 unread messages', 'notify@chat.example', [], '', 'noise', {}),
    ('Welcome to Synthetic Club', 'hello@club.example', [], '', 'noise', {}),
    ('What’s happening nearby · Week of November 9', 'hello@events.example', [], '', 'reading', {}),
    ('Your upcoming appointment on Friday', 'desk@salon.example', [], '', 'needs', {'checked': 0}),
    ('Payment failed for your account', 'billing@utility.example', [], '', 'needs', {'checked': 0, 'fallback': None}),
    ('Your order: action required', 'orders@shop.example', [], '', 'needs', {'checked': 0, 'fallback': 'record'}),
])
def test_fixed_rules_place_mail_by_sender_subject_and_category(subject, address, labels, snippet, kind, extra):
    found = triage.classify({'subject': subject, 'labels': ['INBOX', *labels], 'snippet': snippet}, address)
    assert found['kind'] == kind and found['how'] == 'rule' and found['state'] == 'open'
    assert {name: found[name] for name in extra} == extra


def test_the_owners_word_and_their_own_conversations_come_before_the_rules():
    message = {'subject': 'Payment failed for your account', 'labels': ['INBOX']}
    assert triage.classify(message, 'billing@utility.example', override='noise') == {
        'category': 'primary', 'how': 'owner', 'fallback': None, 'checked': 1, 'odd': None, 'mine': 0, 'state': 'open', 'kind': 'noise'}
    plain = {'subject': 'Re: Saturday', 'labels': ['INBOX']}
    waiting = triage.classify(plain, 'dana@friends.example', wrote=100.0, received=200.0)
    assert (waiting['kind'], waiting['mine'], waiting['state'], waiting['checked']) == ('needs', 1, 'open', 1)
    assert triage.classify(plain, 'dana@friends.example', wrote=300.0, received=200.0)['state'] == 'done'  # the owner answered already
    big = {'subject': 'Your receipt', 'labels': ['INBOX'], 'snippet': 'Total charged $450.00'}
    assert triage.classify(big, 'orders@shop.example', limit=300)['odd'] == 'charge'
    assert triage.classify(big, 'orders@shop.example')['odd'] is None  # under the usual $1,000


# --- one sort, end to end ----------------------------------------------------------------------------

def test_one_sort_places_the_week_reads_only_what_might_need_the_owner_and_stores_no_text(gmail, key):
    models = week(gmail)
    assert models.sort() == {'status': 'ok', 'new': 11, 'complete': True}
    assert kinds() == {'m1': 'needs', 'm2': 'needs', 'm3': 'record', 'm4': 'record', 'm5': 'expires', 'm6': 'reading',
                       'm7': 'noise', 'm8': 'unsorted', 'm9': 'record', 'm10': 'reading', 'm11': 'needs'}
    rows = {row['id']: row for row in triage._rows('1=1')}
    assert [(rows[m]['how'], rows[m]['checked']) for m in ('m1', 'm2', 'm3', 'm8', 'm10')] == [
        ('model', 1), ('rule', 1), ('rule', 2), ('off', 1), ('model', 2)]
    assert [rows[m]['step'] for m in ('m1', 'm2', 'm11')] == [
        'Reply to Dana about Thursday lunch', 'Return the signed slip by Friday', '']  # a step with a card number is dropped

    # Only mail that might need the owner was opened, and never the sender AI is off for.
    assert [p['message_id'] for p in gmail.read_paths('/gmail/message')] == ['m2', 'm3', 'm11', 'm10', 'm1']
    assert not gmail.posts
    [placed], [stepped] = models.said('sorted_mail'), models.said('next_steps')
    assert placed == [
        {'ref': 'e1', 'from': 'Dana Lee', 'domain': 'friends.example', 'subject': 'Lunch on Thursday?', 'gmail_category': 'primary'},
        {'ref': 'e2', 'from': 'Committee', 'domain': 'club.example', 'subject': 'Notes from the committee', 'gmail_category': 'primary'}]
    assert [d['subject'] for d in stepped] == ['Lunch on Thursday?', 'Permission slip due Friday', 'Payment failed for your account']
    assert '[link]' in stepped[1]['body'] and 'https:' not in json.dumps(stepped)
    sent = json.dumps(models.luna_requests) + json.dumps(models.jev_requests)
    assert '@' not in sent  # no address leaves
    assert not any(words in sent for words in ('private matter', 'Quiet Person', 'quiet.example', 'between us'))  # AI is off for them
    assert all(r['provider'] == luna.PROVIDER and r['provider']['zdr'] is True for r in models.luna_requests)
    assert [list(r['questions']) for r in models.jev_requests] == [['needs_owner']] * 4
    assert all(r['provider']['zdr'] is True and set(r['state']) == {'subject', 'body'} for r in models.jev_requests)
    assert '[link]' in models.jev_requests[0]['state']['body'] and 'https:' not in models.jev_requests[0]['state']['body']

    kept = json.dumps(triage._rows('1=1'))
    for text in gmail.texts.values():
        assert not text or text not in kept
    assert 'Total $20.00' not in kept and '12345' not in kept

    last = state.last_run(home.today().isoformat(), triage.KIND)
    assert (last['status'], last['luna_calls'], last['jev_calls'], last['cost']) == ('ok', 2, 4, pytest.approx(0.004))
    assert state.used(home.today().isoformat(), overnight.KIND)['luna_calls'] == 0  # the review's own allowance is untouched

    # A second sort finds nothing new and costs nothing.
    gmail.reads.clear()
    assert models.sort() == {'status': 'ok', 'new': 0, 'complete': True}
    assert len(models.luna_requests) == 2 and len(models.jev_requests) == 4 and not gmail.read_paths('/gmail/message')


def test_the_picture_counts_what_needs_the_owner_and_what_was_sorted(gmail, key):
    week(gmail).sort()
    seen = triage.picture()
    assert [r['id'] for r in seen['needs']] == ['m1', 'm2', 'm11'] and seen['waiting'] == []
    assert [r['id'] for r in seen['odd']] == ['m9'] and seen['review'] == 0 and seen['filed_today'] == 0
    assert {kind: [r['id'] for r in entry['open']] for kind, entry in seen['kinds'].items()} == {
        'calendar': [], 'record': ['m3', 'm4'], 'expires': ['m5'], 'reading': ['m6', 'm10'], 'noise': ['m7'], 'unsorted': ['m8']}
    assert seen['sorted_today'] == 7
    assert triage.counts_text({kind: entry['new'] for kind, entry in seen['kinds'].items()}) == (
        '2 records, 2 newsletters, 1 sign-in code, 1 promotion, 1 unsorted')
    found = today_keys()
    assert list(found) == ['gmail-needs', 'gmail-odd']
    assert (found['gmail-needs']['title'], found['gmail-needs']['tone']) == ('3 emails need you', 'info')
    assert found['gmail-needs']['why'] == 'From Dana Lee, School Office and Utility. Hub sorted 7 others today.'
    assert found['gmail-odd']['title'] == 'A security notice needs a look' and 'Bank' in found['gmail-odd']['why']
    assert today.brief(list(found.values())) == '2 things for today:\n• 3 emails need you\n• A security notice needs a look'
    tile = home.gmail()
    assert (tile['headline'], tile['href'], tile['tone']) == ('3 emails need you', '/gmail', 'warn')
    assert '7 sorted today' in tile['detail'] and tile['lines'][0]['label'] == '1 record worth a look'


def test_without_a_model_key_the_rules_still_sort_and_nothing_is_opened(gmail):
    models = week(gmail)
    assert models.sort() == {'status': 'ok', 'new': 11, 'complete': True}
    assert not models.luna_requests and not models.jev_requests
    assert {path for path, _ in gmail.reads} == {'/gmail/search'}
    assert kinds()['m1'] == 'unsorted' and [r['id'] for r in triage.picture()['needs']] == ['m2', 'm3', 'm11']  # shown rather than missed
    page = shown(client.get('/gmail'))
    assert 'The model key isn’t set up' in page and '3 emails need you' in page


def test_a_busy_week_is_read_in_pages_and_reported_as_unfinished(gmail):
    for n in range(260):
        gmail.add(f'b{n}', 'Deals <deals@store.example>', f'Offer {n}', labels=['CATEGORY_PROMOTIONS'])
    assert triage.sort() == {'status': 'ok', 'new': 250, 'complete': False}
    assert len([p for p in gmail.read_paths('/gmail/search') if p['query'] == 'in:inbox newer_than:7d']) == triage.PAGES


def test_mail_without_a_usable_sender_or_id_and_the_owners_own_mail_are_skipped(gmail):
    gmail.add('ok', 'Pat Kim <pat@example.net>', 'Hello')
    gmail.add('bad', 'not an address', 'Hello')
    gmail.add('mine', 'Me <me@example.com>', 'A note to self', labels=['SENT'])
    gmail.add('', 'Pat Kim <pat@example.net>', 'No id')
    assert triage.sort()['new'] == 1 and list(kinds()) == ['ok']


# --- limits and failures -----------------------------------------------------------------------------

def test_the_daily_sorting_allowance_stops_luna_and_leaves_the_rest_for_tomorrow(gmail, key):
    models, day = week(gmail), home.today().isoformat()
    state.record_run(day, triage.KIND, 'ok', luna_calls=triage.LUNA_CALLS_PER_DAY)
    assert models.sort()['status'] == 'ok'
    assert not models.luna_requests and len(models.jev_requests) == 3
    row = triage.get('m1')
    assert (row['kind'], row['how']) == ('unsorted', 'rule')  # still Luna's to place on a later run


def test_the_daily_check_allowance_stops_jev_before_any_message_is_opened_for_it(gmail, key):
    models, day = week(gmail), home.today().isoformat()
    state.record_run(day, triage.KIND, 'ok', jev_calls=triage.JEV_CALLS_PER_DAY)
    assert models.sort()['status'] == 'ok'
    assert not models.jev_requests
    assert [p['message_id'] for p in gmail.read_paths('/gmail/message')] == ['m1']  # only to word a step a person asked for
    assert [r['id'] for r in triage.picture()['needs']] == ['m1', 'm2', 'm3', 'm10', 'm11']


def test_the_months_cost_limit_stops_both_models(gmail, key):
    models, day = week(gmail), home.today().isoformat()
    state.record_run(day, overnight.KIND, 'ok', cost=overnight.MONTH_COST_CAP)
    assert models.sort() == {'status': 'ok', 'new': 11, 'complete': True}
    assert not models.luna_requests and not models.jev_requests and not gmail.read_paths('/gmail/message')
    last = state.last_run(day, triage.KIND)
    assert (last['luna_calls'], last['jev_calls'], last['cost']) == (0, 0, 0)


def test_a_model_that_fails_marks_the_sort_unfinished_and_keeps_what_the_rules_placed(gmail, key):
    models, day = week(gmail), home.today().isoformat()

    def broken(*args):
        raise RuntimeError('synthetic failure')
    assert triage.sort(models.luna, broken)['status'] == 'error'
    assert state.last_run(day, triage.KIND)['status'] == 'error'
    assert 'm2' in [r['id'] for r in triage.picture()['needs']] and kinds()['m4'] == 'record'
    assert 'The last sort didn’t finish.' in shown(client.get('/gmail'))

    assert triage.sort(broken, models.jev)['status'] == 'error'
    row = triage.get('m8')
    assert (row['kind'], row['how']) == ('unsorted', 'off')
    assert 'synthetic failure' not in json.dumps(store.recent_activity()) + json.dumps(state.runs(triage.KIND))


def test_when_gmail_cannot_be_read_the_sort_fails_by_name_and_the_morning_carries_on(gmail):
    gmail.down = True
    with pytest.raises(bridge.BridgeError):
        triage.sort()
    assert state.last_run(home.today().isoformat(), triage.KIND)['status'] == 'error'
    assert sorting.run() == []
    assert ('engine_sort_failed', 'gmail') in [(a['action'], a['detail']) for a in store.recent_activity()]
    assert triage.picture()['synced_at'] is None and client.get('/gmail').status_code == 200


# --- loose ends --------------------------------------------------------------------------------------

def test_someone_waiting_two_days_is_flagged_until_the_owner_answers(gmail):
    gmail.add('n1', 'School Office <office@school.example>', 'Permission slip due Friday', days=3, thread='slip')
    triage.sort()
    seen = triage.picture()
    assert [r['id'] for r in seen['waiting']] == ['n1'] and seen['needs'][0]['days'] == 3
    item = today_keys()['gmail-needs']
    assert (item['title'], item['tone']) == ('1 email needs you', 'warn')
    assert item['why'] == 'From School Office. It has waited 2 days or more.'
    assert '1 waiting 2 days or more' in shown(client.get('/gmail'))

    gmail.sent('s1', 'Re: Permission slip due Friday', 0, 'School Office <office@school.example>', 'Signed and attached.', thread='slip')
    triage.sort()
    assert states()['n1'] == 'done' and triage.picture()['needs'] == [] and today_keys() == {}


def test_whoever_has_waited_longest_leads_the_list(gmail):
    gmail.add('n1', 'Utility <billing@utility.example>', 'Payment failed for your account')
    gmail.add('n2', 'School Office <office@school.example>', 'Permission slip due Friday', days=3)
    gmail.add('n3', 'Coach Rivera <coach@team.example>', 'Please confirm Saturday practice', days=1)
    triage.sort()
    assert [r['id'] for r in triage.picture()['needs']] == ['n2', 'n1', 'n3']
    assert today_keys()['gmail-needs']['why'] == 'From School Office, Utility and Coach Rivera. One has waited 2 days or more.'


def test_a_reply_in_a_conversation_the_owner_is_part_of_needs_them_without_any_model(gmail):
    gmail.sent('s1', 'Saturday', 3, 'Dana Lee <dana@friends.example>', 'Shall we go on Saturday?', thread='sat')
    gmail.add('n1', 'Dana Lee <dana@friends.example>', 'Re: Saturday', days=1, thread='sat')
    gmail.sent('s2', 'The gate', 0, 'Pat Kim <pat@example.net>', 'All fixed now.', thread='gate')
    gmail.add('n2', 'Pat Kim <pat@example.net>', 'Re: The gate', days=1, thread='gate')
    triage.sort()
    rows = {r['id']: r for r in triage._rows('1=1')}
    assert (rows['n1']['kind'], rows['n1']['mine'], rows['n1']['state']) == ('needs', 1, 'open')
    assert rows['n2']['state'] == 'done'  # the owner wrote after it arrived
    assert 'you’re in this conversation' in shown(client.get('/gmail'))


def test_mail_that_needs_the_owner_is_followed_for_two_weeks_then_let_go(gmail):
    gmail.add('n1', 'School Office <office@school.example>', 'Permission slip due Friday', days=5)
    gmail.add('p1', 'Deals <deals@store.example>', '50% off everything', days=5, labels=['CATEGORY_PROMOTIONS'])
    triage.sync(NOW)
    gmail.now = later = NOW + 3 * DAY  # both are now older than the weekly look
    triage.sync(later)
    assert states() == {'n1': 'open', 'p1': 'old'}
    assert triage.picture(later)['needs'][0]['days'] == 8
    assert any('from:(office@school.example)' in p['query'] for p in gmail.read_paths('/gmail/search'))

    gmail.now = later = NOW + 10 * DAY
    triage.sync(later)
    assert states()['n1'] == 'old' and triage.picture(later)['needs'] == []  # past two weeks it is the backlog's business

    gmail.add('n2', 'School Office <office@school.example>', 'Please confirm the trip', days=-9)
    triage.sync(later)
    gmail.leave_inbox('n2')  # the owner archived it in Gmail
    gmail.now = later = NOW + 17 * DAY
    triage.sync(later)
    assert states()['n2'] == 'gone'


def test_recent_mail_that_left_the_inbox_drops_off_and_comes_back_if_it_returns(gmail):
    week(gmail)
    triage.sort()
    gmail.leave_inbox('m2')
    triage.sort()
    assert states()['m2'] == 'gone' and 'm2' not in [r['id'] for r in triage.picture()['needs']]
    gmail.messages['m2']['labels'].append('INBOX')
    triage.sort()
    assert states()['m2'] == 'open'


def test_names_and_subject_lines_are_blanked_after_two_weeks_and_rows_removed_after_sixty_days(gmail, key):
    models = week(gmail)
    gmail.sent('s1', 'Quote for the fence', 4, 'Pat Kim <pat@example.net>', 'Could you please send the quote?')
    models.sort()
    assert triage.get('m1')['step'] and triage.picture()['asked'][0]['who'] == 'Pat Kim'
    gmail.now = later = NOW + 15 * DAY
    triage.sync(later)
    kept = json.dumps(triage._rows('1=1'))
    assert len(triage._rows('1=1')) == 11
    for words in ('Dana', 'Lunch', 'Reply to Dana', 'School Office', 'receipt'):
        assert words not in kept
    assert triage.picture(later)['asked'] == [] and today.gmail_triage() == []
    triage.sync(NOW + 61 * DAY)
    assert triage._rows('1=1') == []


def test_the_owners_unanswered_questions_are_followed_and_everything_else_sent_is_left_alone(gmail, key):
    store.set_ai_blocked('private@quiet.example', True)
    gmail.sent('s1', 'Quote for the fence', 4, 'Pat Kim <pat@example.net>', 'Could you please send the quote?', thread='ta')
    gmail.sent('s2', 'Picnic', 4, 'Dana Lee <dana@friends.example>, Pat Kim <pat@example.net>', 'Please bring a rug.', thread='tb')
    gmail.sent('s3', 'A private matter', 4, 'Quiet Person <private@quiet.example>', 'Please tell me what you think.', thread='tc')
    gmail.sent('s4', 'Dinner', 4, 'Dana Lee <dana@friends.example>', 'Please pick a day.', thread='td')
    gmail.add('a4', 'Dana Lee <dana@friends.example>', 'Re: Dinner', days=3, thread='td', box=None)  # answered, and archived since
    gmail.sent('s5', 'Tomorrow', 1, 'Pat Kim <pat@example.net>', 'Please call me.', thread='te')
    gmail.sent('s6', 'Thanks', 5, 'Pat Kim <pat@example.net>', 'Thanks, that is all sorted.', thread='tf')
    models = Models()
    assert models.sort()['status'] == 'ok'
    with triage.closing(triage.db()) as conn:
        asked = {r['thread_id']: r['state'] for r in conn.execute('select * from triage_asked')}
    assert asked == {'ta': 'waiting', 'tb': 'no', 'tc': 'no', 'td': 'answered', 'tf': 'no'}  # yesterday's is too recent to count
    assert [(r['questions'].keys() >= {'expects_reply'}, r['state']['subject']) for r in models.jev_requests] == [
        (True, 'Quote for the fence'), (True, 'Thanks')]  # never a group, and never someone AI is off for
    assert 'private matter' not in json.dumps(models.jev_requests) and '@' not in json.dumps(models.jev_requests)
    [waiting] = triage.picture()['asked']
    assert (waiting['who'], waiting['subject'], waiting['days']) == ('Pat Kim', 'Quote for the fence', 4)
    item = today_keys()['gmail-asked']
    assert (item['title'], item['why']) == ('No answer yet to an email you sent', 'To Pat Kim, 4 days ago.')
    page = shown(client.get('/gmail'))
    assert 'No answer yet' in page and 'Quote for the fence' in page and 'please send the quote' not in page

    # Nothing is asked again the same day; a day later the thread is looked at, and a reply settles it.
    gmail.reads.clear()
    models.sort()
    assert not gmail.read_paths('/gmail/thread') and len(models.jev_requests) == 2
    gmail.add('a1', 'Pat Kim <pat@example.net>', 'Re: Quote for the fence', days=-0.5, thread='ta', box=None)
    gmail.now = later = NOW + 21 * 3600
    models.sort(now=later)
    assert triage.picture(later)['asked'] == [] and len(models.jev_requests) == 2


def test_the_owner_can_stop_watching_a_question(gmail, key):
    gmail.sent('s1', 'Quote for the fence', 4, 'Pat Kim <pat@example.net>', 'Could you please send the quote?', thread='ta')
    Models().sort()
    assert client.post('/gmail/asked/dismiss', data={'thread_id': 'ta'}, follow_redirects=False).status_code == 403  # no Origin
    assert tap('/gmail/asked/dismiss', thread_id='ta') == '/gmail'
    assert triage.picture()['asked'] == [] and tap('/gmail/asked/dismiss', thread_id='ta') == '/gmail?said=no'


# --- records worth a look, and the owner's corrections ------------------------------------------------

def test_odd_records_surface_until_the_owner_says_they_look_fine(gmail):
    gmail.add('c1', 'Synthetic Shop <orders@shop.example>', 'Your receipt for the sofa', snippet='You paid $1,450.00')
    gmail.add('s1', 'Bank <alerts@bank.example>', 'Unusual sign-in to your account')
    receipts(gmail, 2)
    triage.sort()
    seen = triage.picture()
    assert [r['id'] for r in seen['odd']] == ['c1', 's1'] and [r['id'] for r in seen['kinds']['record']['open']] == ['r1', 'r2']
    item = today_keys()['gmail-odd']
    assert item['title'] == '2 records need a look' and item['why'].startswith('From Synthetic Shop and Bank.')
    page = shown(client.get('/gmail'))
    assert 'A charge of $1,000 or more.' in page and 'A security notice: check that this was you.' in page and '1,450' not in page
    trust('record', 'undo')
    assert filing.file('record') == {'status': 'filed', 'count': 2}  # the odd ones stay in the inbox

    assert tap('/gmail/ok', message_id='c1') == '/gmail' and tap('/gmail/ok', message_id='r1') == '/gmail?said=no'
    assert [r['id'] for r in triage.picture()['odd']] == ['s1'] and today_keys()['gmail-odd']['title'] == 'A security notice needs a look'
    assert filing.file('record') == {'status': 'filed', 'count': 1} and states()['c1'] == 'filed' and states()['s1'] == 'open'


def test_the_charge_limit_is_the_owners_to_set(gmail):
    assert triage.charge_limit() == 1000
    assert tap('/gmail/charge-limit', dollars='2000') == '/gmail?said=saved' and triage.charge_limit() == 2000
    assert tap('/gmail/charge-limit', dollars='0') == '/gmail?said=no' and triage.charge_limit() == 2000
    assert not triage.set_charge_limit(True) and not triage.set_charge_limit('50')
    gmail.add('c1', 'Synthetic Shop <orders@shop.example>', 'Your receipt for the sofa', snippet='You paid $1,450.00')
    triage.sort()
    assert triage.picture()['odd'] == []


def test_the_owner_can_correct_one_message_or_a_whole_sender(gmail):
    week(gmail)
    triage.sort()
    assert tap('/gmail/move', message_id='m7', kind='needs') == '/gmail?said=moved'
    row = triage.get('m7')
    assert (row['kind'], row['how'], row['checked']) == ('needs', 'owner', 1)
    assert tap('/gmail/move', message_id='m7', kind='bogus') == '/gmail?said=no' == tap('/gmail/move', message_id='nope', kind='noise')
    assert tap('/gmail/done', message_id='m7') == '/gmail' and states()['m7'] == 'done'
    assert tap('/gmail/done', message_id='m7') == '/gmail?said=no'

    # “Always” moves the sender's open mail now and decides their mail from then on, whatever the subject says.
    assert tap('/gmail/move', message_id='m3', kind='noise', always='1') == '/gmail?said=moved'
    assert triage.sender_kinds() == {'orders@shop.example': 'noise'} and kinds()['m4'] == 'noise'
    gmail.add('m12', 'Synthetic Shop <orders@shop.example>', 'Action required: payment failed')
    triage.sort()
    row = triage.get('m12')
    assert (row['kind'], row['how'], row['checked']) == ('noise', 'owner', 1)
    assert 'orders@shop.example' in shown(client.get('/gmail')) and 'always Promotions and notifications' in shown(client.get('/gmail'))

    assert tap('/gmail/always', sender='orders@shop.example', kind='ask') == '/gmail?said=saved' and triage.sender_kinds() == {}
    assert tap('/gmail/always', sender='not an address', kind='noise') == '/gmail?said=no'
    assert tap('/gmail/always', sender='orders@shop.example', kind='bogus') == '/gmail?said=no'
    gmail.add('m13', 'Synthetic Shop <orders@shop.example>', 'Your receipt for order 13')
    triage.sort()
    assert kinds()['m13'] == 'record'


# --- filing ------------------------------------------------------------------------------------------

def test_nothing_is_filed_until_the_owner_raises_that_kind(gmail):
    receipts(gmail, 3)
    triage.sort()
    assert {filing.trusted(kind) for kind in filing.LABELS} == {'suggest'}
    assert filing.file('record') == {'status': 'refused', 'count': 0} == filing.file('needs')
    assert tap('/gmail/file/record') == '/gmail?said=off' == tap('/gmail/file/needs')
    inbox.wait()
    assert not gmail.posts and set(states().values()) == {'open'}
    assert tap('/gmail/trust', kind='record', level='bogus') == '/gmail?said=no' == tap('/gmail/trust', kind='needs', level='undo')
    assert tap('/gmail/trust', kind='record', level='undo') == '/gmail?said=saved' and filing.trusted('record') == 'undo'
    assert filing.trusted('noise') == 'suggest'


def test_filing_labels_ready_mail_out_of_the_inbox_and_every_step_can_be_put_back(gmail):
    receipts(gmail, 3)
    gmail.messages['r2']['labels'].append('STARRED')
    gmail.add('n1', 'School Office <office@school.example>', 'Permission slip due Friday')
    gmail.add('v1', 'Bank <no-reply@bank.example>', 'Your verification code')
    gmail.add('v2', 'Bank <no-reply@bank.example>', 'Your sign-in link', days=3)
    triage.sort()
    trust('record', 'undo')
    trust('expires', 'undo')
    assert filing.file('record') == {'status': 'filed', 'count': 2}
    label = next(entry['id'] for entry in gmail.labels if entry['name'] == 'Hub/Records')
    assert gmail.posts == [('/gmail/labels/create', {'name': 'Hub/Records'}),
                           ('/gmail/labels/apply', {'message_ids': ['r1', 'r3'], 'add_label_ids': [label], 'remove_label_ids': ['INBOX']})]
    assert gmail.messages['r1']['labels'] == [label] and 'INBOX' in gmail.messages['r2']['labels']  # starred mail stays
    assert states() == {'r1': 'filed', 'r2': 'open', 'r3': 'filed', 'n1': 'open', 'v1': 'open', 'v2': 'open'}
    assert ('mail filed', '2 × Records') in [(a['action'], a['detail']) for a in store.recent_activity()]
    assert filing.file('record') == {'status': 'nothing', 'count': 0}

    # Codes wait two days in case they are still needed.
    assert filing.file('expires') == {'status': 'filed', 'count': 1} and states()['v1'] == 'open' and states()['v2'] == 'filed'

    # The label is made once; later batches reuse it.
    gmail.add('r4', 'Synthetic Shop <orders@shop.example>', 'Your receipt for order 4')
    triage.sort()
    assert filing.file('record') == {'status': 'filed', 'count': 1}
    assert [body['name'] for path, body in gmail.posts if path == '/gmail/labels/create'] == ['Hub/Records', 'Hub/Expired']
    assert triage.picture()['filed_today'] == 4
    item = today_keys()['gmail-filed']
    assert item['title'] == 'Hub filed 4 emails' and item['why'].startswith('3 records, 1 sign-in code.')
    assert 'filed' not in today.brief(list(today_keys().values()))  # told on the home page, not in the morning message

    # One batch back, then one message back; Hub leaves both alone afterwards.
    batches = [b for b in filing.batches() if b['kind'] == 'record']
    assert sorted(b['count'] for b in batches) == [1, 2]
    first = next(b['change_id'] for b in batches if b['count'] == 2)
    assert tap('/gmail/file/undo', change_id=first) == '/gmail?said=undone'
    assert gmail.messages['r1']['labels'] == ['INBOX'] and states()['r1'] == states()['r3'] == 'kept'
    assert tap('/gmail/file/undo', change_id=first) == '/gmail?said=gone'
    assert tap('/gmail/file/restore', message_id='r4') == '/gmail?said=restored'
    assert gmail.messages['r4']['labels'] == ['INBOX'] and states()['r4'] == 'kept'
    assert tap('/gmail/file/restore', message_id='r4') == '/gmail?said=gone'
    triage.sort()
    assert filing.file('record') == {'status': 'nothing', 'count': 0}


def test_filing_sends_at_most_fifty_messages_in_a_call(gmail):
    receipts(gmail, 120)
    triage.sort()
    trust('record', 'undo')
    assert filing.file('record') == {'status': 'filed', 'count': 120}
    assert [len(body['message_ids']) for body in gmail.applied()] == [50, 50, 20]
    assert len(filing.batches()) == 3


def test_a_refusal_changes_nothing_and_an_unconfirmed_change_stops_filing_until_the_owner_looks(gmail):
    receipts(gmail, 2)
    gmail.add('q1', 'Deals <deals@store.example>', '50% off everything', labels=['CATEGORY_PROMOTIONS'])
    triage.sort()
    trust('record', 'undo')
    trust('noise', 'undo')
    gmail.apply_result = bridge.BridgeError('refused', status=403)
    assert filing.file('record') == {'status': 'refused', 'count': 0}
    assert set(states().values()) == {'open'} and filing.waiting_review() == []

    gmail.apply_result = bridge.BridgeError('no answer')
    assert filing.file('record') == {'status': 'review', 'count': 0}
    assert states() == {'r1': 'review', 'r2': 'review', 'q1': 'open'} and triage.picture()['review'] == 2
    gmail.apply_result = None
    sent = len(gmail.posts)
    assert filing.file('noise') == {'status': 'review', 'count': 0} and len(gmail.posts) == sent  # never retried, nothing else filed
    item = today_keys()['gmail-review']
    assert item['title'] == 'Hub couldn’t confirm a filing change' and item['why'].startswith('2 emails may or may not')
    assert today.gmail_next() == []
    page = shown(client.get('/gmail'))
    assert 'Hub couldn’t confirm a filing change.' in page and 'I checked, carry on' in page
    assert home.gmail()['lines'][0]['label'] == 'A filing change needs checking'

    # The owner looked: the next sort sees what is really in the inbox before anything is filed again.
    assert tap('/gmail/file/checked') == '/gmail?said=checked'
    assert set(states().values()) == {'open'} and filing.ready('record') == []
    triage.sort()
    assert filing.file('record') == {'status': 'filed', 'count': 2}


def test_an_unconfirmed_change_with_an_id_can_be_undone_from_the_page(gmail):
    receipts(gmail, 2)
    triage.sort()
    trust('record', 'undo')
    gmail.apply_result = {'status': 'needs_review', 'change_id': 'chg-9', 'changed': 0}
    assert filing.file('record') == {'status': 'review', 'count': 0}
    assert 'Undo it' in shown(client.get('/gmail'))
    gmail.undo_result = bridge.BridgeError('refused', status=404)
    assert filing.undo('chg-9') == 'refused' and set(states().values()) == {'review'}
    gmail.undo_result = bridge.BridgeError('no answer')
    assert filing.undo('chg-9') == 'review' and set(states().values()) == {'review'}
    gmail.undo_result = {'undone': True}
    assert filing.undo('chg-9') == 'undone' and set(states().values()) == {'kept'} and triage.picture()['review'] == 0


def test_nothing_is_filed_while_changes_are_paused_or_gmail_is_down(gmail):
    receipts(gmail, 2)
    triage.sort()
    trust('record', 'auto')
    gmail.paused = True
    assert filing.file('record') == {'status': 'paused', 'count': 0}
    gmail.paused, gmail.down = False, True
    assert filing.file('record') == {'status': 'paused', 'count': 0}  # an unreachable helper counts as paused
    assert not gmail.posts and set(states().values()) == {'open'}


def test_filing_looks_at_the_inbox_again_when_its_picture_is_stale(gmail):
    receipts(gmail, 2)
    triage.sort()
    trust('record', 'undo')
    gmail.leave_inbox('r2')  # the owner archived it by hand since the last sort
    triage.put_setting('synced_at', triage.synced_at() - filing.SYNC_FRESH - 60)
    assert filing.file('record') == {'status': 'filed', 'count': 1}
    assert [body['message_ids'] for body in gmail.applied()] == [['r1']] and states()['r2'] == 'gone'


def test_mail_the_owner_moves_back_to_the_inbox_is_left_alone(gmail):
    receipts(gmail, 2)
    triage.sort()
    trust('record', 'undo')
    filing.file('record')
    gmail.messages['r1']['labels'].append('INBOX')
    triage.sync()
    assert states()['r1'] == 'filed'  # too soon after filing to be sure Gmail's search has caught up
    triage.sync(time.time() + triage.SETTLE_SECONDS + 60)
    assert states() == {'r1': 'kept', 'r2': 'filed'}
    assert filing.file('record')['status'] == 'nothing'


def test_the_file_button_works_behind_the_page_and_reports_once(gmail):
    receipts(gmail, 2)
    triage.sort()
    trust('record', 'undo')
    assert 'File 2 emails now' in shown(client.get('/gmail'))
    assert client.post('/gmail/file/record', follow_redirects=False).status_code == 403 and not gmail.posts  # no Origin
    assert tap('/gmail/file/record') == '/gmail'
    inbox.wait()
    page = shown(client.get('/gmail'))
    assert 'Filed 2 emails. Undo is under that kind below.' in page and 'Put all back' in page and 'Filed in the last 14 days (2)' in page
    assert 'Undo is under that kind' not in shown(client.get('/gmail'))


def test_at_just_do_it_the_morning_job_sorts_then_files_and_tells_the_owner(gmail):
    receipts(gmail, 2)
    gmail.add('n1', 'School Office <office@school.example>', 'Permission slip due Friday')
    trust('record', 'auto')
    response = client.post('/today/prepare', headers=LOCAL)
    assert response.status_code == 200 and response.json()['done'] == 0  # no note per kind; one line on the list instead
    assert states() == {'r1': 'filed', 'r2': 'filed', 'n1': 'open'} and len(gmail.applied()) == 1
    found = today_keys()
    assert found['gmail-filed']['title'] == 'Hub filed 2 emails' and found['gmail-needs']['title'] == '1 email needs you'
    assert today.brief(list(found.values())) == '1 thing for today:\n• 1 email needs you'
    assert client.post('/today/prepare', headers={**ORIGIN, 'tailscale-user-login': 'me@example.com'}).status_code == 403


def test_the_morning_job_files_nothing_at_tap_to_file(gmail):
    receipts(gmail, 2)
    trust('record', 'undo')
    assert client.post('/today/prepare', headers=LOCAL).status_code == 200
    assert not gmail.posts and set(states().values()) == {'open'}


# --- the offers on the home list ----------------------------------------------------------------------

def test_the_home_list_offers_filing_once_enough_sorted_mail_is_waiting(gmail):
    receipts(gmail, 9)
    triage.sort()
    assert today.gmail_next() == []  # not worth an offer yet
    gmail.add('r10', 'Synthetic Shop <orders@shop.example>', 'Your receipt for order 10')
    triage.sort()
    [offer] = today.gmail_next()
    assert (offer['key'], offer['title'], offer['href']) == ('gmail-next-filing', 'Hub can file 10 sorted emails for you', '/gmail')
    trust('record', 'undo')
    [offer] = today.gmail_next()
    assert (offer['key'], offer['title']) == ('gmail-next-file', '10 sorted emails are ready to file')
    gmail.paused = True
    assert today.gmail_next() == []
    gmail.paused = False
    today.hide('gmail-next-file')
    assert today.gmail_next() == []


# --- what the overnight review is given ---------------------------------------------------------------

def test_the_overnight_review_gets_the_sorted_picture_and_no_message_text(gmail, key):
    assert catalog.gmail_evidence() == {'inbox_total': 93012, 'inbox_unread': 16104}  # never sorted: the counts alone
    models = week(gmail)
    gmail.sent('s1', 'Quote for the fence', 4, 'Pat Kim <pat@example.net>', 'Could you please send the quote?')
    models.sort()
    store.set_ai_blocked('billing@utility.example', True)
    gmail.reads.clear()
    found = catalog.gmail_evidence()
    assert not gmail.read_paths('/gmail/message') and not gmail.read_paths('/gmail/search')
    assert found['sorted_today'] == {'Records': 2, 'Codes and sign-in links': 1, 'Reading': 2, 'Promotions and notifications': 1, 'Not sorted': 1}
    assert found['emails_that_need_the_owner'] == [
        {'ref': 'm1', 'sender': 'dana@friends.example', 'from': 'Dana Lee', 'subject': 'Lunch on Thursday?',
         'next_step': 'Reply to Dana about Thursday lunch', 'days_waiting': 0},
        {'ref': 'm2', 'sender': 'office@school.example', 'from': 'School Office', 'subject': 'Permission slip due Friday',
         'next_step': 'Return the signed slip by Friday', 'days_waiting': 0}]  # the sender AI is now off for is left out
    assert found['the_owner_wrote_and_got_no_answer'] == [{'to': 'Pat Kim', 'subject': 'Quote for the fence', 'days_ago': 4}]
    for text in gmail.texts.values():
        assert not text or text not in json.dumps(found)


# --- the Gmail page -----------------------------------------------------------------------------------

def test_the_page_before_any_sort_offers_one_and_reads_only_hubs_own_picture(gmail):
    response = client.get('/gmail')
    page = shown(response)
    assert response.status_code == 200 and 'Hub hasn’t sorted your inbox yet' in page and 'Sort my inbox' in page
    assert 'Sorted for you' not in page and 'Hub never sends or deletes mail, and filing never marks it read.' in page
    assert {path for path, _ in gmail.reads} == {'/gmail/profile'}

    assert client.post('/gmail/sort', follow_redirects=False).status_code == 403 and triage.synced_at() == 0  # no Origin
    gmail.add('n1', 'School Office <office@school.example>', 'Permission slip due Friday')
    assert tap('/gmail/sort') == '/gmail'
    triage.wait()
    page = shown(client.get('/gmail'))
    assert 'Sorted just now' in page and '1 email needs you' in page and 'Permission slip due Friday' in page


def test_the_page_shows_the_sorted_week_and_never_message_text(gmail, key):
    week(gmail).sort()
    gmail.reads.clear()
    response = client.get('/gmail')
    page = shown(response)
    assert {path for path, _ in gmail.reads} == {'/gmail/profile'}
    assert '3 emails need you' in page and 'Hub sorted 7 others today: 2 records, 2 newsletters, 1 sign-in code, 1 promotion, 1 unsorted.' in page
    assert 'Reply to Dana about Thursday lunch' in page and 'Return the signed slip by Friday' in page and '12345' not in page
    for heading in ('Needs you', 'Worth a look', 'Sorted for you', 'Records', 'Codes and sign-in links', 'Reading',
                    'Promotions and notifications', 'Calendar notices', 'Not sorted', 'Settings'):
        assert heading in page
    assert 'Leave in the inbox' in page and 'File 2 emails now' not in page  # nothing is offered for filing at “tell me”
    assert '/gmail/search?q=from%3Adana%40friends.example+newer_than%3A2d' in response.text
    for text in gmail.texts.values():
        assert not text or text not in page
    engine = shown(client.get('/engine'))
    assert 'Today: 2 of 6 sorting calls and 4 of 40 message checks used.' in engine and '2 sorting · 4 checks' in engine
    for name in ('File receipts, statements and confirmations', 'File newsletters', 'File sign-in codes and links'):
        assert name in engine


def test_untrusted_names_and_subjects_are_escaped_and_notices_come_only_from_the_fixed_list(gmail):
    gmail.add('x1', '"<b>Bold</b>" <pat@example.net>', '<script>alert(1)</script> please reply')
    triage.sort()
    body = client.get('/gmail').text
    assert '<script>alert' not in body and '<b>Bold' not in body and '&lt;script&gt;alert(1)' in body
    assert 'Saved.' in shown(client.get('/gmail?said=saved'))
    assert 'Filed 3 emails. Undo is under that kind below.' in shown(client.get('/gmail?said=filed&n=3'))
    injected = client.get('/gmail?said=%3Cscript%3Ex%3C/script%3E&n=5')
    assert injected.status_code == 200 and '<script>x' not in injected.text and 'role="status"' not in injected.text


def test_long_kinds_show_six_rows_until_asked_for_all(gmail):
    receipts(gmail, 8)
    triage.sort()
    page = shown(client.get('/gmail'))
    assert 'Show all 8' in page and 'Your receipt for order 6' in page and 'Your receipt for order 7' not in page
    page = shown(client.get('/gmail?show=record'))
    assert 'Your receipt for order 8' in page and 'Show all 8' not in page


def test_the_page_still_opens_when_gmail_cannot_be_reached(gmail):
    receipts(gmail, 2)
    triage.sort()
    trust('record', 'undo')
    gmail.down = True
    response = client.get('/gmail')
    assert response.status_code == 200 and 'The Gmail helper isn’t answering.' in shown(response) and 'maintenance pause' not in response.text
    assert re.search(r'<button class="btn primary" disabled>File 2 emails now', response.text)
    gmail.down, gmail.paused = False, True
    bridge.clear_cache()
    assert 'Changes are paused.' in shown(client.get('/gmail'))   # the maintenance marker keeps its own words


# --- the backlog --------------------------------------------------------------------------------------

@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, 'start', lambda: calls.append('start'))
    return calls


def test_the_backlog_page_previews_one_step_and_starts_it_when_nothing_else_is_planned(gmail, started):
    response = client.get('/gmail/backlog')
    page = shown(response)
    assert 'Clear the backlog: 1,234 messages' in page and 'Archive them' in page and 'Starred mail stays where it is.' in page
    assert '<script>x' not in response.text and not gmail.posts
    assert client.post('/gmail/backlog/start', follow_redirects=False).status_code == 403 and store.items() == []  # no Origin
    assert tap('/gmail/backlog/start') == '/gmail/plan'
    [item] = store.items()
    assert (item['decision'], item['sender'], item['status']) == ('sweep', actions.sweep_key(nightly.backlog_spec()), 'planned')
    assert started == ['start'] and not gmail.posts  # the plan's own worker does the archiving, with its undo
    assert tap('/gmail/backlog/start') == '/gmail/plan' and len(store.items()) == 1
    assert 'The backlog clear is in your plan' in shown(client.get('/gmail/backlog'))
    assert today.gmail_next() == []


def test_the_backlog_step_waits_in_the_plan_when_paused_or_when_other_changes_are_planned(gmail, started):
    gmail.paused = True
    assert 'Add to plan (changes are paused)' in shown(client.get('/gmail/backlog'))
    assert tap('/gmail/backlog/start') == '/gmail/plan' and started == [] and len(store.items()) == 1

    store.set_decision('news@loud.example', 'archive', 6)
    gmail.paused = False
    assert tap('/gmail/backlog/start') == '/gmail/plan' and started == []  # the owner applies the plan as a whole


# --- the owner's word against work in progress, and what a model is given ------------------------------

def test_no_model_is_given_an_email_address(gmail, key):
    models = week(gmail)
    gmail.add('a1', 'solo@friends.example', 'Can you call me back?',
              text='Please call me.\n\nOn Monday, Sam <sam@other.example> wrote:\n> see you then')
    gmail.add('a2', 'Clinic <desk@clinic.example>', 'Action required: return your form',
              text='Please send the form to desk@clinic.example by Monday.')
    models.place['Can you call me back?'] = ('needs', True, 'reading')
    models.steps.update({'Can you call me back?': 'Call them back', 'Action required: return your form': 'Send the form by Monday'})
    models.sort()
    sent = json.dumps(models.luna_requests) + json.dumps(models.jev_requests)
    assert '@' not in sent and sent.count('[address]') >= 3  # the quoted reply for Luna; the clinic's for Jev and for Luna

    found = catalog.gmail_evidence()
    emails = {m['sender']: m for m in found['emails_that_need_the_owner']}
    assert emails['solo@friends.example']['from'] == 'friends.example'  # no name on the message: the domain, not the address
    request = overnight.build_request({'gmail': found}, [], home.today().isoformat())
    assert '@' not in request['messages'][1]['content'] and 'friends.example' in request['messages'][1]['content']
    href, cta = overnight._link('gmail', emails['solo@friends.example']['ref'], {'gmail': found})
    assert 'solo%40friends.example' in href and cta == 'Find the message'  # the address stays in Hub, for its own link


def test_a_models_answer_never_overrules_what_the_owner_said_meanwhile(gmail, key):
    models = week(gmail)

    def luna_during_a_tap(request):
        if request['response_format']['json_schema']['name'] == 'sorted_mail':
            assert triage.move('m10', 'reading')  # the owner's tap lands while Luna is still thinking
        return models.luna(request)
    triage.sort(luna_during_a_tap, models.jev)
    row = triage.get('m10')
    assert (row['kind'], row['how']) == ('reading', 'owner') and 'm10' not in [r['id'] for r in triage.picture()['needs']]


def test_mail_the_owner_moves_while_hub_is_filing_stays_in_the_inbox(gmail, monkeypatch):
    receipts(gmail, 2)
    triage.sort()
    trust('record', 'undo')
    label = filing._label

    def label_during_a_tap(name):
        assert triage.move('r1', 'needs')  # between Hub's look at what is ready and its change
        return label(name)
    monkeypatch.setattr(filing, '_label', label_during_a_tap)
    assert filing.file('record') == {'status': 'filed', 'count': 1}
    assert [body['message_ids'] for body in gmail.applied()] == [['r2']]
    assert (states()['r1'], kinds()['r1']) == ('open', 'needs')


def test_a_put_back_hub_could_not_confirm_is_never_filed_again(gmail):
    receipts(gmail, 2)
    triage.sort()
    trust('record', 'undo')
    filing.file('record')
    [batch] = filing.batches()
    gmail.undo_result = bridge.BridgeError('no answer')
    assert filing.undo(batch['change_id']) == 'review' and set(states().values()) == {'review'}
    gmail.undo_result = None
    gmail.messages['r1']['labels'].append('INBOX')  # as it turns out, one did come back
    assert tap('/gmail/file/checked') == '/gmail?said=checked'
    assert set(states().values()) == {'filed'} and filing.ready('record') == []
    triage.sync(time.time() + triage.SETTLE_SECONDS + 60)
    assert states() == {'r1': 'kept', 'r2': 'filed'}
    assert filing.file('record')['status'] == 'nothing' and len(gmail.applied()) == 1
    assert filing.undo(batch['change_id']) == 'undone' and states() == {'r1': 'kept', 'r2': 'kept'}  # the other can still be put back


def test_a_refusal_part_way_says_what_was_already_filed(gmail, monkeypatch):
    receipts(gmail, 60)
    triage.sort()
    trust('record', 'undo')

    def second_call_refused(path, body):
        if path == '/gmail/labels/apply' and len(gmail.applied()) == 1:
            raise bridge.BridgeError('refused', status=403)
        return gmail.post(path, body)
    monkeypatch.setattr(bridge, 'post', second_call_refused)
    assert filing.file('record') == {'status': 'part', 'count': 50}
    assert sorted(states().values()) == ['filed'] * 50 + ['open'] * 10 and filing.waiting_review() == []
    assert inbox._notice('part', 50)['text'].startswith('Filed 50 emails, then the Gmail helper refused the rest.')


def test_mail_in_the_middle_of_being_filed_is_not_reported_as_in_doubt(gmail, monkeypatch):
    receipts(gmail, 2)
    triage.sort()
    filing._mark(['r1'], state='filing')
    monkeypatch.setattr(inbox, 'filing_now', lambda: True)
    page = shown(client.get('/gmail'))
    assert 'Filing…' in page and 'couldn’t confirm a filing change' not in page
    monkeypatch.setattr(inbox, 'filing_now', lambda: False)  # the run was cut short: now it is in doubt
    assert 'Hub couldn’t confirm a filing change.' in shown(client.get('/gmail'))


def test_an_unexpected_failure_still_counts_the_calls_already_made(gmail, key, monkeypatch):
    models, day = week(gmail), home.today().isoformat()

    def broken(*args):
        raise ValueError('synthetic failure')
    monkeypatch.setattr(triage, '_steps', broken)
    assert models.sort()['status'] == 'error'
    last = state.last_run(day, triage.KIND)
    assert (last['status'], last['luna_calls'], last['jev_calls'], last['cost']) == ('error', 1, 4, USAGE['cost'])
    assert kinds()['m4'] == 'record' and triage._lock.acquire(blocking=False)  # what was placed stands; the lock is free
    triage._lock.release()


def test_senders_with_ai_off_never_use_up_a_runs_places(gmail, key, monkeypatch):
    monkeypatch.setattr(triage, 'GATE_PER_RUN', 2)
    store.set_ai_blocked('private@quiet.example', True)
    for n in range(1, 4):
        gmail.add(f'p{n}', 'Quiet Person <private@quiet.example>', f'Action required: form {n}', text='Please sign.')
    gmail.add('s1', 'School Office <office@school.example>', 'Action required: permission slip', text='Please sign and return the slip.')
    models = Models(steps={'Action required: permission slip': 'Return the signed slip'})
    models.sort()
    assert [p['message_id'] for p in gmail.read_paths('/gmail/message')] == ['s1']
    assert triage.get('s1')['step'] == 'Return the signed slip' and len(models.jev_requests) == 1


def test_a_step_waits_for_the_next_run_when_the_message_cannot_be_read(gmail, key, monkeypatch):
    gmail.add('s1', 'School Office <office@school.example>', 'Action required: permission slip', text='Please sign and return the slip.')
    models = Models(steps={'Action required: permission slip': 'Return the signed slip'})
    triage.sync()
    triage._set('s1', checked=1)  # checked on an earlier run; its text was not kept
    unreadable, read = {'now': True}, triage._text
    monkeypatch.setattr(triage, '_text', lambda message_id: None if unreadable['now'] else read(message_id))
    models.sort()
    assert triage.get('s1')['step'] is None and not models.luna_requests
    unreadable['now'] = False
    models.sort()
    assert triage.get('s1')['step'] == 'Return the signed slip'


def test_a_date_without_a_zone_is_utc_and_a_date_far_from_this_week_is_not_believed(gmail):
    assert triage._when('Mon, 02 Nov 2026 10:00:00 -0000') == triage._when('Mon, 02 Nov 2026 10:00:00 +0000')
    receipts(gmail, 2)
    gmail.messages['r1']['date'] = formatdate(NOW - 400 * DAY, usegmt=True)
    gmail.messages['r2']['date'] = formatdate(NOW + 30 * DAY, usegmt=True)
    triage.sort()
    triage.sort()
    assert states() == {'r1': 'old', 'r2': 'open'}  # kept, not added and removed on every sort
    assert time.time() - 8 * DAY < triage.get('r1')['received'] and triage.get('r2')['received'] <= time.time()


@pytest.mark.parametrize('answer', [ValueError('unreadable reply'), ['not', 'a', 'dict']])
def test_an_unexpected_reply_to_filing_waits_for_the_owner_instead_of_saying_nothing_moved(gmail, answer, monkeypatch):
    receipts(gmail, 2)
    triage.sort()
    trust('record', 'undo')
    def post(path, body):
        if path != '/gmail/labels/apply':
            return gmail.post(path, body)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(bridge, 'post', post)
    assert filing.file('record') == {'status': 'review', 'count': 0}
    assert set(states().values()) == {'review'}


def test_an_unreadable_bridge_answer_is_an_unknown_bridge_error(monkeypatch):
    class Reply:
        status_code = 200
        def json(self):
            raise ValueError('not json')
    monkeypatch.setattr(bridge.httpx, 'request', lambda *a, **k: Reply())
    monkeypatch.setattr(bridge.config, 'BRIDGE_TOKEN_FILE', type('T', (), {'read_text': lambda self: 'x'})())
    with pytest.raises(bridge.BridgeError) as caught:
        bridge._request('POST', '/gmail/labels/apply', body={})
    assert caught.value.status is None   # never one of filing's “refused” codes
