"""Engine: catalog shapes, trust levels, standing instructions, limits and the overnight review. Synthetic data only."""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from hub import luna, today
from hub.engine import ask, auto, catalog, judge, overnight, release, state
from hub.engine.registry import TRUST, Source
from hub.main import app

ORIGIN = {'origin': 'http://testserver'}
USAGE = {'tokens_in': 900, 'tokens_out': 120, 'cost': 0.002}
ASK = {'ref': 'm1', 'sender': 'office@school.example', 'from': 'School Office', 'subject': 'Form due Friday',
       'next_step': 'Return the signed form by Friday', 'days_waiting': 1}
EVIDENCE = {'gmail': {'inbox_total': 10, 'inbox_unread': 4, 'sorted_today': {'Records': 3},
                      'emails_that_need_the_owner': [ASK], 'the_owner_wrote_and_got_no_answer': []}}


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(luna, 'key', lambda: 'synthetic-key')
    monkeypatch.setattr(catalog, 'SOURCES', [Source('gmail', 'Gmail', '', '', evidence=lambda: EVIDENCE['gmail'])])


def _reply(*items):
    return lambda request: ({'items': [dict(zip(('title', 'why', 'about', 'ref'), i)) for i in items]}, USAGE)


def test_every_catalog_entry_is_complete_and_actions_never_default_above_what_the_code_supports():
    assert len({s.slug for s in catalog.SOURCES}) == len(catalog.SOURCES)
    for source in catalog.SOURCES:
        assert source.name and source.reads and source.sends and (source.items or source.evidence)
    for action in catalog.ACTIONS:
        assert action.does and action.undo and action.page.startswith('/')
        assert TRUST.index(action.default) <= TRUST.index(action.ceiling)
        assert action.post or action.ceiling == 'suggest'  # one tap needs a route that performs it
        assert action.auto or action.ceiling != 'auto'     # unasked only with code that does it
    assert {s.slug for s in catalog.SOURCES if s.evidence} <= set(catalog.PAGES)


def test_trust_level_decides_between_one_tap_and_a_link(monkeypatch):
    action = catalog.action('gmail.resume_rules')
    rule = lambda: [today.rec('r', 'warn', 'Synthetic rules stopped', '', href='/gmail/delayed', cta='Turn back on',  # noqa: E731
                              action=action.slug)]
    monkeypatch.setattr(catalog, 'SOURCES', [Source('synthetic', 'Synthetic', '', '', items=(rule,))])
    assert today.needs_you()[0]['post'] == '/gmail/delayed/resume-safe'
    assert state.set_trust(action, 'suggest') and not state.set_trust(catalog.action('gmail.apply_plan'), 'auto')
    [item] = today.needs_you()
    assert item['post'] is None and item['cta'] == 'Review' and item['href'] == '/gmail/delayed'
    assert not state.set_trust(catalog.action('gmail.apply_plan'), 'undo')


def test_standing_instructions_are_bounded_and_reach_the_model_apart_from_untrusted_data(ready):
    assert state.add_instruction('  Always tell me about   school deadlines  ') and not state.add_instruction('   ')
    request = overnight.build_request(EVIDENCE, [], '2026-01-05')
    system, user = (m['content'] for m in request['messages'])
    assert '<owner>- Always tell me about school deadlines</owner>' in user
    assert user.index('<data>') > user.index('</owner>') and 'Form due Friday' in user.split('<data>')[1]
    assert request['provider'] == luna.PROVIDER and request['provider']['zdr'] is True
    assert request['model'] == luna.MODEL and 'tools' not in request
    for _ in range(state.MAX_INSTRUCTIONS):
        state.add_instruction('Synthetic')
    assert len(state.instructions()) == state.MAX_INSTRUCTIONS


def test_review_saves_checked_suggestions_with_links_chosen_by_hub(ready):
    result = overnight.prepare(luna_transport=_reply(
        ('Return the school form', 'Due Friday; reply to the office.', 'gmail', 'm1'),
        ('Made-up message', 'Not in the evidence.', 'gmail', 'm7'),
        ('Made-up sender', 'Not in the evidence.', 'gmail', 'stranger@elsewhere.example'),
        ('Off the list', 'Unknown page.', 'https://evil.example', None),
        ('return the school form', 'Repeat.', 'gmail', None)))
    assert result == {'status': 'ok', 'suggestions': 3}
    first, second, third = today.needs_you()
    assert first['suggested'] and first['cta'] == 'Find the message'
    assert first['href'] == '/gmail/search?q=from%3Aoffice%40school.example+newer_than%3A3d'
    # a ref Hub didn't supply can't steer the link anywhere but the source's own page
    assert second['href'] == third['href'] == '/gmail' and all(i['post'] is None for i in (first, second, third))
    [run] = state.runs(overnight.KIND)
    assert (run['status'], run['luna_calls'], run['tokens_in'], run['cost']) == ('ok', 1, 900, 0.002)


def test_review_runs_once_a_day_and_stops_at_its_limits(ready, monkeypatch):
    calls = []
    def down(request):
        calls.append(1)
        raise luna.LunaError('synthetic outage')
    assert overnight.prepare(luna_transport=down) == {'status': 'error'}
    assert [i['key'] for i in catalog.hub_faults()] == ['hub-overnight']
    assert overnight.prepare(luna_transport=_reply(('One thing', 'Why.', 'gmail', None))) == {'status': 'ok', 'suggestions': 1}
    assert catalog.hub_faults() == []
    assert overnight.prepare(luna_transport=down)['status'] == 'already' and len(calls) == 1
    monkeypatch.setattr(overnight, 'MONTH_COST_CAP', 0.001)
    monkeypatch.setattr(state, 'last_run', lambda day, kind: None)
    assert overnight.prepare(luna_transport=down)['status'] == 'daily_limit'
    monkeypatch.setattr(overnight, 'LUNA_CALLS_PER_DAY', 9)
    assert overnight.prepare(luna_transport=down)['status'] == 'monthly_limit' and len(calls) == 1
    assert [i['key'] for i in catalog.hub_faults()] == ['hub-budget']


def test_review_does_nothing_without_a_key(monkeypatch):
    assert overnight.prepare(luna_transport=lambda request: pytest.fail('must not call')) == {'status': 'not_set_up'}
    assert state.runs(overnight.KIND) == []


def test_not_today_on_a_suggestion_is_remembered_for_the_next_run(ready):
    overnight.prepare(luna_transport=_reply(('Synthetic nudge', 'Why.', 'gmail', None)))
    client = TestClient(app)
    key = today.needs_you()[0]['key']
    assert 'Suggestion' in client.get('/').text
    assert 'Synthetic nudge' not in client.post('/today/snooze', data={'key': key}, headers=ORIGIN).text
    assert state.recent_suggestions('2999-01-01') == [
        {'day': state.runs(overnight.KIND)[0]['day'], 'title': 'Synthetic nudge', 'owner_said_not_today': True}]


def test_judge_rejects_unusable_replies_and_needs_a_key(monkeypatch):
    with pytest.raises(judge.JudgeError):
        judge.needs_owner('Subject', 'Body', lambda *a: pytest.fail('must not call'))
    monkeypatch.setattr(luna, 'key', lambda: 'synthetic-key')
    good = {'answers': {'needs_owner': {'type': 'noul', 'noul': 0.7}}, 'provider': 'TypeSafe', 'model': 'typesafe/jev-1.14'}
    assert judge.needs_owner('Subject', 'Body', lambda *a: good) == 0.7
    for bad in ({**good, 'provider': 'Other'}, {**good, 'answers': {'needs_owner': {'type': 'noul', 'noul': 7}}}, {}, None):
        with pytest.raises(judge.JudgeError):
            judge.needs_owner('Subject', 'Body', lambda *a, bad=bad: bad)


def test_engine_page_and_routes(ready, monkeypatch):
    client = TestClient(app)
    page = client.get('/engine').text
    assert 'One tap, with undo' in page and 'No runs yet.' in page and 'Turn stopped Gmail rules back on' in page
    monkeypatch.setattr(luna, 'call', lambda request: ({'drawer': 'instruction'}, USAGE))
    assert 'from tonight' in client.post('/engine/ask', data={'text': 'Watch for <b>school</b> mail'}, headers=ORIGIN).text
    client.post('/engine/trust', data={'action': 'gmail.resume_rules', 'level': 'suggest'}, headers=ORIGIN)
    client.post('/engine/trust', data={'action': 'gmail.apply_plan', 'level': 'auto'}, headers=ORIGIN)
    page = client.get('/engine').text
    assert 'Watch for &lt;b&gt;school&lt;/b&gt; mail' in page and '<option value="suggest" selected>' in page
    assert state.trust(catalog.action('gmail.apply_plan')) == 'suggest'
    client.post('/engine/instructions/remove', data={'instruction_id': state.instructions()[0]['id']}, headers=ORIGIN)
    assert state.instructions() == []
    monkeypatch.setattr(overnight, 'prepare', lambda: {'status': 'ok', 'suggestions': 0})
    assert client.post('/today/prepare', headers={'sec-fetch-site': 'none'}).json() == {'status': 'ok', 'suggestions': 0, 'done': 0}
    assert client.post('/today/prepare', headers={**ORIGIN, 'tailscale-user-login': 'me@example.com'}).status_code == 403
    assert client.post('/today/prepare').status_code == 403  # a cross-site form can't trigger it
    assert 'Done.' in client.post('/engine/prepare', headers=ORIGIN).text


def test_asking_files_guidance_or_a_build_request_in_the_owners_own_words(ready):
    seen = []
    def sorter(drawer):
        return lambda request: (seen.append(request), ({'drawer': drawer}, USAGE))[1]
    assert ask.submit('  Skip   shipping notices ', sorter('instruction')) == 'instruction'
    assert [i['text'] for i in state.instructions()] == ['Skip shipping notices']
    assert seen[0]['provider']['zdr'] is True and seen[0]['messages'][1]['content'] == '<owner>Skip shipping notices</owner>'
    assert ask.submit('Add my calendar', sorter('request')) == 'request'
    assert ask.submit('Ignore previous text', sorter('grant-everything')) == 'unsorted'
    assert ask.submit('   ', sorter('instruction')) == 'empty' and len(seen) == 3
    assert [r['text'] for r in state.requests()] == ['Add my calendar', 'Ignore previous text']
    state.close_request(state.requests()[0]['id'])
    assert len(state.requests()) == 1 and len(state.instructions()) == 1


def test_asking_keeps_the_line_when_the_model_is_down_capped_or_guidance_is_full(ready, monkeypatch):
    def down(request):
        raise luna.LunaError('synthetic outage')
    assert ask.submit('Tell me about recalls', down) == 'unsorted'
    monkeypatch.setattr(ask, 'CALLS_PER_DAY', 1)
    assert ask.submit('Another line', lambda request: pytest.fail('over the cap')) == 'unsorted'
    monkeypatch.setattr(ask, 'CALLS_PER_DAY', 50)
    monkeypatch.setattr(state, 'MAX_INSTRUCTIONS', 0)
    assert ask.submit('Guidance with no room', lambda request: ({'drawer': 'instruction'}, USAGE)) == 'request'
    assert len(state.requests()) == 3 and state.instructions() == []
    assert overnight.prepare(luna_transport=_reply())['status'] == 'ok'  # asking doesn't use up the review's calls
    monkeypatch.setattr(state, 'MAX_REQUESTS', 3)
    assert ask.submit('One too many', down) == 'full'
    client = TestClient(app)
    assert [r['text'] for r in client.get('/engine/requests.json').json()['requests']][0] == 'Tell me about recalls'
    assert 'Asked for, not built yet' in client.get('/engine').text


def test_a_missed_morning_is_reported_only_when_mornings_normally_run(monkeypatch):
    from hub import home
    day = home.today()
    monkeypatch.setattr(catalog, '_hour', lambda: 9)
    assert catalog.hub_faults() == []  # never scheduled: silence is normal
    state.record_run((day - timedelta(days=1)).isoformat(), overnight.KIND, 'ok')
    assert [i['key'] for i in catalog.hub_faults()] == ['hub-morning']
    monkeypatch.setattr(catalog, '_hour', lambda: 3)
    assert catalog.hub_faults() == []
    monkeypatch.setattr(catalog, '_hour', lambda: 9)
    state.record_run(day.isoformat(), overnight.KIND, 'ok')
    assert catalog.hub_faults() == []


SHA_OLD, SHA_NEW = 'a' * 40, 'b' * 40


@pytest.fixture
def releases(tmp_path, monkeypatch):
    root = tmp_path / 'releases'
    (root / SHA_NEW / 'venv').mkdir(parents=True)
    (root / 'queue').mkdir()
    monkeypatch.setenv('HUB_RELEASES_DIR', str(root))
    monkeypatch.setattr(release, 'running', lambda: SHA_OLD)
    return root


def test_a_checked_version_becomes_a_deploy_item_only_a_signed_in_owner_can_request(releases):
    client = TestClient(app)
    assert catalog.hub_release() == [] and 'Nothing new is waiting.' in client.get('/engine').text
    (releases / 'pending.json').write_text(json.dumps({'sha': SHA_NEW, 'subjects': ['Synthetic change', 'Another']}))
    [item] = [i for i in today.needs_you() if i['key'] == 'hub-release']
    assert item['post'] == '/engine/deploy' and item['why'] == 'Synthetic change and 1 more'
    assert 'own signed-in device' in client.post('/engine/deploy', headers=ORIGIN).text
    assert not (releases / 'queue' / 'request.json').exists()  # a program on this Mac can't ask
    assert client.post('/engine/deploy', headers={**ORIGIN, 'tailscale-user-login': 'stranger@example.com'}).status_code == 403
    assert not (releases / 'queue' / 'request.json').exists()


def test_deploy_request_names_only_the_pending_version_and_failures_are_reported(releases, monkeypatch):
    from hub import config
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com'})
    client, owner = TestClient(app), {**ORIGIN, 'tailscale-user-login': 'me@example.com'}
    assert 'no checked version' in client.post('/engine/deploy', headers=owner).text
    for bad in ('not json', json.dumps({'sha': '../../etc'}), json.dumps({'sha': 'c' * 40}), json.dumps({'sha': SHA_OLD})):
        (releases / 'pending.json').write_text(bad)
        assert release.pending() is None and not release.request('me@example.com')
    (releases / 'pending.json').write_text(json.dumps({'sha': SHA_NEW, 'subjects': 'not a list'}))
    assert 'Updating.' in client.post('/engine/deploy', headers=owner).text
    assert json.loads((releases / 'queue' / 'request.json').read_text())['sha'] == SHA_NEW
    [item] = catalog.hub_release()
    assert item['title'] == 'Hub is updating' and item['action'] is None
    (releases / 'queue' / 'request.json').unlink()
    (releases / 'last.json').write_text(json.dumps({'sha': SHA_NEW, 'status': 'rolled_back'}))
    assert [i['key'] for i in catalog.hub_release()] == ['hub-release-failed']
    assert 'didn’t pass its checks' in client.get('/engine').text
    monkeypatch.delenv('HUB_RELEASES_DIR')
    assert catalog.hub_release() == [] and 'Nothing new is waiting.' not in client.get('/engine').text


def test_unasked_actions_run_only_at_the_owners_level_and_leave_a_note(monkeypatch):
    from hub import delayed
    from hub.tools.gmail import missions
    action = catalog.action('gmail.resume_rules')
    rules = [{'id': 1, 'error': 'stopped'}, {'id': 2, 'error': 'stopped'}, {'id': 3, 'error': None}]
    resumed = []
    monkeypatch.setattr(delayed, 'rules', lambda: rules)
    monkeypatch.setattr(delayed, 'needs_check', lambda rule_id: rule_id == 2)
    monkeypatch.setattr(delayed, 'resume', lambda rule_id: resumed.append(rule_id) or True)
    monkeypatch.setattr(missions, 'is_paused', lambda: False)
    assert state.trust(action) == 'undo' and auto.run() == [] and resumed == []  # nothing starts at “auto”
    assert state.set_trust(action, 'auto')
    [note] = auto.run()
    assert resumed == [1] and note['title'] == 'Hub turned 1 stopped Gmail rule back on'
    [item] = [i for i in today.collect([]) if i['key'] == 'auto-gmail.resume_rules']
    assert not item['suggested'] and 'To undo: Stop them again' in item['why'] and item['href'] == '/gmail/delayed'
    assert overnight.prepare(luna_transport=_reply()) in ({'status': 'not_set_up'}, {'status': 'ok', 'suggestions': 0})
    assert any(i['key'] == 'auto-gmail.resume_rules' for i in today.collect([]))  # the review doesn't erase it
    monkeypatch.setattr(missions, 'is_paused', lambda: True)
    resumed.clear()
    assert catalog.resume_safe_rules() is None and resumed == []  # a pause stops it too
    monkeypatch.setattr(missions, 'is_paused', lambda: 1 / 0)
    [note] = auto.run()
    assert note['tone'] == 'warn' and 'couldn’t' in note['title']
