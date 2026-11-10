"""Luna sender assessments and the review list. Synthetic data and a fake model only."""
import json

import pytest
from fastapi.testclient import TestClient

from hub import bridge, luna, store
from hub.main import app
from hub.tools.gmail import assess
from tests.test_app import ORIGIN, fake_get

client = TestClient(app)
SENDER = 'news@loud.example'


def action(**over):
    base = {"unsupported": None, "future": True, "existing": True, "older_than_days": None, "subject_include": [],
            "subject_exclude": [], "skip_inbox": True, "mark_read": False, "label": None}
    return {**base, **over}


REPLY = {"assessment": "A newsletter you rarely open.", "options": [
    {"kind": "action", "title": "Skip inbox, keep receipts", "why": "92% unread.", "action": action(subject_exclude=["receipt"])},
    {"kind": "action", "title": "Sneaky", "why": "x", "action": action(subject_include=["from:boss@x.example OR"])},
    {"kind": "archive_after_30_days", "title": "Archive after 30 days", "why": "Sometimes useful.", "action": None},
    {"kind": "keep", "title": "Keep", "why": "y", "action": None},
]}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(bridge, 'get', fake_get)
    monkeypatch.setattr(luna, 'configured', lambda: True)
    assess._cache.clear()
    sent = []
    def transport(request):
        sent.append(request)
        return json.loads(json.dumps(REPLY))
    monkeypatch.setattr(luna, 'complete', transport)
    return sent


def test_assessment_validates_every_option_and_sends_no_bodies(setup):
    page = client.post('/gmail/assess', headers=ORIGIN, data={'sender': SENDER, 'source': 'quiet'})
    assert page.status_code == 200
    assert 'A newsletter you rarely open.' in page.text and 'Skip inbox, keep receipts' in page.text
    assert 'Sneaky' not in page.text and "didn't pass its safety checks" in page.text  # unsafe subject term dropped
    assert 'Set up 30-day rule' in page.text
    assert 'name="decision" value="keep"' not in page.text  # only the first three options are considered
    [request] = setup
    data = json.loads(request['messages'][1]['content'][len('<data>'):-len('</data>')])
    assert set(data) >= {'sender', 'messages_in_inbox', 'unread_share_percent', 'messages_last_30_days', 'recent_messages'}
    assert all(set(m) == {'subject', 'age_days', 'unread'} for m in data['recent_messages'])
    assert 'snippet' not in request['messages'][1]['content']
    assert request['provider']['zdr'] is True and request['provider']['allow_fallbacks'] is False
    assert not store.items()  # assessing never plans anything


def test_cached_assessment_does_not_call_luna_twice(setup):
    client.post('/gmail/assess', headers=ORIGIN, data={'sender': SENDER})
    client.post('/gmail/assess', headers=ORIGIN, data={'sender': SENDER})
    assert len(setup) == 1
    assert 'Skip inbox, keep receipts' in client.get(f'/gmail/assess?sender={SENDER}').text


def test_feedback_reaches_luna_with_previous_options(setup):
    client.post('/gmail/assess', headers=ORIGIN, data={'sender': SENDER})
    client.post('/gmail/assess', headers=ORIGIN, data={'sender': SENDER, 'feedback': 'Label them instead',
                                                       'previous': json.dumps([{'kind': 'keep', 'title': 'Keep', 'action': None}])})
    data = json.loads(setup[-1]['messages'][1]['content'][len('<data>'):-len('</data>')])
    assert data['owner_feedback_on_previous_options'] == 'Label them instead' and data['previous_options']


def test_opted_out_sender_never_reaches_luna(setup):
    store.set_ai_blocked(SENDER, True)
    page = client.post('/gmail/assess', headers=ORIGIN, data={'sender': SENDER})
    assert 'turned off Luna for this sender' in page.text and not setup


def test_picking_an_option_plans_it_and_returns_to_queue(setup):
    page = client.post('/gmail/assess', headers=ORIGIN, data={'sender': SENDER})
    spec = page.text.split('name="spec" value="', 1)[1].split('"', 1)[0]
    import html
    response = client.post('/gmail/quiet/custom/accept', headers=ORIGIN, follow_redirects=False,
                           data={'sender': SENDER, 'spec': html.unescape(spec), 'source': 'quiet'})
    assert response.status_code == 303 and response.headers['location'] == '/gmail/quiet'
    [item] = store.items()
    assert item['decision'] == 'custom' and item['spec']['match']['exclude'] == ['receipt']


def test_luna_error_is_shown_plainly(monkeypatch):
    def broken(request):
        raise luna.LunaError("Luna couldn't be reached")
    monkeypatch.setattr(luna, 'complete', broken)
    page = client.post('/gmail/assess', headers=ORIGIN, data={'sender': SENDER})
    assert "Luna couldn&#39;t be reached" in page.text


def test_invalid_sender_is_rejected(setup):
    response = client.post('/gmail/assess', headers=ORIGIN, data={'sender': 'x OR from:y'}, follow_redirects=False)
    assert response.status_code == 303 and not setup


def test_suggestions_are_cautious():
    assert assess.suggestion({'covered': True, 'unread_share': 10}) == 'archive'
    assert assess.suggestion({'covered': False, 'unread_share': 95}) == 'filter_archive'
    assert assess.suggestion({'covered': False, 'unread_share': 50}) == ''
    assert assess.suggestion({'covered': False, 'unread_share': 10}) == ''  # never pre-selects keep
    assert assess.suggestion({'covered': False, 'unread_share': None}) == ''


def test_review_list_adds_only_chosen_rows():
    page = client.get('/gmail/review')
    assert page.status_code == 200 and 'Add choices to plan' in page.text
    response = client.post('/gmail/review', headers=ORIGIN, follow_redirects=False, data={
        'sender_0': 'a@shop.example', 'decision_0': 'filter_archive', 'count_0': '40', 'exact_0': '1',
        'sender_1': 'b@shop.example', 'decision_1': '', 'count_1': '5',
        'sender_2': 'c OR d', 'decision_2': 'archive',
        'sender_3': 'e@shop.example', 'decision_3': 'delete'})
    assert response.status_code == 303 and 'added=1' in response.headers['location']
    [item] = store.items()
    assert item['sender'] == 'a@shop.example' and item['decision'] == 'filter_archive' and item['count'] == 40
