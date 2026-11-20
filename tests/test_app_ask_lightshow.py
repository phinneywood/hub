"""Ask and the light show (hub/app/ask/tools.py light_show): started with something Music Assistant found, a card
to choose from when the words fit several things, ended on request, and never started by plain music. A made-up
house with a made-up show (tests/app_world.py) and a stand-in for the model (tests/ask_script.py)."""
import pytest

from hub.tools.house import lightshow, music, read
from tests import app_world
from tests.test_app_ask import SIGNED_IN, ask, client, picture
from tests.test_app_ask import house, world  # noqa: F401 - the fixtures

SHOW = app_world.SHOW


@pytest.fixture
def show(house, monkeypatch):  # noqa: F811 - the fixture above
    app_world.add_show(house)
    lightshow.forget()
    read.clear_cache()
    monkeypatch.setattr(lightshow, 'CHECK_WAIT', 0)
    return house


def started(world):
    return [body for what, body in world.sent if what == 'script/turn_on' and body['entity_id'] == SHOW['start']]


def test_a_light_show_starts_with_the_one_playlist_that_fits(show):
    answer, cards, _ = ask('light show with Sunday Morning')
    assert answer == 'The light show is on with Sunday Morning.'
    assert started(show) == [{'entity_id': SHOW['start'], 'variables': {'media_id': 'library://playlist/3', 'media_type': 'playlist'}}]
    assert picture()['show']['playing']


def test_words_that_fit_several_things_bring_a_card_and_nothing_starts_until_a_tap(show):
    answer, cards, _ = ask('light show with day')
    assert answer == 'Which one for the light show?' and not started(show)
    (card,) = cards
    assert card['kind'] == 'showpick' and [c['name'] for c in card['items']] == ['Sunday Morning', 'Rainy Day']
    assert all(c['handle'].startswith('#') and 'uri' not in c for c in card['items'])      # a handle, never an address
    rainy = card['items'][1]
    answer = client.post('/app/api/show/start', json={'handle': rainy['handle']}, headers=SIGNED_IN).json()
    assert answer['ok'] and answer['said'] == 'Light show with Rainy Day' and answer['batch'] is None
    assert started(show)[-1]['variables'] == {'media_id': 'library://playlist/5', 'media_type': 'playlist'}


def test_a_tap_starts_only_what_a_lookup_found_and_only_when_signed_in(show):
    for handle in ('#000000', 'library://playlist/5', 'Rainy Day', ''):
        answer = client.post('/app/api/show/start', json={'handle': handle}, headers=SIGNED_IN).json()
        assert answer['error'].startswith('Hub doesn’t remember that choice')
    ask('light show with day')
    assert client.post('/app/api/show/start', json={'handle': music._handle('library://playlist/5')},
                       headers={'origin': 'http://testserver'}).status_code in (401, 403)
    assert not started(show)


def test_the_show_ends_when_asked(show):
    ask('light show with Sunday Morning')
    answer, _, _ = ask('end the light show')
    assert answer == 'The light show has ended.' and show.states[SHOW['flag']]['state'] == 'off' and picture()['show'] is None
    answer, _, _ = ask('stop the show')
    assert 'No light show is on' in answer


def test_playing_music_never_starts_a_show(show):
    ask('play sunday morning in the living room')
    assert not started(show) and picture()['show'] is None


def test_a_house_without_a_show_offers_no_card(house):  # noqa: F811
    answer, cards, _ = ask('light show with day')
    assert 'no light show' in answer.lower() and not cards
    assert not [w for w, _ in house.sent if w == 'script/turn_on']


def offered():
    from hub.app import routes
    routes._picks.update(at=float('-inf'), found=[])      # the playlists read afresh, as the sheet opening them would
    return client.get('/app/api/picks', headers=SIGNED_IN).json()['picks']


def test_the_light_show_sheet_starts_with_one_of_the_playlists_it_offers(show):
    assert picture()['can_show'] and picture()['show'] is None
    rainy = next(p for p in offered() if p['name'] == 'Rainy Day')
    answer = client.post('/app/api/show/start', json={'uri': rainy['uri']}, headers=SIGNED_IN).json()
    assert answer['ok'] and answer['said'] == 'Light show with Rainy Day'
    assert started(show)[-1]['variables'] == {'media_id': rainy['uri'], 'media_type': 'playlist'}
    assert picture()['show']['playing']


def test_the_sheet_starts_only_a_playlist_it_offered(show):
    offered()
    for uri in ('library://playlist/999', 'library://track/1', 'Rainy Day'):
        answer = client.post('/app/api/show/start', json={'uri': uri}, headers=SIGNED_IN).json()
        assert answer['error'] == 'Hub can’t find that playlist any more.'
    assert not started(show)


def test_a_house_without_a_show_offers_no_way_to_start_one(house):  # noqa: F811
    assert picture()['can_show'] is False
