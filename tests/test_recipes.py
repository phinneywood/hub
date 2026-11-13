"""Recipes: named routines of allowed steps, proposed by Ask, saved only by a signed-in tap, checked at every run.
A made-up household behind Hub's doors; nothing real is read or changed."""
import pytest
from fastapi.testclient import TestClient

from hub.engine import catalog, recipes
from hub.main import app
from tests import golden_world

ME = 'me@example.com'
SIGNED_IN = {'tailscale-user-login': ME, 'origin': 'http://testserver'}
ASK = 'save this as movie time: dim the reading lamp to 20% and turn on movie night'
STEPS = [{'tool': 'home_control', 'args': {'device': 'Reading Lamp', 'action': 'brightness', 'brightness_percent': 20}},
         {'tool': 'home_control', 'args': {'device': 'Movie Night', 'action': 'activate'}}]


@pytest.fixture
def house(monkeypatch):
    """The made-up household; writes are recorded by the fake services instead of a dry run."""
    world = golden_world.install(monkeypatch)
    sent = []
    from hub.tools.house import broker
    monkeypatch.setattr(broker, '_exchange', lambda m, t: sent.append(m) or [] if m['op'] == 'request' else world.exchange(m, t))
    return sent


# ---- a recipe can never add a capability ----

@pytest.mark.parametrize('steps, why', [
    ([], 'at least one step'),
    ([{'tool': 'lock', 'args': {'device': 'Front Door'}}], 'doesn’t fit'),
    ([{'tool': 'edit_playlist', 'args': {'action': 'delete', 'playlist': 'Calm Dinner'}}], 'doesn’t fit'),
    ([{'tool': 'home_control', 'args': {'device': 'Reading Lamp', 'action': 'unlock'}}], 'doesn’t fit'),
    ([{'tool': 'home_control', 'args': {'device': 'Reading Lamp', 'action': 'on', 'login': 'x@example.com'}}], 'doesn’t fit'),
    ([{'tool': 'home_control', 'args': {'device': 'Reading Lamp', 'action': 'on', 'said': 'remember'}}], 'doesn’t fit'),
    ([{'tool': 'home_control', 'args': {'device': 'Front Door Lock', 'action': 'off'}}], 'Front Door Lock'),
    ([{'tool': 'play_music', 'args': {'speaker': 'Garage Speaker', 'action': 'play'}}], 'Garage Speaker'),
    (STEPS * 5, 'at most 8'),
])
def test_only_steps_ask_could_already_take(house, steps, why):
    with pytest.raises(recipes.Refused, match=why):
        recipes.check(steps)


def test_steps_are_kept_in_the_controls_own_terms(house):
    kept = recipes.check(STEPS)
    assert kept == STEPS and [recipes.describe(s) for s in kept] == ['Reading Lamp at 20%', 'Movie Night scene']


def test_proposed_only_when_asked_and_under_the_owners_name(house):
    with pytest.raises(recipes.Refused, match='doesn’t ask'):
        recipes.propose('movie time', STEPS, 'dim the reading lamp to 20%', login=ME)
    with pytest.raises(recipes.Refused, match='owner gave'):
        recipes.propose('cinema', STEPS, ASK, login=ME)
    with pytest.raises(recipes.Refused, match='short name'):
        recipes.propose('a very long name for a recipe here', STEPS, ASK, login=ME)
    found = catalog.propose_recipe(ME, 'Movie time', STEPS, said=ASK)
    assert 'Nothing is saved yet' in found['needs_tap'] and 'Reading Lamp at 20%' in found['needs_tap']
    assert [r['name'] for r in recipes.waiting()] == ['Movie time'] and recipes.saved() == [] and house == []
    assert 'signed in' in catalog.propose_recipe(None, 'Movie time', STEPS, said=ASK)['refused']


# ---- saved only by a tap, listed, renamed, trimmed, removed ----

def test_saved_by_a_signed_in_tap_then_edited_on_the_page(house):
    proposal = recipes.propose('movie time', STEPS, ASK, login=ME)
    client = TestClient(app)
    page = client.get('/engine/recipes', headers=SIGNED_IN).text
    assert 'movie time' in page and 'Reading Lamp at 20%' in page and ASK in page
    assert client.post(f'/engine/recipes/{proposal["id"]}/save', headers={'origin': 'http://testserver'}).status_code in (401, 403)
    assert client.post(f'/engine/recipes/{proposal["id"]}/save', headers={'tailscale-user-login': ME,
                                                                         'origin': 'http://evil.example'}).status_code == 403
    client.post(f'/engine/recipes/{proposal["id"]}/save', headers=SIGNED_IN)
    [kept] = recipes.saved()
    assert kept['name'] == 'movie time' and recipes.waiting() == [] and house == []
    client.post('/engine/recipes/rename', data={'recipe_id': kept['id'], 'name': 'film night'}, headers=SIGNED_IN)
    client.post('/engine/recipes/drop-step', data={'recipe_id': kept['id'], 'index': 1}, headers=SIGNED_IN)
    [kept] = recipes.saved()
    assert kept['name'] == 'film night' and [recipes.describe(s) for s in kept['steps']] == ['Reading Lamp at 20%']
    client.post('/engine/recipes/remove', data={'recipe_id': kept['id']}, headers=SIGNED_IN)
    assert recipes.saved() == [] and 'Recipes' in client.get('/settings', headers=SIGNED_IN).text


def test_a_proposal_with_the_same_name_replaces_the_recipe_when_saved(house):
    recipes.keep('movie time', STEPS)
    recipes.save(recipes.propose('movie time', STEPS[:1], ASK, login=ME)['id'])
    [kept] = recipes.saved()
    assert len(kept['steps']) == 1


# ---- run by name, through the usual controls, checked again ----

def test_runs_by_name_through_the_usual_controls(house):
    from hub.tools.house import control
    recipes.keep('movie time', STEPS)
    assert 'movie time' in recipes.for_ask() and 'Reading Lamp at 20%' in recipes.for_ask()
    refused = catalog.run_recipe(ME, 'movie time', said='what is on tonight?')
    assert 'doesn’t name' in refused['refused'] and house == []
    found = catalog.run_recipe(ME, 'Movie Time', said='movie time please')
    assert found['ran'] == 'movie time' and [s['step'] for s in found['steps']] == ['Reading Lamp at 20%', 'Movie Night scene']
    assert [m['path'] for m in house] == ['/api/services/light/turn_on', '/api/services/scene/turn_on']
    assert len(control.recent()) == 2                    # each step recorded with Undo, like a typed request


def test_a_recipe_that_no_longer_fits_does_not_run(house, monkeypatch):
    from hub.tools.house import read
    recipes.keep('movie time', STEPS)
    real = read.snapshot

    def without_scene(fresh=False):
        found = real(fresh)
        return {**found, 'scenes': []}
    monkeypatch.setattr(read, 'snapshot', without_scene)
    found = catalog.run_recipe(ME, 'movie time', said='movie time')
    assert 'can’t run as saved' in found['refused'] and house == []      # checked whole before any step runs


def test_a_step_that_is_refused_stops_the_rest(house):
    from hub.engine import state
    recipes.keep('movie time', STEPS)
    state.set_trust(catalog.action('house.control'), 'suggest')
    found = catalog.run_recipe(ME, 'movie time', said='movie time')
    assert 'Suggest only' in found['stopped'] and len(found['steps']) == 1 and house == []


def test_recipes_reach_ask_on_every_turn(house):
    from hub.agent import agent
    assert 'Recipes the owner saved' not in (agent.settings('hi', ME, [])['instructions'] or '')
    recipes.keep('movie time', STEPS)
    assert 'movie time: Reading Lamp at 20%; Movie Night scene' in agent.settings('hi', ME, [])['instructions']
