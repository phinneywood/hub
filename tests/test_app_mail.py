"""Mail in the app (hub/app/mail.py): what needs the owner, then what Hub sorted, and the taps.

Made-up rows in Hub's own sorting tables, in a throwaway data folder. Gmail itself is never reached: filing and
Undo go to a stand-in for the bridge.
"""
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from hub import home
from hub.engine import catalog, state
from hub.main import app
from hub.tools.gmail import filing, inbox, missions, triage

client = TestClient(app)
SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
DAY = 86400


@pytest.fixture
def mailbox(monkeypatch):
    """A sorted inbox: two emails that need the owner, a question nobody answered, an odd record and some reading."""
    now = time.time()
    monkeypatch.setattr(missions, 'is_paused', lambda: False)
    triage.put_setting('synced_at', now - 60)
    rows = [('m1', 'dentist@example.com', 'Maple Street Dental', 'Your appointment', 'needs', 1, 'Confirm Tuesday at 3:00.'),
            ('m2', 'sam@example.com', 'Sam Rivera', 'Field trip form', 'needs', 4, 'Send the signed form back.'),
            ('r1', 'shop@example.com', 'Example Shop', 'Your receipt', 'record', 0, ''),
            ('r2', 'bank@example.com', 'Example Bank', 'Large payment', 'record', 0, ''),
            ('n1', 'news@example.com', 'The Weekly Example', 'This week', 'reading', 0, ''),
            ('n2', 'news@example.com', 'The Weekly Example', 'Last week', 'reading', 3, '')]
    with closing(triage.db()) as conn, conn:
        for message_id, sender, name, subject, kind, days, step in rows:
            conn.execute('insert into triage_messages(id, thread_id, sender, name, subject, kind, how, step, received, seen_at, sorted_day,'
                         ' odd) values (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (message_id, 't' + message_id, sender, name, subject, kind, 'rules', step or None, now - days * DAY - 60, now - 30,
                          home.today().isoformat(), 'large amount' if message_id == 'r2' else None))
        conn.execute("insert into triage_asked(thread_id, message_id, who, subject, sent_at, state, checked_at)"
                     " values ('a1', 'q1', 'Alex', 'Saturday plans?', ?, 'waiting', ?)", (now - 4 * DAY, now))
    state.set_trust(catalog.action(filing.ACTIONS['reading']), 'undo')
    return now


def picture():
    return client.get('/app/api/mail', headers=SIGNED_IN).json()


def test_mail_leads_with_what_needs_the_owner_and_who_never_answered(mailbox):
    seen = picture()
    assert [(e['who'], e['step'], e['days']) for e in seen['needs']] == [('Sam Rivera', 'Send the signed form back.', 4),
                                                                        ('Maple Street Dental', 'Confirm Tuesday at 3:00.', 1)]
    assert seen['needs'][0]['find'].startswith('/gmail/search?q=from%3Asam%40example.com')
    assert [(a['subject'], a['to'], a['days']) for a in seen['asked']] == [('Saturday plans?', 'Alex', 4)]
    assert [e['who'] for e in seen['odd']] == ['Example Bank']                   # a record worth a look is not filed quietly
    shelves = {s['kind']: s for s in seen['shelves']}
    assert shelves['reading']['count'] == 2 and shelves['reading']['ready'] == 2 and shelves['reading']['level'] == 'undo'
    assert shelves['record']['count'] == 1 and shelves['record']['ready'] == 0                  # left in the inbox: nothing offered
    assert seen['sorted'] and seen['working'] == '' and seen['paused'] is False


def test_a_handled_email_goes_and_comes_back(mailbox):
    assert client.post('/app/api/mail/done', json={'key': 'm2'}, headers=SIGNED_IN).json() == {'ok': True, 'said': 'Marked as handled'}
    assert [e['id'] for e in picture()['needs']] == ['m1']
    assert client.post('/app/api/mail/back', json={'key': 'm2'}, headers=SIGNED_IN).json()['ok']
    assert [e['id'] for e in picture()['needs']] == ['m2', 'm1']
    assert client.post('/app/api/mail/done', json={'key': 'nope'}, headers=SIGNED_IN).status_code == 409
    assert client.post('/app/api/mail/answered', json={'key': 'a1'}, headers=SIGNED_IN).json()['said'] == 'Stopped watching'
    assert client.post('/app/api/mail/fine', json={'key': 'r2'}, headers=SIGNED_IN).json()['ok']
    seen = picture()
    assert seen['asked'] == [] and seen['odd'] == []


def test_filing_a_kind_and_putting_it_back_go_through_the_bridge_like_the_older_page(mailbox, monkeypatch):
    sent = []

    def bridge_post(path, body=None, **kw):
        sent.append((path, body))
        if path == '/gmail/undo':
            return {'undone': True}
        if path == '/gmail/labels/create':
            return {'id': 'Label_1'}
        return {'status': 'applied', 'change_id': 'c1'}

    monkeypatch.setattr(filing.bridge, 'post', bridge_post)
    monkeypatch.setattr(filing.bridge, 'get', lambda *a, **kw: {'labels': [], 'messages': []})
    assert client.post('/app/api/mail/file', json={'key': 'record'}, headers=SIGNED_IN).status_code == 409     # left in the inbox
    answer = client.post('/app/api/mail/file', json={'key': 'reading'}, headers=SIGNED_IN).json()
    assert answer == {'ok': True, 'said': 'Filing'}
    inbox.wait()
    seen = picture()
    shelf = next(s for s in seen['shelves'] if s['kind'] == 'reading')
    assert shelf['count'] == 0 and shelf['undo'] == [{'change': 'c1', 'count': 2, 'at': pytest.approx(time.time(), abs=60)}]
    assert seen['notice'] == 'Filed 2 emails.' and picture()['notice'] == ''            # told once
    assert sent[1] == ('/gmail/labels/apply', {'message_ids': ['n1', 'n2'], 'add_label_ids': ['Label_1'], 'remove_label_ids': ['INBOX']})
    answer = client.post('/app/api/mail/undo', json={'key': shelf['undo'][0]['change']}, headers=SIGNED_IN).json()
    assert answer == {'ok': True, 'said': 'Back in the inbox'} and sent[-1][0] == '/gmail/undo'


def test_only_a_signed_in_device_may_tap(mailbox):
    assert client.post('/app/api/mail/done', json={'key': 'm1'}, headers={'origin': 'http://testserver'}).status_code == 403
    assert client.post('/app/api/mail/done', json={'key': 'm1'}, headers={**SIGNED_IN, 'origin': 'https://elsewhere.example'}).status_code == 403
    assert client.post('/app/api/mail/send', json={'key': 'm1'}, headers=SIGNED_IN).status_code == 409
    assert len(picture()['needs']) == 2
