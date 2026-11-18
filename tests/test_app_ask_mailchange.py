"""Ask changes mail (hub/app/ask/mailchange.py): archive, label and mark read only after the person's own tap.

The same two made-up people and mailbox as tests/test_app_ask_mail.py. A stand-in for the bridge's write paths keeps
every call, so a test can see that nothing reached Gmail before the tap.
"""
import time

import pytest
from fastapi.testclient import TestClient

from hub import bridge
from hub.app.ask import mailchange, tools
from hub.main import app
from tests import mail_world
from tests.test_app_ask import ask, house, world  # noqa: F401 - fixtures
from tests.test_app_ask_mail import ALEX, AS_ALEX, AS_CASEY, CASEY, mail  # noqa: F401 - fixtures

client = TestClient(app)


@pytest.fixture
def gmail(mail, monkeypatch):
    """The bridge's labels and write paths (one label, Receipts). Returns the writes it got."""
    return mail_world.install_writes(monkeypatch)


def tap(number, choice, headers=AS_ALEX):
    return client.post(f'/app/api/mail/change/{number}/{choice}', headers=headers)


def test_archive_waits_for_the_tap_then_goes_and_undo_puts_it_back(gmail):
    ask('Search my mail for sink', AS_ALEX)
    answer, cards, end = ask('Archive the first one', AS_ALEX)
    [card] = cards
    assert answer == 'Archive this email: waiting for your tap.' and end['status'] == 'ok'
    assert card['kind'] == 'confirm' and card['path'] == 'mail/change' and card['do'] == 'Archive'
    assert card['lines'] == ['Riverside Plumbing: Re: Kitchen sink estimate']
    assert gmail == []                                                   # nothing reached Gmail before the tap
    assert client.get(f'/app/api/mail/change/{card["id"]}', headers=AS_ALEX).json()['status'] == 'waiting'
    answer = tap(card['id'], 'confirm').json()
    assert answer == {'ok': True, 'said': 'Archived', 'batch': None, 'undo': True}
    assert gmail == [('/gmail/labels/apply', {'message_ids': ['m-plumb-2'], 'add_label_ids': [], 'remove_label_ids': ['INBOX']})]
    assert tap(card['id'], 'confirm').status_code == 409                # once
    assert tap(card['id'], 'undo').json()['said'] == 'Put back as it was'
    assert gmail[-1] == ('/gmail/undo', {'change_id': 'c1'})
    assert client.get(f'/app/api/mail/change/{card["id"]}', headers=AS_ALEX).json()['status'] == 'undone'


def test_a_label_is_found_or_made_only_on_the_tap_and_read_takes_unread_off(gmail):
    ask('Search my mail for sink', AS_ALEX)
    _, [card], _ = ask('Label the second one Receipts', AS_ALEX)
    assert card['title'] == 'Label this email “Receipts”?'
    tap(card['id'], 'confirm')
    assert gmail[-1][1]['add_label_ids'] == ['Label_7']
    _, [card], _ = ask('Label the first one Plumbing', AS_ALEX)
    assert card['lines'][-1] == 'Makes a new label, Plumbing.' and len(gmail) == 1     # not made yet
    tap(card['id'], 'confirm')
    assert [p for p, _ in gmail[1:]] == ['/gmail/labels/create', '/gmail/labels/apply'] and gmail[-1][1]['add_label_ids'] == ['Label_new']
    _, [card], _ = ask('Mark the first one as read', AS_ALEX)
    tap(card['id'], 'confirm')
    assert gmail[-1][1] == {'message_ids': ['m-plumb-2'], 'add_label_ids': [], 'remove_label_ids': ['UNREAD']}


def test_not_now_leaves_it_and_a_newer_card_replaces_an_older_one(gmail):
    ask('Search my mail for sink', AS_ALEX)
    _, [first], _ = ask('Archive the first one', AS_ALEX)
    _, [second], _ = ask('Archive the second one', AS_ALEX)
    assert tap(first['id'], 'confirm').status_code == 409
    assert tap(second['id'], 'dismiss').json()['said'] == 'Left as it was'
    assert tap(second['id'], 'confirm').status_code == 409 and gmail == []


def test_only_the_person_who_asked_can_tap_and_casey_cant_propose_on_alexs_mail(gmail):
    ask('Search my mail for sink', AS_ALEX)
    _, [card], _ = ask('Archive the first one', AS_ALEX)
    assert tap(card['id'], 'confirm', AS_CASEY).status_code == 403
    assert tap(card['id'], 'confirm', {'origin': 'http://testserver'}).status_code == 403
    assert client.get(f'/app/api/mail/change/{card["id"]}', headers=AS_CASEY).json()['status'] == 'gone'
    turn = tools.Turn(text='archive the first one', login=CASEY)
    assert 'not_yours' in tools.mail_change(type('Ctx', (), {'deps': turn}), do='archive', which=[1])
    assert gmail == []


def test_an_emails_words_never_propose_a_change(gmail):
    turn = tools.Turn(text='what did the plumber say?', login=ALEX)
    ctx = type('Ctx', (), {'deps': turn})
    tools.mail_search(ctx, query='sink')
    assert 'refused' in tools.mail_change(ctx, do='archive', which=[1, 2])
    assert not [c for c in turn.cards if c['kind'] == 'confirm'] and gmail == []


def test_numbers_hub_didnt_show_and_unknown_labels_are_refused(gmail):
    turn = tools.Turn(text='archive them', login=ALEX)
    ctx = type('Ctx', (), {'deps': turn})
    tools.mail_search(ctx, query='sink')
    assert 'not_done' in tools.mail_change(ctx, do='archive', which=[9])
    turn.text = 'take the Travel label off the first'
    assert 'no label called Travel' in tools.mail_change(ctx, do='unlabel', which=[1], label='Travel')['not_done']
    assert not [c for c in turn.cards if c['kind'] == 'confirm'] and gmail == []


def test_gmail_saying_no_changes_nothing_and_the_card_waits(gmail, monkeypatch):
    ask('Search my mail for sink', AS_ALEX)
    _, [card], _ = ask('Archive the first one', AS_ALEX)

    def paused(path, body=None, **kw):
        raise bridge.BridgeError('Changes are paused', status=403)

    monkeypatch.setattr(bridge, 'post', paused)
    assert tap(card['id'], 'confirm').json()['error'] == 'Gmail changes are paused, so nothing was changed.'
    assert mailchange.card(card['id'])['status'] == 'waiting'


def test_an_unconfirmed_result_is_never_retried(gmail, monkeypatch):
    ask('Search my mail for sink', AS_ALEX)
    _, [card], _ = ask('Archive the first one', AS_ALEX)
    monkeypatch.setattr(bridge, 'post', lambda path, body=None, **kw: (_ for _ in ()).throw(bridge.BridgeError('timed out')))
    assert 'didn’t confirm' in tap(card['id'], 'confirm').json()['error']
    assert mailchange.card(card['id'])['status'] == 'unsure' and tap(card['id'], 'confirm').status_code == 409


def test_a_card_waits_an_hour(gmail):
    ask('Search my mail for sink', AS_ALEX)
    _, [card], _ = ask('Archive the first one', AS_ALEX)
    with mailchange._db() as conn:
        conn.execute('update ask_mail_waiting set ts = ? where id = ?', (time.time() - 2 * 3600, card['id']))
    assert 'more than an hour' in tap(card['id'], 'confirm').json()['error'] and gmail == []


def test_a_dry_run_records_the_proposal_and_keeps_no_card(gmail):
    from hub.golden import dryrun
    turn = tools.Turn(text='archive the plumber emails', login=ALEX)
    ctx = type('Ctx', (), {'deps': turn})
    with dryrun.layer() as ledger:
        tools.mail_search(ctx, query='sink')
        assert 'needs_tap' in tools.mail_change(ctx, do='archive', which=[1, 2])
    assert ledger.changes == [{'turn': 0, 'to': 'proposal', 'what': 'mail', 'data': {'do': 'archive', 'emails': 2, 'label': None}}]
    with mailchange._db() as conn:
        assert conn.execute('select count(*) from ask_mail_waiting').fetchone()[0] == 0
    assert gmail == []
