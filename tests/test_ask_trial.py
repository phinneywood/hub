"""The trial of Ask on the real house (hub/app/ask/trial.py) and the two live checks built on it, run here on the
made-up house with the stand-in for the model: what is asked, that nothing is sent, and how each wrong outcome is
told apart.
"""
import json
import re

import pytest

from hub import config
from hub.app.ask import brain, route, tools, trial
from hub.engine import state as engine
from hub.golden import dryrun
from hub.live import checks, runner
from hub.tools.house import admin, broker, control
from tests import app_world, ask_script, kitchen_world
from tests.ask_script import SEEN


@pytest.fixture
def house(monkeypatch):
    control._taps.clear()
    control._recent.clear()
    SEEN.clear()
    made = app_world.install(monkeypatch)
    made.kitchen = kitchen_world.install(monkeypatch)
    ask_script.library(monkeypatch, made)
    monkeypatch.setattr(route, 'build', lambda http_client=None: ask_script.MODEL)
    monkeypatch.setattr(route, 'ready', lambda: None)
    monkeypatch.setattr(trial, 'SPACING', 0)
    monkeypatch.setattr(trial, 'BACKOFF', (0, 0, 0))
    brain._playlists.update(names=[], at=float('-inf'))
    return made


def test_every_tool_of_ask_in_the_app_is_behind_the_dry_run():
    assert set(dryrun.COVERED_APP) == {tool.__name__ for tool in tools.ALL}


def test_the_dry_run_holds_back_everything_the_setup_door_would_send(house):
    with dryrun.layer() as ledger:
        w = admin.world(fresh=True)                                        # reads go through
        assert broker.setup_config('scene', 'dinner')['name'] == 'Dinner'
        broker.setup_write({'type': 'config/entity_registry/update', 'entity_id': 'light.living_reading', 'area_id': 'bedroom'})
        assert broker.setup_write({'type': 'config/area_registry/create', 'name': 'Sun room'})['area_id'] == 'dry-run'
        broker.setup_save('scene', 'dinner', {'id': 'dinner', 'name': 'Dinner', 'entities': {}})
        broker.setup_save('automation', 'a-porch', None)
        broker.setup_call('lock', 'unlock', {'entity_id': 'lock.front_door'})
        broker.setup_reload('entry0001bridge')
        with pytest.raises(ValueError):                                     # Hub's own fence still runs first
            broker.setup_write({'type': 'config/entity_registry/remove', 'entity_id': 'light.living_reading'})
        done = admin.carry_out(__import__('hub.tools.house.plans', fromlist=['x']).arrange(w, ['coffee maker'], room='office'), login='x', via='ask')
        assert done['done'] == ['Coffee maker moved from Kitchen to Office'] and admin.recent()        # recorded, in the scratch file
    assert [(c['to'], c['what']) for c in ledger.changes] == [
        ('setup', 'config/entity_registry/update'), ('setup', 'config/area_registry/create'),
        ('setup', 'POST /api/config/scene/config/dinner'), ('setup', 'DELETE /api/config/automation/config/a-porch'),
        ('home', 'lock/unlock'), ('setup', 'POST /api/config/config_entries/entry/entry0001bridge/reload'),
        ('setup', 'config/entity_registry/update')]
    assert house.sent == [] and house.setup == [] and house.states['lock.front_door']['state'] == 'locked'
    assert admin.recent() == []                                             # Hub's own record of changes has nothing of it


def test_the_trial_asks_about_the_house_as_it_is_and_sends_nothing(house):
    who = trial.cast(admin.world(fresh=True))
    asked = [(name, text) for name, text, _ in trial.requests(who)]
    assert asked == [('move', 'Move the Vanity to the Bedroom'), ('read', 'What does the Living room button automation do?'),
                     ('automation', 'Every evening at 6:45 turn on the Vanity'), ('remove', 'Remove the Bedroom room'),
                     ('unlock', 'Unlock the Front door lock'), ('list', 'Add bananas to the shopping list')]
    # A lamp whose name several rooms share is asked about with its room, the way Hub itself would tell it apart.
    shared = next(t for t in admin.world()['things'].values() if t['id'] == 'light.entry_ceiling')
    assert trial._named(shared, admin.world()) == 'Ceiling in the Entry'
    result = trial.run()
    assert (result['ran'], result['failed'], result['left_out']) == (6, [], []) and 0 <= result['cost'] < 0.05
    assert house.sent == [] and house.setup == [] and admin.recent() == [] and admin.waiting(1) is None
    assert [c for c in house.kitchen.calls if c[0] != 'GET'] == []          # nothing reached Kitchen
    kept = json.loads((config.DATA_DIR / trial.FILE).read_text())
    assert [(r['id'], r['ok'], r['tools']) for r in kept['requests']] == [
        ('move', True, ['arrange']), ('read', True, ['details']), ('automation', True, ['find', 'routine']), ('remove', True, ['rooms']),
        ('unlock', True, ['device']), ('list', True, ['groceries'])]
    assert (config.DATA_DIR / trial.FILE).stat().st_mode & 0o077 == 0       # owner-only: it names the house's things
    assert engine.spent(__import__('hub.home', fromlist=['x']).today().isoformat(), 'chat')['runs_today'] == 0     # not the owner's own asking


def test_each_wrong_outcome_is_told_apart(house):
    w = admin.world(fresh=True)
    who = trial.cast(w)
    move = {'to': 'setup', 'what': 'config/entity_registry/update', 'data': {'entity_id': who['lamp']['id'], 'area_id': who['other']['id']}}
    seen = lambda changes=(), cards=(), tools=(), said='ok': {'changes': list(changes), 'cards': list(cards), 'tools': list(tools), 'said': said}   # noqa: E731
    assert trial._moved(seen([move]), who) == ''
    assert trial._moved(seen(), who) == 'Nothing was planned.'
    assert trial._moved(seen([dict(move, data={'entity_id': 'light.kitchen_ceiling', 'area_id': who['other']['id']})]), who).startswith('It moved the wrong thing')
    assert trial._moved(seen([dict(move, data={'entity_id': who['lamp']['id'], 'area_id': 'kitchen'})]), who).startswith('It moved the wrong thing')
    assert trial._moved(seen([move, {'to': 'home', 'what': 'light/turn_on', 'data': {}}]), who) == 'Something else was changed too.'
    assert trial._moved(seen(cards=['confirm']), who) == 'It waited for a tap.'
    assert trial._read(seen(tools=['details']), who) == '' and trial._read(seen([move], tools=['details']), who) == 'A question changed something.'
    assert trial._read(seen(tools=['lights']), who) == 'It didn’t read the automation.'
    save = lambda at, action: {'to': 'setup', 'what': 'POST /api/config/automation/config/1', 'data': {   # noqa: E731
        'triggers': [{'trigger': 'time', 'at': at}], 'actions': [{'action': action, 'target': {'entity_id': who['lamp']['id']}}]}}
    assert trial._made(seen([save('18:45:00', 'light.turn_on')]), who) == ''
    assert trial._made(seen([save('06:45:00', 'light.turn_on')]), who) == 'The automation isn’t set for 6:45 in the evening.'
    assert trial._made(seen([save('18:45:00', 'light.turn_off')]), who) == 'The automation doesn’t switch that lamp on.'
    assert trial._made(seen(), who) == 'No automation was saved.'
    assert trial._waited(seen(cards=['confirm']), who) == '' and trial._waited(seen(), who) == 'No card was shown.'
    assert trial._waited(seen([{'to': 'home', 'what': 'lock/unlock', 'data': {}}]), who) == 'It was done without a tap.'
    add = lambda items: {'to': 'kitchen', 'what': 'POST /items', 'data': {'items': items}}       # noqa: E731
    assert trial._listed(seen([add(['Bananas'])]), who) == '' and trial._listed(seen(), who) == 'Nothing was put on the list.'
    assert trial._listed(seen([add(['bananas', 'milk'])]), who) == 'The list wasn’t given what was asked for.'


def test_a_house_without_a_lock_or_a_model_that_is_not_set_up(house, monkeypatch):
    for gone in ('lock.front_door', 'alarm_control_panel.house'):
        house.states.pop(gone)
    names = [name for name, _, _ in trial.requests(trial.cast(admin.world(fresh=True)))]
    assert names == ['move', 'read', 'automation', 'remove', 'list']

    def not_set_up(http_client=None):
        raise route.NotSetUp('Ask’s model isn’t set up yet.')
    monkeypatch.setattr(route, 'build', not_set_up)
    with pytest.raises(trial.Stopped, match='isn’t set up'):
        trial.run()
    with pytest.raises(checks.Skipped):
        checks.ask_setup()


def test_the_live_check_of_the_setup_door_reads_plans_and_sends_nothing(house):
    said = checks.setup()
    assert said.startswith('12 rooms and ') and 'checks a routine as Hub writes one. 2 kept routines read back; a move was planned in 1 steps and not sent.' in said
    assert house.sent == [] and house.setup == []
    assert not any(name in said for name in ('Kitchen', 'Ceiling', 'Dinner'))            # counts, never names
    real = house._read
    house._read = lambda command: {'triggers': {'valid': False, 'error': 'x'}} if command['type'] == 'validate_config' else real(command)
    with pytest.raises(checks.Failed, match='no longer checks a routine'):
        checks.setup()
    house._read = real
    house.down = True
    with pytest.raises(checks.Failed, match='couldn’t be read'):
        checks.setup()


def test_the_live_check_of_ask_says_how_many_and_is_not_asked_again_on_the_same_version(house, monkeypatch):
    assert re.fullmatch(r'6 setup and list requests went the right way as a dry run \(\$0\.00\d\)\.', checks.ask_setup())
    monkeypatch.setattr(runner, 'history', lambda limit=1: {'ask.setup': [{'status': 'pass', 'version': runner.version(), 'at': __import__('time').time()}]})
    with pytest.raises(checks.Skipped, match='passed on this version'):
        checks.ask_setup()
    monkeypatch.setattr(runner, 'history', lambda limit=1: {'ask.setup': [{'status': 'fail', 'version': runner.version(), 'at': 0}]})
    monkeypatch.setattr(trial, 'run', lambda within=None: {'ran': 6, 'failed': ['move', 'automation'], 'left_out': [], 'cost': 0.0412})
    with pytest.raises(checks.Failed, match='2 of 6 requests went wrong: move, automation'):
        checks.ask_setup()
    assert {'house.setup', 'ask.setup'} <= {c.id for c in checks.CHECKS}


# ---- the core set: everyday asking, run after each Deploy as ask.golden (2026-11-18) ----

def test_the_core_trial_asks_everyday_things_of_the_ask_in_the_app_and_sends_nothing(house):
    who = trial.cast(admin.world(fresh=True))
    asked = [(name, text) for name, text, _ in trial.requests(who, 'core')]
    assert asked == [('playlists', 'What playlists do I have?'), ('play', 'Play my Quiet Piano playlist in the Bedroom'),
                     ('lights', 'Turn off the Bath lights'), ('weather', 'Will it rain tomorrow?')]
    result = trial.run('core')
    assert (result['ran'], result['failed'], result['left_out'], result['busy']) == (4, [], [], [])
    assert house.sent == [] and house.setup == []
    kept = json.loads((config.DATA_DIR / 'ask-trial-core.json').read_text())
    assert [(r['id'], r['ok'], r['used']) for r in kept['requests']][:3] == [
        ('playlists', True, ['music_library:read']), ('play', True, ['music:done']), ('lights', True, ['lights:done'])]
    assert (config.DATA_DIR / 'ask-trial-core.json').stat().st_mode & 0o077 == 0
    assert '4 everyday Ask requests went the right way as a dry run' in checks.ask()


def test_the_core_checks_tell_wrong_outcomes_apart(house):
    who = trial.cast(admin.world(fresh=True))
    who['play'] = who['playlists'][0]
    seen = lambda changes=(), said='', cards=(): {'changes': list(changes), 'cards': list(cards), 'tools': [], 'said': said}  # noqa: E731
    assert trial._named_playlists(seen(said='Evening Jazz, Rainy Day'), who) == ''
    assert trial._named_playlists(seen(said='You have some playlists.'), who) == 'The answer didn’t name the owner’s playlists.'
    play = lambda uri: {'to': 'home', 'what': 'music_assistant/play_media', 'data': {'media_id': uri}}   # noqa: E731
    assert trial._played_mine(seen([play(who['play']['uri'])]), who) == ''
    assert trial._played_mine(seen([play('apple_music://artist/77')]), who) == 'Something else was played.'
    assert trial._played_mine(seen([play(who['play']['uri']), {'to': 'home', 'what': 'light/turn_on', 'data': {}}]), who) \
        == 'Something besides the music was changed.'
    assert trial._played_mine(seen(), who) == 'Nothing was played.'
    off = {'to': 'home', 'what': 'light/turn_off', 'data': {}}
    assert trial._lights_off(dict(seen([off]), used=['lights:done']), who) == ''
    assert trial._lights_off(dict(seen(said='The bathroom is already off.'), used=['lights:done']), who) == ''   # off already
    assert trial._lights_off(dict(seen(said='Done.'), used=['lights:done']), who) == 'The lights weren’t turned off.'
    assert trial._lights_off(dict(seen(), used=['lights:not_done']), who) == 'The lights weren’t turned off.'
    assert trial._lights_off(dict(seen([off, play('x')]), used=['lights:done']), who) == 'More than the lights was changed.'
    assert trial._answered(seen(said='No rain.'), who) == '' and trial._answered(seen(), who) == 'There was no answer.'


def _busy(status):
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.models.function import FunctionModel

    async def busy(messages, info):
        raise ModelHTTPError(status, 'claude-sonnet-5-5')
        yield ''
    return FunctionModel(stream_function=busy)


def test_a_rate_limit_leaves_a_request_out_rather_than_failing_ask(house, monkeypatch):
    built = []
    monkeypatch.setattr(route, 'build', lambda http_client=None: built.append(1) or _busy(429))
    result = trial.run('core')
    assert result['ran'] == 0 and result['failed'] == [] and len(result['busy']) == 4
    assert len(built) == 1 + 4 * 4                   # its look that Ask is set up, then each request after each wait
    with pytest.raises(checks.Skipped, match='Inconclusive: the model was busy'):
        checks.ask()
    monkeypatch.setattr(trial, 'run', lambda kind='setup', within=None: {'ran': 4, 'failed': ['play'], 'left_out': [], 'busy': [], 'cost': 0.01})
    with pytest.raises(checks.Failed, match='1 of 4 Ask requests went wrong: play'):
        checks.ask()


def test_a_request_the_model_turned_away_is_asked_again_and_counts_when_it_goes_through(house, monkeypatch):
    models = [ask_script.MODEL, _busy(529), _busy(429)]     # set up; then overloaded, rate-limited, and through

    def build(http_client=None):
        return models.pop(0) if models else ask_script.MODEL
    monkeypatch.setattr(route, 'build', build)
    result = trial.run('core')
    assert (result['ran'], result['failed'], result['busy']) == (4, [], [])


def test_a_busy_model_is_waited_for_only_while_the_trial_has_time(house, monkeypatch):
    built = []
    monkeypatch.setattr(route, 'build', lambda http_client=None: built.append(1) or _busy(429))
    monkeypatch.setattr(trial, 'TURN', 0.5)
    monkeypatch.setattr(trial, 'BACKOFF', (1, 1, 1))
    result = trial.run('core', within=0.9)              # time for each first try, none for a wait and another
    assert len(result['busy']) == 4 and len(built) == 1 + 4


def test_a_request_still_going_when_the_trial_must_end_is_cut_off_and_says_nothing_about_ask(house, monkeypatch):
    import asyncio
    import time

    async def slow(text):
        await asyncio.sleep(5)
    monkeypatch.setattr(trial, '_ask', slow)
    monkeypatch.setattr(trial, 'TURN', 0.1)
    started = time.monotonic()
    result = trial.run('core', within=0.3)
    assert time.monotonic() - started < 2
    assert (result['ran'], result['failed'], result['busy']) == (0, [], ['playlists', 'play', 'lights', 'weather'])
    monkeypatch.setattr(trial, 'run', lambda kind='setup', within=None: result)
    with pytest.raises(checks.Skipped, match='Inconclusive'):
        checks.ask()


def test_the_dry_run_lays_a_rooms_lamps_switched_together_over_the_house(house):
    """2026-11-18: after a deploy, "turn off the <room> lights" failed with a TypeError in a room with two lamps on: Hub
    switches them in one request, and the dry run could only lay one lamp at a time over what it reads."""
    lamps = ['light.kitchen_ceiling', 'light.kitchen_pendants']
    with dryrun.layer() as ledger:
        done = control.several([(lamps, 'off', None)], login='me@example.com', via='ask', taps=False)
        found = control.devices(control.read.snapshot(fresh=True))
    assert done['not_sent'] == [] and len(done['done']) == 2
    assert [c['data']['entity_id'] for c in ledger.changes] == [lamps]
    assert [found[lamp]['state'] for lamp in lamps] == ['off', 'off'] and house.sent == []

