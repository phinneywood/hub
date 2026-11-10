"""Inbox-wide cleanups: fixed queries, preview before plan, batched apply, undo. Synthetic only."""
import pytest
from fastapi.testclient import TestClient

from hub import actions, apply, bridge, senders, store
from hub.main import app
from tests.test_app import ORIGIN, STATE, fake_get

client = TestClient(app)


@pytest.fixture(autouse=True)
def reads(monkeypatch):
    monkeypatch.setattr(bridge, 'get', fake_get)


def test_sweep_queries_are_fixed_and_never_touch_starred():
    old = actions.sweep(365)
    assert actions.sweep_query(old) == 'in:inbox -is:starred -is:important older_than:365d'
    promo = actions.sweep(30, 'promotions', protect_important=False)
    assert actions.sweep_query(promo) == 'in:inbox -is:starred category:promotions older_than:30d'
    read = actions.sweep(90, effect='read')
    assert actions.sweep_query(read) == 'in:inbox -is:starred -is:important is:unread older_than:90d'
    assert read['effects'] == {'skip_inbox': False, 'mark_read': True, 'label': None, 'label_new': False}
    assert actions.existing_query(old) == actions.sweep_query(old)
    for bad in [lambda: actions.sweep(0), lambda: actions.sweep(30, 'from:x'), lambda: actions.sweep(30, effect='delete'),
                lambda: actions.sweep(True)]:
        with pytest.raises(actions.ActionError):
            bad()


def test_describe_sweep():
    assert actions.describe_sweep(actions.sweep(365), 5000, False) == \
        'Archive more than 5,000 inbox messages older than 1 year. Never starred or important mail.'
    assert 'stay in the inbox' in actions.describe_sweep(actions.sweep(90, effect='read'))
    assert actions.age_text(730) == '2 years' and actions.age_text(180) == '6 months' and actions.age_text(7) == '7 days'


def test_preview_is_read_only_and_rejects_unlisted_choices(monkeypatch):
    monkeypatch.setattr(bridge, 'post', lambda *a: pytest.fail('preview must not write'))
    page = client.get('/gmail/sweep/preview?kind=archive&days=365&protect=1')
    assert page.status_code == 200 and '1,234' in page.text and 'Add to plan' in page.text
    assert 'Choose one of the listed ages' in client.get('/gmail/sweep/preview?kind=archive&days=12').text
    assert 'Choose a category' in client.get('/gmail/sweep/preview?kind=category&days=30&category=inbox').text
    assert client.get('/gmail/sweep').status_code == 200


def test_add_puts_sweep_in_plan_and_shows_on_plan_page():
    response = client.post('/gmail/sweep/add', headers=ORIGIN, data={'kind': 'category', 'days': 30, 'category': 'social', 'protect': 1},
                           follow_redirects=False)
    assert response.status_code == 303
    [item] = store.items()
    assert item['decision'] == 'sweep' and item['count'] == 1234 and item['spec']['category'] == 'social'
    assert not senders.valid_sender(item['sender'])  # never mistaken for a sender
    plan = client.get('/gmail/plan').text
    assert 'Inbox cleanup' in plan and 'Social inbox messages older than 30 days' in plan
    # Adding the same cleanup again replaces the planned one instead of duplicating it.
    client.post('/gmail/sweep/add', headers=ORIGIN, data={'kind': 'category', 'days': 30, 'category': 'social', 'protect': 1})
    assert len(store.items()) == 1


def test_sweep_does_not_affect_sender_review_queue():
    client.post('/gmail/sweep/add', headers=ORIGIN, data={'kind': 'archive', 'days': 365, 'protect': 1})
    assert client.get('/gmail/quiet').status_code == 200


def test_apply_runs_sweep_in_batches_and_undoes_each(monkeypatch):
    STATE['profile'] = {**STATE['profile'], 'organization_paused': False}
    spec = actions.sweep(365)
    store.set_decision(actions.sweep_key(spec), 'sweep', 5000, False, spec=spec)
    posts = []
    def post(path, body):
        posts.append((path, body))
        if path == '/gmail/cleanup/preview':
            assert body['query'] == actions.sweep_query(spec) and body['remove_label_ids'] == ['INBOX']
            return {'preview_id': f'p{len(posts)}', 'selected_messages': 1000}
        if path == '/gmail/cleanup/apply':
            batch = sum(1 for p, _ in posts if p == path)
            return {'processed': 1000, 'change_ids': [f'c{batch}'], 'complete': True, 'more_matches_exist': batch < 3}
        if path == '/gmail/undo':
            return {'undone': True}
        raise AssertionError(path)
    monkeypatch.setattr(bridge, 'post', post)
    assert apply.run(budget=5)['remaining'] == 0
    [item] = store.items()
    assert item['status'] == 'done' and item['archived'] == 3000 and item['change_ids'] == ['c1', 'c2', 'c3']
    assert not any(p == '/gmail/filters/apply' for p, _ in posts)  # a sweep never creates a filter
    apply.start_undo(item['id'])
    apply.run(budget=5)
    assert store.items()[0]['status'] == 'undone'
    assert [b['change_id'] for p, b in posts if p == '/gmail/undo'] == ['c3', 'c2', 'c1']


def test_large_sweep_counts_quickly_then_fully_on_request(monkeypatch):
    seen = []
    def get(path, ttl=60, **params):
        if path == '/gmail/count':
            seen.append(params['max'])
            return {'count': params['max'], 'exact': False}
        return fake_get(path, ttl, **params)
    monkeypatch.setattr(bridge, 'get', get)
    page = client.get('/gmail/sweep/preview?kind=archive&days=365&protect=1')
    assert 'more than 1,000 messages' in page.text and 'Count up to 5,000' in page.text
    page = client.get('/gmail/sweep/preview?kind=archive&days=365&protect=1&full=1')
    assert 'more than 5,000 messages' in page.text and 'Count up to' not in page.text
    assert seen == [1000, 5000]
