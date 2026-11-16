"""The trial of Ask on the real house (hub/app/ask/trial.py) and the two live checks built on it, run here on the
made-up house with the stand-in for the model: what is asked, that nothing is sent, and how each wrong outcome is
told apart.
"""
import json
import re

import pytest

from hub import config
from hub.app.ask import route, tools, trial
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
    monkeypatch.setattr(trial, 'run', lambda: {'ran': 6, 'failed': ['move', 'automation'], 'left_out': [], 'cost': 0.0412})
    with pytest.raises(checks.Failed, match='2 of 6 requests went wrong: move, automation'):
        checks.ask_setup()
    assert {'house.setup', 'ask.setup'} <= {c.id for c in checks.CHECKS}
