"""Ask Hub's per-turn trace, failed answers kept in the thread, the Ask activity page and the ask-trace CLI.

Fictional data and scripted models only: no live device, mailbox or model call.
"""
import json
import time
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from hub import ask_trace, luna, store as hub_store
from hub.agent import model, routes, store
from hub.engine import catalog, improve, registry
from hub.main import app
from hub.tools.house import control, music

from .test_ask import ORIGIN, OWNER, STRANGER, THREAD, ask, fictional

OWNER_WORDS = 'What was the trick with the Sunday playlist from alex@friends.example?'


def _results(messages):
    """The tool results the model has been given since the owner's last message."""
    found = {}
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    found = {}
                elif isinstance(part, ToolReturnPart):
                    found[part.tool_name] = part.content
    return found


def calls(*tools, then='Done.', args=None):
    """A model that calls `tools` once, then answers `then`."""
    async def stream(messages, info):
        if tools and not _results(messages):
            yield {n: DeltaToolCall(name=t, json_args=json.dumps((args or {}).get(t, {})), tool_call_id=f'call_{t}_{n}')
                   for n, t in enumerate(tools)}
        else:
            yield then
    return FunctionModel(stream_function=stream, model_name='scripted-trace')


def broken_after(*tools):
    """A model that calls `tools`, then fails on the next call with a provider-style error."""
    async def stream(messages, info):
        if not _results(messages):
            yield {n: DeltaToolCall(name=t, json_args='{}', tool_call_id=f'call_{t}_{n}') for n, t in enumerate(tools)}
        else:
            raise RuntimeError('synthetic provider detail: account 12345 at https://provider.example')
    return FunctionModel(stream_function=stream, model_name='scripted-broken')


@pytest.fixture
def client(monkeypatch):
    fictional(monkeypatch)
    monkeypatch.setattr(luna, 'key', lambda: 'synthetic-key')
    return TestClient(app)


def use(monkeypatch, scripted):
    monkeypatch.setattr(model, 'build', lambda http_client=None: scripted)


def _music_down(monkeypatch):
    monkeypatch.setattr(music, 'ready', lambda: True)

    def down(search=None):
        raise music.MusicDown('Music Assistant didn’t answer (synthetic timeout).')
    monkeypatch.setattr(music, 'playlists', down)


@pytest.fixture
def lamp(monkeypatch):
    """A fictional light control that writes a house change row as the real one does."""
    def run(login, room: str = '') -> dict:
        """Switch the lamp in a room."""
        batch = uuid.uuid4().hex[:12]
        with closing(control._db()) as conn, conn:
            conn.execute('insert into house_changes(batch, ts, login, via, entity, name, action, words, before, outcome) '
                         "values (?,?,?,?,?,?,?,?,?,?)", (batch, time.time(), login, 'ask', 'light.reading_lamp',
                                                           'Reading lamp', 'on', 'Turned on Reading lamp', '{}', 'done'))
        return {'batch': batch, 'done': ['Turned on Reading lamp'], 'unknown': [], 'not_sent': [], 'undo': True}
    lamp_control = registry.Control('home_control', 'Home control', 'reads', 'sends', '/house/changes', 'Home changes',
                                    'house.control', run)
    monkeypatch.setattr(catalog, 'controls', lambda: [lamp_control])
    return lamp_control


# ---- a turn whose lookup can't read its source ----

def test_a_lookup_that_cannot_read_music_keeps_the_error_type_and_traceback_in_the_trace_only(client, monkeypatch, capfd):
    _music_down(monkeypatch)
    use(monkeypatch, calls('music_library', then='Music Assistant isn’t answering, so I can’t check the playlist.'))
    reply, chunks = ask(client, OWNER_WORDS, headers={**ORIGIN, **OWNER})
    assert reply.status_code == 200 and 'error' not in [c['type'] for c in chunks]
    [turn] = ask_trace.turns()
    assert (turn['status'], turn['thread'], turn['model_calls'], turn['tools'], turn['tool_errors']) == ('ok', THREAD, 2, 1, 1)
    [call] = turn['tool_calls']
    assert (call['tool'], call['kind'], call['outcome'], call['error_type']) == ('music_library', 'lookup', 'unavailable', 'MusicDown')
    assert 'synthetic timeout' in call['error_message'] and 'Traceback' in call['traceback'] and 'playlists' in call['traceback']
    assert ask_trace.turn(turn['id'])['id'] == turn['id']
    stored = json.dumps(ask_trace.turns())
    assert 'Sunday playlist' not in stored and 'trick' not in stored and 'can’t check' not in stored   # no message or answer
    log = capfd.readouterr().err
    line = next(l for l in log.splitlines() if l.startswith('ask turn='))
    assert f'turn={turn["id"]}' in line and 'status=ok' in line and 'tool_errors=1' in line
    assert 'Sunday' not in log and 'MusicDown' not in line and 'synthetic timeout' not in log            # ids, times, status only


def test_a_lookup_that_breaks_outright_is_an_error_outcome_and_the_answer_goes_on(client, monkeypatch):
    def broken() -> dict:
        """Events on the owner's calendar."""
        raise KeyError('synthetic missing field')
    lookup = next(l for l in catalog.lookups() if l.slug == 'calendar_week')
    monkeypatch.setattr(catalog, 'lookups', lambda: [registry.Lookup(lookup.slug, lookup.name, lookup.reads, lookup.sends,
                                                                     lookup.page, lookup.open, broken)])
    use(monkeypatch, calls('calendar_week', then='I couldn’t read the calendar.'))
    reply, _ = ask(client, 'What is on this week?')
    assert reply.status_code == 200
    [call] = ask_trace.turns()[0]['tool_calls']
    assert (call['outcome'], call['error_type']) == ('error', 'KeyError') and 'synthetic missing field' in call['traceback']


# ---- a turn whose model call fails ----

def test_a_failed_model_call_is_traced_saved_as_a_failed_answer_and_can_be_improved(client, monkeypatch, capfd):
    _music_down(monkeypatch)
    use(monkeypatch, broken_after('music_library'))
    reply, chunks = ask(client, OWNER_WORDS, headers={**ORIGIN, **OWNER})
    assert [c['errorText'] for c in chunks if c['type'] == 'error'] == [routes.FAILED]
    assert '12345' not in reply.text and 'provider.example' not in reply.text
    [turn] = ask_trace.turns()
    assert (turn['status'], turn['error_type'], turn['model_calls']) == ('failed', 'RuntimeError', 2)
    assert 'Traceback' in turn['traceback'] and '[link]' in turn['traceback'] and 'provider.example' not in turn['traceback']
    failed_call = turn['model_calls_list'][-1]
    assert failed_call['error_type'] == 'RuntimeError' and turn['model_calls_list'][0]['error_type'] is None
    assert [c['tool'] for c in turn['tool_calls']] == ['music_library']
    assert 'status=failed' in capfd.readouterr().err

    saved = client.get(f'/ask/threads/{THREAD}/messages').json()
    assert [m['role'] for m in saved] == ['user', 'assistant']
    said = ''.join(p.get('text', '') for p in saved[1]['parts'])
    assert said.startswith('**This answer failed.**') and 'Music library (couldn’t read it)' in said
    assert 'Nothing was changed' in said
    assert [t['title'] for t in client.get('/ask/threads').json()] == [OWNER_WORDS[:store.TITLE_LENGTH]]

    filed = client.post(f'/ask/threads/{THREAD}/improve', json={'turn': 0}, headers={**ORIGIN, **OWNER})
    assert filed.status_code == 200
    brief = improve.requests()[0]['brief']
    assert 'This answer failed (RuntimeError)' in brief and turn['id'] in brief
    assert 'music_library (unavailable: “Hub could not read this just now.”; MusicDown: Music Assistant didn’t answer' in brief
    assert 'Sunday' not in brief and 'trick' not in brief and '12345' not in brief and '@' not in brief
    assert 'The answer said Hub couldn’t' not in brief

    use(monkeypatch, calls(then='The trick was the shuffle setting.'))
    again, _ = ask(client, 'What was the trick?')
    assert again.status_code == 200                                   # the thread is free and goes on after a failure
    assert len(client.get(f'/ask/threads/{THREAD}/messages').json()) == 4


def test_the_engine_record_counts_the_model_calls_a_failed_turn_made(client, monkeypatch):
    from hub.engine import state
    use(monkeypatch, broken_after('trip_status'))
    ask(client, 'How is the trip plan?')
    run = state.runs(routes.KIND)[0]
    assert (run['status'], run['luna_calls']) == ('error', 2)


# ---- a turn that changes something ----

def test_a_control_that_breaks_keeps_its_arguments_scrubbed_and_its_traceback(client, monkeypatch):
    def run(login, room: str = '') -> dict:
        """Switch the lamp in a room."""
        raise ValueError('synthetic: no such entity')
    monkeypatch.setattr(catalog, 'controls', lambda: [registry.Control(
        'home_control', 'Home control', 'reads', 'sends', '/house/changes', 'Home changes', 'house.control', run)])
    use(monkeypatch, calls('home_control', then='That didn’t work.',
                           args={'home_control': {'room': 'Den, see https://lamps.example or ask sam@home.example'}}))
    ask(client, 'Turn on the den lamp', headers={**ORIGIN, **OWNER})
    [call] = ask_trace.turns()[0]['tool_calls']
    assert (call['kind'], call['outcome'], call['error_type']) == ('control', 'error', 'ValueError')
    assert json.loads(call['args']) == {'room': 'Den, see [link] or ask [address]'}
    assert 'synthetic: no such entity' in call['traceback']


def test_a_control_links_to_the_change_rows_it_made_and_the_activity_page_shows_them(client, monkeypatch, lamp):
    use(monkeypatch, calls('home_control', then='The reading lamp is on.', args={'home_control': {'room': 'Den'}}))
    ask(client, 'Turn on the reading lamp', headers={**ORIGIN, **OWNER})
    [call] = ask_trace.turns()[0]['tool_calls']
    assert call['outcome'] == 'done' and len(call['changes']) == 1
    change = call['changes'][0]
    assert change['table'] == 'house_changes' and change['href'] == f'/house/changes#batch-{change["batch"]}'
    page = client.get('/engine/ask-activity', headers=OWNER)
    assert page.status_code == 200 and change['href'] in page.text and 'home_control' in page.text
    assert 'Turn on the reading lamp' not in page.text and 'The reading lamp is on' not in page.text
    assert f'id="batch-{change["batch"]}"' in client.get('/house/changes', headers=OWNER).text


def test_when_the_model_fails_after_a_change_the_chat_never_says_nothing_changed(client, monkeypatch, lamp):
    use(monkeypatch, broken_after('home_control'))
    _, chunks = ask(client, 'Turn on the reading lamp', headers={**ORIGIN, **OWNER})
    assert [c['errorText'] for c in chunks if c['type'] == 'error'] == [routes.FAILED_CHANGED]
    said = ''.join(p.get('text', '') for p in client.get(f'/ask/threads/{THREAD}/messages').json()[1]['parts'])
    assert 'Something may have changed' in said and 'Nothing was changed' not in said and 'Home control (done)' in said


# ---- the page, the CLI and what is kept ----

def test_the_activity_page_is_signed_in_only_and_can_show_only_failed_turns(client, monkeypatch):
    use(monkeypatch, calls('trip_status', then='Flights are not chosen yet.'))
    ask(client, 'How is the trip plan?')
    use(monkeypatch, broken_after('trip_status'))
    ask(client, 'And the flights?')
    assert client.get('/engine/ask-activity', headers=STRANGER).status_code == 403
    every = client.get('/engine/ask-activity', headers=OWNER).text
    failed = client.get('/engine/ask-activity?failed=1', headers=OWNER).text
    ok_id, failed_id = [t['id'] for t in reversed(ask_trace.turns())]
    assert ok_id in every and failed_id in every and failed_id in failed and ok_id not in failed
    assert 'RuntimeError' in failed and 'Turn traceback' in failed
    assert 'trip plan' not in every and 'flights?' not in every
    assert '/engine/ask-activity' in client.get('/settings', headers=OWNER).text


def test_the_cli_prints_a_turn_or_the_last_turns_and_never_creates_a_trace(client, monkeypatch, capsys, tmp_path):
    from hub import config
    empty = tmp_path / 'elsewhere'
    assert ask_trace.main(['--data-dir', str(empty), '--last', '3']) == 1 and not (empty / ask_trace.FILE).exists()
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    _music_down(monkeypatch)
    use(monkeypatch, broken_after('music_library'))
    ask(client, OWNER_WORDS)
    capsys.readouterr()
    [turn] = ask_trace.turns()
    assert ask_trace.main([turn['id']]) == 0
    out = capsys.readouterr().out
    assert f'turn {turn["id"]}' in out and 'failed' in out and 'music_library (lookup) {} -> unavailable' in out, out
    assert 'MusicDown' in out and 'Traceback' not in out and 'Sunday' not in out
    assert ask_trace.main(['--failed', '--traceback']) == 0 and 'Traceback' in capsys.readouterr().out
    assert ask_trace.main(['--json', '--last', '1']) == 0
    assert json.loads(capsys.readouterr().out)[0]['id'] == turn['id']
    assert ask_trace.main(['nosuchturn']) == 1


def test_traces_are_owner_only_and_kept_fourteen_days(client, monkeypatch):
    use(monkeypatch, calls(then='Hello.'))
    ask(client, 'hello')
    path = ask_trace._path()
    assert path.stat().st_mode & 0o777 == 0o600
    with closing(ask_trace._db()) as conn, conn:
        conn.execute('update turns set started = started - ?', ((ask_trace.KEEP_DAYS + 1) * 86400,))
    ask(client, 'hello again')
    assert len(ask_trace.turns()) == 1
    with closing(ask_trace._db()) as conn:
        assert conn.execute('select count(*) from tool_calls').fetchone()[0] == 0
        assert conn.execute('select count(*) from model_calls').fetchone()[0] == 1


def test_hub_db_never_gets_the_traceback_and_the_trace_never_gets_lookup_results(client, monkeypatch):
    use(monkeypatch, calls('sorted_mail', then='Two need you.', args={'sorted_mail': {'kind': 'needs'}}))
    ask(client, 'Which emails need me?')
    stored = json.dumps(ask_trace.turns())
    assert 'Maple Street Dental' not in stored and 'confirm your cleaning' not in stored     # what the lookup returned
    assert '"kind": "needs"' in ask_trace.turns()[0]['tool_calls'][0]['args'] or 'needs' in stored
    with closing(hub_store.db()) as conn:
        activity = json.dumps([dict(r) for r in conn.execute('select * from activity')])
    assert 'Traceback' not in activity


def test_outside_an_ask_turn_tools_still_run_and_nothing_is_traced():
    from hub.agent import agent
    lookup = next(l for l in catalog.lookups() if l.slug == 'trip_status')
    tool = agent._tool(lookup)

    class Ctx:
        deps = agent.Turn(text='hi')
    assert tool.function(Ctx()) is not None
    assert ask_trace.turns() == []


def test_the_tool_schemas_are_unchanged_by_tracing():
    from hub.agent import agent
    tools = agent.build()._function_toolset.tools
    mail = tools['sorted_mail'].function_schema.json_schema
    assert list(mail['properties']) == ['kind'] and 'up to 10 of them' in tools['sorted_mail'].description
    library = tools['music_library'].function_schema.json_schema
    assert set(library['properties']) == {'search', 'playlist'}
    assert 'ctx' not in mail['properties'] and 'kwargs' not in library['properties']
