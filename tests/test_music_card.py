"""The chat's Now playing card: live state through the broker, and taps through control.change. Fake broker only."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import hub
from hub.main import app
from hub.tools.house import broker, control, nowplaying, read, zones
from tests.test_house import SIGNED_IN, fake  # noqa: F401 - the fixture

LIVING, KITCHEN = 'media_player.living_room', 'media_player.kitchen'
JSON = {**SIGNED_IN, 'accept': 'application/json'}


@pytest.fixture
def world(fake, monkeypatch):  # noqa: F811
    """The living room playing alone at 15%, the kitchen idle at 20%; joins and unjoins land in the fake house."""
    control._recent.clear()
    states = {s['entity_id']: s for s in fake['states']}
    states[LIVING]['attributes']['group_members'] = [LIVING]
    states[LIVING]['attributes'].update(media_album_name='Album C')
    inner = broker._exchange

    def exchange(message, timeout):
        if message['op'] == 'request':
            service, data = message['path'].rsplit('/', 1)[1], message['body']
            if service == 'join':
                group = list(states[data['entity_id']]['attributes'].get('group_members') or [data['entity_id']])
                group += [m for m in data['group_members'] if m not in group]
                for m in group:
                    states[m]['attributes']['group_members'] = list(group)
                    if m != data['entity_id']:
                        states[m]['state'] = states[data['entity_id']]['state']
            elif service == 'unjoin':
                for s in states.values():
                    s['attributes']['group_members'] = [m for m in s['attributes'].get('group_members') or ()
                                                        if m != data['entity_id']]
                states[data['entity_id']]['attributes']['group_members'] = []
        return inner(message, timeout)
    monkeypatch.setattr(broker, '_exchange', exchange)
    read.clear_cache()
    fake['by_id'] = states
    return fake


def sent(world):
    return [m['path'].removeprefix('/api/services/') for m in world['asked'] if m['op'] == 'request']


def test_the_card_reads_the_speakers_live(world):
    now = nowplaying.state(LIVING)
    assert now['leader'] == LIVING and now['playing'] and now['label'] == 'Playing'
    assert (now['title'], now['artist'], now['album']) == ('Song A', 'Band B', 'Album C')
    chips = {s['id']: s for s in now['speakers']}
    assert set(chips) == {LIVING, KITCHEN}            # Music Assistant speakers only, as Ask sees them
    assert chips[LIVING]['in'] and not chips[KITCHEN]['in'] and chips[KITCHEN]['volume'] == 20
    assert now['volume'] == 15
    assert nowplaying.state('media_player.gone') == {'gone': True}


def test_adding_a_room_joins_it_in_sync_at_the_playing_volume_with_undo(world):
    client = TestClient(app)
    assert client.post(f'/house/now/{LIVING}', data={'action': 'add', 'target': KITCHEN},
                       headers={'origin': 'http://testserver'}).status_code == 403          # signed in only
    assert client.post(f'/house/now/{LIVING}', data={'action': 'add', 'target': KITCHEN},
                       headers={'origin': 'http://evil.example', 'tailscale-user-login': 'me@example.com'}).status_code == 403
    assert sent(world) == []
    reply = client.post(f'/house/now/{LIVING}', data={'action': 'add', 'target': KITCHEN}, headers=JSON).json()
    assert reply['ok'] and 'joined' in reply['said'] and reply['batch']
    assert sent(world) == ['media_player/volume_set', 'media_player/join']     # silent kitchen starts at 15%, not louder
    assert world['by_id'][KITCHEN]['attributes']['volume_level'] == 0.15
    now = client.get(f'/house/now/{LIVING}', headers=SIGNED_IN).json()
    assert {s['id'] for s in now['speakers'] if s['in']} == {LIVING, KITCHEN}
    assert client.get(f'/house/now/{KITCHEN}', headers=SIGNED_IN).json()['leader'] == LIVING   # same group either way
    undone = client.post(f'/house/undo/{reply["batch"]}', headers=JSON).json()
    assert undone['ok'] and sent(world)[-2:] == ['media_player/unjoin', 'media_player/volume_set']
    assert world['by_id'][KITCHEN]['attributes']['volume_level'] == 0.2
    assert [r['via'] for r in control.recent()] == ['undo', 'card']


def test_a_room_comes_with_its_zone(world, monkeypatch):
    monkeypatch.setattr(zones, 'mates', lambda e, players: {KITCHEN: [LIVING]}.get(e, []))
    with pytest.raises(control.Refused, match='already playing'):
        nowplaying.tap(LIVING, 'add', login='me@example.com', target=LIVING)
    joined = []
    monkeypatch.setattr(control, 'change', lambda targets, action, **kw: joined.append((targets, action, kw['members'])))
    nowplaying.tap(LIVING, 'add', login='me@example.com', target=KITCHEN)
    assert joined == [([LIVING], 'group', [KITCHEN])]                   # the mate already plays: not added twice


def test_removing_a_room_leaves_the_group_playing_and_the_leader_stays(world):
    nowplaying.tap(LIVING, 'add', login='me@example.com', target=KITCHEN)
    with pytest.raises(control.Refused, match='leads this group'):
        nowplaying.tap(LIVING, 'remove', login='me@example.com', target=LIVING)
    result = nowplaying.tap(KITCHEN, 'remove', login='me@example.com', target=KITCHEN)
    assert result['done'] and sent(world)[-1] == 'media_player/unjoin'
    assert [s['id'] for s in nowplaying.state(LIVING)['speakers'] if s['in']] == [LIVING]
    assert nowplaying.state(LIVING)['playing']
    with pytest.raises(control.Refused, match='isn’t part of a group'):
        nowplaying.tap(LIVING, 'remove', login='me@example.com', target=KITCHEN)


def test_the_group_slider_moves_every_speaker_by_the_same_step_in_one_change(world):
    nowplaying.tap(LIVING, 'add', login='me@example.com', target=KITCHEN)
    world['by_id'][KITCHEN]['attributes']['volume_level'] = 0.25
    read.clear_cache()
    assert nowplaying.state(LIVING)['volume'] == 20
    result = nowplaying.tap(LIVING, 'group_volume', login='me@example.com', value=30)
    levels = {e: s['attributes']['volume_level'] for e, s in world['by_id'].items() if e in (LIVING, KITCHEN)}
    assert levels == {LIVING: 0.25, KITCHEN: 0.35}
    rows = control.summary(result['batch'])
    assert len(rows['done']) == 2 and rows['undo']                       # one batch, one Undo for the whole group


def test_play_pause_next_and_one_speakers_volume(world):
    client = TestClient(app)
    for action in ('pause', 'play', 'next'):
        assert client.post(f'/house/now/{LIVING}', data={'action': action}, headers=JSON).json()['ok']
    assert sent(world) == ['media_player/media_pause', 'media_player/media_play', 'media_player/media_next_track']
    reply = client.post(f'/house/now/{LIVING}', data={'action': 'volume', 'target': KITCHEN, 'value': 40}, headers=JSON).json()
    assert reply['said'] == 'Kitchen speaker volume 40%'
    refused = client.post(f'/house/now/{LIVING}', data={'action': 'volume', 'target': 'light.counter', 'value': 40}, headers=JSON)
    assert refused.status_code == 409 and 'Pick one' in refused.json()['error']
    assert client.post(f'/house/now/{LIVING}', data={'action': 'unlock'}, headers=JSON).status_code == 409


def test_a_resume_that_stays_silent_is_said_plainly(world, monkeypatch):
    world['by_id'][LIVING]['state'] = 'paused'
    read.clear_cache()
    inner = broker._exchange
    monkeypatch.setattr(broker, '_exchange', lambda m, t: [] if m['op'] == 'request' else inner(m, t))  # accepted, silent
    reply = TestClient(app).post(f'/house/now/{LIVING}', data={'action': 'play'}, headers=JSON).json()
    assert not reply['ok'] and 'isn’t playing' in reply['said']


def test_the_card_respects_the_limit_of_changes_a_minute(world):
    control._recent.extend([__import__('time').monotonic()] * control.PER_MINUTE)
    reply = TestClient(app).post(f'/house/now/{LIVING}', data={'action': 'pause'}, headers=JSON)
    assert reply.status_code == 409 and 'in the last minute' in reply.json()['error'] and sent(world) == []


def test_a_down_broker_says_so(world, monkeypatch):
    def refuse(message, timeout):
        raise broker.NotSent('The Home Assistant broker on this Mac isn’t answering.')
    monkeypatch.setattr(broker, '_exchange', refuse)
    read.clear_cache()
    reply = TestClient(app).get(f'/house/now/{LIVING}', headers=SIGNED_IN)
    assert reply.status_code == 503 and 'broker' in reply.json()['error']


# ---- Stage 2 and 3: the mini-player on Hub's pages and the Music screen ----

def test_every_group_is_listed_playing_first_with_the_music_assistant_link(world, monkeypatch):
    monkeypatch.setattr(read, 'links', lambda: {'music_url': 'https://music.example:8095'})
    found = TestClient(app).get('/house/now', headers=SIGNED_IN).json()
    assert [g['leader'] for g in found['groups']] == [LIVING, KITCHEN]      # two groups; the playing one first
    assert found['groups'][0]['playing'] and not found['groups'][1]['playing']
    assert found['open'] == 'https://music.example:8095'
    nowplaying.tap(LIVING, 'add', login='me@example.com', target=KITCHEN)
    assert [g['leader'] for g in nowplaying.groups()] == [LIVING]            # one group once the kitchen joins
    assert TestClient(app).get(f'/house/now/{LIVING}', headers=SIGNED_IN).json()['open'] == 'https://music.example:8095'


def test_taps_from_the_bar_and_the_music_screen_are_recorded_as_theirs(world):
    client = TestClient(app)
    for via in ('bar', 'music'):
        assert client.post(f'/house/now/{LIVING}', data={'action': 'pause', 'via': via}, headers=JSON).json()['ok']
    assert [r['via'] for r in control.recent()] == ['music', 'bar']
    assert client.post(f'/house/now/{LIVING}', data={'action': 'pause', 'via': 'ask'}, headers=JSON).status_code == 409
    page = client.get('/house/changes', headers=SIGNED_IN).text
    assert 'from the mini-player' in page and 'from Music' in page and 'href="/house/music"' in page


def test_the_music_screen_and_the_mini_player_are_on_hubs_pages_but_not_ask(world, monkeypatch):
    monkeypatch.setattr(read, 'links', lambda: {'music_url': 'https://music.example:8095'})
    client = TestClient(app)
    screen = client.get('/house/music', headers=SIGNED_IN).text
    assert 'data-music-screen' in screen and 'Open in Music Assistant' in screen and 'target="_blank" rel="noopener"' in screen
    assert 'id="mp-bar"' in screen and '/static/music.js' in screen
    ask = client.get('/ask', headers=SIGNED_IN).text
    assert 'mp-bar' not in ask and 'music.js' not in ask                      # Ask has its own card
    script = (Path(hub.__file__).parent / 'static/music.js').read_text()    # the installed package's, as deployed
    assert 'innerHTML' not in script                                           # names are data, set as text only


# ---- Today's Home card: the way to Music and Music Assistant ----

def test_todays_home_card_leads_to_music_and_music_assistant(world, monkeypatch):
    from hub import home
    monkeypatch.setattr(read, 'links', lambda: {'music_url': 'https://music.example:8095'})
    tile = home.house()
    assert tile['links'] == [{'label': 'Music', 'href': '/house/music'},
                             {'label': 'Open in Music Assistant', 'href': 'https://music.example:8095'}] and not tile['more']
    page = TestClient(app).get('/today', headers=SIGNED_IN).text
    assert '<a class="btn small" href="https://music.example:8095" target="_blank" rel="noopener">Open in Music Assistant' in page
    world['by_id'][LIVING]['state'] = 'paused'                               # paused still counts: it can be resumed
    read.clear_cache()
    assert [l['label'] for l in home.house()['links']] == ['Music', 'Open in Music Assistant']
    monkeypatch.setattr(read, 'links', lambda: {})
    assert [l['label'] for l in home.house()['links']] == ['Music']          # no address set: no Music Assistant button
    world['by_id'][LIVING]['state'] = 'idle'
    read.clear_cache()
    quiet = home.house()
    assert quiet['links'] == [] and quiet['more'] == {'label': 'Music', 'href': '/house/music'}
    assert 'class="tile-more" href="/house/music"' in TestClient(app).get('/today', headers=SIGNED_IN).text


def test_todays_home_card_keeps_the_quiet_music_link_when_home_is_down(monkeypatch):
    from hub import home

    def refuse(message, timeout):
        raise broker.NotSent('The Home Assistant broker on this Mac isn’t answering.')
    monkeypatch.setattr(broker, '_exchange', refuse)
    read.clear_cache()
    assert home.house()['more'] == {'label': 'Music', 'href': '/house/music'}
