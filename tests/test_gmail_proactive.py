"""Gmail work Hub does ahead of the owner: the nightly preparation, the offers it leads to and the rules it may run.

Synthetic data only; the Gmail helper is a fake.
"""
import json
import stat
import time
from contextlib import closing
from datetime import date

import pytest
from fastapi.testclient import TestClient

from hub import bridge, delayed, home, model_key, store, today, worker
from hub.engine import auto, catalog, prep, state
from hub.engine.registry import Source
from hub.main import app
from hub.tools.gmail import assess, missions, nightly
from tests.test_app import ORIGIN, fake_get

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(bridge, 'get', fake_get)
    monkeypatch.setattr(missions, 'is_paused', lambda: False)
    monkeypatch.setattr(assess, 'is_paused', lambda: False)


def _save(rows=(), old=0, at=None, backlog=0, how='exact'):
    saved = {'at': time.time() if at is None else at, 'sample': 500, 'rows': list(rows),
             'old_mail': {'count': old, 'exact': True}, 'backlog': {'count': backlog, 'how': how, 'floor': min(backlog, 5000)}}
    nightly._path().write_text(json.dumps(saved))
    return saved


def _sender(address, count=40, suggested='filter_archive', name=''):
    return {'sender': address, 'name': name, 'sampled': 9, 'count': count, 'exact': True, 'unread_share': 96,
            'covered': False, 'suggested': suggested}


def test_nightly_preparation_saves_names_and_counts_but_no_subjects():
    saved = nightly.prepare(now=1_800_000_000)
    assert [r['sender'] for r in saved['rows']] == ['news@loud.example', 'bob@example.com']
    assert saved['rows'][0]['name'] == 'Loud News' and saved['rows'][0]['suggested'] == 'filter_archive'
    assert saved['old_mail'] == {'count': 1234, 'exact': True}
    assert saved['backlog'] == {'count': 1234, 'how': 'exact', 'floor': 1234} == nightly.saved_backlog(now=1_800_000_000)
    text = nightly._path().read_text()
    assert 'script' not in text and stat.S_IMODE(nightly._path().stat().st_mode) == 0o600
    assert nightly.load(now=1_800_000_000 + 3600)['rows'] == saved['rows']
    assert nightly.load(now=1_800_000_000 + nightly.FRESH_SECONDS + 1) is None  # a missed morning isn't shown as fresh
    assert [r['sender'] for r in nightly.waiting(saved, {'news@loud.example'})] == ['bob@example.com']
    assert store.inbox_history()[-1]['total'] == 93012  # the day's inbox count is kept even if no page is opened


def test_a_damaged_or_missing_batch_is_ignored():
    assert nightly.load() is None and nightly.waiting(None, set()) == []
    nightly._path().write_text('{"at": "soon"')
    assert nightly.load() is None
    _save([_sender('a@shop.example'), _sender('c OR d')])
    assert [r['sender'] for r in nightly.load()['rows']] == ['a@shop.example']


def test_review_list_opens_from_the_prepared_batch_without_asking_gmail_again(monkeypatch):
    _save([_sender('deals@shop.example', name='Shop <b>Deals</b>'), _sender('paper@news.example', suggested='')])
    def no_reads(path, ttl=60, **params):
        assert path not in ('/gmail/count', '/gmail/search', '/gmail/overview', '/gmail/filters'), path
        return fake_get(path, ttl, **params)
    monkeypatch.setattr(bridge, 'get', no_reads)
    page = client.get('/gmail/review').text
    assert 'Prepared just now.' in page and 'Shop &lt;b&gt;Deals&lt;/b&gt;' in page and 'deals@shop.example' in page
    assert 'Hub pre-selected 1 you almost never open' in page and 'Apply these now' in page
    store.set_decision('deals@shop.example', 'keep')
    page = client.get('/gmail/review').text
    assert 'deals@shop.example' not in page and 'paper@news.example' in page  # decided since: gone from the batch


def test_apply_now_plans_the_choices_and_starts_them_unless_paused(monkeypatch):
    started = []
    monkeypatch.setattr(worker, 'start', lambda: started.append(1))
    form = {'sender_0': 'deals@shop.example', 'name_0': 'Shop Deals', 'decision_0': 'filter_archive', 'count_0': '40',
            'exact_0': '1', 'sender_1': 'paper@news.example', 'decision_1': '', 'then': 'apply'}
    response = client.post('/gmail/review', headers=ORIGIN, follow_redirects=False, data=form)
    assert response.headers['location'] == '/gmail/plan' and started == [1]
    [item] = store.items()
    assert item['sender'] == 'deals@shop.example' and item['display_name'] == 'Shop Deals'
    nothing = client.post('/gmail/review', headers=ORIGIN, follow_redirects=False,
                          data={'sender_0': 'paper@news.example', 'decision_0': '', 'then': 'apply'})
    assert 'added=0' in nothing.headers['location'] and started == [1]  # nothing chosen: nothing applied
    monkeypatch.setattr(assess, 'is_paused', lambda: True)
    paused = client.post('/gmail/review', headers=ORIGIN, follow_redirects=False,
                         data={**form, 'sender_0': 'other@shop.example'})
    assert paused.headers['location'] == '/gmail/plan?paused=1' and started == [1]
    assert 'Apply these now' not in client.get('/gmail/review').text
    assert client.post('/gmail/review', data=form, follow_redirects=False).status_code == 403  # cross-site


def test_the_backlog_offer_is_a_preview_and_says_how_sure_the_number_is(monkeypatch):
    monkeypatch.setattr(home, 'today', lambda: date(2026, 1, 5))
    assert today.gmail_next() == []  # nothing prepared: nothing offered
    _save(backlog=87_000, how='about')
    [item] = today.gmail_next()
    assert item['key'] == 'gmail-next-backlog' and item['title'] == 'About 87,000 inbox messages are over 30 days old'
    assert item['href'] == '/gmail/backlog' and item['cta'] == 'Preview' and item['action'] is None  # a preview, never one tap
    _save(backlog=5000, how='more')
    assert today.gmail_next()[0]['title'] == 'More than 5,000 inbox messages are over 30 days old'
    _save(backlog=1234)
    assert today.gmail_next()[0]['title'] == '1,234 inbox messages are over 30 days old'
    _save(backlog=today.OLD_MAIL_WORTH - 1)
    assert today.gmail_next() == []  # too small to be worth a step
    _save([_sender('deals@shop.example', count=300)], old=20_000)
    assert today.gmail_next() == []  # loud senders and year-old mail are tools now, not offers
    nightly._path().write_text(json.dumps({'at': time.time(), 'rows': [], 'backlog': {'count': '9000', 'how': 'sure'}}))
    assert nightly.saved_backlog() is None and today.gmail_next() == []  # a damaged count is not shown


def test_a_planned_sweep_stops_the_backlog_offer(monkeypatch):
    monkeypatch.setattr(home, 'today', lambda: date(2026, 1, 5))
    _save(backlog=20_000)
    assert today.gmail_next()[0]['key'] == 'gmail-next-backlog'
    store.set_decision('sweep:archive:365', 'sweep', 20_000)
    assert today.gmail_next() == []


def test_not_this_week_hides_an_offer_for_a_week_and_the_slot_for_the_day(monkeypatch):
    day = [date(2026, 1, 5)]
    monkeypatch.setattr(home, 'today', lambda: day[0])
    monkeypatch.setattr(home, 'SUMMARIES', {})
    monkeypatch.setattr(catalog, 'SOURCES', [Source('gmail', 'Gmail', '', '', items=(today.gmail_next,))])
    _save(backlog=20_000)
    page = client.get('/today').text
    assert '20,000 inbox messages are over 30 days old' in page and 'Not this week' in page
    gone = client.post('/today/snooze', data={'key': 'gmail-next-backlog'}, headers=ORIGIN, follow_redirects=False)
    assert gone.headers['location'] == '/today?snoozed=7' and 'Hidden for a week.' in client.get(gone.headers['location']).text
    assert today.needs_you() == []
    assert store.snoozed('2026-01-05') >= {'gmail-next', 'gmail-next-backlog'}  # the offer, and the slot for today
    for later in (date(2026, 1, 6), date(2026, 1, 11)):
        day[0] = later
        _save(backlog=20_000)
        assert today.needs_you() == [] and 'gmail-next' not in store.snoozed(later.isoformat())
    day[0] = date(2026, 1, 12)
    _save(backlog=20_000)
    assert [i['key'] for i in today.needs_you()] == ['gmail-next-backlog']
    client.post('/today/snooze', data={'key': 'tile-network'}, headers=ORIGIN)  # anything else: today only
    assert store.snoozed('2026-01-13') == set()


def _archiving(monkeypatch, each=2):
    def post(path, body):
        if path.endswith('preview'):
            sender = body['query'].split()[0][len('from:'):]
            return {'preview_id': 'p', 'selected_messages': each, 'more_matches_exist': False,
                    'sample': [{'id': str(i), 'from': sender} for i in range(each)]}
        return {'complete': True, 'processed': each, 'change_ids': ['c'], 'needs_review': False}
    monkeypatch.setattr(bridge, 'post', post)
    monkeypatch.setattr(bridge, 'get', lambda *a, **k: {'organization_paused': False})


def test_overdue_rules_are_counted_and_a_budget_leaves_the_rest_due(monkeypatch):
    _archiving(monkeypatch)
    delayed.enable('shop@example.com', now=1000)
    delayed.enable('news@example.com', now=1000)
    due = 1000 + delayed.INTERVAL
    assert delayed.overdue(now=due + delayed.INTERVAL - 1) == {'rules': 0, 'days': 0}  # up to a day late is normal
    assert delayed.overdue(now=due + 6 * delayed.INTERVAL) == {'rules': 2, 'days': 7}
    delayed.run_due(now=due, budget=0)
    assert delayed.runs() == [] and delayed.archived_at(due) == (0, 0)
    delayed.run_due(now=due, budget=60)
    assert delayed.archived_at(due) == (4, 2) and delayed.overdue(now=due + 3600) == {'rules': 0, 'days': 0}


def test_late_rules_become_a_one_tap_item_and_run_unasked_only_at_the_owners_level(monkeypatch):
    _archiving(monkeypatch)
    monkeypatch.setattr(home, 'SUMMARIES', {})
    monkeypatch.setattr(catalog, 'SOURCES', [Source('gmail', 'Gmail', '', '', items=(today.gmail_rules_due,))])
    action = catalog.action('gmail.run_rules')
    delayed.enable('shop@example.com', now=time.time() - 4 * delayed.INTERVAL)
    [item] = today.needs_you()
    assert item['title'] == '1 archive rule hasn’t run for 4 days' and item['post'] == '/gmail/delayed/run-now'
    assert item['later'] == 'Not today'
    assert state.trust(action) == 'undo' and auto.run() == [] and delayed.runs() == []  # nothing starts at “auto”
    assert state.set_trust(action, 'auto')
    [note] = auto.run()
    assert note['title'] == 'Hub archived 2 old messages from 1 sender' and note['href'] == '/gmail/delayed'
    assert 'Each check has its own Undo' in note['why']
    assert [i['key'] for i in today.needs_you()] == ['auto-gmail.run_rules']  # caught up: only the report is left
    assert auto.run() == []  # nothing due, nothing said
    delayed.enable('news@example.com', now=time.time() - 4 * delayed.INTERVAL)
    monkeypatch.setattr(missions, 'is_paused', lambda: True)
    assert catalog.run_rules() is None and today.gmail_rules_due() == [] and len(delayed.runs()) == 1  # a pause stops both


def test_sources_prepare_once_a_day_and_one_failure_does_not_stop_the_rest(monkeypatch):
    ran = []
    monkeypatch.setattr(catalog, 'SOURCES', [
        Source('broken', 'Broken', '', '', prepare=lambda: 1 / 0), Source('plain', 'Plain', '', ''),
        Source('fine', 'Fine', '', '', prepare=lambda: ran.append('fine'))])
    assert prep.run() == ['fine'] and ran == ['fine']
    assert [(a['action'], a['detail']) for a in store.recent_activity()] == [('engine_prepare_failed', 'broken')]
    from hub.engine import overnight
    monkeypatch.setattr(overnight, 'prepare', lambda: {'status': 'ok', 'suggestions': 0})
    assert client.post('/today/prepare', headers={'sec-fetch-site': 'none'}).status_code == 200 and ran == ['fine', 'fine']


def test_the_real_catalog_prepares_gmail_and_the_key_reminder():
    assert [s.slug for s in catalog.SOURCES if s.prepare] == ['gmail', 'hub']
    assert prep.run() == ['gmail', 'hub'] and nightly.load()['old_mail']['count'] == 1234


def test_old_key_reminder_waits_for_the_date_and_for_1password_to_carry_the_key(monkeypatch):
    day = [date(2026, 11, 12)]
    monkeypatch.setattr(home, 'today', lambda: day[0])
    monkeypatch.setattr(model_key, '_onepassword', lambda: 'synthetic')
    monkeypatch.setattr(model_key, '_exists', lambda service: service == model_key.LEGACY_SERVICE)
    notes = lambda: [s['key'] for s in state.suggestions(day[0].isoformat())]  # noqa: E731
    catalog.old_key_reminder()
    assert notes() == []  # too early
    day[0] = date(2026, 11, 13)
    catalog.old_key_reminder()
    [item] = [i for i in today.collect([]) if i['key'] == 'hub-old-key']
    assert item['title'] == 'Time to delete the old copy of the model key' and item['href'] == '/status'
    assert 'deploy/remove-old-model-key' in client.get('/status').text
    day[0] = date(2026, 11, 14)
    catalog.old_key_reminder()
    assert notes() == ['hub-old-key']  # again each morning until it is done
    day[0] = date(2026, 11, 15)
    monkeypatch.setattr(model_key, '_exists', lambda service: False)
    catalog.old_key_reminder()
    assert notes() == [] and 'remove-old-model-key' not in client.get('/status').text  # deleted: the reminder stops
    monkeypatch.setattr(model_key, '_exists', lambda service: True)
    monkeypatch.setattr(model_key, '_onepassword', lambda: None)
    monkeypatch.setattr(model_key, '_keychain', lambda service: 'synthetic')
    model_key._cache.update(value=None, source=None, at=float('-inf'))
    catalog.old_key_reminder()
    assert notes() == []  # 1Password isn't answering: the old copy is still the one in use, so it stays


def test_checking_for_the_old_copy_never_asks_for_its_value(monkeypatch):
    import subprocess
    seen = []
    monkeypatch.undo()
    monkeypatch.setattr(subprocess, 'run', lambda args, **kw: seen.append((args, kw)) or subprocess.CompletedProcess(args, 0))
    assert model_key.legacy_present()
    [(args, kw)] = seen
    assert '-w' not in args and args[-4:] == ['-s', 'hub-openrouter', '-a', 'hub']
    assert kw['stdout'] is subprocess.DEVNULL and kw['stderr'] is subprocess.DEVNULL


def test_sender_card_leads_with_keep_for_mail_the_owner_reads(monkeypatch):
    def reads(path, ttl=60, **params):
        if path == '/gmail/count' and 'is:unread' in params.get('query', ''):
            return {'count': 100, 'exact': True}
        return fake_get(path, ttl, **params)
    monkeypatch.setattr(bridge, 'get', reads)
    page = client.get('/gmail/quiet').text
    assert '<button class="btn primary" name="decision" value="keep"' in page
    assert '<button class="btn" name="decision" value="filter_archive"' in page and page.count('value="keep"') == 1
    monkeypatch.setattr(bridge, 'get', fake_get)
    page = client.get('/gmail/quiet').text
    assert '<button class="btn primary" name="decision" value="filter_archive"' in page
    assert '<button class="btn" name="decision" value="keep"' in page


def test_clean_up_page_leads_with_the_prepared_batch_and_the_home_tile_opens_the_inbox():
    assert home.gmail()['href'] == '/gmail' and 'Hub got ready' not in client.get('/gmail/tools').text
    _save([_sender('news@loud.example'), _sender('bob@example.com', suggested='')], old=4321)
    page = client.get('/gmail/tools').text
    assert 'Review 2 senders Hub got ready · 1 pre-selected' in page and '<strong>4,321</strong> of these are over a year old' in page
    assert home.gmail()['href'] == '/gmail'
