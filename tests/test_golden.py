"""Golden tests, offline: every scenario through the real Ask pipeline with a scripted model, a made-up
household, and the dry-run layer. Also: the checks catch the real failures they were written for, the
dry-run layer never lets a write out, and live runs keep to their cost cap and privacy rules."""
import json

import pytest
from pydantic_ai.messages import ModelResponse

from hub import config
from hub.agent import agent
from hub.engine import catalog, state
from hub.golden import checks, dryrun, runner, scenarios
from hub.golden.scenarios import Say, Scenario, Use
from tests import golden_world


@pytest.fixture
def world(monkeypatch):
    return golden_world.install(monkeypatch)


@pytest.mark.parametrize('scenario', scenarios.SCENARIOS, ids=lambda s: s.id)
def test_scenario_passes_offline(world, scenario):
    trace, failures = runner.offline(scenario)
    assert failures == [], failures
    assert world.sent == []                     # the dry-run layer stopped every write before the fake services
    assert len(trace.replies) == len(scenario.turns)


# ---- the checks catch what went wrong before ----

def _bad(scenario_id, script):
    good = scenarios.find(scenario_id)
    return Scenario(good.id, good.about, good.turns, good.checks, script, good.tags, good.seed)


def _failed(scenario):
    _, failures = runner.offline(scenario)
    return ' | '.join(f['check'] + ': ' + f['why'] for f in failures)


def test_the_whole_sentence_as_a_search_is_caught(world):
    sentence = 'Softly play some music in the living room while I read a bedtime story'
    found = _failed(_bad('music-softly-in-one-room', ((
        Use('play_music', speaker='{living_room_speaker}', action='play_media', what=sentence),
        Say('Playing music softly.')),)))
    assert 'Searched with the owner’s sentence' in found


def test_free_text_reaching_the_speaker_is_caught():
    trace = checks.Trace(texts=['play something'], replies=['ok'], changes=[
        {'turn': 0, 'to': 'home', 'what': 'music_assistant/play_media',
         'data': {'entity_id': 'media_player.x', 'media_id': 'something calm for reading', 'media_type': 'track'}}])
    assert 'free text' in checks.ConcretePick()(trace)


def test_as_well_starting_a_second_stream_is_caught():
    trace = checks.Trace(texts=['play it in the kitchen as well'], replies=['ok'], names=dict(scenarios.ALIASES),
                         devices={'media_player.kitchen': 'Kitchen Speaker'}, changes=[
        {'turn': 0, 'to': 'home', 'what': 'music_assistant/play_media',
         'data': {'entity_id': 'media_player.kitchen', 'media_id': 'apple_music://playlist/p1', 'media_type': 'playlist'}}])
    assert 'second stream' in checks.Joined('living_room_speaker', 'kitchen_speaker')(trace)


def test_hub_itself_turns_a_second_play_for_as_well_into_a_join(world):
    """The model replays the old mistake (a second play_media for "as well"); Hub's own guard joins instead."""
    _, failures = runner.offline(_bad('music-as-well-joins', (
        (Use('play_music', speaker='{living_room_speaker}', action='play_media', what='calm piano'), Say('Playing {picked}.')),
        (Use('play_music', speaker='{kitchen_speaker}', action='play_media', what='calm piano'), Say('Done.')))))
    assert failures == []


def test_remember_becoming_a_playlist_is_caught(world):
    found = _failed(_bad('remember-is-a-note-not-a-playlist', ((
        Use('edit_playlist', action='create', playlist='the kitchen and living room are one room'),
        Say('I made the playlist.')),)))
    assert 'Called edit_playlist' in found and 'No note was proposed' in found


def test_loud_music_and_a_stray_room_are_caught(world):
    found = _failed(_bad('music-softly-in-one-room', ((
        Use('play_music', speaker='{living_room_speaker}', action='play_media', what='calm piano', volume_percent=60),
        Use('home_control', device='{kitchen}', action='off'),
        Say('Playing {picked}.')),)))
    assert '60% volume' in found and 'Kitchen Pendant' in found


def test_lights_in_the_wrong_room_are_caught(world):
    found = _failed(_bad('lights-room-off', ((Use('home_control', device='all lights', action='off'), Say('Done.')),)))
    assert 'didn’t ask about' in found


# ---- the dry-run layer ----

def test_every_tool_that_can_change_something_is_behind_the_dry_run():
    assert set(dryrun.COVERED) == {c.slug for c in catalog.controls()} | {h.slug for h in catalog.handoffs()}
    assert set(checks.CONTROLS) == {c.slug for c in catalog.controls()}
    assert set(checks.HANDOFFS) == {h.slug for h in catalog.handoffs()}


def test_dry_run_runs_hubs_real_checks_and_records_without_sending(world):
    from hub.tools.house import broker, control
    with dryrun.layer() as ledger:
        catalog.home_control('me@example.com', 'Reading Lamp', 'brightness', brightness_percent=30)
        with pytest.raises(ValueError):          # the broker's own allowlist still refuses
            broker.act('lock', 'unlock', {'entity_id': 'lock.front_door'})
        found = catalog.play_music('me@example.com', 'Living Room Speaker', 'play_media', what='calm piano')
    assert [c['what'] for c in ledger.changes] == ['light/turn_on', 'media_player/volume_set', 'music_assistant/play_media']
    assert ledger.changes[0]['data'] == {'entity_id': 'light.reading_lamp', 'brightness_pct': 30}
    assert found['done'] and world.sent == []
    assert control.recent() == []               # Hub's own change log, and so its Undo, never saw it


def test_dry_run_lays_changes_over_later_reads(world):
    from hub.tools.house import read
    with dryrun.layer():
        catalog.play_music('me@example.com', 'Living Room Speaker', 'play_media', what='calm piano')
        catalog.play_music('me@example.com', 'Living Room Speaker', 'group', with_speakers=['Kitchen Speaker'])
        speakers = {d['name']: d for d in read.snapshot(fresh=True)['speakers']}
    assert speakers['Living Room Speaker']['state'] == 'playing'
    assert speakers['Kitchen Speaker']['group'] == ['Living Room Speaker']
    assert {d['state'] for d in read.snapshot(fresh=True)['speakers']} == {'idle'}   # gone once the layer is


def test_dry_run_holds_back_music_edits_proposals_and_kitchen(world):
    from hub.engine import notes
    from hub.tools.house import music
    with dryrun.layer() as ledger:
        added = catalog.edit_playlist('me@example.com', 'add', 'Calm Dinner', songs=['Morning Light'])
        made = catalog.edit_playlist('me@example.com', 'create', 'Sunday Morning', said='make a playlist called Sunday Morning')
        noted = catalog.remember('me@example.com', 'the kitchen lights stay on at dinner',
                                 said='remember the kitchen lights stay on at dinner')
        sent = catalog.kitchen_send('add oat milk', 'me@example.com')
    assert added['done'] and made['needs_tap'] and noted['needs_tap'] and sent == {'message': dryrun.DRY_ID}
    assert [(c['to'], c['what']) for c in ledger.changes] == [
        ('music', 'music/playlists/add_playlist_tracks'), ('proposal', 'playlist'), ('proposal', 'note'), ('kitchen', 'POST /messages')]
    assert world.sent == [] and music.recent_changes() == [] and music.proposals() == [] and notes.waiting() == []


def test_live_writes_only_for_harmless_scenarios(world, monkeypatch):
    harmless = Scenario('t', 't', ('dim the {reading_lamp} to 30%',), (), scenarios.find('lights-dim-one-lamp').script,
                        harmless=True)
    risky = Scenario('r', 'r', harmless.turns, (), harmless.script)
    aliases = runner.names()
    runner.run(risky, lambda: runner.scripted(risky, aliases), aliases=aliases, writes=True)
    assert world.sent == []
    model = runner.scripted(harmless, aliases)
    trace, _ = runner.run(harmless, lambda: model, aliases=aliases, writes=True)
    assert [m[1]['path'] for m in world.sent] == ['/api/services/light/turn_on'] and trace.changes == []


# ---- live runs: names, cost cap, ledger, private results ----

def test_live_names_come_from_the_private_settings_file():
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'golden': {'names': {
        'living_room': 'Den', 'not_an_alias': 'x', 'kitchen': ' '}}}))
    found = runner.names(live=True)
    assert found['living_room'] == 'Den' and found['kitchen'] == 'Kitchen' and 'not_an_alias' not in found
    assert runner.names()['living_room'] == 'Living Room'


def _scripted_for(scenario, aliases):
    model = runner.scripted(scenario, aliases)
    return lambda: model


def test_live_run_records_privately_and_in_the_engine_ledger(world, monkeypatch):
    from hub import luna
    monkeypatch.setattr(luna, 'configured', lambda: True)
    monkeypatch.setattr(runner, '_cost', lambda messages: 0.01 * sum(isinstance(m, ModelResponse) for m in messages))
    result = runner.live(only=('what-needs-me-today', 'lights-room-off'), model_for=_scripted_for)
    assert [r['status'] for r in result['scenarios']] == ['pass', 'pass'] and result['writes'] == 'dry run'
    assert result['cost'] == pytest.approx(0.05) and result['calls'] == 5 and world.sent == []   # 2 answers, then 3
    files = list((config.DATA_DIR / 'golden' / 'runs').glob('*.json'))
    assert len(files) == 1 and oct(files[0].stat().st_mode)[-3:] == '600'
    assert state.runs(runner.KIND)[0]['cost'] == pytest.approx(0.05)
    summary = runner.latest()
    assert summary['passed'] == 2 and summary['total'] == 2 and not runner.running()
    assert state.used(store_day(), 'overnight')['month_cost'] == 0      # its own budget, not the shared one


def store_day():
    from hub import home
    return home.today().isoformat()


def test_live_run_stops_at_the_cost_cap(world, monkeypatch):
    from hub import luna
    monkeypatch.setattr(luna, 'configured', lambda: True)
    monkeypatch.setattr(runner, '_cost', lambda messages: 0.2)
    result = runner.live(only=('what-needs-me-today', 'calendar-free-tomorrow', 'lights-room-off', 'lights-dim-one-lamp'),
                         cap=0.5, model_for=_scripted_for)
    assert [r['status'] for r in result['scenarios']] == ['pass', 'pass', 'skipped', 'skipped']
    assert 'cost cap' in result['stopped'] and result['cost'] <= 0.5


def test_live_run_refuses_without_a_key_or_over_the_month(monkeypatch):
    from hub import luna
    with pytest.raises(runner.Stopped, match='key'):
        runner.live()
    monkeypatch.setattr(luna, 'configured', lambda: True)
    state.record_run(store_day(), runner.KIND, 'ok', cost=runner.MONTH_COST_CAP)
    with pytest.raises(runner.Stopped, match='budget'):
        runner.live()


def test_cli_live_needs_the_live_data_folder(monkeypatch, capsys):
    from hub.golden import __main__ as cli
    monkeypatch.delenv('HUB_DATA_DIR', raising=False)
    assert cli.main(['live']) == 2 and 'HUB_DATA_DIR' in capsys.readouterr().out
    monkeypatch.setenv('HUB_DATA_DIR', str(config.DATA_DIR))
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)
    assert cli.main(['live', '--live-writes']) == 2


# ---- the scenarios themselves ----

def test_scenarios_are_well_formed():
    ids = [s.id for s in scenarios.SCENARIOS]
    assert len(ids) == len(set(ids))
    tools = {*agent.describe()}
    for s in scenarios.SCENARIOS:
        assert s.about and s.checks and len(s.script) == len(s.turns)
        for turn in s.script:
            assert isinstance(turn[-1], Say) and all(isinstance(m, Use) and m.tool in tools for m in turn[:-1])
        for text in s.turns:
            text.format(**scenarios.ALIASES)
    assert runner.REQUESTS_PER_TURN == agent.REQUESTS_PER_TURN
    seeded = {s.id for s in scenarios.SCENARIOS if s.seed}
    assert {'music-softly-in-one-room', 'music-as-well-joins', 'remember-is-a-note-not-a-playlist'} <= seeded


# ---- the Settings page and Insights ----

def test_golden_page_and_run_button(monkeypatch):
    from fastapi.testclient import TestClient
    from hub import luna
    from hub.golden import routes
    from hub.main import app
    started = []
    monkeypatch.setattr(routes.subprocess, 'Popen', lambda args, **kw: started.append((args, kw['env']['HUB_DATA_DIR'])))
    client = TestClient(app)
    signed_in = {'tailscale-user-login': 'me@example.com'}
    page = client.get('/golden', headers=signed_in)
    assert page.status_code == 200 and 'No live run yet' in page.text and 'music-as-well-joins' in page.text
    assert 'The model key isn’t set up' in page.text
    client.post('/golden/run', headers={**signed_in, 'origin': 'http://testserver'}, follow_redirects=False)
    assert started == []                                        # no key: nothing starts
    monkeypatch.setattr(luna, 'configured', lambda: True)
    assert client.post('/golden/run', headers={**signed_in, 'origin': 'http://evil.example'}).status_code == 403
    reply = client.post('/golden/run', headers={**signed_in, 'origin': 'http://testserver'}, follow_redirects=False)
    assert reply.status_code == 303 and started == [([__import__('sys').executable, '-m', 'hub.golden', 'live'], str(config.DATA_DIR))]
    assert 'Golden tests' in client.get('/settings', headers=signed_in).text


def test_insights_shows_the_latest_run(world, monkeypatch):
    from fastapi.testclient import TestClient
    from hub import luna
    from hub.main import app
    signed_in = {'tailscale-user-login': 'me@example.com'}
    assert 'No live run yet' in TestClient(app).get('/insights', headers=signed_in).text
    monkeypatch.setattr(luna, 'configured', lambda: True)
    runner.live(only=('what-needs-me-today',), model_for=_scripted_for)
    page = TestClient(app).get('/insights', headers=signed_in).text
    assert '1 of 1' in page and '/golden' in page
