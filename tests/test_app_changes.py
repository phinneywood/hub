"""The Changes screen in the app (hub/app/changes.py): house and playlist changes with Undo, and playlists waiting.
Synthetic data only; Home Assistant and Music Assistant are never reached."""
from fastapi.testclient import TestClient

from hub import config
from hub.app import changes
from hub.main import app
from hub.tools.house import control, music

client = TestClient(app)
OWNER = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}


def test_the_screen_lists_house_and_playlist_changes_newest_first(monkeypatch):
    monkeypatch.setattr(control, 'recent', lambda: [
        {'batch': 'h1', 'done': ['Kitchen on'], 'unknown': [], 'not_sent': [], 'undo': True, 'undone': False, 'ts': 100, 'via': 'app'}])
    monkeypatch.setattr(music, 'recent_changes', lambda: [
        {'batch': 'm1', 'done': ['Added a song to Road trip'], 'unknown': [], 'not_sent': [], 'undo': True, 'undone': False,
         'ts': 200, 'via': 'ask'}])
    monkeypatch.setattr(music, 'proposals', lambda: [{'id': 'p1', 'name': 'Sunday', 'songs': ['A song'], 'status': 'waiting'}])
    seen = client.get('/app/api/changes').json()
    assert [(c['batch'], c['where'], c['via']) for c in seen['changes']] == [('m1', 'music', 'from Ask'), ('h1', 'house', 'from the app')]
    assert seen['waiting'] == [{'id': 'p1', 'name': 'Sunday', 'songs': ['A song']}]


def test_playlist_taps_need_a_signed_in_device_and_say_when_they_cant(monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com'})
    settled = []
    monkeypatch.setattr(music, 'settle', lambda pid, status: settled.append((pid, status)))
    monkeypatch.setattr(music, 'proposal', lambda pid: None)
    assert client.post('/app/api/playlist/skip', json={'key': 'p1'}, headers={'origin': 'http://testserver'}).status_code == 403
    assert client.post('/app/api/playlist/skip', json={'key': 'p1'}, headers=OWNER).json()['ok'] and settled == [('p1', 'skipped')]
    gone = client.post('/app/api/playlist/make', json={'key': 'p1'}, headers=OWNER)
    assert gone.status_code == 409 and 'isn’t waiting' in gone.json()['error']

    def down(batch, *, login, via):
        raise music.MusicDown('Music Assistant isn’t answering.')
    monkeypatch.setattr(music, 'undo', down)
    assert client.post('/app/api/playlist/undo', json={'key': 'm1'}, headers=OWNER).status_code == 409
    assert client.post('/app/api/playlist/nonsense', json={}, headers=OWNER).status_code == 409
    assert changes.VIA['app'] == 'from the app'
