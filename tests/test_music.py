"""Playlists through Music Assistant's own API. A fake Music Assistant and made-up songs only."""
import io
import json
import urllib.error
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from hub import config, home, model_key
from hub.engine import catalog, state
from hub.main import app
from hub.tools.house import control, music

SONGS = {'apple_music://track/1': ('Morning Light', 'Alex Rivers'), 'apple_music://track/2': ('Slow River', 'Sam Lake'),
         'apple_music://track/3': ('Quiet Hours', 'The Example Band'), 'apple_music://track/4': ('Dinner Waltz', 'Sam Lake')}
SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}


class FakeMusic:
    """Music Assistant as Hub sees it: library playlists, search, and background edits that land at once."""

    def __init__(self):
        self.lists = {'7': {'name': 'Calm Dinner', 'domain': 'builtin', 'editable': True,
                            'uris': ['apple_music://track/1', 'apple_music://track/2', 'apple_music://track/3']},
                      '8': {'name': 'Road Trip', 'domain': 'apple_music', 'editable': True, 'uris': ['apple_music://track/1']},
                      '9': {'name': 'Top Hits', 'domain': 'apple_music', 'editable': False, 'uris': []}}
        self.asked, self.next_id, self.down, self.lag, self.background, self.waiting = [], 20, set(), False, False, []
        self.tokens = [{'name': 'Hub', 'expires_at': '2027-11-12T00:00:00+00:00'}]

    def _item(self, pid):
        p = self.lists[pid]
        return {'item_id': pid, 'name': p['name'], 'is_editable': p['editable'], 'uri': f'library://playlist/{pid}',
                'provider_mappings': [{'provider_domain': p['domain'], 'item_id': f'x{pid}'}]}

    def _song(self, uri, position=None):
        name, artist = SONGS[uri]
        return {'name': name, 'artists': [{'name': artist}], 'uri': uri, **({'position': position} if position else {})}

    def _later(self, work):
        self.waiting.append(work) if self.background else work()

    def __call__(self, command, args, timeout):
        self.asked.append((command, args))
        if command in self.down:
            raise music.NotSent('Music Assistant isn’t answering.')
        if command == 'music/playlists/library_items':
            wanted = args.get('search', '').lower()
            return [self._item(pid) for pid, p in self.lists.items() if wanted in p['name'].lower()]
        if command == 'music/playlists/playlist_tracks':
            while self.waiting:                    # background edits land by the next read
                self.waiting.pop(0)()
            return [self._song(u, n) for n, u in enumerate(self.lists[args['item_id']]['uris'], 1)]
        if command == 'music/search':
            words = args['search_query'].lower().split()
            hits = [self._song(u) for u, (n, a) in SONGS.items() if all(w in f'{n} {a}'.lower() for w in words)]
            return {'tracks': hits, 'albums': [], 'artists': [], 'playlists': []}
        if command == 'music/recently_played_items':
            return [{'name': 'Quiet Hours', 'media_type': 'track'}]
        if command == 'music/playlists/create_playlist':
            self.next_id += 1
            self.lists[str(self.next_id)] = {'name': args['name'], 'domain': 'builtin', 'editable': True, 'uris': []}
            return self._item(str(self.next_id))
        if command == 'music/playlists/add_playlist_tracks':
            p = self.lists[args['db_playlist_id']]
            new = [u for u in dict.fromkeys(args['uris']) if u not in p['uris']]   # skipped as of when it was asked
            if not self.lag:
                self._later(lambda: p['uris'].extend(new))
            return {'id': 'task'}
        if command == 'music/playlists/remove_playlist_tracks':
            p = self.lists[args['db_playlist_id']]
            gone = set(args['positions_to_remove'])
            self._later(lambda: p.update(uris=[u for n, u in enumerate(p['uris'], 1) if n not in gone]))
            return {'id': 'task'}
        if command == 'music/library/remove_item':
            del self.lists[args['library_item_id']]
            return None
        if command == 'auth/tokens':
            return self.tokens
        raise AssertionError(command)


@pytest.fixture
def ma(monkeypatch):
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'home': {'music_url': 'https://music.example:8095'}}))
    fake = FakeMusic()
    monkeypatch.setattr(music, '_send', fake)
    monkeypatch.setattr(music, 'WAIT', 0)
    monkeypatch.setattr(control, 'CHECK_WAIT', 0)
    monkeypatch.setattr(control, '_recent', type(control._recent)())
    music._expiry.update(value=None, at=float('-inf'))
    return fake


def names(fake, pid):
    return [SONGS[u][0] for u in fake.lists[pid]['uris']]


def test_only_allowed_commands_leave_hub(ma):
    for command, args in (('music/playlists/update', {}), ('music/playlists/remove', {}), ('config/providers/get_value', {}),
                          ('auth/token/create', {}), ('music/search', {'providers': ['x']})):
        with pytest.raises(ValueError):
            music._call(command, args)
    assert ma.asked == []


def test_send_uses_the_address_and_key_and_never_puts_the_key_in_the_url(monkeypatch):
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'home': {'music_url': 'https://music.example:8095/'}}))
    music._key.update(value=None, at=float('-inf'))
    seen = {}
    monkeypatch.setattr(model_key, 'secret', lambda ref: seen.setdefault('ref', ref) and 'k-123')

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def urlopen(request, timeout):
        seen.update(url=request.full_url, auth=request.get_header('Authorization'), body=json.loads(request.data))
        return Reply(b'[]')
    monkeypatch.setattr(music.urllib.request, 'urlopen', urlopen)
    assert music.playlists() == []
    assert seen['ref'] == 'op://Hub/Music Assistant/credential'
    assert seen['url'] == 'https://music.example:8095/api' and seen['auth'] == 'Bearer k-123'
    assert seen['body']['command'] == 'music/playlists/library_items' and 'k-123' not in json.dumps(seen['body'])

    def refused(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, 'no', {}, None)
    monkeypatch.setattr(music.urllib.request, 'urlopen', refused)
    with pytest.raises(music.NotSent, match='key'):
        music.playlists()

    def slow(request, timeout):
        raise urllib.error.URLError(TimeoutError())
    monkeypatch.setattr(music.urllib.request, 'urlopen', slow)
    with pytest.raises(music.MusicDown) as caught:
        music.playlists()
    assert not isinstance(caught.value, music.NotSent)


def test_without_address_or_key_nothing_is_sent(monkeypatch):
    music._key.update(value=None, at=float('-inf'))
    with pytest.raises(music.NotSent, match='address'):
        music.playlists()
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'home': {'music_url': 'https://music.example:8095'}}))
    with pytest.raises(music.NotSent, match='no Music Assistant key'):
        music.playlists()


def test_library_lookup(ma, monkeypatch):
    found = catalog.library_lookup()
    assert found['playlists'][0] == {'name': 'Calm Dinner', 'where': 'Music Assistant', 'can_edit': 'everything'}
    assert found['playlists'][1]['can_edit'] == 'add songs only' and found['playlists'][2]['can_edit'] == 'no'
    assert found['recently_played'] == ['Quiet Hours']
    assert catalog.library_lookup(playlist='calm dinner')['songs'][1] == '2. Slow River – Sam Lake'
    assert catalog.library_lookup(search='sam lake')['tracks'] == ['Slow River – Sam Lake', 'Dinner Waltz – Sam Lake']
    assert 'Which playlist?' in catalog.library_lookup(playlist='nothing like it')['error']
    ma.down.add('music/playlists/library_items')
    assert catalog.library_lookup() == catalog.UNAVAILABLE
    (config.DATA_DIR / 'household.json').write_text('{}')
    assert 'isn’t set up' in catalog.library_lookup()['unavailable']


def test_create_and_undo(ma):
    found = music.create('Sunday', ['dinner waltz', 'no such song'], login='me@example.com', via='tap')
    assert found['done'] == ['Made playlist “Sunday” with 1 songs'] and found['missing'] == ['no such song']
    new = next(pid for pid, p in ma.lists.items() if p['name'] == 'Sunday')
    assert names(ma, new) == ['Dinner Waltz']
    assert 'already a playlist' in catalog.edit_playlist('me@example.com', 'create', 'sunday', said='make a playlist Sunday')['refused']
    batch = music.recent_changes()[0]['batch']
    music.undo(batch, login='me@example.com', via='tap')
    assert new not in ma.lists
    with pytest.raises(music.Refused):
        music.undo(batch, login='me@example.com', via='tap')


def test_add_remove_move_and_undo_put_the_list_back(ma):
    assert catalog.edit_playlist('me@example.com', 'add', 'calm dinner', songs=['Dinner Waltz – Sam Lake'])['done'] == [
        'Added 1 song to “Calm Dinner”']
    assert names(ma, '7') == ['Morning Light', 'Slow River', 'Quiet Hours', 'Dinner Waltz']
    catalog.edit_playlist('me@example.com', 'move', 'calm dinner', songs=['dinner waltz'], position=1)
    assert names(ma, '7') == ['Dinner Waltz', 'Morning Light', 'Slow River', 'Quiet Hours']
    catalog.edit_playlist('me@example.com', 'remove', 'calm dinner', songs=['slow river', 'not there'])
    assert names(ma, '7') == ['Dinner Waltz', 'Morning Light', 'Quiet Hours']
    removes = [a for c, a in ma.asked if c == 'music/playlists/remove_playlist_tracks']
    assert removes[-1]['positions_to_remove'] == [3]                       # 1-based, as Music Assistant counts
    for change in music.recent_changes()[:2]:
        music.undo(change['batch'], login='me@example.com', via='tap')
    assert names(ma, '7') == ['Morning Light', 'Slow River', 'Quiet Hours', 'Dinner Waltz']


def test_rename_copies_then_removes_the_old_one(ma):
    found = catalog.edit_playlist('me@example.com', 'rename', 'calm dinner', new_name='Quiet Dinner', said='Rename the Calm Dinner playlist')
    assert found['done'] == ['Renamed “Calm Dinner” to “Quiet Dinner”'] and '7' not in ma.lists
    new = next(pid for pid, p in ma.lists.items() if p['name'] == 'Quiet Dinner')
    assert names(ma, new) == ['Morning Light', 'Slow River', 'Quiet Hours']
    music.undo(music.recent_changes()[0]['batch'], login='me@example.com', via='tap')
    assert [p['name'] for p in ma.lists.values()].count('Calm Dinner') == 1 and new not in ma.lists


def test_a_copy_that_doesnt_fill_keeps_both_and_is_not_confirmed(ma):
    ma.lag = True
    found = catalog.edit_playlist('me@example.com', 'rename', 'calm dinner', new_name='Quiet Dinner', said='Rename the Calm Dinner playlist')
    assert found['not_confirmed'] == ['Renamed “Calm Dinner” to “Quiet Dinner”'] and 'Both playlists' in found['note']
    assert '7' in ma.lists and found['done'] == []


def test_apple_playlists_only_gain_songs(ma):
    found = catalog.edit_playlist('me@example.com', 'add', 'road trip', songs=['quiet hours'])
    assert found['done'] == ['Added 1 song to “Road Trip”'] and 'undo' not in found and 'Music app' in found['note']
    assert not music.recent_changes()[0]['undo']
    for action, extra in (('remove', {'songs': ['morning light']}), ('rename', {'new_name': 'X'}), ('delete', {}),
                          ('move', {'songs': ['morning light'], 'position': 1})):
        assert 'Apple Music playlist' in catalog.edit_playlist('me@example.com', action, 'road trip', **extra,
                                                               said='rename or delete the road trip playlist')['refused']
    assert 'can’t be edited' in catalog.edit_playlist('me@example.com', 'add', 'top hits', songs=['quiet hours'])['refused']


def test_ask_never_deletes_and_needs_a_signed_in_owner_and_trust(ma):
    found = catalog.edit_playlist('me@example.com', 'delete', 'calm dinner', said='Delete the Calm Dinner playlist')
    assert 'Nothing is deleted yet' in found['needs_tap'] and '7' in ma.lists
    assert 'signed in' in catalog.edit_playlist(None, 'add', 'calm dinner', songs=['dinner waltz'])['refused']
    state.set_trust(catalog.action('house.playlists'), 'suggest')
    assert 'Suggest only' in catalog.edit_playlist('me@example.com', 'add', 'calm dinner', songs=['dinner waltz'])['refused']
    assert len(ma.lists['7']['uris']) == 3


def test_an_unreachable_music_assistant_is_not_done(ma):
    ma.down.add('music/playlists/add_playlist_tracks')
    found = catalog.edit_playlist('me@example.com', 'add', 'calm dinner', songs=['dinner waltz'])
    assert found['not_done'] == ['Added 1 song to “Calm Dinner”'] and 'undo' not in found


def test_delete_needs_a_signed_in_tap_on_the_confirmation_page(ma):
    client = TestClient(app)
    page = client.get('/house/playlists', headers=SIGNED_IN).text
    assert 'Calm Dinner' in page and '/house/playlists/delete/7' in page and '/house/playlists/delete/8' not in page
    confirm = client.get('/house/playlists/delete/7', headers=SIGNED_IN).text
    assert 'Delete “Calm Dinner”?' in confirm and '3 songs' in confirm and '7' in ma.lists
    assert client.post('/house/playlists/delete/7', headers={'sec-fetch-site': 'same-origin'}).status_code == 403
    assert '7' in ma.lists
    reply = client.post('/house/playlists/delete/7', headers=SIGNED_IN, follow_redirects=False)
    assert reply.status_code == 303 and reply.headers['location'].startswith('/house/playlists?done=')
    assert '7' not in ma.lists
    after = client.get(reply.headers['location'], headers=SIGNED_IN).text
    assert 'Deleted playlist “Calm Dinner”' in after
    refused = client.get('/house/playlists/delete/8', headers=SIGNED_IN).text
    assert 'Apple Music playlist' in refused and '8' in ma.lists


def test_undo_from_the_page(ma):
    catalog.edit_playlist('me@example.com', 'remove', 'calm dinner', songs=['slow river'])
    client = TestClient(app)
    batch = music.recent_changes()[0]['batch']
    assert f'/house/playlists/undo/{batch}' in client.get('/house/playlists', headers=SIGNED_IN).text
    reply = client.post(f'/house/playlists/undo/{batch}', headers=SIGNED_IN, follow_redirects=False)
    assert reply.status_code == 303 and names(ma, '7') == ['Morning Light', 'Slow River', 'Quiet Hours']


def test_play_a_playlist_by_its_library_address(ma, monkeypatch):
    assert music.playlist_uri('calm dinner') == 'library://playlist/7'
    assert music.playlist_uri('calm') is None                  # only an exact name; otherwise Music Assistant searches
    ma.down.add('music/playlists/library_items')
    assert music.playlist_uri('calm dinner') is None


def test_reminders_a_month_before_sign_in_and_key_end(ma, monkeypatch):
    signed = date(2026, 11, 9)
    settings = {'home': {'music_url': 'https://music.example:8095', 'apple_music_signed_in': signed.isoformat()}}
    (config.DATA_DIR / 'household.json').write_text(json.dumps(settings))
    ends = signed + timedelta(days=180)
    monkeypatch.setattr(home, 'today', lambda: ends - timedelta(days=31))
    assert catalog.music_items() == []
    monkeypatch.setattr(home, 'today', lambda: ends - timedelta(days=10))
    [item] = catalog.music_items()
    assert item['key'] == 'music-apple' and item['tone'] == 'info' and 'ends May 8' in item['title']
    monkeypatch.setattr(home, 'today', lambda: ends + timedelta(days=1))
    assert catalog.music_items()[0]['tone'] == 'warn' and 'ended' in catalog.music_items()[0]['title']
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'home': {'music_url': 'https://music.example:8095'}}))
    monkeypatch.setattr(home, 'today', lambda: date(2027, 10, 20))
    [key] = catalog.music_items()
    assert key['key'] == 'music-key' and 'Nov 12' in key['title']
    calls = [c for c, _ in ma.asked if c == 'auth/tokens']
    assert len(calls) == 1                                     # remembered, so Today doesn't ask each time


def test_reminders_fail_soft(ma, monkeypatch):
    ma.down.add('auth/tokens')
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'home': {'music_url': 'https://music.example:8095',
                                                                         'apple_music_signed_in': 'not a date'}}))
    assert catalog.music_items() == []


def test_catalog_lists_the_new_lookup_control_and_action():
    assert 'music_library' in [l.slug for l in catalog.lookups()]
    [edit] = [c for c in catalog.controls() if c.slug == 'edit_playlist']
    assert edit.action == 'house.playlists' and catalog.action('house.playlists').ceiling == 'undo'


def test_background_edits_are_read_back_before_the_next_step(ma):
    ma.background = True
    ma.lists['7']['uris'].append('apple_music://track/4')
    found = catalog.edit_playlist('me@example.com', 'move', 'calm dinner', songs=['dinner waltz'], position=1)
    assert found['done'] and names(ma, '7') == ['Dinner Waltz', 'Morning Light', 'Slow River', 'Quiet Hours']
    music.undo(music.recent_changes()[0]['batch'], login='me@example.com', via='tap')
    assert names(ma, '7') == ['Morning Light', 'Slow River', 'Quiet Hours', 'Dinner Waltz']


def test_a_move_that_doesnt_land_is_not_confirmed(ma):
    ma.lag = True                                              # adds never land
    found = catalog.edit_playlist('me@example.com', 'move', 'calm dinner', songs=['quiet hours'], position=1)
    assert found['not_confirmed'] and found['done'] == [] and 'Music Assistant didn’t confirm' in found['note']


def test_lists_with_a_song_twice_are_left_alone(ma):
    ma.lists['7']['uris'].append('apple_music://track/1')
    for action, extra in (('move', {'songs': ['quiet hours'], 'position': 1}), ('rename', {'new_name': 'Other'})):
        assert 'twice' in catalog.edit_playlist('me@example.com', action, 'calm dinner', **extra, said='rename it')['refused']
    assert len(ma.lists['7']['uris']) == 4


def test_an_unconfirmed_rename_has_no_undo(ma):
    ma.lag = True
    catalog.edit_playlist('me@example.com', 'rename', 'calm dinner', new_name='Quiet Dinner', said='Rename the Calm Dinner playlist')
    assert not music.recent_changes()[0]['undo']
    with pytest.raises(music.Refused):
        music.undo(music.recent_changes()[0]['batch'], login='me@example.com', via='tap')


def test_a_playlist_plays_by_address_but_is_recorded_by_name(ma, monkeypatch):
    from hub.tools.house import broker, read
    sent = []
    speaker = {'id': 'media_player.kitchen', 'name': 'Kitchen speaker', 'kind': 'media_player', 'state': 'playing',
               'level': None, 'volume': 0.2, 'members': [], 'group': []}
    monkeypatch.setattr(read, 'snapshot', lambda fresh=False: {'rooms': [], 'speakers': [speaker], 'scenes': []})
    monkeypatch.setattr(broker, 'act', lambda domain, service, data: sent.append(data))
    found = catalog.play_music('me@example.com', 'kitchen speaker', 'play_media', what='Calm Dinner', kind='playlist')
    assert found['done'] == ['Kitchen speaker playing “Calm Dinner”'] and sent[-1]['media_id'] == 'library://playlist/7'


def test_refusals_to_the_model_are_scrubbed(ma):
    ma.lists['7']['name'] = 'Mix from friend@example.com'
    refused = catalog.edit_playlist('me@example.com', 'add', 'nothing like it', songs=['quiet hours'])['refused']
    assert 'friend@example.com' not in refused
    assert 'friend@example.com' not in catalog.library_lookup(playlist='nothing like it')['error']


# ---- Playing from Ask: a concrete pick, the asked room only, soft means quiet ----

CATALOG = {'apple_music://playlist/p1': 'Calm Piano Evening', 'apple_music://playlist/p2': 'Gentle Piano Lullabies',
           'apple_music://album/a1': 'Quiet Hours'}


class PlayingMusic(FakeMusic):
    """Search also finds Apple Music playlists and albums, and the owner's own playlists from the library."""

    def __call__(self, command, args, timeout):
        found = super().__call__(command, args, timeout)
        if command == 'music/search':
            words = args['search_query'].lower().split()
            def hit(name):
                return all(w in name.lower() for w in words)
            mine = [self._item(pid) for pid, p in self.lists.items() if hit(p['name'])]
            found['playlists'] = mine + [{'name': n, 'uri': u} for u, n in CATALOG.items() if 'playlist' in u and hit(n)]
            found['albums'] = [{'name': n, 'uri': u, 'artists': [{'name': 'The Example Band'}]}
                               for u, n in CATALOG.items() if 'album' in u and hit(n)]
        return found


@pytest.fixture
def house(ma, monkeypatch):
    """Two Music Assistant speakers, the living room grouped with the kitchen, both idle; changes land at once."""
    from hub.tools.house import broker, read
    monkeypatch.setattr(music, '_send', PlayingMusic())
    world = {'media_player.living_room': {'state': 'idle', 'volume': 0.14, 'members': ['media_player.kitchen'], 'name': 'Living room'},
             'media_player.kitchen': {'state': 'idle', 'volume': 0.3, 'members': ['media_player.living_room'], 'name': 'Kitchen speaker'},
             'sent': [], 'fail': set(), 'stuck': set()}

    def snapshot(fresh=False):
        speakers = [{'id': e, 'name': s['name'], 'kind': 'media_player', 'state': s['state'], 'level': None,
                     'volume': s['volume'], 'members': list(s['members']),
                     'group': [world[m]['name'] for m in s['members']]} for e, s in world.items() if e.startswith('media_player.')]
        return {'rooms': [], 'speakers': speakers, 'scenes': []}

    def act(domain, service, data):
        world['sent'].append((f'{domain}/{service}', data))
        if service in world['fail']:
            raise broker.HomeDown('Home Assistant couldn’t be read just now.')
        if service in world['stuck']:          # accepted, but nothing happens
            return
        s = world[data['entity_id']]
        if service == 'unjoin':
            for m in s['members']:
                world[m]['members'] = [x for x in world[m]['members'] if x != data['entity_id']]
            s['members'] = []
        elif service == 'join':
            s['members'] = sorted(set(s['members']) | set(data['group_members']))
            for m in data['group_members']:
                world[m]['members'] = sorted({data['entity_id'], *s['members']} - {m})
        elif service == 'volume_set':
            s['volume'] = data['volume_level']
        elif service in ('play_media', 'media_play'):
            s['state'] = 'playing'
        elif service == 'media_pause':
            s['state'] = 'paused'
    monkeypatch.setattr(read, 'snapshot', snapshot)
    monkeypatch.setattr(broker, 'act', act)
    return world


def test_a_mood_plays_something_concrete_and_says_what(house):
    found = catalog.play_music('me@example.com', 'kitchen speaker', 'play_media', what='calm piano')
    played = [d for p, d in house['sent'] if p == 'music_assistant/play_media']
    assert played == [{'entity_id': 'media_player.kitchen', 'media_id': 'apple_music://playlist/p1',
                       'media_type': 'playlist', 'enqueue': 'replace'}]     # never the owner's words as a search
    assert 'Calm Piano Evening' in found['picked'] and found['done']


def test_the_owners_own_playlists_come_first(house):
    found = catalog.play_music('me@example.com', 'kitchen speaker', 'play_media', what='calm', kind='playlist')
    assert house['sent'][-1][1]['media_id'] == 'library://playlist/7'
    assert 'Calm Dinner' in found['picked'] and 'your' in found['picked']


def test_nothing_matched_is_said_plainly_and_nothing_plays(house):
    found = catalog.play_music('me@example.com', 'kitchen speaker', 'play_media',
                               what='soft gentle instrumental music for a slow evening of reading aloud')
    assert 'Nothing matched' in found['nothing_matched'] and 'not_confirmed' not in found
    assert house['sent'] == []


def test_a_grouped_speaker_plays_only_where_asked_and_undo_regroups(house):
    found = catalog.play_music('me@example.com', 'living room', 'play_media', what='calm piano', soft=True)
    assert [p for p, _ in house['sent']] == ['media_player/unjoin', 'media_player/volume_set', 'music_assistant/play_media']
    assert house['media_player.kitchen']['state'] == 'idle' and house['media_player.living_room']['volume'] == 0.15
    assert 'on its own' in found['done'][0]
    batch = control.recent()[0]['batch']
    control.undo(batch, login='me@example.com', via='tap')
    assert house['media_player.living_room']['members'] == ['media_player.kitchen']
    assert house['media_player.living_room']['volume'] == 0.14 and house['media_player.living_room']['state'] == 'paused'


def test_a_grouped_speaker_that_is_playing_asks_first(house):
    house['media_player.kitchen']['state'] = 'playing'
    found = catalog.play_music('me@example.com', 'living room', 'play_media', what='calm piano')
    assert 'Kitchen speaker' in found['ask_first'] and house['sent'] == []
    found = catalog.play_music('me@example.com', 'living room', 'play_media', what='calm piano', group_too=True)
    assert [p for p, _ in house['sent']] == ['media_player/volume_set', 'music_assistant/play_media'] and found['done']


def test_soft_lowers_the_volume_even_when_already_playing(house):
    house['media_player.kitchen'].update(state='playing', members=[], volume=0.5)
    house['media_player.living_room']['members'] = []
    catalog.play_music('me@example.com', 'kitchen speaker', 'play_media', what='quiet hours', kind='album', soft=True)
    assert house['media_player.kitchen']['volume'] == 0.15
    catalog.play_music('me@example.com', 'kitchen speaker', 'play_media', what='quiet hours', kind='album', soft=True,
                       volume_percent=8)
    assert house['media_player.kitchen']['volume'] == 0.08


def test_music_that_did_not_start_is_said_plainly(house):
    house['fail'].add('play_media')
    house['media_player.living_room']['members'] = house['media_player.kitchen']['members'] = []
    found = catalog.play_music('me@example.com', 'kitchen speaker', 'play_media', what='calm piano')
    assert found['not_confirmed'] and 'isn’t playing' in found['note']


def test_music_accepted_but_silent_is_not_done_and_says_what_was_sent(house):
    house['stuck'].add('play_media')
    found = catalog.play_music('me@example.com', 'living room', 'play_media', what='calm piano', soft=True)
    assert found['done'] == [] and found['not_confirmed'] == ['Living room on its own playing “Calm Piano Evening” at 15%']
    assert 'Calm Piano Evening' in found['note'] and 'nothing seems to be playing' in found['note']
    assert control.recent()[0]['unknown'] and not control.recent()[0]['done']      # recorded as not confirmed, never done
    assert control.recent()[0]['undo']                                              # the split and volume can still go back


def test_resume_that_does_not_start_is_said_plainly(house):
    house['stuck'].add('media_play')
    found = catalog.play_music('me@example.com', 'kitchen speaker', 'play')
    assert found['not_confirmed'] == ['Kitchen speaker playing'] and 'nothing seems to be playing' in found['note']
    house['stuck'].clear()
    assert catalog.play_music('me@example.com', 'kitchen speaker', 'play')['done'] == ['Kitchen speaker playing']


def test_a_description_is_never_sent_to_music_assistant(house, monkeypatch):
    with pytest.raises(control.Refused, match='never a description'):
        control.change(['media_player.kitchen'], 'play_media', login='me@example.com', via='ask',
                       query='soft gentle instrumental music for reading', kind='playlist')
    with pytest.raises(control.Refused):
        control.change(['media_player.kitchen'], 'play_media', login='me@example.com', via='ask',
                       query='library://album/3', kind='playlist')                   # the address must be of that kind
    monkeypatch.setattr(music, 'ready', lambda: False)
    found = catalog.play_music('me@example.com', 'kitchen speaker', 'play_media', what='calm piano')
    assert 'Nothing was played' in found['unavailable'] and house['sent'] == []


def test_grouping_with_a_silent_speaker_follows_the_one_playing(house):
    _apart(house, 'media_player.kitchen', volume=0.2)
    found = catalog.play_music('me@example.com', 'living room', 'group', with_speakers=['kitchen speaker'])
    assert house['sent'][-1] == ('media_player/join', {'entity_id': 'media_player.kitchen',
                                                       'group_members': ['media_player.living_room']})
    assert found['done'] == ['Living room joined Kitchen speaker at 20%']


def test_grouping_two_rooms_playing_different_music_asks(house):
    _apart(house, 'media_player.kitchen')
    house['media_player.patio'] = {'state': 'playing', 'volume': 0.4, 'members': [], 'name': 'Patio'}
    found = catalog.play_music('me@example.com', 'living room', 'group', with_speakers=['kitchen speaker', 'patio'])
    assert 'More than one' in found['refused'] and house['sent'] == []


# ---- "As well" joins the music already playing; zones play together; playlists only when asked ----

def _apart(house, playing=None, volume=0.15):
    """The two rooms on their own, one of them playing."""
    house['media_player.living_room']['members'] = house['media_player.kitchen']['members'] = []
    if playing:
        house[playing].update(state='playing', volume=volume)


def _zone(house):
    """The owner says the living room and the kitchen are one space, and taps Save."""
    from hub.engine import notes
    proposed = notes.propose('Living room and Kitchen are one space: play music there together',
                             'Remember from now on that the living room and the kitchen are one space, play music in sync',
                             login='me@example.com', zone=['media_player.living_room', 'media_player.kitchen'])
    notes.save(proposed['id'])


def test_as_well_joins_the_playing_room_in_sync_instead_of_a_second_stream(house):
    _apart(house, 'media_player.living_room')
    found = catalog.play_music('me@example.com', 'kitchen speaker', 'play_media', what='Gentle Guitar',
                               said='Could you play it in the kitchen as well')
    assert house['sent'] == [('media_player/volume_set', {'entity_id': 'media_player.kitchen', 'volume_level': 0.15}),
                             ('media_player/join', {'entity_id': 'media_player.living_room',
                                                    'group_members': ['media_player.kitchen']})]    # no second play_media
    assert found['done'] == ['Kitchen speaker joined Living room at 15%'] and 'in sync' in found['joined']
    assert house['media_player.living_room']['members'] == ['media_player.kitchen']
    control.undo(control.recent()[0]['batch'], login='me@example.com', via='tap')
    assert house['sent'][2:] == [('media_player/unjoin', {'entity_id': 'media_player.kitchen'}),
                                 ('media_player/volume_set', {'entity_id': 'media_player.kitchen', 'volume_level': 0.3})]
    assert house['media_player.living_room']['state'] == 'playing'          # Undo leaves the first room playing


def test_as_well_with_a_second_stream_already_playing_joins_and_keeps_its_volume(house):
    _apart(house, 'media_player.living_room')
    house['media_player.kitchen'].update(state='playing', volume=0.2)
    found = catalog.play_music('me@example.com', 'kitchen speaker', 'play_media', what='calm piano', said='play that there too')
    assert [p for p, _ in house['sent']] == ['media_player/join'] and found['done'] == ['Kitchen speaker joined Living room']


def test_as_well_with_two_rooms_playing_asks_which(house):
    _apart(house, 'media_player.living_room')
    house['media_player.patio'] = {'state': 'playing', 'volume': 0.4, 'members': [], 'name': 'Patio'}
    found = catalog.play_music('me@example.com', 'kitchen speaker', 'play_media', what='calm piano', said='in the kitchen too')
    assert 'Living room' in found['ask_first'] and 'Patio' in found['ask_first'] and house['sent'] == []


def test_as_well_with_nothing_playing_just_plays_there(house):
    _apart(house)
    found = catalog.play_music('me@example.com', 'kitchen speaker', 'play_media', what='calm piano', said='play calm piano too')
    assert 'music_assistant/play_media' in [p for p, _ in house['sent']] and 'joined' not in found


def test_a_zone_plays_together_softly_and_one_undo_puts_it_all_back(house):
    _apart(house)
    _zone(house)
    found = catalog.play_music('me@example.com', 'living room', 'play_media', what='calm piano', soft=True,
                               said='play some soft music in the living room')
    assert house['sent'] == [
        ('media_player/volume_set', {'entity_id': 'media_player.living_room', 'volume_level': 0.15}),
        ('music_assistant/play_media', {'entity_id': 'media_player.living_room', 'media_id': 'apple_music://playlist/p1',
                                        'media_type': 'playlist', 'enqueue': 'replace'}),
        ('media_player/volume_set', {'entity_id': 'media_player.kitchen', 'volume_level': 0.15}),
        ('media_player/join', {'entity_id': 'media_player.living_room', 'group_members': ['media_player.kitchen']})]
    assert 'Kitchen speaker' in found['zone'] and len(found['done']) == 2
    [batch] = {r['batch'] for r in control.recent()[:1]}
    control.undo(batch, login='me@example.com', via='tap')
    assert house['media_player.living_room']['state'] == 'paused' and house['media_player.living_room']['volume'] == 0.14
    assert house['media_player.living_room']['members'] == [] and house['media_player.kitchen']['volume'] == 0.3


def test_a_zone_already_grouped_plays_in_its_group_without_splitting(house):
    _zone(house)                       # the fixture's two speakers are grouped already
    found = catalog.play_music('me@example.com', 'living room', 'play_media', what='calm piano', soft=True)
    assert [p for p, _ in house['sent']] == ['media_player/volume_set', 'music_assistant/play_media', 'media_player/volume_set']
    assert house['media_player.kitchen']['volume'] == 0.15 and 'grouping' not in found
    control.undo(control.recent()[0]['batch'], login='me@example.com', via='tap')
    assert house['media_player.kitchen']['volume'] == 0.3 and house['media_player.living_room']['members'] == ['media_player.kitchen']


def test_only_here_keeps_a_zone_room_on_its_own(house):
    _apart(house)
    _zone(house)
    found = catalog.play_music('me@example.com', 'living room', 'play_media', what='calm piano', group_too=False)
    assert 'media_player/join' not in [p for p, _ in house['sent']] and 'zone' not in found
    assert house['media_player.kitchen']['state'] == 'idle'


def test_a_zone_never_brings_in_rooms_outside_it(house):
    _zone(house)
    house['media_player.living_room']['members'] = ['media_player.patio']
    house['media_player.kitchen']['members'] = []
    house['media_player.patio'] = {'state': 'idle', 'volume': 0.6, 'members': ['media_player.living_room'], 'name': 'Patio'}
    catalog.play_music('me@example.com', 'living room', 'play_media', what='calm piano')
    assert not [d for _, d in house['sent'] if 'media_player.patio' in (d.get('entity_id'), *d.get('group_members', ()))]
    assert house['sent'][0] == ('media_player/unjoin', {'entity_id': 'media_player.living_room'})   # out of the patio's group
    assert house['media_player.living_room']['members'] == ['media_player.kitchen']
    house['sent'].clear()
    house['media_player.patio'].update(state='playing', members=['media_player.living_room'])
    house['media_player.living_room'].update(members=['media_player.patio'], state='playing')
    assert 'Patio' in catalog.play_music('me@example.com', 'living room', 'play_media', what='calm piano')['ask_first']
    assert house['sent'] == []


def test_removing_the_note_removes_the_zone_and_a_household_setting_makes_one(house):
    from hub.tools.house import zones
    _zone(house)
    speakers = {e: {'name': s['name']} for e, s in house.items() if e.startswith('media_player.')}
    assert zones.mates('media_player.kitchen', speakers) == ['media_player.living_room']
    state.remove_instruction(state.instructions()[0]['id'])
    assert zones.mates('media_player.kitchen', speakers) == []
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'home': {
        'music_url': 'https://music.example:8095', 'speaker_zones': [['Living room', 'media_player.kitchen'], ['nowhere']]}}))
    assert zones.mates('media_player.kitchen', speakers) == ['media_player.living_room']


def test_a_sentence_never_becomes_a_playlist(ma):
    said = ('You should remember from now on that the kitchen and the living room are in the same great room, '
            'so when you play music, you should play it sync')
    found = catalog.edit_playlist('me@example.com', 'create', said, said=said)
    assert 'doesn’t ask to create a playlist' in found['refused']
    found = catalog.edit_playlist('me@example.com', 'create', said, said='make a playlist ' + said)
    assert 'reads like a sentence' in found['refused']
    assert 'reads like a sentence' in catalog.edit_playlist('me@example.com', 'rename', 'calm dinner',
                                                            new_name='Please remember to play it in sync',
                                                            said='rename the calm dinner playlist')['refused']
    for action in ('rename', 'delete'):
        assert 'doesn’t ask' in catalog.edit_playlist('me@example.com', action, 'calm dinner', new_name='Quiet',
                                                      said='play calm dinner in the kitchen')['refused']
    assert music.proposals() == [] and len(ma.lists) == 3 and music.recent_changes() == []


def test_making_a_playlist_waits_for_a_signed_in_tap(ma):
    before = dict(ma.lists)
    found = catalog.edit_playlist('me@example.com', 'create', 'Sunday', songs=['dinner waltz'],
                                  said='Make a playlist called Sunday with Dinner Waltz')
    assert 'Nothing is made yet' in found['needs_tap'] and ma.lists == before
    client = TestClient(app)
    page = client.get('/house/playlists', headers=SIGNED_IN).text
    [waiting] = music.proposals()
    assert 'Make “Sunday”?' in page and f'/house/playlists/make/{waiting["id"]}' in page
    assert client.post(f'/house/playlists/make/{waiting["id"]}', headers={'sec-fetch-site': 'same-origin'}).status_code == 403
    assert ma.lists == before
    reply = client.post(f'/house/playlists/make/{waiting["id"]}', headers=SIGNED_IN, follow_redirects=False)
    assert reply.status_code == 303 and music.proposals() == []
    new = next(pid for pid, p in ma.lists.items() if p['name'] == 'Sunday')
    assert names(ma, new) == ['Dinner Waltz'] and music.recent_changes()[0]['via'] == 'tap'
    again = client.post(f'/house/playlists/make/{waiting["id"]}', headers=SIGNED_IN)
    assert 'isn’t waiting any more' in again.text and len(ma.lists) == len(before) + 1
    catalog.edit_playlist('me@example.com', 'create', 'Later', said='make a playlist called Later')
    client.post(f'/house/playlists/skip/{music.proposals()[0]["id"]}', headers=SIGNED_IN)
    assert music.proposals() == []
