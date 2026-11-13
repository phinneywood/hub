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
        self.asked, self.next_id, self.down, self.lag = [], 20, set(), False
        self.tokens = [{'name': 'Hub', 'expires_at': '2027-11-12T00:00:00+00:00'}]

    def _item(self, pid):
        p = self.lists[pid]
        return {'item_id': pid, 'name': p['name'], 'is_editable': p['editable'], 'uri': f'library://playlist/{pid}',
                'provider_mappings': [{'provider_domain': p['domain'], 'item_id': f'x{pid}'}]}

    def _song(self, uri, position=None):
        name, artist = SONGS[uri]
        return {'name': name, 'artists': [{'name': artist}], 'uri': uri, **({'position': position} if position else {})}

    def __call__(self, command, args, timeout):
        self.asked.append((command, args))
        if command in self.down:
            raise music.NotSent('Music Assistant isn’t answering.')
        if command == 'music/playlists/library_items':
            wanted = args.get('search', '').lower()
            return [self._item(pid) for pid, p in self.lists.items() if wanted in p['name'].lower()]
        if command == 'music/playlists/playlist_tracks':
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
            if not self.lag:
                p['uris'] += [u for u in args['uris'] if u not in p['uris']]
            return {'id': 'task'}
        if command == 'music/playlists/remove_playlist_tracks':
            p = self.lists[args['db_playlist_id']]
            gone = set(args['positions_to_remove'])
            p['uris'] = [u for n, u in enumerate(p['uris'], 1) if n not in gone]
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
    found = catalog.edit_playlist('me@example.com', 'create', 'Sunday', songs=['dinner waltz', 'no such song'])
    assert found['done'] == ['Made playlist “Sunday” with 1 songs'] and found['songs_not_found'] == ['no such song']
    new = next(pid for pid, p in ma.lists.items() if p['name'] == 'Sunday')
    assert names(ma, new) == ['Dinner Waltz']
    assert 'already a playlist' in catalog.edit_playlist('me@example.com', 'create', 'sunday')['refused']
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
    found = catalog.edit_playlist('me@example.com', 'rename', 'calm dinner', new_name='Quiet Dinner')
    assert found['done'] == ['Renamed “Calm Dinner” to “Quiet Dinner”'] and '7' not in ma.lists
    new = next(pid for pid, p in ma.lists.items() if p['name'] == 'Quiet Dinner')
    assert names(ma, new) == ['Morning Light', 'Slow River', 'Quiet Hours']
    music.undo(music.recent_changes()[0]['batch'], login='me@example.com', via='tap')
    assert [p['name'] for p in ma.lists.values()].count('Calm Dinner') == 1 and new not in ma.lists


def test_a_copy_that_doesnt_fill_keeps_both_and_is_not_confirmed(ma):
    ma.lag = True
    found = catalog.edit_playlist('me@example.com', 'rename', 'calm dinner', new_name='Quiet Dinner')
    assert found['not_confirmed'] == ['Renamed “Calm Dinner” to “Quiet Dinner”'] and 'Both playlists' in found['note']
    assert '7' in ma.lists and found['done'] == []


def test_apple_playlists_only_gain_songs(ma):
    found = catalog.edit_playlist('me@example.com', 'add', 'road trip', songs=['quiet hours'])
    assert found['done'] == ['Added 1 song to “Road Trip”'] and 'undo' not in found and 'Music app' in found['note']
    assert not music.recent_changes()[0]['undo']
    for action, extra in (('remove', {'songs': ['morning light']}), ('rename', {'new_name': 'X'}), ('delete', {}),
                          ('move', {'songs': ['morning light'], 'position': 1})):
        assert 'Apple Music playlist' in catalog.edit_playlist('me@example.com', action, 'road trip', **extra)['refused']
    assert 'can’t be edited' in catalog.edit_playlist('me@example.com', 'add', 'top hits', songs=['quiet hours'])['refused']


def test_ask_never_deletes_and_needs_a_signed_in_owner_and_trust(ma):
    found = catalog.edit_playlist('me@example.com', 'delete', 'calm dinner')
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
