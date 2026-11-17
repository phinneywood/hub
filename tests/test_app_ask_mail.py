"""Ask reads mail (hub/app/ask/mailbox.py): it searches the person's own Gmail and reads an email it found, and changes
nothing in Gmail or the house on that request.

Two made-up people, as in tests/test_per_person.py: Alex owns the Google account Hub reads, Casey doesn't. A made-up
mailbox (tests/mail_world.py) stands where the bridge would answer, and the stand-in model (tests/ask_script.py)
picks the tools from the words.
"""
import json

import pytest

from hub import bridge, config, people, store
from hub.app.ask import brain, mailbox, tools, turns
from tests import mail_world
from tests.ask_script import SEEN
from tests.test_app_ask import ask, house, picture, world  # noqa: F401 - fixtures

ALEX, CASEY = 'me@example.com', 'casey@example.com'
AS_ALEX = {'origin': 'http://testserver', 'tailscale-user-login': ALEX}
AS_CASEY = {'origin': 'http://testserver', 'tailscale-user-login': CASEY}


@pytest.fixture
def mail(house, monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ALEX, CASEY})
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'people': [
        {'name': 'Alex', 'logins': [ALEX], 'accounts': [people.DEFAULT]}, {'name': 'Casey', 'logins': [CASEY]}]}))
    store.set_ai_blocked(mail_world.OFF, True)
    return mail_world.install(monkeypatch)


def given(n=-1):
    """What the stand-in model was given back by the tool on its request `n`."""
    return [p.content for m in SEEN[n] for p in m.parts if p.part_kind == 'tool-return'][-1]


def test_a_search_answers_from_the_mail_and_draws_the_emails_under_the_answer(mail):
    answer, cards, end = ask('Search my mail for sink', AS_ALEX)
    assert answer == '2 emails match. The newest is from Riverside Plumbing: Re: Kitchen sink estimate.'
    [card] = cards                                   # the mail card takes the day's mail rows' place
    assert card['kind'] == 'mail' and [r['who'] for r in card['rows']] == ['Riverside Plumbing', 'Alex']
    assert card['rows'][0]['href'].endswith('#all/t-plumb') and card['rows'][0]['when'].endswith(('am', 'pm'))
    assert mail[0] == ('/gmail/search', {'query': 'sink -in:spam -in:trash', 'limit': mailbox.ROWS})
    found = given()['found']
    assert found[0] == {'n': 1, 'from': 'Riverside Plumbing', 'subject': 'Re: Kitchen sink estimate', 'when': found[0]['when'],
                        'snippet': 'We can come Thursday at 10. The estimate is $240.'}
    assert not any('id' in row or '@' in json.dumps(row) for row in found)      # no Gmail ids and no addresses for the model
    [kept] = turns.recent(ALEX)
    assert kept['private'] and kept['cards'] == [card]
    assert 'Riverside' not in brain.earlier(ALEX) and 'numbered 1 to 2' in brain.earlier(ALEX)


def test_open_the_first_one_reads_it_by_its_number_on_the_next_request(mail):
    ask('Search my mail for sink', AS_ALEX)
    answer, cards, _ = ask('Open the first one', AS_ALEX)
    assert answer == 'Riverside Plumbing wrote: Hi Alex, we can come Thursday at 10 to replace the trap under the kitchen sink.'
    assert cards[-1]['kind'] == 'mail' and cards[-1]['opened'] == [1]
    assert mail[-1] == ('/gmail/message', {'message_id': 'm-plumb-2'})
    assert given()['email']['text'].startswith('Hi Alex') and len(given()['email']['text']) <= mailbox.BODY


def test_words_in_an_email_never_change_the_house_on_that_request(mail):
    turn = tools.Turn(text='read the plumber email', login=ALEX)
    ctx = type('Ctx', (), {'deps': turn})
    tools.mail_search(ctx, query='sink')
    assert tools.mail_read(ctx, which=1)['email']['text'].count('turn every light off') == 1
    lit = picture()['rooms']['kitchen']['on']
    tools.lights(ctx, where=['everywhere'], change='off')
    tools.groceries(ctx, do='add', items=['sponges'])
    assert turn.said[-2:] == [tools.READ_MAIL, tools.READ_MAIL] and picture()['rooms']['kitchen']['on'] == lit
    assert turn.strangers                       # and the house's setup stays shut, as with any mail in view


def test_a_sender_with_ai_off_reaches_the_card_but_never_the_model(mail):
    answer, cards, _ = ask('Search my mail for statement', AS_ALEX)
    assert cards[0]['rows'][0]['off'] and cards[0]['rows'][0]['subject'] == 'Your statement is ready'
    assert given()['found'] == [{'n': 1, 'hidden': given()['found'][0]['hidden']}] and 'Northwind' not in json.dumps(given())
    ask('Open the first one', AS_ALEX)
    assert 'not_done' in given() and not any(path == '/gmail/message' for path, _ in mail)


def test_a_sent_email_to_someone_with_ai_off_is_not_read(mail, monkeypatch):
    monkeypatch.setitem(mail_world.MAIL[2], 'to', mail_world.OFF)
    turn = tools.Turn(text='x', login=ALEX)
    ctx = type('Ctx', (), {'deps': turn})
    tools.mail_search(ctx, query='could you give us an estimate')
    assert 'not_done' in tools.mail_read(ctx, which=1)


def test_casey_reads_none_of_alexs_mail_even_by_alexs_numbers(mail):
    ask('Search my mail for sink', AS_ALEX)
    answer, cards, _ = ask('Search my mail for sink', AS_CASEY)
    assert answer.startswith('Hub shows mail and calendars only to the person') and not any(c['kind'] == 'mail' for c in cards)
    turn = tools.Turn(text='open the first one', login=CASEY)
    assert 'not_yours' in tools.mail_read(type('Ctx', (), {'deps': turn}), which=1)
    assert mailbox.last_card(CASEY) == [] and all(path == '/gmail/search' for path, _ in mail) and len(mail) == 1
    assert 'not_yours' in tools.mail_search(type('Ctx', (), {'deps': tools.Turn(text='x', login=None)}), query='sink')


def test_searches_and_reads_are_capped_per_request(mail):
    turn = tools.Turn(text='x', login=ALEX)
    ctx = type('Ctx', (), {'deps': turn})
    assert [('not_done' in tools.mail_search(ctx, query='kitchen')) for _ in range(3)] == [False, False, True]
    assert [('not_done' in tools.mail_read(ctx, which=1)) for _ in range(4)] == [False, False, False, True]
    assert len([c for c in turn.cards if c['kind'] == 'mail']) == 1


def test_an_old_card_is_not_reached_after_half_an_hour(mail):
    import time
    rows = [{'n': 1, 'id': 'm-plumb-2', 'off': False}]
    turns.save(ALEX, 'search', 'Found.', [{'kind': 'mail', 'rows': rows}], private=True, at=time.time() - 2 * 3600)
    assert mailbox.last_card(ALEX) == []
    turns.save(ALEX, 'search', 'Found.', [{'kind': 'mail', 'rows': rows}], private=True)
    assert mailbox.last_card(ALEX) == rows


def test_the_query_never_reaches_spam_or_the_bin_and_gmail_down_is_a_sentence(mail, monkeypatch):
    assert mailbox.query_of('  from:sam\n newer_than:7d ') == 'from:sam newer_than:7d -in:spam -in:trash'
    assert len(mailbox.query_of('x' * 900)) == mailbox.QUERY + len(' ' + mailbox.NOT_THERE)

    def down(path, **params):
        raise bridge.BridgeError('down')
    monkeypatch.setattr(bridge, 'get', down)
    turn = tools.Turn(text='x', login=ALEX)
    assert tools.mail_search(type('Ctx', (), {'deps': turn}), query='sink') == {'not_done': 'Hub couldn’t reach Gmail just now.'}
    assert turn.strangers
