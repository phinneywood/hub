"""Each person sees only their own mail and calendar, everywhere in Hub (hub/people.py, docs/people.md).

Two made-up people share a made-up house. Alex is the owner: the Google account Hub reads is Alex's. Casey is allowed
in but has no Google account connected yet. Casey, and a program on this Mac with no login, get nothing of Alex's mail
or calendar from any page, screen, tap or answer, and nothing of it is even read for them; school and the house stay
everyone's. Gmail itself is never reached.
"""
import json
import time
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from hub import auth, bridge, config, luna, people, store as hub_store
from hub.agent import agent, model as chat_model, store as threads
from hub.app.ask import private
from hub.app.routes import NOT_CONNECTED
from hub.engine import catalog, registry
from hub.golden import runner
from hub.main import app
from hub.tools.gmail import missions, triage
from tests import test_app
from tests.ask_script import SEEN
from tests.test_app_ask import ask as ask_app, house, said_to_model, world  # noqa: F401 - the made-up house and Ask's stand-in
from tests.test_app_mail import mailbox  # noqa: F401 - a made-up sorted inbox
from tests.test_ask import ask as ask_chat, fictional

ALEX, CASEY = 'me@example.com', 'casey@example.com'
ORIGIN = {'origin': 'http://testserver'}
client = TestClient(app)


def by(who):
    return {'tailscale-user-login': who} if who else {}


@pytest.fixture(autouse=True)
def two_people(monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ALEX, CASEY})
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'people': [
        {'name': 'Alex', 'logins': [ALEX], 'accounts': [people.DEFAULT]},
        {'name': 'Casey', 'logins': [CASEY]}]}))


def lookup(slug):
    return next(found for found in catalog.lookups() if found.slug == slug)


def test_the_mail_and_calendar_lookups_read_alexs_account_and_the_rest_are_everyones():
    assert people.sees(ALEX, people.DEFAULT) and not people.sees(CASEY, people.DEFAULT) and not people.sees(None, people.DEFAULT)
    assert {slug: catalog.account_of(lookup(slug)) for slug in ('sorted_mail', 'inbox_trend', 'calendar_week')} == dict.fromkeys(
        ('sorted_mail', 'inbox_trend', 'calendar_week'), people.DEFAULT)
    assert catalog.account_of(lookup('today_list')) == people.DEFAULT        # mail counts and calendar titles, until it can be cut
    assert all(catalog.account_of(lookup(slug)) is None for slug in ('trip_status', 'network_status', 'ai_spend', 'home_status'))


# ---- the older pages ----

@pytest.mark.parametrize('who', [CASEY, None])
def test_the_mail_and_calendar_pages_show_someone_else_a_calm_page_and_read_nothing(who, monkeypatch):
    def untouched(*args, **kwargs):
        raise AssertionError('the mailbox was read for someone it isn’t theirs')
    monkeypatch.setattr(bridge, 'get', untouched)
    for path in ('/gmail', '/gmail/quiet', '/gmail/labels', '/gmail/search?q=a', '/gmail/senders', '/calendar'):
        page = client.get(path, headers=by(who))
        # A 200, not a 403: after every deploy the deployer loads the Gmail pages on the Mac with no login, as do Hub's
        # live checks, and a release whose pages don't answer 200 goes back by itself.
        assert page.status_code == 200 and '<h1>Not yours to open</h1>' in page.text and auth.NOT_YOURS in page.text, path
    refused = client.post('/gmail/done', data={'message_id': 'm2'}, headers={**ORIGIN, **by(who)})
    assert refused.status_code == 403 and refused.json() == {'error': auth.NOT_YOURS}


def test_alex_opens_them_as_before(monkeypatch):
    monkeypatch.setattr(bridge, 'get', test_app.fake_get)
    assert 'Bills' in client.get('/gmail/labels', headers=by(ALEX)).text
    assert 'Bills' not in client.get('/gmail/labels', headers=by(CASEY)).text


def test_all_tools_lists_the_mail_and_calendar_only_for_alex():
    def listed(who):
        menu = client.get('/tools', headers=by(who)).text.split('<nav class="menu"')[1].split('</nav>')[0]
        return {part.split('"')[0] for part in menu.split('href="')[1:]}
    assert {'/gmail', '/calendar', '/trip'} <= listed(ALEX)
    assert not {'/gmail', '/calendar'} & listed(CASEY) and '/trip' in listed(CASEY)


# ---- the app ----

@pytest.fixture
def settled(mailbox, monkeypatch):
    """The made-up inbox, with the mail helper not paused: Mail settings asks the bridge otherwise."""
    monkeypatch.setattr(missions, 'pause_state', lambda: 'ok')
    return mailbox


def test_the_mail_screens_are_alexs_and_anyone_else_is_told_theirs_isnt_connected(settled):
    assert [e['who'] for e in client.get('/app/api/mail', headers=by(ALEX)).json()['needs']] == ['Sam Rivera', 'Maple Street Dental']
    assert {row['kind']: row['level'] for row in client.get('/app/api/mail-settings', headers=by(ALEX)).json()['levels']}['reading'] == 'undo'
    for who in (CASEY, None):
        for path in ('/app/api/mail', '/app/api/mail-settings'):
            assert client.get(path, headers=by(who)).json() == {'ok': True, 'connected': False}, (who, path)


def test_caseys_taps_on_alexs_mail_change_nothing(settled):
    for path, body in (('/app/api/mail/done', {'key': 'm2'}), ('/app/api/mail/fine', {'key': 'r2'}),
                       ('/app/api/mail/answered', {'key': 'a1'}), ('/app/api/mail-settings/level', {'key': 'reading', 'value': 'suggest'})):
        refused = client.post(path, json=body, headers={**ORIGIN, **by(CASEY)})
        assert refused.status_code == 403 and refused.json() == {'error': NOT_CONNECTED}, path
        assert client.post(path, json=body, headers=ORIGIN).status_code == 403                  # this Mac changes nothing either
    seen = client.get('/app/api/mail', headers=by(ALEX)).json()
    assert [e['id'] for e in seen['needs']] == ['m2', 'm1'] and [e['who'] for e in seen['odd']] == ['Example Bank']
    assert [a['subject'] for a in seen['asked']] == ['Saturday plans?']
    assert {row['kind']: row['level'] for row in client.get('/app/api/mail-settings', headers=by(ALEX)).json()['levels']}['reading'] == 'undo'


def test_the_app_knows_whether_the_mail_is_the_persons_own(house):
    for who, theirs in ((ALEX, True), (CASEY, False), (None, False)):
        assert client.get('/app/api/home', headers=by(who)).json()['mail'] is theirs, who


# ---- Ask in the app ----

@pytest.fixture
def reads(monkeypatch):
    """What Ask read of Alex's account: every mail, calendar and today lookup it made, made up."""
    made = []
    monkeypatch.setattr(catalog, 'mail_lookup', lambda kind=None: made.append(('mail', kind)) or {'emails': [{'from': 'Maple Street Dental'}]})
    monkeypatch.setattr(catalog, 'calendar_lookup', lambda: made.append(('calendar', None)) or {'events': [{'title': 'Dentist at Maple Street'}]})
    monkeypatch.setattr(catalog, 'today_lookup', lambda: made.append(('today', None)) or {'items': [{'what': '2 emails need you'}]})
    monkeypatch.setattr(catalog, 'preschool_evidence', lambda: {'class_dates': [{'title': 'Picture day'}]})
    return made


def test_caseys_question_about_mail_goes_to_the_model_with_none_of_alexs(house, reads):
    ask_app('Which emails need me?', headers={**ORIGIN, **by(CASEY)})
    told = said_to_model(SEEN[0])
    assert 'not_yours' in told and 'Maple Street' not in told and reads == []
    SEEN.clear()
    ask_app('Which emails need me?', headers={**ORIGIN, **by(ALEX)})
    assert 'Maple Street Dental' in said_to_model(SEEN[0])
    assert reads == [('mail', None), ('mail', 'needs'), ('calendar', None), ('today', None)]    # "need me" is about today too


def test_a_question_the_words_missed_is_looked_up_for_alex_only(house, reads):
    ask_app('when is the dentist', headers={**ORIGIN, **by(CASEY)})
    assert len(SEEN) == 2 and 'not_yours' in said_to_model(SEEN[1]) and 'Maple Street' not in said_to_model(SEEN[1]) and reads == []
    SEEN.clear()
    ask_app('when is the dentist', headers={**ORIGIN, **by(ALEX)})
    assert 'Dentist at Maple Street' in said_to_model(SEEN[1]) and reads == [('calendar', None)]


def test_school_is_everyones_and_todays_list_waits_until_it_can_be_cut_to_the_person_asking(reads):
    for who in (ALEX, CASEY, None):
        assert private.data('anything from school?', {'school'}, who)['school'] == {'class_dates': [{'title': 'Picture day'}]}
    assert reads == []
    found = private.data('What needs me today?', {'today'}, CASEY)
    assert found['needs_the_owner_today'] == found['calendar'] == catalog.NOT_YOURS and 'Picture day' in json.dumps(found)
    assert reads == []
    found = private.data('What needs me today?', {'today'}, ALEX)
    assert reads == [('calendar', None), ('today', None)] and found['needs_the_owner_today']['items']


def test_without_asks_own_model_luna_is_given_none_of_alexs_mail_for_casey(mailbox):
    def given(who):
        return private.request('Which emails need me?', {'mail'}, who)['messages'][1]['content']
    assert 'not_yours' in given(CASEY) and 'Sam Rivera' not in given(CASEY) and 'Maple Street' not in given(CASEY)
    assert 'Sam Rivera' in given(ALEX)


# ---- the older chat ----

def test_the_older_chats_mail_and_calendar_lookups_read_nothing_for_anyone_but_alex(monkeypatch):
    made = []
    monkeypatch.setattr(triage, 'picture', lambda now=None: made.append('mail') or {'synced_at': None})
    monkeypatch.setattr(catalog, 'calendar_evidence', lambda: made.append('calendar') or {'next_7_days': []})
    monkeypatch.setattr(hub_store, 'inbox_history', lambda days: made.append('inbox') or [])

    def run(slug, who, **kwargs):
        return agent._tool(lookup(slug)).function(SimpleNamespace(deps=agent.Turn(text='hi', login=who)), **kwargs).return_value
    for who in (CASEY, None):
        assert run('sorted_mail', who, kind='needs') == run('calendar_week', who) == run('inbox_trend', who) == catalog.NOT_YOURS
    assert made == []
    assert run('sorted_mail', ALEX, kind='needs') == {'sorted': False, 'note': 'Mail has not been sorted yet.'}
    assert run('calendar_week', ALEX) == {'next_7_days': []} and run('inbox_trend', ALEX)['days'] == []
    assert made == ['mail', 'calendar', 'inbox']
    assert run('trip_status', CASEY) != catalog.NOT_YOURS                     # the household's own things are everyone's


def test_a_lookup_that_cuts_its_answer_to_the_asker_is_told_who_asks_and_the_model_cant_say():
    def needs(kind: str = 'all', login=None, accounts=frozenset()) -> dict:
        """A made-up lookup that answers each person with their own things."""
        return {'kind': kind, 'login': login, 'accounts': sorted(accounts)}
    tool = agent._tool(registry.Lookup('made_up', 'Made up', 'Nothing', 'Nothing', '/today', 'Open', needs))
    assert list(tool.function_schema.json_schema['properties']) == ['kind']
    for who, theirs in ((ALEX, [people.DEFAULT]), (CASEY, []), (None, [])):
        found = tool.function(SimpleNamespace(deps=agent.Turn(text='hi', login=who)), kind='needs').return_value
        assert found == {'kind': 'needs', 'login': who, 'accounts': theirs}


def test_golden_runs_read_as_the_owner_though_they_ask_as_a_made_up_login():
    assert not people.sees(runner.LOGIN, people.DEFAULT) and runner.READS == {people.DEFAULT}
    assert agent.settings('hi', runner.LOGIN, [], accounts=runner.READS)['deps'].sees() == {people.DEFAULT}
    assert agent.settings('hi', CASEY, [])['deps'].sees() == frozenset()


LOOKED = []


async def reader(messages, info):
    """A stand-in for the older chat's model: looks at the mail and the calendar, then says it looked."""
    LOOKED.append(messages)
    if any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
        yield 'Looked.'
    else:
        yield {0: DeltaToolCall(name='sorted_mail', json_args='{"kind": "needs"}', tool_call_id='call_mail'),
               1: DeltaToolCall(name='calendar_week', json_args='{}', tool_call_id='call_calendar')}


@pytest.fixture
def chat(monkeypatch):
    fictional(monkeypatch)
    monkeypatch.setattr(luna, 'key', lambda: 'synthetic-key')
    monkeypatch.setattr(chat_model, 'build', lambda http_client=None: FunctionModel(stream_function=reader, model_name='reader'))
    LOOKED.clear()


def returned():
    return {part.tool_name: part.content for message in LOOKED[-1] if isinstance(message, ModelRequest)
            for part in message.parts if isinstance(part, ToolReturnPart)}


def test_the_older_chat_gives_casey_none_of_alexs_mail_and_keeps_each_persons_chats_apart(chat):
    reply, _ = ask_chat(client, 'Which emails need me?', thread='thread-casey', headers={**ORIGIN, **by(CASEY)})
    assert reply.status_code == 200 and returned() == {'sorted_mail': catalog.NOT_YOURS, 'calendar_week': catalog.NOT_YOURS}
    assert 'Maple Street' not in reply.text
    reply, _ = ask_chat(client, 'Which emails need me?', thread='thread-alex-1', headers={**ORIGIN, **by(ALEX)})
    assert 'Maple Street Dental' in json.dumps(returned()['sorted_mail']) and 'Swim lesson' in json.dumps(returned()['calendar_week'])

    def chats(who):
        return [t['id'] for t in client.get('/ask/threads', headers=by(who)).json()]
    assert chats(ALEX) == ['thread-alex-1'] and chats(CASEY) == ['thread-casey'] and chats(None) == []
    assert client.get('/ask/threads/thread-alex-1/messages', headers=by(CASEY)).status_code == 404
    assert 'Maple Street Dental' in client.get('/ask/threads/thread-alex-1/messages', headers=by(ALEX)).text
    assert ask_chat(client, 'And tomorrow?', thread='thread-alex-1', headers={**ORIGIN, **by(CASEY)})[0].status_code == 404
    assert client.post('/ask/threads/thread-alex-1/improve', json={'turn': 0}, headers={**ORIGIN, **by(CASEY)}).status_code == 404
    client.delete('/ask/threads/thread-alex-1', headers={**ORIGIN, **by(CASEY)})
    assert chats(ALEX) == ['thread-alex-1']
    client.delete('/ask/threads/thread-alex-1', headers={**ORIGIN, **by(ALEX)})
    assert chats(ALEX) == []


def test_chats_kept_from_before_each_persons_were_apart_are_the_owners():
    now = time.time()
    with closing(hub_store.db()) as conn, conn:          # the table as it was, without a login
        conn.execute("create table ask_threads(id text primary key, title text not null, created real not null, updated real not null,"
                     " history text not null default '[]')")
        conn.execute("insert into ask_threads values ('thread-before', 'Which emails need me?', ?, ?, '[]')", (now, now))
    assert [t['id'] for t in threads.threads(ALEX)] == ['thread-before']
    assert threads.threads(CASEY) == threads.threads(None) == [] and threads.taken('thread-before', CASEY)
    threads.save('thread-before', [], CASEY)
    threads.delete('thread-before', CASEY)
    assert [t['id'] for t in threads.threads(ALEX)] == ['thread-before'] and not threads.taken('thread-before', ALEX)


def test_todays_list_in_the_older_chat_is_alexs_until_it_can_be_cut_to_whoever_asks():
    for who in (CASEY, None):
        turn = SimpleNamespace(deps=agent.Turn(text='what needs me today?', login=who))
        assert agent._tool(lookup('today_list')).function(turn).return_value == catalog.NOT_YOURS


def test_one_persons_many_chats_never_push_out_anothers(monkeypatch):
    monkeypatch.setattr(threads, 'MAX_THREADS', 3)
    threads.save('thread-alex', [], ALEX)
    for n in range(5):
        threads.save(f'thread-casey-{n}', [], CASEY)
    threads.sweep()
    assert [t['id'] for t in threads.threads(ALEX)] == ['thread-alex'] and len(threads.threads(CASEY)) == 3


# ---- Preschool's personal mailbox is Alex's; the class guide and the shared mailbox are everyone's ----

@pytest.fixture
def preschool_mail(monkeypatch):
    """What Preschool would read of a mailbox, scan, or change, noted instead of done."""
    import preschool
    from preschool import mail as preschool_mail, scanner, store as preschool_store
    done = {'read': [], 'scanned': [], 'changed': []}
    monkeypatch.setattr(preschool_mail.bridge, 'get', lambda path, **params: done['read'].append(path) or {'messages': []})
    monkeypatch.setattr(scanner, 'run', lambda force=False: done['scanned'].append(preschool_store.current_account()))
    monkeypatch.setattr(preschool.threading, 'Thread', lambda target, daemon=True: SimpleNamespace(start=target))
    for name in ('add_sender', 'remove_sender', 'set_setting', 'dismiss'):
        monkeypatch.setattr(preschool_store, name, lambda *args, name=name: done['changed'].append((name, *args)))
    return done


PERSONAL_PAGES = ('/preschool/discover', '/preschool/preview?sender=news@example.com', '/preschool/candidate/abcdef12',
                  '/preschool/manage', '/preschool/item/1/edit')
PERSONAL_TAPS = (('/preschool/sender', {'sender': 'news@example.com'}), ('/preschool/sender/remove', {'sender': 'news@example.com'}),
                 ('/preschool/scan', {}), ('/preschool/auto', {'enabled': '1'}), ('/preschool/backfill', {}),
                 ('/preschool/group', {'query': 'class news'}), ('/preschool/item/1/dismiss', {'dismissed': '1'}),
                 ('/preschool/item/1/edit', {'kind': 'event', 'title': 'Picture day'}))


@pytest.mark.parametrize('who', [CASEY, None])
def test_preschools_personal_mailbox_shows_someone_else_a_calm_page_and_changes_nothing(who, preschool_mail):
    for path in PERSONAL_PAGES:
        answer = client.get(path, headers=by(who))
        assert answer.status_code == 200 and 'Not yours to open' in answer.text, path
    for path, form in PERSONAL_TAPS:
        assert client.post(path, data=form, headers={**ORIGIN, **by(who)}, follow_redirects=False).status_code == 403, path
    assert preschool_mail == {'read': [], 'scanned': [], 'changed': []}


def test_the_class_guide_and_the_shared_mailbox_stay_everyones(preschool_mail):
    assert 'Not yours to open' not in client.get('/preschool/guide', headers=by(CASEY)).text
    shared = client.get('/preschool/shared/discover', headers=by(CASEY))
    assert shared.status_code == 200 and 'Not yours to open' not in shared.text
    assert preschool_mail['read'] and all(path.startswith('/gmail/shared/') for path in preschool_mail['read'])
    client.post('/preschool/shared/scan', headers={**ORIGIN, **by(CASEY)}, follow_redirects=False)
    client.post('/preschool/shared/auto', data={'enabled': '1'}, headers={**ORIGIN, **by(CASEY)}, follow_redirects=False)
    assert preschool_mail['scanned'] == ['shared'] and len(preschool_mail['changed']) == 2      # the shared mailbox's only
    client.post('/preschool/scan', headers={**ORIGIN, **by(ALEX)}, follow_redirects=False)
    assert preschool_mail['scanned'] == ['shared', 'personal', 'shared']


def test_a_record_from_alexs_mailbox_is_alexs_and_one_from_the_shared_mailbox_everyones(preschool_mail, monkeypatch):
    import preschool
    key = 'a' * 64
    monkeypatch.setattr(preschool, '_resolve_item', lambda found: ('personal', {'id': 7}))
    assert client.post(f'/preschool/record/{key}/dismiss', headers={**ORIGIN, **by(CASEY)}, follow_redirects=False).status_code == 403
    assert 'Not yours to open' in client.get(f'/preschool/record/{key}/edit', headers=by(CASEY)).text
    monkeypatch.setattr(preschool, '_resolve_candidate', lambda found: ('personal', {'id': 'abcdef12'}))
    assert 'Not yours to open' in client.get(f'/preschool/review/{key}', headers=by(CASEY)).text
    assert preschool_mail['changed'] == []
    monkeypatch.setattr(preschool, '_resolve_item', lambda found: ('shared', {'id': 8}))
    assert client.post(f'/preschool/record/{key}/dismiss', headers={**ORIGIN, **by(CASEY)}, follow_redirects=False).status_code == 303
    assert preschool_mail['changed'] == [('dismiss', 8, True)]


# ---- the Engine: how far Hub files Alex's mail, and the runs that read as Alex ----

def test_how_far_hub_files_alexs_mail_is_alexs_to_set():
    from hub.engine import state
    noise = catalog.action('gmail.file_noise')
    before = state.trust(noise)
    target = 'auto' if before != 'auto' else 'suggest'
    tap = {'key': 'gmail.file_noise', 'value': target}
    refused = client.post('/app/api/engine/trust', json=tap, headers={**ORIGIN, **by(CASEY)})
    missing = client.post('/app/api/engine/trust', json={**tap, 'key': 'gmail.file_nothing'}, headers={**ORIGIN, **by(CASEY)})
    assert (refused.status_code, refused.json()) == (missing.status_code, missing.json())
    client.post('/engine/trust', data={'action': 'gmail.file_noise', 'level': target}, headers={**ORIGIN, **by(CASEY)}, follow_redirects=False)
    assert state.trust(noise) == before and catalog.action_account(noise) == people.DEFAULT
    household = catalog.action('house.control')
    assert catalog.action_account(household) is None
    assert client.post('/app/api/engine/trust', json={'key': 'house.control', 'value': 'suggest'}, headers={**ORIGIN, **by(CASEY)}).json()['ok']
    assert client.post('/app/api/engine/trust', json=tap, headers={**ORIGIN, **by(ALEX)}).json()['ok'] and state.trust(noise) == target


def test_runs_that_read_as_alex_start_only_for_alex(monkeypatch):
    from hub.engine import overnight
    from hub.golden import routes as golden
    started = []
    monkeypatch.setattr(overnight, 'prepare', lambda: started.append('review') or {'status': 'ok'})
    monkeypatch.setattr(golden.subprocess, 'Popen', lambda *args, **kwargs: started.append('golden'))
    monkeypatch.setattr(golden.runner, 'refusal', lambda: None)
    for who in (CASEY, None):
        for path in ('/engine/prepare', '/golden/run'):
            answer = client.post(path, headers={**ORIGIN, **by(who)}, follow_redirects=False)
            assert (answer.status_code, answer.json()) == (404, {'detail': 'Not Found'}), (who, path)
        assert client.post('/app/api/engine/run', json={}, headers={**ORIGIN, **by(who)}).status_code in (403, 409)
    assert started == []
    for path in ('/engine/prepare', '/golden/run'):
        assert client.post(path, headers={**ORIGIN, **by(ALEX)}, follow_redirects=False).status_code == 303
    assert client.post('/app/api/engine/run', json={}, headers={**ORIGIN, **by(ALEX)}).json()['ok']
    assert started == ['review', 'golden', 'review']
