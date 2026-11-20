"""Ask and the light show (hub/app/ask/tools.py light_show): a show plays only songs that have a hand-made show. With
none named, all of them, shuffled; a named one plays alone; anything else has no show yet and nothing starts. Ended on
request, and never started by plain music. A made-up house with a made-up show and made-up songs (tests/app_world.py)
and a stand-in for the model (tests/ask_script.py)."""
import pytest

from hub.tools.house import lightshow, read
from tests import app_world
from tests.test_app_ask import SIGNED_IN, ask, client, picture
from tests.test_app_ask import house, world  # noqa: F401 - the fixtures

SHOW = app_world.SHOW
TITLES = sorted(t for _, t, _, _ in app_world.SHOW_SONGS)


@pytest.fixture
def show(house, tmp_path, monkeypatch):  # noqa: F811 - the fixture above
    app_world.add_show(house)
    lightshow.forget()
    read.clear_cache()
    monkeypatch.setattr(lightshow, 'CHECK_WAIT', 0)
    monkeypatch.setattr(lightshow, 'SONGS', app_world.show_songs(tmp_path / 'maps'))
    return house


def started(world):
    return [body for what, body in world.sent if what == 'script/turn_on' and body['entity_id'] == SHOW['start']]


def queued(world):
    return [body for what, body in world.sent if what == 'music_assistant/play_media' and body.get('enqueue') == 'add']


def uri(title):
    return next(u for _, t, _, u in app_world.SHOW_SONGS if t == title)


def test_a_light_show_with_nothing_named_plays_every_song_with_a_show_shuffled(show):
    answer, cards, _ = ask('light show')
    assert answer.startswith('The light show is on: 3 songs, starting with ') and not cards
    (first,) = started(show)
    rest = [q['media_id'] for q in queued(show)]
    assert first['variables']['media_type'] == 'track' and len(rest) == 2
    assert sorted([first['variables']['media_id'], *rest]) == sorted(u for *_, u in app_world.SHOW_SONGS)
    assert app_world.NO_SHOW[3] not in rest and all(q['entity_id'] == SHOW['player'] for q in queued(show))
    assert picture()['show']['playing']


def test_a_named_song_with_a_show_plays_alone(show):
    answer, _, _ = ask('light show with Paper Kites')
    assert answer == 'The light show is on with Paper Kites.'
    assert started(show)[0]['variables'] == {'media_id': uri('Paper Kites'), 'media_type': 'track'} and not queued(show)


def test_anything_without_a_show_starts_nothing_and_names_the_songs_that_have_one(show):
    for words in ('light show with Thunder Road', 'light show with Sunday Morning'):
        answer, cards, _ = ask(words)
        assert 'There’s no light show for' in answer and 'Morning Run, Night Drive and Paper Kites' in answer
        assert not cards
    assert not started(show) and not queued(show)


def test_the_show_ends_when_asked(show):
    ask('light show with Night Drive')
    answer, _, _ = ask('end the light show')
    assert answer == 'The light show has ended.' and show.states[SHOW['flag']]['state'] == 'off' and picture()['show'] is None
    answer, _, _ = ask('stop the show')
    assert 'No light show is on' in answer


def test_playing_music_never_starts_a_show(show):
    ask('play sunday morning in the living room')
    assert not started(show) and picture()['show'] is None


def test_a_house_without_a_show_starts_nothing(house):  # noqa: F811
    answer, cards, _ = ask('light show')
    assert 'no light show' in answer.lower() and not cards
    assert not [w for w, _ in house.sent if w == 'script/turn_on']


def test_the_sheet_lists_the_songs_with_a_show_and_starts_one_or_all(show):
    songs = client.get('/app/api/show/songs', headers=SIGNED_IN).json()['songs']
    assert [s['title'] for s in songs] == TITLES and all(set(s) == {'id', 'title', 'artist'} for s in songs)
    kites = next(s for s in songs if s['title'] == 'Paper Kites')
    answer = client.post('/app/api/show/start', json={'song': kites['id']}, headers=SIGNED_IN).json()
    assert answer['ok'] and answer['said'] == 'Light show with Paper Kites'
    assert started(show)[-1]['variables'] == {'media_id': uri('Paper Kites'), 'media_type': 'track'}
    client.post('/app/api/show/end', headers=SIGNED_IN)
    answer = client.post('/app/api/show/start', json={'all': True}, headers=SIGNED_IN).json()
    assert answer['ok'] and answer['said'].startswith('Light show with ') and answer['said'].endswith(' and 2 more')


def test_the_sheet_starts_only_a_song_with_a_show_and_only_when_signed_in(show):
    for body in ({'song': app_world.NO_SHOW[0]}, {'song': 'library://track/71'}, {'uri': uri('Night Drive')},
                 {'handle': '#1a2b3c'}, {'all': 'yes'}, {}):
        answer = client.post('/app/api/show/start', json=body, headers=SIGNED_IN).json()
        assert answer.get('error'), body
    assert client.post('/app/api/show/start', json={'all': True}, headers={'origin': 'http://testserver'}).status_code in (401, 403)
    assert client.get('/app/api/show/songs', headers={'origin': 'http://testserver'}).status_code in (401, 403)
    assert not started(show)


def test_a_house_without_a_show_offers_no_way_to_start_one(house):  # noqa: F811
    assert picture()['can_show'] is False
