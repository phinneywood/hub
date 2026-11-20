"""Errands (hub/app/errands/): what Hub gets ready from the mail and the class guide, what Luna is told, the taps that
finish an errand, and the proof that closes one by itself. On a made-up week (tests/errands_world.py) and a made-up
Kitchen (tests/kitchen_world.py): no mailbox, calendar, model, shop or kitchen service is reached."""
import json
import re
import threading
from contextlib import closing
from datetime import date, datetime, time as clock, timedelta

import pytest
from fastapi.testclient import TestClient

from hub import config, home, luna, people, store as hub_store
from hub.app import today as screen
from hub.app.ask import private, route
from hub.app.errands import asked, draft, links, picture, read, store, taps, watch
from hub.engine import catalog, overnight, state
from hub.golden import dryrun
from hub.main import app
from hub.tools.gmail import triage
from tests import app_world, ask_script, errands_world, kitchen_world
from tests.errands_world import HOST, NOW

client = TestClient(app)
ME = 'me@example.com'                # the owner: Hub reads their mail (the bridge's main account)
PARTNER = 'casey@example.com'        # the other person at home, whose own mail Hub doesn't read
SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': ME}
AS_PARTNER = {'origin': 'http://testserver', 'tailscale-user-login': PARTNER}
ON_THIS_MAC = {'origin': 'http://testserver'}
ADDRESS = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
MAILBOX = 'mailbox.example'          # stands for a mailbox anyone can have (gmail.com and the like)


@pytest.fixture
def week(monkeypatch):
    """The made-up week, not read yet."""
    return errands_world.install(monkeypatch)


def household(value):
    (config.DATA_DIR / 'household.json').write_text(json.dumps(value))


@pytest.fixture
def two(monkeypatch):
    """Two people at home, as household.json says (hub/people.py): the owner, whose Google account Hub reads, and a
    partner whose account it doesn't read yet."""
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ME, PARTNER})
    household({'people': [{'name': 'Alex', 'logins': [ME], 'accounts': [people.DEFAULT]},
                          {'name': 'Casey', 'logins': [PARTNER], 'accounts': []}]})


@pytest.fixture
def ready(week):
    """The made-up week, read once as the morning job would."""
    found = read.run(transport=week.luna)
    assert found['status'] == 'ok', found
    return week


def by_title(words):
    return next(e for e in store.rows() if words in e['title'])


def errands(headers=SIGNED_IN):
    """The Errands screen, as the owner's phone (or another device) sees it."""
    return client.get('/app/api/errands', headers=headers).json()


def tap(errand, do, headers=SIGNED_IN, **body):
    return client.post(f'/app/api/errands/{errand["id"]}/{do}', json=body, headers=headers)


def sorted_mail(message_id, sender, subject, received, kind='record', state='filed'):
    """One more email, as Hub's sort left it."""
    with closing(triage.db()) as conn, conn:
        conn.execute('insert into triage_messages(id, thread_id, sender, name, subject, kind, how, checked, received, seen_at,'
                     ' sorted_day, state) values (?,?,?,?,?,?,?,?,?,?,?,?)',
                     (message_id, 't-' + message_id, sender, '', subject, kind, 'rules', 1, received, received,
                      NOW.date().isoformat(), state))


# ---- the read: what is read, what Luna is told, and the limits ----

def test_the_morning_read_gets_errands_ready_from_the_mail_and_the_class_guide(week):
    assert read.run(transport=week.luna) == {'status': 'ok', 'read': 8, 'made': 7, 'skipped': 1, 'unusable': 0}
    made = {e['title']: (e['kind'], e['source'], e['state']) for e in store.rows() if e['state'] != 'skipped'}
    assert made == {
        'RSVP to Jordan’s birthday party': ('rsvp', 'mail', 'ready'),
        'Pay the Lakeside Power bill': ('pay', 'mail', 'ready'),
        'Book a dental cleaning': ('book', 'mail', 'ready'),
        'Water bill pays itself': ('pay', 'mail', 'done'),                  # paid automatically: only told
        'Answer Sam’s invitation to parent coffee': ('rsvp', 'mail', 'ready'),
        'Get a small pumpkin for Friday': ('buy', 'school', 'ready'),
        'Return the photo order form': ('sign', 'school', 'ready'),
    }
    assert len(week.asked) == 2                     # six emails in one call, the class guide's two to-dos in another
    assert 'e-quiet' not in week.read               # the owner switched AI off for that sender
    assert 'e-receipt' not in week.read             # a receipt needs nothing
    news = store.rows("key='mail:e-news'")[0]
    assert (news['state'], news['title'], news['subject']) == ('skipped', '', None)     # nothing needed: nothing kept
    assert read.run(transport=week.luna)['read'] == 0 and len(week.asked) == 2          # each item is read once
    assert state.runs(read.KIND)[0]['luna_calls'] == 0 and state.runs(read.KIND)[1]['luna_calls'] == 2


def test_luna_sees_no_address_link_or_sender_switched_off_on_its_private_route(ready):
    request = ready.asked[0]
    assert (request['model'], request['provider'], request['stream']) == (luna.MODEL, luna.PROVIDER, False)
    assert request['response_format']['json_schema']['strict'] is True
    told = json.dumps([r['messages'] for r in ready.asked], ensure_ascii=False)
    assert not ADDRESS.search(told) and 'http' not in told and 'acct=' not in told
    assert 'recipe' not in told and 'Robin' not in told
    data = json.loads(request['messages'][1]['content'].removeprefix('<data>').removesuffix('</data>'))
    bill = next(item for item in data['items'] if 'Lakeside' in item['subject'])
    assert bill['from'] == 'Lakeside Power' and '[link]' in bill['text']
    assert bill['links'] == [{'ref': 'L1', 'label': 'Pay now', 'site': 'lakesidepower.example'}]     # not the mailer's, not unsubscribe
    party = next(item for item in data['items'] if 'Jordan' in item['subject'])
    assert party['from'] == 'Jamie Chen' and '[address]' in party['text']
    assert data['free_times'] == ['Tuesday November 17, 1 to 5 pm', 'Wednesday November 18, 9 am to noon',
                                  'Thursday November 19, 9 am to noon', 'Friday November 20, 9 am to noon']   # the standup is Tuesday at 9
    guide = json.loads(ready.asked[1]['messages'][1]['content'].removeprefix('<data>').removesuffix('</data>'))
    assert [(i['from'], i['title'], i['date']) for i in guide['items']] == [
        ('the class guide', 'Return the photo order form', '2026-11-18'), ('the class guide', 'Bring a small pumpkin', '2026-11-20')]
    assert guide['free_times'] == []          # the household's errands are drafted apart, with nothing from the owner's calendar


def test_a_read_keeps_to_the_days_calls(week):
    state.record_run(NOW.date().isoformat(), read.KIND, 'ok', luna_calls=read.LUNA_CALLS_PER_DAY)
    assert read.run(transport=week.luna) == {'status': 'limit', 'made': 0, 'skipped': 0, 'unusable': 0}
    assert not week.asked and not week.read


def test_a_read_keeps_to_the_months_money(week):
    state.record_run(NOW.date().isoformat(), 'overnight', 'ok', cost=overnight.MONTH_COST_CAP)
    assert read.run(transport=week.luna)['status'] == 'limit' and not week.asked


def test_an_answer_that_cant_be_used_is_tried_twice_then_left(week):
    week.answers['cleaning'] = {'kind': 'pay me', 'title': 'Pay'}
    read.run(transport=week.luna)
    dentist = (store.MAIN, 'mail:e-dentist')
    assert dentist not in store.keys() and store.tries()[dentist] == 1
    read.run(transport=week.luna)
    assert store.tries()[dentist] == 2
    week.read.clear()
    read.run(transport=week.luna)
    assert 'e-dentist' not in week.read


def test_without_its_model_nothing_is_read_and_without_the_helper_the_class_guide_still_is(week, monkeypatch):
    monkeypatch.setattr(luna, 'configured', lambda: False)
    assert read.run(transport=week.luna) == {'status': 'not_set_up'} and not week.read
    assert errands()['set_up'] is False
    monkeypatch.setattr(luna, 'configured', lambda: True)
    week.down = True
    assert read.run(transport=week.luna)['status'] == 'error'
    seen = errands()
    assert seen['looked']['failed'] is True
    assert {e['title'] for e in seen['ready']} == {'Return the photo order form', 'Get a small pumpkin for Friday'}


def test_the_morning_job_reads_errands_after_the_mail_is_sorted():
    slugs = [source.slug for source in catalog.SOURCES]
    assert slugs.index('gmail') < slugs.index('preschool') < slugs.index('errands')
    errands_source = next(source for source in catalog.SOURCES if source.slug == 'errands')
    assert errands_source.sort is catalog.errands_morning and [l.slug for l in errands_source.lookups] == ['errands']
    assert (catalog.action('errands.ready').ceiling, catalog.action('errands.list').default) == ('suggest', 'undo')


# ---- the drafting: only what the email says is kept ----

def candidate(text, subject='Your bill', sender='billing@news.example.com', reply_to='', found_links=(), sorted_kind='needs'):
    return {'key': 'mail:x', 'source': 'mail', 'sorted': sorted_kind, 'sender': sender, 'from': 'Example', 'reply_to': reply_to,
            'subject': subject, 'received': NOW.date(), 'text': text, 'said': subject + '\n' + text, 'links': list(found_links),
            'message': None}


def answer(**parts):
    return {'kind': 'pay', 'title': 'Pay the bill', 'why': 'It is due.', 'due': None, 'amount': None, 'autopay': False,
            'event': None, 'quote': '', 'choices': [], **parts}


def choice(**parts):
    return {'label': 'Do it', 'reply': None, 'link': None, 'calendar': False, 'call': None, 'buy': [], **parts}


PAY = {'ref': 'L1', 'label': 'Pay now', 'url': 'https://pay.example.com/b', 'host': 'pay.example.com', 'site': 'example.com'}
BILL = 'Amount due: $84.20. Due date: November 28, 2026. Questions? Call (555) 555-0101.'


def test_an_amount_a_date_a_quote_a_number_and_a_link_are_kept_only_when_the_email_has_them(monkeypatch):
    monkeypatch.setattr(links, 'PERSONAL', links.PERSONAL | {MAILBOX})
    made = draft.check(answer(amount=84.2, due='2026-11-28', quote='Amount due: $84.20.', choices=[
        choice(label='Pay the bill', link='L1'), choice(label='Call them', call='(555) 555-0101')]),
        candidate(BILL, found_links=[PAY]), NOW.date())
    assert (made['facts']['amount'], made['due'], made['quote']) == (84.2, '2026-11-28', 'Amount due: $84.20.')
    assert [(c['label'], (c['link'] or {}).get('url'), c['call']) for c in made['choices']] == [
        ('Pay the bill', 'https://pay.example.com/b', None), ('Call them', None, '5555550101')]
    made = draft.check(answer(amount=99.0, due='2026-11-29', quote='Pay $99 today please.', choices=[
        choice(label='Pay the bill', link='L7'), choice(label='Call them', call='(555) 555-0199')]),
        candidate(BILL, found_links=[PAY]), NOW.date())
    assert (made['facts']['amount'], made['due'], made['quote']) == (None, None, '')
    # neither way survived: a bill is still paid on the sender's own site
    assert [(c['label'], c['link']['url']) for c in made['choices']] == [('Pay the bill', 'https://example.com/')]
    personal = draft.check(answer(), candidate(BILL, sender=f'alex@{MAILBOX}'), NOW.date())
    assert personal['choices'] == []                    # a personal mailbox has no site to pay on


def test_a_drafted_reply_holds_no_link_address_or_long_number_and_needs_someone_to_read_it():
    asked = candidate('Can you come on Saturday?', subject='Party', sender='alex@example.com', reply_to='alex@example.com')

    def drafted(text, to='alex@example.com'):
        made = draft.check(answer(kind='reply', title='Answer Alex', choices=[choice(label='Reply', reply=text)]),
                           dict(asked, reply_to=to), NOW.date())
        return made['choices'][0]['reply'] if made['choices'] else None

    assert drafted('Hi Alex!\n\n\n\nYes, we can.\n\nThanks!') == 'Hi Alex!\n\nYes, we can.\n\nThanks!'
    assert drafted('See https://example.com for more') is None
    assert drafted('Write to sam@example.com instead') is None
    assert drafted('Call me on 555 555 0101') is None
    assert drafted('Yes!', to='') is None               # nobody reads replies there


def test_yes_to_an_invitation_puts_it_on_the_calendar_unless_the_calendar_sent_it():
    party = candidate('A party at Pine Park on Saturday, November 21 from 2-4pm.', subject='Party', sender='alex@example.com',
                      reply_to='alex@example.com')
    event = {'title': 'Party', 'date': '2026-11-21', 'start': '14:00', 'end': '16:00', 'place': 'Pine Park'}
    said = answer(kind='rsvp', title='RSVP to the party', event=event, choices=[
        choice(label='No, sorry', reply='Hi Alex, sorry, we can’t make it.'), choice(label='Yes!', reply='Hi Alex, we’ll come!')])
    made = draft.check(said, party, NOW.date())
    assert made['facts']['event'] == event
    assert [(c['label'], c['calendar']) for c in made['choices']] == [('No, sorry', False), ('Yes!', True)]
    assert not any(c['calendar'] for c in draft.check(said, dict(party, sorted='calendar'), NOW.date())['choices'])
    wrong = draft.check(dict(said, event=dict(event, date='2026-11-22')), party, NOW.date())
    assert wrong['facts']['event'] is None              # a day the email doesn't name is left out
    late = draft.check(dict(said, event=dict(event, start='15:00', end='17:00')), party, NOW.date())
    assert (late['facts']['event']['start'], late['facts']['event']['end']) == (None, None)


@pytest.mark.parametrize('text, day, named', [
    ('Due Saturday, November 21.', date(2026, 11, 21), True),
    ('due 11/21/2026', date(2026, 11, 21), True),
    ('by the 21st of November', date(2026, 11, 21), True),
    ('see you Saturday', date(2026, 11, 21), True),             # within a week of the email
    ('see you next Saturday', date(2026, 11, 28), True),
    ('see you tomorrow', date(2026, 11, 17), True),
    ('see you Saturday', date(2026, 11, 28), False),
    ('due November 2', date(2026, 11, 21), False),
])
def test_a_date_counts_only_when_the_email_names_it(text, day, named):
    assert draft.date_in(day, text, received=date(2026, 11, 16)) is named


@pytest.mark.parametrize('text, at, given', [
    ('from 2-4pm', '14:00', True), ('from 2-4pm', '16:00', True), ('at 2:30 PM', '14:30', True), ('at noon', '12:00', True),
    ('at 09:30', '09:30', True), ('at 14:00', '14:00', True), ('at 2 in the afternoon', '14:00', True),
    ('at 2 am', '14:00', False), ('room 2', '14:00', False),
])
def test_a_time_counts_only_when_the_email_gives_it(text, at, given):
    assert draft.time_in(clock.fromisoformat(at), text) is given


# ---- the addresses a tap opens ----

def test_only_links_on_the_senders_own_site_are_offered(monkeypatch):
    monkeypatch.setattr(links, 'PERSONAL', links.PERSONAL | {MAILBOX})
    sender = 'billing@news.example.com'
    assert links.offered('https://pay.example.com/bill?a=1', sender)['host'] == 'pay.example.com'
    for url in ('http://pay.example.com/', 'https://pay.example.org/', 'https://user:pw@pay.example.com/',
                'https://pay.example.com:8443/', 'https://xn--80ak6aa92e.example.com/', 'https://192.168.1.10/',
                'https://pay.example.com/a b', 'javascript:alert(1)'):
        assert links.offered(url, sender) is None, url
    assert links.offered(f'https://pay.{MAILBOX}/', f'alex@{MAILBOX}') is None           # a personal mailbox has no site
    found = links.links({'links': [{'label': 'Pay now', 'url': 'https://pay.example.com/b'},
                                   {'label': 'View your bill', 'url': 'https://click.mailer.example/t'},
                                   {'label': 'Unsubscribe', 'url': 'https://example.com/unsubscribe'},
                                   {'label': '', 'url': 'https://example.com/x'}]}, sender)
    assert [(l['ref'], l['label'], l['url']) for l in found] == [('L1', 'Pay now', 'https://pay.example.com/b')]
    assert (links.site('www.example.co.uk'), links.site('a.b.district.k12.wa.us')) == ('example.co.uk', 'district.k12.wa.us')


def test_a_reply_goes_where_someone_reads_it(monkeypatch):
    monkeypatch.setattr(links, 'PERSONAL', links.PERSONAL | {MAILBOX})
    assert links.answer_to({'reply_to': 'Help <help@example.com>'}, 'billing@news.example.com') == 'help@example.com'
    assert links.answer_to({'reply_to': 'someone@elsewhere.example'}, 'billing@news.example.com') == 'billing@news.example.com'
    assert links.answer_to({'reply_to': 'sam@elsewhere.example'}, f'alex@{MAILBOX}') == 'sam@elsewhere.example'   # a person's own
    assert links.answer_to({}, 'no-reply@example.com') == ''
    assert links.answer_to({}, 'calendar-notification@google.com') == ''


def test_each_address_a_tap_opens_is_filled_in():
    assert links.mailto('alex@example.com', 'Re: Party', 'Hi!\nYes.') == 'mailto:alex@example.com?subject=Re%3A%20Party&body=Hi%21%0D%0AYes.'
    assert links.gmail('alex@example.com', 'Re: Party', 'Hi!', 'me@example.com') == (
        'https://mail.google.com/mail/?authuser=me%40example.com&view=cm&fs=1&to=alex%40example.com&su=Re%3A%20Party&body=Hi%21')
    event = {'title': 'Party', 'date': '2026-11-21', 'start': '14:00', 'end': '16:00', 'place': 'Pine Park'}
    assert links.calendar(event, 'RSVP by Thursday', 'me@example.com') == (
        'https://calendar.google.com/calendar/render?action=TEMPLATE&text=Party&dates=20261121T140000%2F20261121T160000'
        '&ctz=America%2FLos_Angeles&location=Pine%20Park&details=RSVP%20by%20Thursday&authuser=me%40example.com')
    assert 'dates=20261121%2F20261122&' in links.calendar({'title': 'Photo day', 'date': '2026-11-21'})     # all day
    assert (links.call('5555550142'), links.subject('Party'), links.subject('Re: Party')) == ('tel:+15555550142', 'Re: Party', 'Re: Party')


# ---- the screen ----

def test_the_screen_shows_the_most_pressing_first_with_what_the_email_said(ready):
    seen = errands()
    assert [e['title'] for e in seen['ready']] == [
        'Answer Sam’s invitation to parent coffee', 'Return the photo order form', 'RSVP to Jordan’s birthday party',
        'Get a small pumpkin for Friday', 'Pay the Lakeside Power bill', 'Book a dental cleaning']
    coffee, form, party, pumpkin, bill, dentist = seen['ready']
    assert (party['name'], party['from'], party['due'], party['facts']) == (
        'Invitation', 'Jamie Chen', {'text': 'Due Thursday', 'tone': ''}, ['Sat, Nov 21, 2–4 pm', 'Pine Park'])
    assert party['quote'] == 'Please let me know by Thursday, November 19 if you can make it!'
    assert [(w['label'], [s['kind'] for s in w['steps']]) for w in party['ways']] == [
        ('Yes, we’ll come', ['mail', 'calendar']), ('Can’t make it', ['mail'])]
    reply, calendar = party['ways'][0]['steps']
    assert (reply['label'], reply['go']) == ('Reply to Jamie Chen', 'Open in your mail app')
    assert reply['href'].startswith('mailto:jamie@example.com?subject=Re%3A%20Jordan%E2%80%99s%20birthday%20party&body=Hi%20Jamie%21')
    assert calendar['href'].startswith('https://calendar.google.com/calendar/render?action=TEMPLATE&text=Jordan%E2%80%99s'
                                       '%20birthday%20party&dates=20261121T140000%2F20261121T160000')
    assert party['email'] == {'href': 'https://mail.google.com/mail/?authuser=me@example.com#all/t-e-party', 'label': 'Open the email'}
    assert coffee['from'] == 'Sam Lee' and [s['href'] for w in coffee['ways'] for s in w['steps']] == [
        'https://calendar.google.com/calendar/event?action=RESPOND&eid=abc&rst=1',
        'https://calendar.google.com/calendar/event?action=RESPOND&eid=abc&rst=2']
    assert (form['from'], form['family'], form['ways'], form['email']['href']) == ('Class guide', True, [], '/preschool/')
    assert [s['label'] for s in pumpkin['ways'][0]['steps']] == ['Add to the shopping list']
    assert (bill['facts'], bill['due']['text'], bill['ways'][0]['steps'][0]['href']) == (
        ['$84.20'], 'Due Nov 28', 'https://pay.lakesidepower.example/bill?acct=1001')
    assert dentist['due'] is None and dentist['ways'][0]['steps'][0]['detail'] == (
        'Hello,\n\nI’d like to book a cleaning. Tuesday November 17, 1 to 5 pm or Wednesday November 18, 9 am to noon '
        'would work well.\n\nThanks!')
    assert dentist['ways'][1]['steps'][0]['href'] == 'tel:+15555550142'
    assert [(e['title'], e['ended']['text']) for e in seen['done']] == [('Water bill pays itself', 'Paid automatically')]
    assert (seen['set_up'], seen['compose'], seen['looked']) == (True, 'mail', {'text': 'Looked just now', 'failed': False})


def test_replies_can_open_in_gmail_on_the_web(ready):
    said = client.post('/app/api/errands/settings', json={'compose': 'gmail'}, headers=SIGNED_IN).json()['said']
    assert said == 'Replies open in Gmail'
    party = next(e for e in errands()['ready'] if 'Jordan' in e['title'])
    reply = party['ways'][1]['steps'][0]
    assert reply['href'].startswith('https://mail.google.com/mail/?authuser=me%40example.com&view=cm&fs=1&to=jamie%40example.com')
    assert reply['go'] == 'Open in Gmail'
    assert client.post('/app/api/errands/settings', json={'compose': 'fax'}, headers=SIGNED_IN).status_code == 400


# ---- the taps ----

def test_opening_a_step_is_noted_and_the_card_says_what_hub_waits_for(ready):
    party = by_title('Jordan')
    found = tap(party, 'go', choice=1, step='mail').json()
    assert (found['ok'], found['said'], found['card']['state']) == (True, '', 'opened')
    assert found['card']['opened'] == {'label': 'Opened just now', 'watch': 'Hub closes this when it sees your reply.', 'way': 1, 'left': 0}
    assert [s['taken'] for s in found['card']['ways'][1]['steps']] == [True]
    assert tap(party, 'go', choice=0, step='list').json()['error'] == 'That step isn’t part of it.'
    assert tap(party, 'go', choice=7, step='mail').json()['error'] == 'That choice isn’t there any more.'
    assert client.post('/app/api/errands/999/done', json={}, headers=SIGNED_IN).json()['error'] == 'That errand isn’t there any more.'


def test_the_shopping_list_is_the_one_thing_hub_does_and_undo_takes_it_back(ready, monkeypatch):
    kitchen = kitchen_world.install(monkeypatch)
    pumpkin = by_title('pumpkin')
    found = tap(pumpkin, 'list', choice=0).json()
    assert (found['said'], found['undo'], found['card']['state'], found['card']['ended']['text']) == (
        'Small pumpkin is on the list', True, 'done', 'On the shopping list')
    assert found['card']['ended']['seen'] is False      # finished by a tap, not seen by Hub
    assert 'small pumpkin' in [item['name'] for item in kitchen.items]
    assert tap(pumpkin, 'back').json()['said'] == 'Put back'
    assert 'small pumpkin' not in [item['name'] for item in kitchen.items]
    assert store.get(pumpkin['id'])['state'] == 'ready'


def test_the_shopping_list_follows_the_engine_page(ready, monkeypatch):
    kitchen = kitchen_world.install(monkeypatch)
    state.set_trust(catalog.action(taps.LIST_ACTION), 'suggest')
    pumpkin = by_title('pumpkin')
    assert tap(pumpkin, 'list', choice=0).json()['error'] == 'Adding to the list from errands is off on the Engine page.'
    assert 'small pumpkin' not in [item['name'] for item in kitchen.items]
    step = picture.card(store.get(pumpkin['id']))['ways'][0]['steps'][0]
    assert (step['label'], step['href']) == ('Open Kitchen', '/app/#kitchen')


def test_done_tomorrow_and_not_this_each_with_undo(ready):
    party = by_title('Jordan')
    assert tap(party, 'done').json()['said'] == 'Done'
    assert triage.get('e-party')['state'] == 'done'     # the email is handled with it
    tap(party, 'back')
    assert store.get(party['id'])['state'] == 'ready' and triage.get('e-party')['state'] == 'open'
    later = tap(party, 'later').json()
    assert (later['said'], later['card']['until']) == ('Back tomorrow morning', 'Back tomorrow morning')
    assert store.get(party['id'])['until'] == datetime.combine(NOW.date() + timedelta(days=1), clock(7), home.PACIFIC).timestamp()
    tap(party, 'back')
    assert tap(party, 'not').json()['said'] == 'Gone from the list'
    assert triage.get('e-party')['state'] == 'open'     # Not this leaves the email as it was
    assert tap(party, 'done').json()['error'] == 'That errand is already finished.'
    tap(party, 'back')
    assert store.get(party['id'])['state'] == 'ready'
    assert tap(party, 'back').json()['error'] == 'There’s nothing to undo.'


def test_every_tap_needs_a_signed_in_device(ready):
    party = by_title('Jordan')
    for path, body in ((f'{party["id"]}/done', {}), ('read', {}), ('settings', {'compose': 'gmail'})):
        assert client.post(f'/app/api/errands/{path}', json=body, headers=ON_THIS_MAC).status_code == 403
    away = {'origin': 'https://evil.example', 'tailscale-user-login': 'me@example.com'}
    assert client.post(f'/app/api/errands/{party["id"]}/done', json={}, headers=away).status_code == 403
    assert store.get(party['id'])['state'] == 'ready' and store.setting('compose:' + store.MAIN) is None


# ---- closing by itself ----

def test_a_reply_seen_in_sent_mail_closes_the_errand_and_its_email(ready):
    party = by_title('Jordan')
    tap(party, 'go', choice=1, step='mail')
    assert watch.run() == 0                             # nothing sent yet
    ready.replied(HOST, NOW.timestamp() + 60)
    assert watch.run() == 1
    done = store.get(party['id'])
    assert (done['state'], done['how'], triage.get('e-party')['state']) == ('done', 'replied', 'done')
    assert picture.card(done)['ended'] == {'text': 'You replied', 'when': 'just now', 'seen': True}
    assert ready.searches[-1] == f'in:sent to:{HOST} newer_than:3d'


def test_look_again_waits_for_a_look_under_way_then_looks_itself(ready):
    """The screen starts a look of its own while an errand waits for proof. One still reading when the reply is sent
    and Look again is tapped looked too early: Look again's look waits for it to finish, then looks again."""
    party = by_title('Jordan')
    tap(party, 'go', choice=1, step='mail')
    assert watch._lock.acquire(blocking=False)          # the screen's look, under way before the reply
    ready.replied(HOST, NOW.timestamp() + 60)
    read.read_soon(transport=ready.luna)
    threading.Timer(0.3, watch._lock.release).start()
    read.wait(10)
    assert (store.get(party['id'])['state'], store.get(party['id'])['how']) == ('done', 'replied')


def test_a_look_that_fails_inside_says_what_failed_in_the_log(ready, monkeypatch, caplog):
    monkeypatch.setattr(watch, '_run', lambda now: 1 / 0)
    assert watch.run() == 0                             # never raises
    assert 'errands look failed: ZeroDivisionError' in caplog.text


def test_a_yes_closes_once_it_is_also_on_the_calendar_or_its_day_has_passed(ready, monkeypatch):
    party = by_title('Jordan')
    tap(party, 'go', choice=0, step='mail')
    ready.replied(HOST, NOW.timestamp() + 60)
    assert watch.run() == 0
    assert picture.card(store.get(party['id']))['opened']['watch'] == 'You replied. Add it to your calendar too, or tap Done.'
    ready.saved('Jordan’s birthday party', errands_world.party_day(NOW), '14:00')
    assert watch.run() == 1 and store.get(party['id'])['how'] == 'replied'
    tap(party, 'back')
    tap(party, 'go', choice=0, step='mail')
    ready.days.clear()
    watch.run()
    monkeypatch.setattr(home, 'today', lambda: errands_world.party_day(NOW) + timedelta(days=1))
    watch.run()
    assert (store.get(party['id'])['state'], store.get(party['id'])['how']) == ('done', 'replied')   # the part that mattered


def test_a_bill_closes_when_an_email_from_its_own_site_says_it_was_paid(ready):
    bill = by_title('Lakeside')
    tap(bill, 'go', choice=0, step='open')
    sorted_mail('e-other', 'orders@shop.example', 'Payment received', NOW.timestamp() + 60)
    assert watch.run() == 0                             # another site's payment is not this one
    sorted_mail('e-paid', 'billing@lakesidepower.example', 'Thank you for your payment', NOW.timestamp() + 120)
    assert watch.run() == 1
    assert (store.get(bill['id'])['how'], picture.card(store.get(bill['id']))['ended']['text']) == ('confirmed', 'Confirmed by email')


def test_handled_in_mail_ends_it_set_aside_comes_back_and_a_passed_day_lets_it_go(ready, monkeypatch):
    dentist, party, bill, pumpkin = by_title('dental'), by_title('Jordan'), by_title('Lakeside'), by_title('pumpkin')
    triage.done('e-dentist')
    tap(bill, 'later')
    watch.run()
    assert (store.get(dentist['id'])['state'], store.get(dentist['id'])['how']) == ('done', 'handled')
    assert store.get(bill['id'])['state'] == 'later' and bill['id'] not in [e['id'] for e in errands()['ready']]
    watch.run(now=taps.morning(NOW.timestamp()) + 1)
    assert store.get(bill['id'])['state'] == 'ready'
    monkeypatch.setattr(home, 'today', lambda: NOW.date() + timedelta(days=13))
    watch.run()
    assert [store.get(e['id'])['state'] for e in (party, pumpkin, bill)] == ['missed', 'missed', 'ready']   # a bill still needs paying
    assert watch.run(now=NOW.timestamp() + (watch.STALE_DAYS + 1) * 86400) == 1
    assert store.get(bill['id'])['state'] == 'missed'


def test_looking_for_proof_is_bounded_and_an_errand_nobody_tapped_is_looked_at_rarely(ready):
    watch.run()
    looked = len(ready.searches)
    assert looked == 1                                  # only the party's sender can be answered by mail
    watch.run()
    assert len(ready.searches) == looked                # not again within CHECK_EVERY
    ready.replied(HOST, NOW.timestamp() + 60)           # answered from the mail app, without a tap
    assert watch.run(now=NOW.timestamp() + watch.CHECK_EVERY + 61) == 1
    assert store.get(by_title('Jordan')['id'])['how'] == 'replied'


def test_a_helper_that_doesnt_answer_closes_nothing(ready):
    party = by_title('Jordan')
    tap(party, 'go', choice=1, step='mail')
    ready.replied(HOST, NOW.timestamp() + 60)
    ready.down = True
    assert watch.run() == 0 and store.get(party['id'])['state'] == 'opened'
    ready.down = False
    assert watch.run() == 1


def test_the_screen_looks_for_proof_behind_itself_once_something_is_opened(ready):
    assert errands()['checking'] is False
    party = by_title('Jordan')
    tap(party, 'go', choice=1, step='mail')
    ready.replied(HOST, NOW.timestamp() + 60)
    watch._soon['at'] = 0.0
    errands()
    watch.wait()
    assert store.get(party['id'])['how'] == 'replied'


# ---- Today, Hub's list and Ask ----

def test_today_shows_the_three_most_pressing_and_lists_no_email_or_class_to_do_twice(ready):
    part = screen._ready(ME)
    assert [c['title'] for c in part['cards']] == [
        'Answer Sam’s invitation to parent coffee', 'Return the photo order form', 'RSVP to Jordan’s birthday party']
    assert part['more'] == 3 and [c['family'] for c in part['cards']] == [False, True, False]
    assert {row['key'] for row in screen._emails()} == {'mail:e-news', 'mail:e-quiet'}       # the others are errands
    assert client.get('/app/api/today', headers=SIGNED_IN).json()['ready'] == part
    assert [row['title'] for row in screen._school(NOW.date())] == ['Photo day']
    tap(by_title('pumpkin'), 'done')
    assert [row['title'] for row in screen._school(NOW.date())] == ['Photo day', 'Bring a small pumpkin']


def test_hubs_list_has_one_line_for_errands_without_a_sender_switched_off(ready):
    line = picture.items(ME)[0]
    assert (line['key'], line['tone'], line['title']) == ('errands', 'info', '6 errands ready for your tap')
    assert line['why'] == ('Answer Sam’s invitation to parent coffee; Return the photo order form; RSVP to Jordan’s birthday '
                           'party; and 3 more.')
    hub_store.set_ai_blocked(HOST, True)
    assert 'Jordan' not in picture.items(ME)[0]['why']
    found = catalog.errands_lookup(login=ME)
    assert found['count'] == 5 and not any('Jordan' in e['what'] for e in found['errands'])
    # The list itself is made for no one in particular, so it counts only the household's: the class guide's two.
    assert [(i['title'], i['why']) for i in catalog.errand_items()] == [
        ('2 errands ready for your tap', 'Return the photo order form; Get a small pumpkin for Friday.')]


def test_what_ask_may_read_of_errands_holds_no_address_link_or_email_text(ready):
    found = catalog.errands_lookup(login=ME)
    told = json.dumps(found, ensure_ascii=False)
    assert found['count'] == 6 and not ADDRESS.search(told) and 'http' not in told and 'Hi Jamie' not in told
    assert set(found['errands'][0]) == {'ref', 'what', 'why', 'from', 'kind', 'due', 'ways', 'started'}
    tap(by_title('Jordan'), 'done')
    assert {e['what'] for e in catalog.errands_lookup('done', login=ME)['errands']} == {
        'RSVP to Jordan’s birthday party', 'Water bill pays itself'}


def ask(text, headers=SIGNED_IN):
    with client.stream('POST', '/app/api/ask', json={'text': text}, headers={**headers, 'accept': 'text/event-stream'}) as stream:
        events = [json.loads(line.removeprefix('data: ')) for line in stream.iter_lines() if line.startswith('data: ')]
    return ''.join(e['text'] for e in events if e['t'] == 'say').strip(), [e['card'] for e in events if e['t'] == 'card']


def test_ask_shows_errands_as_cards_and_its_model_sees_no_address(ready, monkeypatch):
    ask_script.SEEN.clear()
    app_world.install(monkeypatch)
    monkeypatch.setattr(route, 'build', lambda http_client=None: ask_script.MODEL)
    monkeypatch.setattr(route, 'ready', lambda: None)
    said, cards = ask('what errands are ready?')
    assert said == '6 errands are ready for your tap. First: Answer Sam’s invitation to parent coffee.'
    assert cards == [{'kind': 'errands', 'ids': [e['id'] for e in picture.ready(login=ME)]}]
    seen = repr(ask_script.SEEN)
    assert HOST not in seen and 'https://' not in seen and 'Hi Jamie' not in seen


@pytest.fixture
def scripted(monkeypatch):
    """Ask on its stand-in model (tests/ask_script.py), in a made-up house, with a made-up Kitchen."""
    ask_script.SEEN.clear()
    app_world.install(monkeypatch)
    monkeypatch.setattr(route, 'build', lambda http_client=None: ask_script.MODEL)
    monkeypatch.setattr(route, 'ready', lambda: None)
    return kitchen_world.install(monkeypatch)


def guide():
    """The titles of the class guide's open errands: the household's, which everyone sees."""
    return sorted(e['title'] for e in store.rows("state in ('ready', 'opened')") if e['account'] == store.HOUSEHOLD)


# ---- each person's own ----

def test_each_person_sees_their_own_errands_and_the_households(two, ready, scripted):
    mine, theirs, mac = errands(), errands(AS_PARTNER), errands(ON_THIS_MAC)
    assert len(guide()) == 2 and len(mine['ready']) == 6 and mine['own'] and mine['signed_in']
    assert sorted(e['title'] for e in theirs['ready']) == guide() and all(e['family'] for e in theirs['ready'])
    assert not theirs['own'] and theirs['signed_in'] and theirs['done'] == []      # the bill that pays itself is the owner's
    assert sorted(e['title'] for e in mac['ready']) == guide() and not mac['own'] and not mac['signed_in']
    # Today, Ask's lookup and what the older questions hand the model: the same for each
    today = client.get('/app/api/today', headers=AS_PARTNER).json()['ready']
    assert sorted(c['title'] for c in today['cards']) == guide() and today['more'] == 0
    assert len(client.get('/app/api/today', headers=SIGNED_IN).json()['ready']['cards']) == 3
    assert sorted(e['what'] for e in catalog.errands_lookup(login=PARTNER)['errands']) == guide()
    assert catalog.errands_lookup('done', login=PARTNER)['errands'] == []
    handed = private.data('what needs me today', {'today'}, PARTNER)['errands_ready_for_a_tap']
    assert sorted(e['what'] for e in handed['errands']) == guide()
    said, cards = ask('what errands are ready?', AS_PARTNER)
    assert said.startswith('2 errands are ready for your tap.')
    assert cards == [{'kind': 'errands', 'ids': [e['id'] for e in picture.ready(login=PARTNER)]}]
    assert 'Lakeside' not in repr(ask_script.SEEN) and 'Jordan' not in repr(ask_script.SEEN)


def test_someone_elses_errand_isnt_there_for_them(two, ready):
    bill, pumpkin, missing = by_title('Lakeside'), by_title('pumpkin'), {'id': 999999}
    for do, body in (('done', {}), ('later', {}), ('not', {}), ('back', {}), ('go', {'choice': 0, 'step': 'open'})):
        theirs, nothing = tap(bill, do, AS_PARTNER, **body), tap(missing, do, AS_PARTNER, **body)
        assert (theirs.status_code, theirs.json()) == (nothing.status_code, nothing.json())
    assert store.get(bill['id'])['state'] == 'ready'
    # the class guide's are everyone's: one person's Done closes it for both, and either can put it back
    assert tap(pumpkin, 'done', AS_PARTNER).json()['said'] == 'Done'
    assert pumpkin['id'] not in [e['id'] for e in errands()['ready']]
    assert tap(pumpkin, 'back').json()['said'] == 'Put back' and store.get(pumpkin['id'])['state'] == 'ready'


def test_where_replies_open_is_each_accounts_and_no_login_is_kept(two, ready):
    refused = client.post('/app/api/errands/settings', json={'compose': 'gmail'}, headers=AS_PARTNER)
    assert refused.status_code == 409 and 'Google account' in refused.json()['error']
    assert client.post('/app/api/errands/settings', json={'compose': 'gmail'}, headers=SIGNED_IN).json()['said'] == 'Replies open in Gmail'
    assert store.compose(store.MAIN) == 'gmail' and errands()['compose'] == 'gmail' and errands(AS_PARTNER)['compose'] == 'mail'
    with closing(store.db()) as conn:
        kept = [row['key'] for row in conn.execute('select key from errands_settings')]
        columns = [row['name'] for row in conn.execute('pragma table_info(errands)')]
    assert '@' not in ' '.join(kept) and 'login' not in columns


def test_without_a_mapping_that_says_whose_nobody_sees_mail_errands(ready, monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ME, PARTNER})          # a second login, and nothing says whose mail is whose
    assert sorted(e['title'] for e in errands()['ready']) == guide()
    household({'people': {'name': 'Alex', 'logins': [ME], 'accounts': [people.DEFAULT]}})      # can't be read: no one's
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ME})
    assert sorted(e['title'] for e in errands()['ready']) == guide()
    assert tap(by_title('Lakeside'), 'done').json() == {'error': 'That errand isn’t there any more.'}


def test_the_morning_reads_only_mail_someone_at_home_owns(week, monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ME, PARTNER})          # nobody owns the main account yet
    assert read.run(transport=week.luna)['status'] == 'ok'
    assert {e['account'] for e in store.rows()} == {store.HOUSEHOLD} and len(week.asked) == 1     # the class guide only
    household({'people': [{'name': 'Alex', 'logins': [ME], 'accounts': [people.DEFAULT]}]})
    read.run(transport=week.luna)
    assert {e['account'] for e in store.rows()} == {store.HOUSEHOLD, store.MAIN}
    asked = []                                                            # "Look again" reads the asker's own
    monkeypatch.setattr(read, 'read_soon', lambda transport=None, accounts=None: asked.append(set(accounts)))
    assert client.post('/app/api/errands/read', headers=AS_PARTNER).json()['said'] == 'Looking through the class guide'
    assert client.post('/app/api/errands/read', headers=SIGNED_IN).json()['said'] == 'Looking through your mail and the class guide'
    assert asked == [set(), {store.MAIN}]


def test_proof_is_looked_for_only_in_mail_someone_at_home_owns(ready, monkeypatch):
    party = by_title('Jordan')
    tap(party, 'go', choice=1, step='mail')
    ready.replied(HOST, NOW.timestamp() + 60)
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ME, PARTNER})          # nobody owns the main account now
    searched = len(ready.searches)
    assert watch.run() == 0 and len(ready.searches) == searched
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ME})
    assert watch.run() == 1


# ---- asked for in Ask ----

def test_ask_finishes_sets_aside_and_puts_back_an_errand_named_in_the_message(two, ready, scripted):
    bill, form, dentist, pumpkin = by_title('Lakeside'), by_title('photo order'), by_title('dental'), by_title('pumpkin')
    said, cards = ask('I paid the power bill')
    assert (said, cards) == ('Marked done.', [{'kind': 'errands', 'ids': [bill['id']], 'batch': f'r{bill["id"]}'}])
    assert store.get(bill['id'])['state'] == 'done'
    assert client.post(f'/app/api/undo/r{bill["id"]}', headers=SIGNED_IN).json() == {'ok': True, 'said': 'Put back'}
    assert store.get(bill['id'])['state'] == 'ready'
    assert ask('the photo form can wait till tomorrow')[0] == 'Set aside until tomorrow morning.'
    assert ask('I don’t need the dental cleaning')[0] == 'Gone from your errands.'
    assert ask('the pumpkin goes on the list')[0] == 'Its things are on the shopping list.'
    assert [store.get(e['id'])['state'] for e in (form, dentist, pumpkin)] == ['later', 'dismissed', 'done']
    assert 'small pumpkin' in [item['name'] for item in scripted.items]
    assert ask('undo the dental cleaning')[0] == 'Put back as it was.' and store.get(dentist['id'])['state'] == 'ready'
    said, cards = ask('open my replies in Gmail')
    assert (said, cards) == ('Replies open in Gmail.', [{'kind': 'errands', 'ids': [], 'batch': 'rcmail'}])
    assert store.compose(store.MAIN) == 'gmail'
    client.post('/app/api/undo/rcmail', headers=SIGNED_IN)
    assert store.compose(store.MAIN) == 'mail'


def test_ask_cant_reach_someone_elses_errand_or_setting(two, ready, scripted):
    bill = by_title('Lakeside')
    said, cards = ask('I paid the power bill', AS_PARTNER)
    assert said == 'Hub couldn’t do that. None of your errands fits “power bill”.' and cards == []
    assert ask('open my replies in Gmail', AS_PARTNER)[0].startswith('Hub couldn’t do that. Hub doesn’t read')
    assert client.post(f'/app/api/undo/r{bill["id"]}', headers=AS_PARTNER).json() == {'error': 'There is nothing left to undo there.'}
    assert store.get(bill['id'])['state'] == 'ready' and store.compose(store.MAIN) == 'mail'


@pytest.fixture
def noted(ready, monkeypatch):
    """Three more made-up errands (a water bill due, a form, hats for a party), and taps that only note what they were
    asked, so a test sees exactly which errands words would change."""
    for key, kind, title, buy in (('mail:e-water-due', 'pay', 'Pay the Example Water bill', None),
                                  ('mail:e-permission', 'sign', 'Sign the permission form', None),
                                  ('mail:e-hats', 'buy', 'Get hats for Friday', ['party hats'])):
        store.add(key, 'mail', {'kind': kind, 'title': title, 'choices': [{'label': 'Add to the list', 'buy': buy}] if buy else []},
                  account=store.MAIN)
    tapped = []
    monkeypatch.setattr(taps, 'tap', lambda errand_id, do, body, login, now=None: tapped.append(store.get(errand_id)['title']) or ('Done', {}))
    return tapped


@pytest.mark.parametrize('do, said, which, picked', [
    ('done', 'I paid the power bill', ['power bill'], ['Lakeside Power']),
    ('done', 'I paid the power bill and the water bill', ['power bill', 'water bill'], ['Lakeside Power', 'Example Water']),
    ('done', 'mark the permission form done', ['permission form'], ['permission form']),
    ('done', 'the power bill is paid', ['power bill'], ['Lakeside Power']),
    ('done', 'power bill done', ['power bill'], ['Lakeside Power']),
    ('done', 'can you mark the power bill done?', ['power bill'], ['Lakeside Power']),
    ('done', 'I paid the power bill, skip the dentist', ['power bill'], ['Lakeside Power']),
    ('done', 'I paid the power bill before the party', ['power bill'], ['Lakeside Power']),
    ('not', 'skip the dentist', ['dentist'], ['dental']),
    ('not', 'I don’t need the party', ['party'], ['birthday party']),
    ('later', 'remind me about the dentist tomorrow', ['dentist'], ['dental']),
    ('later', 'the photo form can wait till tomorrow', ['photo form'], ['photo order form']),
    ('later', 'I paid the power bill and the dentist can wait till tomorrow', ['dentist'], ['dental']),
    ('list', 'put the party things on the list', ['party things'], ['hats']),
    ('list', 'the pumpkin goes on the list', ['pumpkin'], ['pumpkin']),
])
def test_ask_does_what_the_persons_own_words_ask(noted, do, said, which, picked):
    asked.asked(do, which, said, ME)
    assert len(noted) == len(picked) and all(words in title for words, title in zip(picked, noted)), noted


@pytest.mark.parametrize('do, said, which', [
    ('done', 'I paid the power bill before the party', ['power bill', 'party']),       # the party isn't what was paid
    ('done', 'I paid the power bill and the dentist can wait till tomorrow', ['dentist']),
    ('done', 'I paid the power bill and the dentist can wait till tomorrow', ['power bill', 'dentist']),
    ('done', 'I paid the power bill, skip the dentist', ['dentist']),
    ('done', 'I paid the power bill, skip the dentist', ['power bill', 'dentist']),
    ('done', 'the power bill is unpaid, the form is signed', ['power bill']),
    ('done', 'I’m done for the day, what is the power bill about', ['power bill']),
    ('done', 'I paid the power bill, and the party sounds fun', ['power bill', 'party']),
    ('not', 'don’t skip the power bill', ['power bill']),
    ('not', 'never ignore the power bill', ['power bill']),
    ('done', 'did I pay the power bill?', ['power bill']),
    ('done', 'has the power bill been paid', ['power bill']),
    ('done', 'is the power bill paid', ['power bill']),
    ('done', 'who sent the photo form', ['photo form']),
    ('done', 'when was the power bill sent', ['power bill']),
    ('later', 'what’s on tomorrow besides the party', ['party']),
    ('done', 'I haven’t paid the power bill yet', ['power bill']),
    ('done', 'if I paid the power bill, what is left?', ['power bill']),
    ('done', 'I wish I had paid the power bill', ['power bill']),
    ('done', 'tell me when I’ve paid the power bill', ['power bill']),
    ('done', 'the power bill is due on Friday', ['power bill']),           # asks for nothing
    ('done', 'I paid the power bill', ['electricity bill']),              # not the person's words
    ('done', 'I paid the power bill', ['bill power']),                    # nor in their order
    ('done', 'I paid the power bill', ['all errands']),                   # words from mail, not the message
])
def test_ask_changes_nothing_unless_the_persons_own_words_ask_for_it(noted, do, said, which):
    with pytest.raises(taps.Refused):
        asked.asked(do, which, said, ME)
    assert noted == []


def test_the_persons_whole_phrase_must_fit_not_only_the_models_word(ready, monkeypatch):
    monkeypatch.setattr(taps, 'tap', lambda *a, **k: pytest.fail('nothing should change'))
    with pytest.raises(asked.Unclear, match='“water bill”'):                 # only the power bill is open
        asked.asked('done', ['bill'], 'I paid the water bill', ME)


def test_a_name_that_fits_several_errands_or_none_changes_nothing_and_ask_hears_only_what_it_may(ready):
    store.add('mail:e-made-up', 'mail', {'kind': 'book', 'title': 'Book the Lakeside Power meter check'}, account=store.MAIN)
    with pytest.raises(asked.Unclear) as several:
        asked.asked('done', ['lakeside power'], 'I paid lakeside power', ME)
    assert len(several.value.found) == 2
    with pytest.raises(asked.Unclear):
        asked.asked('done', ['power bill', 'gym'], 'I paid the power bill and the gym', ME)
    assert by_title('Lakeside Power bill')['state'] == 'ready'
    hub_store.set_ai_blocked(HOST, True)                                     # the party's sender: AI switched off
    with pytest.raises(asked.Unclear) as none:
        asked.asked('done', ['zebra'], 'I paid the zebra', ME)
    assert none.value.found and not any('Jordan' in title for title in none.value.found)
    assert none.value.found == picture.titles(store.rows_for(ME, "state in ('ready', 'opened', 'later')"))


@pytest.mark.parametrize('said, where', [('open replies in Gmail', 'gmail'), ('use the mail app for replies', 'mail'),
                                         ('please open my replies in gmail', 'gmail')])
def test_where_replies_open_changes_when_the_person_says_so(ready, said, where):
    client.post('/app/api/errands/settings', json={'compose': 'gmail' if where == 'mail' else 'mail'}, headers=SIGNED_IN)
    assert asked.asked('replies', [], said, ME, where)['told'].startswith('Replies open in')
    assert store.compose(store.MAIN) == where


@pytest.mark.parametrize('said', ['what’s in my gmail?', 'don’t open replies in gmail', 'I like gmail', 'I use gmail at work',
                                  'why does it open gmail', 'if replies opened in gmail I would like that'])
def test_where_replies_open_changes_only_when_the_person_says_where(ready, said):
    with pytest.raises(taps.Refused):
        asked.asked('replies', [], said, ME, 'gmail')
    assert store.compose(store.MAIN) == 'mail'


# ---- a database from before errands knew whose they are ----

BEFORE = """
create table if not exists errands(
  id integer primary key, key text not null unique, source text not null, kind text not null,
  title text not null default '', why text not null default '', due text, facts text not null default '{}',
  quote text not null default '', choices text not null default '[]',
  message_id text, thread_id text, sender text, sender_name text, subject text, reply_to text, received real,
  state text not null default 'ready', chose integer, steps text not null default '[]', how text,
  until real, undo text, made_day text not null,
  created_at real not null, updated_at real not null, opened_at real, ended_at real, checked_at real);
create index if not exists errands_state on errands(state);
create table if not exists errands_tried(key text primary key, tries integer not null, at real not null);
create table if not exists errands_settings(key text primary key, value text not null);
"""


def test_a_database_from_before_errands_knew_whose_they_are_is_rebuilt(two):
    with closing(hub_store.db()) as conn, conn:
        conn.executescript(BEFORE)
        for key, source in (('mail:e-old', 'mail'), ('school:old', 'school')):
            conn.execute("insert into errands(key, source, kind, title, made_day, created_at, updated_at)"
                         " values (?, ?, 'pay', ?, '2026-11-16', 1, 1)", (key, source, 'Old ' + source))
        conn.execute("insert into errands_tried(key, tries, at) values ('mail:e-tried', 1, 1), ('school:tried', 2, 1)")
        conn.execute("insert into errands_settings(key, value) values ('compose', 'gmail'), ('account', 'alex@mailbox.example')")
    assert store.keys() == {(store.MAIN, 'mail:e-old'), (store.HOUSEHOLD, 'school:old')}
    assert store.tries() == {(store.MAIN, 'mail:e-tried'): 1, (store.HOUSEHOLD, 'school:tried'): 2}
    assert (store.compose(store.MAIN), store.mailbox(store.MAIN), store.setting('compose'), store.setting('account')) == (
        'gmail', 'alex@mailbox.example', None, None)
    assert [e['title'] for e in store.rows_for(PARTNER)] == ['Old school'] and len(store.rows_for(ME)) == 2
    assert store.add('mail:e-old', 'mail', {'kind': 'pay', 'title': 'Again'}, account=store.MAIN) is None
    assert store.add('mail:e-old', 'mail', {'kind': 'pay', 'title': 'Another account'}, account='other') is not None
    assert store.tried('mail:e-tried', store.MAIN) == 2
    with closing(store.db()) as conn:
        assert 'errands_state' in [r['name'] for r in conn.execute('pragma index_list(errands)')]
        assert {r['name'] for r in conn.execute("select name from sqlite_master where type='table'")} >= {'errands', 'errands_tried'}
        assert not [r for r in conn.execute("select name from sqlite_master where name like '%before'")]


# ---- keeping ----

def test_old_errands_lose_their_words_then_go(ready):
    party = by_title('Jordan')
    tap(party, 'done')
    store.prune(NOW.timestamp() + 15 * 86400)
    kept = store.get(party['id'])
    assert (kept['title'], kept['why'], kept['quote'], kept['choices'], kept['subject'], kept['reply_to']) == ('', '', '', [], '', None)
    assert by_title('dental')['why'] == 'Harbor Dental says your six-month cleaning is due.'      # still waiting: kept
    store.prune(NOW.timestamp() + 61 * 86400)
    assert store.rows() == []


def test_a_question_about_bills_brings_the_bills_only(ready, monkeypatch):
    """2026-11-18: "any bills due in the next week?" drew eight cards, invitations and alerts among them."""
    app_world.install(monkeypatch)
    monkeypatch.setattr(route, 'build', lambda http_client=None: ask_script.MODEL)
    monkeypatch.setattr(route, 'ready', lambda: None)
    _, cards = ask('any bills due this week?')
    bills = [e['id'] for e in picture.ready(login=ME) if e['kind'] in ('pay', 'renew')]
    assert bills and cards == [{'kind': 'errands', 'ids': bills}]

