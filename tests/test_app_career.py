"""The career desk (hub/app/career): one person's plan, field notes, writing and evidence, as Markdown files read
and kept by Hub, shown on the Career screen and steered from Ask. A made-up desk (tests/career_world.py) and the
stand-in for Ask's model (tests/ask_script.py); the worker's model is a made-up reply behind the real route."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from hub import config, household, home
from hub.app.ask import brain, route, tools
from hub.app.career import desk, markdown, worker
from hub.engine import state
from hub.golden import dryrun
from hub.main import app
from hub.tools.house import control
from tests import app_world, ask_script, career_world
from tests.ask_script import SEEN

client = TestClient(app)
ME = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
SAM = {'origin': 'http://testserver', 'tailscale-user-login': 'sam@example.com'}


@pytest.fixture
def alex(monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com', 'sam@example.com'})
    people({'name': 'Alex', 'logins': ['me@example.com'], 'accounts': ['default']})
    return career_world.make()


@pytest.fixture
def house(monkeypatch, alex):
    control._taps.clear()
    control._recent.clear()
    SEEN.clear()
    route._built.clear()
    made = app_world.install(monkeypatch)
    ask_script.library(monkeypatch, made)
    monkeypatch.setattr(route, 'build', lambda http_client=None: ask_script.MODEL)
    monkeypatch.setattr(route, 'ready', lambda: None)
    return made


@pytest.fixture
def started(monkeypatch):
    """Jobs the desk took, held instead of run in the background."""
    held = []
    monkeypatch.setattr(worker, 'start', held.append)
    return held


def people(*entries):
    (config.DATA_DIR / household.FILE).write_text(json.dumps({'people': list(entries)}))


def ask(text, headers=ME):
    with client.stream('POST', '/app/api/ask', json={'text': text}, headers={**headers, 'accept': 'text/event-stream'}) as stream:
        events = [json.loads(line.removeprefix('data: ')) for line in stream.iter_lines() if line.startswith('data: ')]
    return ''.join(e['text'] for e in events if e['t'] == 'say').strip(), [e['card'] for e in events if e['t'] == 'card'], events[-1]


def given():
    """What the stand-in for Ask's model was last given, as text."""
    return [p.content for p in SEEN[-1][-1].parts if p.part_kind == 'user-prompt'][0]


# ---- the Markdown ----

def test_markdown_is_read_into_blocks_the_app_draws_itself_and_only_web_links_are_links():
    meta, body = markdown.front('---\ntitle: "A piece"\nstatus: draft\n---\n\n# Head\n\nOne **bold** and *soft* line\nwrapped.\n\n'
                                '- [ ] a step\n- [x] done step\n\n1. first\n2. second\n\n> quoted\n\n```\ncode <b>\n```\n\n---\n'
                                '[ok](https://example.com/a) and [bad](javascript:alert(1)) and <https://example.org>')
    assert meta == {'title': 'A piece', 'status': 'draft'}
    blocks = markdown.blocks(body)
    assert [b['t'] for b in blocks] == ['h', 'p', 'ul', 'ol', 'quote', 'code', 'hr', 'p']
    assert blocks[1]['text'] == [{'t': 'text', 'text': 'One '}, {'t': 'b', 'text': 'bold'}, {'t': 'text', 'text': ' and '},
                                 {'t': 'i', 'text': 'soft'}, {'t': 'text', 'text': ' line wrapped.'}]
    assert [i['done'] for i in blocks[2]['items']] == [False, True] and blocks[3]['items'][1]['done'] is None
    assert blocks[5]['text'] == 'code <b>'
    links = [s for s in blocks[-1]['text'] if s['t'] == 'link']
    assert [s['href'] for s in links] == ['https://example.com/a', 'https://example.org']
    assert markdown.plain('A [link](https://x.example) with **bold**') == 'A link with bold'
    assert markdown.front(markdown.with_front({'title': 'T', 'status': 'ready'}, 'Body\n')) == ({'title': 'T', 'status': 'ready'}, 'Body\n')


# ---- the screen ----

def test_the_desk_as_its_person_sees_it(alex):
    found = client.get('/app/api/career', headers=ME).json()
    assert found['ok'] and not found['empty'] and found['thesis'].startswith('Alex designs how engineering teams hand work')
    assert found['headline'] == 'Engineering lead for agent tooling'
    assert [s['plain'] for s in found['next']] == ['Rewrite the LinkedIn headline around the thesis', 'Publish one essay a month for six months',
                                                   'Price a two-day agent workshop', 'Ask two teams what they would pay']
    assert [(s['title'], s['open']) for s in found['plan']] == [('Next role', 2), ('Side business', 2)]
    assert found['plan'][0]['about'] == 'A staff role that designs how an engineering org works with agents.'
    assert (found['open'], found['done']) == (4, 1)
    assert [(w['title'], w['status'], w['flags']) for w in found['writing']] == [('The tap is the contract', 'draft', 1), ('One entry point', 'idea', 0)]
    assert found['field'][0] == {'id': '2026-11-10-agent-platform-roles', 'kind': 'field', 'title': 'Agent platform roles in the city',
                                 'date': '2026-11-10', 'sources': 2,
                                 'summary': 'Agent platform and developer productivity teams are hiring program leads who can run evaluations.'}
    assert found['work'][0]['title'] == 'Garden sensors dashboard' and found['market']['title'] == 'What the market asks for'
    assert [n['at'] for n in found['notes']] == ['2026-11-12 18:40', '2026-11-02 09:15']          # newest first
    assert found['ip'] == {'words': 2} and found['model'] is True and found['jobs'] == []
    assert found['budget']['cap'] == worker.MONTH_COST_CAP
    assert 'Project Lantern' not in json.dumps(found)                       # the word list and ip.md are never shown


def test_a_piece_is_read_whole_with_its_flags_and_sources(alex):
    draft = client.get('/app/api/career/piece/writing/2026-11-12-the-tap-is-the-contract', headers=ME).json()
    assert draft['title'] == 'The tap is the contract' and draft['flags'] == ['examplecorp'] and draft['status'] == 'draft'
    assert draft['blocks'][0]['t'] == 'quote' and draft['by'] == 'Hub’s desk, a first draft'
    note = client.get('/app/api/career/piece/field/2026-11-10-agent-platform-roles', headers=ME).json()
    assert [s['href'] for s in note['sources']] == ['https://jobs.example.com/agent-platform', 'https://survey.example.org/2026']
    assert client.get('/app/api/career/piece/market/market', headers=ME).json()['title'] == 'What the market asks for'
    for kind, name in (('writing', '..%2F..%2Fhousehold'), ('ip', 'ip'), ('writing', '.hidden'), ('brand', 'brand'), ('work', 'nothing')):
        assert client.get(f'/app/api/career/piece/{kind}/{name}', headers=ME).status_code == 404


def test_no_login_and_a_login_without_an_account_have_no_desk_and_each_person_sees_only_their_own(alex):
    assert client.get('/app/api/career').json() == {'ok': False, 'why': 'signed-out', 'said': 'Open your career desk from your phone or laptop.'}
    assert client.get('/app/api/career/piece/writing/2026-11-01-one-entry-point').status_code == 404
    assert client.post('/app/api/career/tick', json={'key': 'x'}).status_code == 403
    people({'name': 'Alex', 'logins': ['me@example.com'], 'accounts': ['default']}, {'name': 'Sam', 'logins': ['sam@example.com'], 'accounts': []})
    assert client.get('/app/api/career', headers=SAM).json()['why'] == 'nobody'
    people({'name': 'Alex', 'logins': ['me@example.com'], 'accounts': ['default']}, {'name': 'Sam', 'logins': ['sam@example.com'], 'accounts': ['sam']})
    theirs = client.get('/app/api/career', headers=SAM).json()
    assert theirs['ok'] and theirs['empty'] and theirs['thesis'] == '' and theirs['where'].endswith('career/sam')
    assert client.get('/app/api/career/piece/writing/2026-11-01-one-entry-point', headers=SAM).status_code == 404
    assert client.get('/app/api/career', headers=ME).json()['thesis']


def test_a_desk_can_live_in_the_persons_own_folder(tmp_path):
    elsewhere = career_world.make(folder=tmp_path / 'repos' / 'career')
    (config.DATA_DIR / household.FILE).write_text(json.dumps({'career': {'folders': {'default': str(elsewhere)}}}))      # one login: it owns "default"
    assert client.get('/app/api/career', headers=ME).json()['headline'] == 'Engineering lead for agent tooling'


def test_a_step_is_ticked_in_the_plan_file_and_put_back(alex):
    key = client.get('/app/api/career', headers=ME).json()['next'][0]['key']
    assert client.post('/app/api/career/tick', json={'key': key}, headers=ME).json() == {'ok': True, 'said': 'Done'}
    assert '- [x] Rewrite the LinkedIn headline around the thesis' in (alex / 'plan.md').read_text()
    again = client.get('/app/api/career', headers=ME).json()
    assert again['open'] == 3 and key not in [s['key'] for s in again['next']]
    assert client.post('/app/api/career/tick', json={'key': key}, headers=ME).json()['error'] == 'That step is already done.'
    assert client.post('/app/api/career/tick', json={'key': key, 'back': True}, headers=ME).json()['said'] == 'Opened again'
    assert (alex / 'plan.md').read_text() == career_world.PLAN                  # exactly as it was, every other line untouched
    assert client.post('/app/api/career/tick', json={'key': 'nope'}, headers=ME).json()['error'] == 'That step isn’t in the plan any more.'


def test_a_pieces_status_is_a_label_in_its_front_matter(alex):
    said = client.post('/app/api/career/status', json={'id': '2026-11-01-one-entry-point', 'status': 'ready'}, headers=ME).json()
    assert said == {'ok': True, 'said': 'One entry point: ready'}
    meta, body = markdown.front((alex / 'writing' / '2026-11-01-one-entry-point.md').read_text())
    assert meta['status'] == 'ready' and meta['title'] == 'One entry point' and body.startswith('Everything a household needs')
    assert client.post('/app/api/career/status', json={'id': '2026-11-01-one-entry-point', 'status': 'sent'}, headers=ME).status_code == 409


def test_the_model_switch_is_each_persons_own(alex):
    assert client.post('/app/api/career/model', json={'on': False}, headers=ME).json()['said'] == 'Your desk is kept from Hub’s model'
    assert client.get('/app/api/career', headers=ME).json()['model'] is False
    assert desk.for_model('me@example.com', 'thesis') == {'note': 'The owner keeps their career desk from the model. Say they can turn that on in the desk.'}
    assert client.post('/app/api/career/model', json={'on': True}, headers=ME).json()['said'] == 'Hub’s model may read your desk'


def test_what_the_model_may_be_given_is_bounded_and_never_holds_the_ip_notes_or_words(alex):
    found = desk.for_model('me@example.com', 'what does the market ask of agent platform roles?')
    text = json.dumps(found)
    assert found['thesis'].startswith('Alex designs') and found['plan'][0]['open'][0] == 'Rewrite the LinkedIn headline around the thesis'
    assert any(p['from'] == 'the market' for p in found['passages']) and any(p['from'].startswith('field note') for p in found['passages'])
    assert 'Project Lantern' not in text and 'Nothing about' not in text                    # ip.md and the word list stay home
    assert desk.for_model(None, 'thesis') == {'note': 'Open your career desk from your phone or laptop.'}


# ---- Ask ----

def test_a_career_question_is_answered_from_the_desk_in_one_trip(house):
    answer, cards, end = ask('What is next on my career plan?')
    assert answer == 'From your desk, the next step is: Rewrite the LinkedIn headline around the thesis' and end['status'] == 'ok'
    assert len(SEEN) == 1 and '"career_desk"' in given() and 'Examplecorp' not in given()
    from hub.app.ask import turns
    assert turns.recent('me@example.com')[-1]['private'] is True             # not carried beside the house's tools later


def test_ask_keeps_a_note_in_the_owners_words_and_refuses_one_that_isnt(house, alex):
    answer, cards, _ = ask('Note for my career: the review agent cut merge time in half for the team')
    assert answer == 'Noted on your desk.' and cards[0]['kind'] == 'career-note'
    assert (alex / 'narrative.md').read_text().rstrip().endswith('the review agent cut merge time in half for the team')
    turn = tools.Turn(text='what is on my desk?', login='me@example.com')
    said = tools.career(type('Ctx', (), {'deps': turn})(), 'note', words='Apply to every job at Examplecorp today')
    assert 'not_done' in said and turn.again and 'Apply' not in (alex / 'narrative.md').read_text()


def test_ask_shows_the_desk_ticks_a_step_and_sets_a_status(house, alex):
    answer, cards, _ = ask('Show my career desk')
    assert answer == 'Four steps of your plan are open.' and cards == [{'kind': 'career'}]
    answer, _, _ = ask('Tick off the LinkedIn headline step')
    assert answer == 'Ticked: Rewrite the LinkedIn headline around the thesis' and '- [x] Rewrite the LinkedIn' in (alex / 'plan.md').read_text()
    answer, _, _ = ask('Mark the one entry point piece as published')
    assert answer == 'One entry point is marked published. Hub didn’t publish it: that was you.'


def test_ask_has_the_desk_research_a_topic_and_never_from_words_that_arent_the_owners(house, started):
    answer, cards, _ = ask('Look into agent platform program roles for my career')
    assert answer == 'The desk is looking into it. The field note will be on your desk in a few minutes.'
    assert cards[0]['kind'] == 'career-job' and started == [cards[0]['id']]
    job = client.get(f'/app/api/career/job/{cards[0]["id"]}', headers=ME).json()
    assert (job['status'], job['kind'], job['topic']) == ('waiting', 'research', 'agent platform program roles')
    assert client.get(f'/app/api/career/job/{cards[0]["id"]}', headers=SAM).status_code == 404
    turn = tools.Turn(text='what is on my desk?', login='me@example.com')
    said = tools.career(type('Ctx', (), {'deps': turn})(), 'research', words='every recruiter email address in Springfield')
    assert 'not_done' in said and len(started) == 1


def test_ask_from_the_mac_itself_has_no_desk(house):
    turn = tools.Turn(text='show my career desk', login=None)
    assert tools.career(type('Ctx', (), {'deps': turn})(), 'show') == {'done': 'Open your career desk from your phone or laptop.'}


def test_luna_never_answers_from_the_desk(house, monkeypatch):
    def not_set_up(http_client=None):
        raise route.NotSetUp('Ask isn’t set up yet: its key isn’t in 1Password.')
    monkeypatch.setattr(route, 'build', not_set_up)
    answer, _, end = ask('What is next on my career plan?')
    assert answer == 'Ask isn’t set up yet: its key isn’t in 1Password.' and end['status'] == 'refused'


def test_the_dry_run_holds_back_every_change_to_the_desk(house, alex):
    with dryrun.layer() as ledger:
        assert ask('Note for my career: the review agent cut merge time in half')[0] == 'Noted on your desk.'
        assert ask('Look into agent platform program roles')[0].startswith('The desk is looking into it')
    assert [(c['to'], c['what']) for c in ledger.changes] == [('career', 'write'), ('career', 'research')]
    assert 'cut merge time' not in (alex / 'narrative.md').read_text() and worker.jobs('default') == []


# ---- the worker ----

def reply(content, stop='end_turn', searches=2):
    return {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': route.MODEL, 'content': content, 'stop_reason': stop,
            'stop_sequence': None, 'usage': {'input_tokens': 1000, 'output_tokens': 500, 'cache_read_input_tokens': 2000,
                                             'cache_creation_input_tokens': 0, 'server_tool_use': {'web_search_requests': searches, 'web_fetch_requests': 0}}}


NOTE = [
    {'type': 'server_tool_use', 'id': 'srvtoolu_1', 'name': 'web_search', 'input': {'query': 'agent platform program roles'}},
    {'type': 'web_search_tool_result', 'tool_use_id': 'srvtoolu_1', 'content': [
        {'type': 'web_search_result', 'url': 'https://jobs.example.com/a', 'title': 'Jobs A', 'encrypted_content': 'x', 'page_age': None}]},
    {'type': 'text', 'text': '---\ntitle: Agent platform roles now\nsummary: Three teams are hiring program leads.\n---\n\n## What is happening\n\n'},
    {'type': 'text', 'text': 'Three teams are hiring program leads for agent platforms.', 'citations': [
        {'type': 'web_search_result_location', 'url': 'https://jobs.example.com/a', 'title': 'Jobs A', 'cited_text': 'hiring', 'encrypted_index': 'x'}]},
    {'type': 'text', 'text': '\n\n## What it means for the plan\n\nThe essays should show evaluation work, with numbers from real rollouts.\n'},
]


@pytest.fixture
def model(monkeypatch):
    """The real route to the model, with only the network replaced: each request gets the next reply."""
    httpx, made, sent, replies = route.httpx, route.client, [], []
    monkeypatch.setattr(route, 'key', lambda: 'synthetic-key')
    monkeypatch.setattr(route, 'ready', lambda: None)
    monkeypatch.setattr(route, 'client', lambda transport=None, timeout=route.TIMEOUT: made(httpx.MockTransport(
        lambda request: sent.append(request) or httpx.Response(200, json=replies.pop(0))), timeout))
    return sent, replies


def test_a_research_job_files_a_field_note_with_every_page_it_cited_and_records_its_cost(alex, model, started):
    sent, replies = model
    replies.append(reply(NOTE))
    job = worker.queue('me@example.com', 'research', 'agent platform program roles')
    asyncio.run(worker.run(job['id']))
    done = worker.job('me@example.com', job['id'])
    assert done['status'] == 'done' and done['said'] == 'Filed a field note: Agent platform roles now.'
    assert done['piece'] == {'kind': 'field', 'id': f'{home.today().isoformat()}-agent-platform-roles-now', 'title': 'Agent platform roles now'}
    meta, body = markdown.front((alex / 'field' / f'{done["file"]}.md').read_text())
    assert meta == {'title': 'Agent platform roles now', 'date': home.today().isoformat(), 'summary': 'Three teams are hiring program leads.',
                    'by': 'Hub’s research desk', 'asked': 'agent platform program roles'}
    assert body.rstrip().endswith('## Sources\n\n- [Jobs A](https://jobs.example.com/a)')
    [request] = sent
    body = json.loads(request.content)
    assert str(request.url) == 'https://api.anthropic.com/v1/messages' and body['model'] == 'claude-sonnet-5-5'
    assert body['tools'] == [{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': worker.SEARCHES}]
    assert body['thinking'] == {'type': 'adaptive'} and body['output_config'] == {'effort': 'high'}
    content = body['messages'][0]['content']
    assert content.endswith('<topic>agent platform program roles</topic>') and 'Thesis: Alex designs' in content
    assert 'Examplecorp' not in content and 'Project Lantern' not in content and 'At [withheld] the review agent' in content
    spent = state.spent(home.today().isoformat(), 'career')
    assert spent['month_cost'] == pytest.approx((1000 * 2.0 + 500 * 10.0 + 2000 * 0.2) / 1e6 + 2 * 0.01)
    assert state.spent(home.today().isoformat(), 'chat')['month_cost'] == 0      # a budget of its own


def test_with_the_desk_kept_from_the_model_research_goes_alone_and_drafting_is_refused(alex, model, started):
    sent, replies = model
    desk.set_model_reads('me@example.com', False)
    with pytest.raises(worker.Refused, match='Drafting needs Hub’s model to read your desk'):
        worker.queue('me@example.com', 'draft', 'the tap is the contract')
    replies.append(reply(NOTE))
    job = worker.queue('me@example.com', 'research', 'agent platform program roles')
    asyncio.run(worker.run(job['id']))
    assert json.loads(sent[0].content)['messages'][0]['content'] == '<topic>agent platform program roles</topic>'


def test_a_draft_is_marked_as_the_desks_and_a_long_search_is_picked_up_where_it_paused(alex, model, started):
    sent, replies = model
    replies += [reply([{'type': 'server_tool_use', 'id': 'srvtoolu_1', 'name': 'web_search', 'input': {'query': 'q'}}], stop='pause_turn', searches=1),
                reply([{'type': 'text', 'text': '---\ntitle: Where agents stop\nsummary: The stop is the product.\nform: essay\n---\n\n'
                        '> Assistant draft from your desk. Rewrite it in your own words before it goes anywhere.\n\nAn agent that '
                        'changes things should stop where a person owns the change. [a number from your own rollout]\n'}], searches=0)]
    job = worker.queue('me@example.com', 'draft', 'where agents stop')
    asyncio.run(worker.run(job['id']))
    done = worker.job('me@example.com', job['id'])
    assert done['said'] == 'Drafted: Where agents stop.' and done['piece']['kind'] == 'writing'
    meta, body = markdown.front((alex / 'writing' / f'{done["file"]}.md').read_text())
    assert (meta['status'], meta['form'], meta['by']) == ('draft', 'essay', 'Hub’s desk, a first draft')
    assert body.startswith('> Assistant draft from your desk.')
    assert len(sent) == 2 and json.loads(sent[1].content)['messages'][1]['role'] == 'assistant'      # the paused turn, sent back
    found = client.get('/app/api/career', headers=ME).json()
    assert found['writing'][0]['title'] == 'Where agents stop' and found['jobs'][0]['status'] == 'done'


def test_the_desk_takes_one_job_at_a_time_a_few_a_day_and_stops_at_its_budget(alex, model, started, monkeypatch):
    sent, replies = model
    worker.queue('me@example.com', 'research', 'first topic here')
    with pytest.raises(worker.Refused, match='The desk is still on “first topic here”'):
        worker.queue('me@example.com', 'research', 'second topic here')
    state.record_run(home.today().isoformat(), 'career', 'ok', cost=worker.MONTH_COST_CAP)
    assert worker.why_not('sam', 'research') == f'This month’s budget for the desk (${worker.MONTH_COST_CAP:.0f}) is used up. It starts again next month.'
    with pytest.raises(worker.Refused, match='Say what to look into'):
        worker.queue('me@example.com', 'research', ' ')
    assert sent == []


def test_a_job_the_model_declined_or_that_hub_lost_says_so(alex, model, started, monkeypatch):
    sent, replies = model
    replies.append(reply([{'type': 'text', 'text': ''}], stop='refusal', searches=0))
    job = worker.queue('me@example.com', 'research', 'agent platform program roles')
    asyncio.run(worker.run(job['id']))
    assert worker.job('me@example.com', job['id'])['said'] == 'The model declined this one.'
    assert state.spent(home.today().isoformat(), 'career')['runs_today'] == 1
    lost = worker.queue('me@example.com', 'research', 'another topic entirely')
    monkeypatch.setattr(worker.time, 'time', lambda: 10 ** 10)
    assert worker.job('me@example.com', lost['id'])['said'] == 'Hub stopped before this finished. Ask again.'


def test_the_worker_can_be_switched_off(alex, started):
    (config.DATA_DIR / household.FILE).write_text(json.dumps({'career': {'worker': False}, 'people': [
        {'name': 'Alex', 'logins': ['me@example.com'], 'accounts': ['default']}]}))
    with pytest.raises(worker.Refused, match='switched off'):
        worker.queue('me@example.com', 'research', 'agent platform program roles')
    assert brain.INSTRUCTIONS.count('Hub never publishes, posts, applies for a job or contacts anyone') == 1


# ---- the Career dashboard (hub/app/boards/career.py): each person's own desk, ticked from the board ----

def test_the_career_dashboard_is_only_its_owners_and_ticks_their_plan(alex):
    from hub.app import boards
    people({'name': 'Alex', 'logins': ['me@example.com'], 'accounts': ['default']}, {'name': 'Sam', 'logins': ['sam@example.com'], 'accounts': ['sam']})
    found = boards.picture('career', 'me@example.com')
    blocks = {b['id']: b for b in found['blocks']}
    assert 'open' in found['say'] and 'thesis' in blocks and 'writing' in blocks and 'field' in blocks
    plan = next(b for b in found['blocks'] if b['kind'] == 'checks')
    step = next(i for i in plan['items'] if not i['done'])
    assert boards.tick('me@example.com', 'career', step['id'], True) == 'Ticked'
    assert next(i for b in boards.picture('career', 'me@example.com')['blocks'] if b['kind'] == 'checks'
                for i in b['items'] if i['id'] == step['id'])['done']
    sams = boards.picture('career', 'sam@example.com')                     # Sam's own desk: empty, never Alex's
    assert sams['say'] == 'Your desk is empty so far' and not any(b['kind'] == 'checks' for b in sams['blocks'])
    assert boards.picture('career', None) is None
    with pytest.raises(boards.Refused):
        boards.tick('sam@example.com', 'career', step['id'], False)        # not on Sam's desk
    assert boards.change('me@example.com', 'career', 'model', '') == 'Your desk is kept from Hub’s model'
    assert not desk.model_reads('default') and desk.model_reads('sam')
    assert client.post('/app/api/board/career/set', json={'key': 'model', 'value': 'on'}, headers=ME).status_code == 200
    assert desk.model_reads('default')
