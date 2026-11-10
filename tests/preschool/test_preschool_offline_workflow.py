"""Synthetic source -> scratch catalog -> reviewed fixture -> closed release rehearsal.

All approvals, access values, runtime receipts and backups here are TEST FIXTURES.
They cannot authorize a real edition, represent production evidence or send effects.
"""
import copy
import hashlib
import json
import os

from fastapi.testclient import TestClient
import pytest

from hub.main import app
from preschool import (audience, evaluation, extract, family_gate, hybrid, ical,
                       processor, publication, sources, store, website, _view_key)
from preschool.support import bridge, luna

ORIGIN = 'https://families.example.invalid'
PASSWORD = 'Synthetic rehearsal password only'
FEED = 'f' * 43
BODY = ('Sprouts class outing May 7, 2027.\n\nSmall wreath: $25.\n\n'
        'Large wreath: $40.\n\nGarland: $15.')
MID = 'abcdef1234567890'
SENDER = 'teacher@example.invalid'


def raw_fact(paragraph=1, **changes):
    return {'kind': 'event', 'title': 'Class outing', 'details': '', 'date': '2027-05-07',
            'time': None, 'location': None, 'url': None, 'uncertainty': '',
            'paragraph_id': paragraph, **changes}


@pytest.fixture
def synthetic(monkeypatch):
    def denied(*a, **kw):
        pytest.fail('Offline workflow cannot contact mail, providers or devices')
    monkeypatch.setattr(bridge, 'get', denied)
    monkeypatch.setattr(bridge, 'post', denied)
    monkeypatch.setattr(sources, 'collect', lambda *a, **kw: sources.Evidence([], [], []))
    with store.account('shared'):
        store.set_setting('family_group_addresses', json.dumps(['class@example.invalid']))
    return monkeypatch


def message(body=BODY):
    return {'id': MID, 'from': SENDER, 'subject': 'Synthetic class notice',
            'date': 'Sat, 01 May 2027 09:00:00 -0700', 'text': body,
            'recipients': {'to': 'class@example.invalid'}, 'attachments': []}


def approved_bundle(monkeypatch):
    proposals = [raw_fact()] + [raw_fact(index, kind='update', title=title, details=price, date=None)
                   for index, title, price in ((2, 'Small wreath', '$25.'), (3, 'Large wreath', '$40.'), (4, 'Garland', '$15.'))]
    monkeypatch.setattr(luna, 'complete', lambda request: {'items': copy.deepcopy(proposals)})
    prepared = processor.prepare_message('owner@example.invalid', MID, SENDER, message())
    assert processor.read_message('owner@example.invalid', MID, SENDER, prepared=prepared) == 4
    rows = [{**item, 'view_key': _view_key('item', 'personal', item['id'])} for item in store.all_items()]
    runner = hybrid.Runner(hybrid.Policy(enabled=True, escalation=True, audit_every=1))
    for item in rows:
        role = 'sprouts_instruction' if item['kind'] == 'event' else 'school_context'
        result = runner.judge({'copy': family_gate.public_copy(item), 'evidence': item['source_quote'],
                               'display_role': role, 'as_of_date': '2027-05-01'},
                              evaluation.FixtureJudge(hybrid.CHEAP, dict.fromkeys(hybrid.AXES, .99)),
                              evaluation.FixtureJudge(hybrid.STRONG, dict.fromkeys(hybrid.AXES, .99)))
        assert result['state'] == 'eligible_in_trial' and result['human_review_required']
    # This is explicitly synthetic authored copy, not an automatic real review.
    articles = {slug: {'slug': slug, 'title': title, 'body': 'Synthetic reviewed class guidance.',
                      'source_keys': []} for slug, (title, _) in website.TOPICS.items()}
    prices = [item for item in rows if item['kind'] == 'update']
    articles['updates'].update(body='Optional school-wide context: ' +
        ' '.join(item['title'] + ': ' + item['details'] for item in prices),
        source_keys=[item['view_key'] for item in prices])
    site = website.project(rows, [], articles, '2027-05-01')
    proposal = publication.candidate({**site, 'all_sources': rows, 'articles': articles}, [])
    assert not proposal['holds'] and len(proposal['events']) == 1
    with pytest.raises(ValueError, match='privacy'):
        publication.save_preview(proposal, 0)
    publication.save_preview(proposal, 0, reviewed=True)  # fixture approval only
    snapshot = publication.latest()['payload']
    feed = publication.calendar_feed(snapshot)
    parsed = ical.parse(feed.decode())
    assert [(event['title'], event['date']) for event in parsed] == [(row['title'], row['date']) for row in snapshot['events']]
    assert all(row['uid'] + '@preschool.hub.invalid' in feed.decode() for row in snapshot['events'])
    for private in (SENDER, 'source_quote', 'paragraph_id', 'message_id', 'gmail_url'):
        assert private not in publication.serialized(snapshot)
    return snapshot


def test_source_through_private_reader_and_calendar(synthetic):
    snapshot = approved_bundle(synthetic)
    client = TestClient(app)
    response = client.get('/preschool/')
    assert response.status_code == 200
    assert 'Class outing' in response.text and 'Optional school-wide context' in response.text
    assert 'no-store' in response.headers['cache-control']
    assert client.get('/preschool/calendar.ics').content == publication.calendar_feed(snapshot)
    assert client.get('/preschool/style.css').status_code == 200
    assert client.get('/preschool/', headers={'Tailscale-User-Login': 'other@example.invalid'}).status_code == 403
    for path in ('/login', '/logout', '/subscriptions', '/calendar/' + FEED + '.ics'):
        assert client.get(path).status_code == 404


@pytest.mark.parametrize('failure', ['later-section', 'attachment', 'database'])
def test_failed_extraction_never_commits_partial_catalog(synthetic, failure):
    source = BODY
    attachment = 'Class reminder.'
    if failure == 'later-section':
        source = ('Sprouts class outing May 7, 2027. ' + 'x' * 7000) + '\n\nBring boots.'
    if failure == 'attachment':
        synthetic.setattr(sources, 'collect', lambda *a, **kw: sources.Evidence(
            [{'kind': 'plain text', 'text': attachment}], [], []))
    calls = []
    def complete(request):
        calls.append(request)
        return {'items': [raw_fact()]} if len(calls) == 1 else {'items': [raw_fact(999)]}
    synthetic.setattr(luna, 'complete', complete)
    prepared = processor.prepare_message('owner@example.invalid', MID, SENDER, message(source))
    if failure == 'database':
        # A later bad row rolls back earlier inserts and mailbox binding together.
        item = raw_fact()
        item.pop('paragraph_id')
        item['source_quote'] = 'Sprouts class outing May 7, 2027.'
        with pytest.raises(KeyError):
            store.save_message('owner@example.invalid', {'id': MID, 'sender': SENDER, 'subject': 'Synthetic',
                               'date': '', 'has_attachments': False},
                               [extract.validate({'items': [item]}, BODY)[0], {}])
    else:
        with pytest.raises(extract.ExtractionError):
            processor.read_message('owner@example.invalid', MID, SENDER, prepared=prepared)
        assert len(calls) == 2
    assert not store.processed(MID) and not store.all_items()


def test_audience_or_review_cannot_be_bypassed_by_successful_trial(synthetic):
    rows = [{'view_key': 'a' * 64, 'kind': 'event', 'title': 'Class outing', 'details': '',
             'date': '2027-05-07', 'time': None, 'location': None, 'url': None,
             'uncertainty': '', 'dismissed': False, 'sharing_audience': 'private'}]
    site = website.project(rows, [], {}, '2027-05-01')
    proposal = publication.candidate({**site, 'all_sources': rows, 'articles': {}}, [])
    assert not proposal['events'] and proposal['holds']
    with pytest.raises(ValueError, match='privacy'):
        publication.save_preview(proposal, 0, reviewed=False)
