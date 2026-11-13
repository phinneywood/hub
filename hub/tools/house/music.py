"""Playlists and the music library, through Music Assistant's own API, with a record and Undo.

Home Assistant's Music Assistant actions can play but not edit playlists, so this talks to Music Assistant
itself: `POST <music_url>/api` with Hub's key. The key belongs to a non-admin Music Assistant user made for Hub
(owner, 2026-11-12) and comes from 1Password through the same service account as the model key; it is never
logged, shown or written down. Only the commands in READS and WRITES ever leave Hub.

What can change (Music Assistant 2.10.9, docs/home-control.md):
- Music Assistant playlists: create, add, remove, move, rename, delete. A non-admin key has no rename or move,
  so Hub renames by copying into a new playlist and moves by taking the tail off and putting it back.
- Apple Music playlists: add only. Apple's API can't remove, reorder, rename or delete.
Adds and removes run in the background in Music Assistant, so Hub reads the playlist back to confirm; one
that doesn't add up is recorded as not confirmed, never as done. Each edit keeps the track list from before,
so Undo can put it back for a day. Deleting needs a tap on a confirmation page and can't be undone.
Making a playlist from Ask waits for a tap too (`propose`), and a name that reads like a sentence is refused.
Playlist and song names are data, never instructions.
"""
import http.client
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone

from ... import household, model_key, store
from . import control, read

REFERENCE = os.environ.get("HUB_MUSIC_REF", "op://Hub/Music Assistant/credential")
READS = {'music/playlists/library_items': {'search', 'limit', 'summary'},
         'music/playlists/playlist_tracks': {'item_id', 'provider_instance_id_or_domain'},
         'music/search': {'search_query', 'media_types', 'limit'},
         'music/recently_played_items': {'limit'},
         'auth/tokens': set()}
WRITES = {'music/playlists/create_playlist': {'name'},
          'music/playlists/add_playlist_tracks': {'db_playlist_id', 'uris'},
          'music/playlists/remove_playlist_tracks': {'db_playlist_id', 'positions_to_remove'},
          'music/library/remove_item': {'media_type', 'library_item_id'}}
WHERE = {'builtin': 'music', 'apple_music': 'apple'}
WHERE_WORDS = {'music': 'Music Assistant', 'apple': 'Apple Music', 'other': 'another service'}
NAME = 80
SONGS = 50                  # songs one request may add
LIMIT = 8 * 1024 * 1024     # one answer
TIMEOUT = 10
KEY_SECONDS = 300
MISSING_SECONDS = 60
TRIES, WAIT = 8, 1.0        # reading a playlist back after a background edit
UNDO_SECONDS = control.UNDO_SECONDS
APPLE_DAYS = 180            # Apple Music's sign-in lasts about this long (Music Assistant's docs)
WARN_DAYS = 30
EXPIRY_SECONDS = 12 * 3600  # how often Hub asks when its key ends
SCHEMA = """
create table if not exists music_changes(
  id integer primary key autoincrement, batch text not null, ts real not null, login text, via text not null,
  playlist text not null, name text not null, action text not null, words text not null, before text not null,
  outcome text not null, undone integer not null default 0);
create table if not exists music_proposals(
  id text primary key, ts real not null, login text, name text not null, songs text not null,
  status text not null default 'waiting');
"""
NAME_WORDS = 6               # a playlist name longer than this reads like a sentence
SENTENCE = re.compile(r"[.?;]$|\b(remember|from now on|you should|should you|please|could you|can you|would you|"
                      r"i want|i'd like|make sure|keep in mind|when you|so that)\b", re.I)
PROPOSAL_SECONDS = 24 * 3600

_lock = threading.Lock()
_key = {'value': None, 'at': float('-inf')}
_expiry = {'value': None, 'at': float('-inf')}


class MusicDown(Exception):
    """Music Assistant couldn't be reached or didn't answer. The message is safe to show; an edit may have happened."""


class NotSent(MusicDown):
    """Nothing was sent or Music Assistant refused it: a change certainly didn't happen."""


Refused = control.Refused


# ---- The API ----

def _secret():
    with _lock:
        if time.monotonic() - _key['at'] < (KEY_SECONDS if _key['value'] else MISSING_SECONDS):
            return _key['value']        # a missing key is remembered briefly too, so pages don't wait on 1Password
    value = model_key.secret(REFERENCE)
    with _lock:
        _key.update(value=value, at=time.monotonic())
    return value


def ready():
    """Whether Music Assistant's address is set. Says nothing about the key."""
    return bool(read.links().get('music_url'))


def _send(command, args, timeout):
    base = read.links().get('music_url')
    if not base:
        raise NotSent('Music Assistant’s address isn’t in Hub’s settings yet.')
    key = _secret()
    if not key:
        raise NotSent('Hub has no Music Assistant key yet.')
    body = json.dumps({'message_id': uuid.uuid4().hex[:8], 'command': command, 'args': args}).encode()
    request = urllib.request.Request(base.rstrip('/') + '/api', data=body, method='POST',
                                     headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            raw = reply.read(LIMIT + 1)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise NotSent('Music Assistant didn’t accept Hub’s key.') from None
        if error.code == 400:
            raise NotSent('Music Assistant refused that.') from None
        raise MusicDown('Music Assistant had a problem with that.') from None
    except http.client.HTTPException:
        raise MusicDown('Music Assistant’s answer was cut off.') from None
    except (urllib.error.URLError, OSError) as error:
        if isinstance(getattr(error, 'reason', error), (ConnectionRefusedError, socket.gaierror)):
            raise NotSent('Music Assistant isn’t answering.') from None   # never connected, so nothing was sent
        raise MusicDown('Music Assistant didn’t answer in time.') from None
    if len(raw) > LIMIT:
        raise MusicDown('Music Assistant gave an answer Hub couldn’t read.')
    try:
        return json.loads(raw) if raw.strip() else None
    except ValueError:
        raise MusicDown('Music Assistant gave an answer Hub couldn’t read.') from None


def _call(command, args=None, timeout=TIMEOUT):
    """One allowed command. NotSent: certainly not done. MusicDown: outcome unknown."""
    args = args or {}
    allowed = READS.get(command, WRITES.get(command))
    if allowed is None or set(args) - allowed:
        raise ValueError('Hub may not do that in Music Assistant.')
    return _send(command, args, timeout)


# ---- Reading ----

def _clip(text, size=NAME):
    return ' '.join(str(text or '').split())[:size]


def _artist(item):
    names = [_clip(a.get('name')) for a in item.get('artists') or () if isinstance(a, dict) and a.get('name')]
    return ', '.join(names[:3])


def _playlist(item):
    domains = [m.get('provider_domain') for m in item.get('provider_mappings') or () if isinstance(m, dict)]
    where = next((WHERE[d] for d in domains if d in WHERE), 'other')
    return {'id': str(item.get('item_id')), 'name': _clip(item.get('name')), 'where': where,
            'editable': bool(item.get('is_editable')) or where == 'music', 'uri': str(item.get('uri') or '')}


def playlists(search=None):
    """Playlists in the library: Music Assistant's own and Apple Music's."""
    args = {'limit': 200, 'summary': False}
    if search:
        args['search'] = _clip(search)
    found = _call('music/playlists/library_items', args)
    return [_playlist(p) for p in found or () if isinstance(p, dict) and p.get('item_id') is not None]


def _song(item, position=None):
    return {'pos': position, 'name': _clip(item.get('name')), 'artist': _artist(item), 'uri': str(item.get('uri') or '')}


def tracks(playlist_id):
    """A library playlist's songs, in order, each with its 1-based position."""
    found = _call('music/playlists/playlist_tracks', {'item_id': str(playlist_id), 'provider_instance_id_or_domain': 'library'})
    songs = [_song(t, t.get('position')) for t in found or () if isinstance(t, dict)]
    return sorted(songs, key=lambda s: s['pos'] if isinstance(s['pos'], int) else 0)


def search(query, kinds=('track',), limit=5):
    """Search results by kind: tracks, albums, artists, playlists."""
    found = _call('music/search', {'search_query': _clip(query, 200), 'media_types': list(kinds), 'limit': limit})
    out = {}
    for kind in kinds:
        items = (found or {}).get(f'{kind}s') or (found or {}).get(kind) or ()     # radio has no plural
        out[kind] = [_song(i) for i in items if isinstance(i, dict)][:limit]
    return out


def recent(limit=10):
    found = _call('music/recently_played_items', {'limit': limit})
    return [{'name': _clip(i.get('name')), 'kind': _clip(i.get('media_type'), 20)} for i in found or () if isinstance(i, dict)]


def find(name, pool=None):
    """The library playlist the owner named. Raises Refused with the choices when it isn't clear."""
    pool = pool if pool is not None else playlists()
    wanted = _clip(name).lower()
    exact = [p for p in pool if p['name'].lower() == wanted]
    near = exact or [p for p in pool if wanted and wanted in p['name'].lower()]
    if len(near) == 1:
        return near[0]
    names = [p['name'] for p in near or pool]
    raise Refused(f'Which playlist? {", ".join(names[:12])}' if names else 'There are no playlists in the library yet.')


PLAY_ORDER = ('playlist', 'album', 'artist', 'track')


def _mine(uri):
    return uri.startswith('library://')


def pick(what, kind=None):
    """Something concrete to play for the owner's words, never the words themselves: Music Assistant can't play
    a description. The owner's own playlists come first (an exact name, then one whose name has the words), then
    a search: the asked kind only, or playlists, albums, artists and songs in that order, the owner's library
    before Apple Music's catalog. {'uri', 'name', 'kind', 'mine'}. Refused when nothing matched; MusicDown passes."""
    words = _clip(what, 200)
    if not words:
        raise Refused('Say what to play.')
    if kind in (None, 'playlist'):
        own = [p for p in playlists(words) if p['uri']]
        exact = [p for p in own if p['name'].lower() == words.lower()]
        if own:
            chosen = (exact or own)[0]
            return {'uri': chosen['uri'], 'name': chosen['name'], 'kind': 'playlist', 'mine': True}
    kinds = (kind,) if kind else PLAY_ORDER
    found = search(words, kinds, limit=5)
    for k in kinds:
        hits = sorted((h for h in found.get(k, ()) if h['uri']), key=lambda h: not _mine(h['uri']))
        if hits:
            h = hits[0]
            return {'uri': h['uri'], 'name': f'{h["name"]} – {h["artist"]}' if h['artist'] and k != 'playlist' else h['name'],
                    'kind': k, 'mine': _mine(h['uri'])}
    raise Refused(f'Nothing matched “{words}” in the library or Apple Music. Try a few words: a genre, a mood, '
                  'an artist or a playlist name.')


def playlist_uri(name):
    """The library address of a playlist named exactly this, so it plays that one and not a search hit. Fail-soft."""
    if not ready():
        return None
    try:
        wanted = _clip(name).lower()
        return next((p['uri'] for p in playlists(name) if p['name'].lower() == wanted and p['uri']), None)
    except (MusicDown, ValueError):
        return None


def _songs(phrases):
    """(found songs, phrases with no match). Each phrase is a song's title, ideally with its artist."""
    phrases = [_clip(p, 200) for p in phrases or () if _clip(p, 200)]
    if not phrases:
        raise Refused('Say which songs.')
    if len(phrases) > SONGS:
        raise Refused(f'At most {SONGS} songs at a time.')
    found, missing = [], []
    for phrase in phrases:
        query = ' '.join(w for w in phrase.replace('–', ' ').replace('—', ' ').split() if w != '-')
        hits = [s for s in search(query)['track'] if s['uri']]
        (found.append(hits[0]) if hits else missing.append(phrase))
    return found, missing


# ---- Changing ----

def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _record(batch, login, via, playlist_id, name, action, words, before, outcome):
    with _db() as conn:
        conn.execute('insert into music_changes(batch, ts, login, via, playlist, name, action, words, before, outcome) '
                     'values (?,?,?,?,?,?,?,?,?,?)', (batch, time.time(), login, via, str(playlist_id), name, action, words,
                                                      json.dumps(before), outcome))


def _settle(playlist_id, want):
    """'done' once the playlist matches `want`, else 'unknown'. `want` is a song count, or the exact list of
    song addresses in order when the count alone wouldn't change (a move, or putting a list back)."""
    for n in range(TRIES):
        try:
            now = tracks(playlist_id)
            if (len(now) == want) if isinstance(want, int) else [s['uri'] for s in now] == list(want):
                return 'done'
        except MusicDown:
            pass
        if n < TRIES - 1:
            time.sleep(WAIT)
    return 'unknown'


def _steps(steps):
    """Run (command, args) pairs in order: 'sent', 'not_sent' (certainly nothing happened) or 'unknown'."""
    for n, (command, args) in enumerate(steps):
        try:
            _call(command, args)
        except NotSent:
            return 'not_sent' if n == 0 else 'unknown'
        except MusicDown:
            return 'unknown'
    return 'sent'


def _add(playlist_id, uris):
    return ('music/playlists/add_playlist_tracks', {'db_playlist_id': str(playlist_id), 'uris': list(uris)})


def _remove(playlist_id, positions):
    return ('music/playlists/remove_playlist_tracks', {'db_playlist_id': str(playlist_id), 'positions_to_remove': list(positions)})


def _delete(playlist_id):
    return ('music/library/remove_item', {'media_type': 'playlist', 'library_item_id': str(playlist_id)})


def _new(name):
    """Create an empty Music Assistant playlist. Returns its id. NotSent/MusicDown pass through."""
    made = _call('music/playlists/create_playlist', {'name': name})
    if not isinstance(made, dict) or made.get('item_id') is None:
        raise MusicDown('Music Assistant didn’t say whether it made the playlist.')
    return str(made['item_id'])


def _staged(playlist_id, stages):
    """Run (step, want) stages in order, each read back before the next is sent: Music Assistant edits in the
    background, and an add skips songs that a remove still waiting to run hasn't taken out. Stops at the first
    stage that isn't confirmed. 'done', 'not_sent' (certainly nothing happened) or 'unknown'."""
    for n, (step, want) in enumerate(stages):
        sent = _steps([step])
        if sent != 'sent':
            return sent if n == 0 else 'unknown'
        if _settle(playlist_id, want) != 'done':
            return 'unknown'
    return 'done'


def _report(batch, words, outcome, undo, **extra):
    out = {'batch': batch, 'done': [], 'unknown': [], 'not_sent': [], 'undo': undo and outcome != 'not_sent', **extra}
    out[{'done': 'done', 'unknown': 'unknown'}.get(outcome, 'not_sent')].append(words)
    return out


def check_editable(playlist, *, apple=False):
    if playlist['where'] == 'apple' and not apple:
        raise Refused(f'“{playlist["name"]}” is an Apple Music playlist. Hub can only add songs to those; '
                      'remove, reorder, rename or delete it in the Music app.')
    if playlist['where'] == 'other' or not playlist['editable']:
        raise Refused(f'“{playlist["name"]}” can’t be edited. Only playlists you made can be changed.')


def _unique(uris):
    return list(dict.fromkeys(uris))


def _no_repeats(name, uris):
    """Music Assistant skips a song that is already in a playlist, so a list with a song in it twice can't be
    rebuilt without losing a copy. Hub leaves those alone."""
    if len(set(uris)) != len(uris):
        raise Refused(f'“{name}” has a song in it twice, so Hub can’t rebuild it without losing a copy. '
                      'Change it in Music Assistant.')


def check_name(name):
    """A playlist name, or Refused when it is empty or reads like a sentence or an instruction rather than a name."""
    name = _clip(name)
    if not name:
        raise Refused('Say what to call the playlist.')
    if len(name.split()) > NAME_WORDS or SENTENCE.search(name):
        raise Refused('That reads like a sentence, not a playlist name, so Hub made no playlist. Ask the owner for a short name.')
    return name


def propose(name, songs, *, login):
    """Keep a playlist the owner asked Ask to make, for their tap on the Playlists page. Returns its id."""
    name = check_name(name)
    phrases = [_clip(p, 200) for p in songs or () if _clip(p, 200)]
    if len(phrases) > SONGS:
        raise Refused(f'At most {SONGS} songs at a time.')
    if any(p['name'].lower() == name.lower() for p in playlists(name)):
        raise Refused(f'There is already a playlist called “{name}”.')
    proposal_id = uuid.uuid4().hex[:12]
    with _db() as conn:
        conn.execute('insert into music_proposals(id, ts, login, name, songs) values (?,?,?,?,?)',
                     (proposal_id, time.time(), login, name, json.dumps(phrases)))
    return proposal_id


def proposals():
    """Playlists waiting for the owner's tap, newest first."""
    with _db() as conn:
        rows = conn.execute("select * from music_proposals where status = 'waiting' and ts > ? order by ts desc",
                            (time.time() - PROPOSAL_SECONDS,)).fetchall()
    return [{**dict(r), 'songs': json.loads(r['songs'])} for r in rows]


def proposal(proposal_id):
    return next((p for p in proposals() if p['id'] == str(proposal_id)[:12]), None)


def settle(proposal_id, status):
    with _db() as conn:
        conn.execute("update music_proposals set status = ? where id = ? and status = 'waiting'", (status, str(proposal_id)[:12]))


def create(name, songs, *, login, via):
    """A new Music Assistant playlist, with these songs. Undo deletes it."""
    name = check_name(name)
    found, missing = _songs(songs) if songs else ([], [])
    if any(p['name'].lower() == name.lower() for p in playlists(name)):
        raise Refused(f'There is already a playlist called “{name}”.')
    control._spend(1)
    batch, words = uuid.uuid4().hex[:12], f'Made playlist “{name}”' + (f' with {len(found)} songs' if found else '')
    try:
        playlist_id = _new(name)
    except NotSent:
        _record(batch, login, via, '', name, 'create', words, {}, 'not_sent')
        return _report(batch, words, 'not_sent', False, missing=missing)
    except MusicDown:
        _record(batch, login, via, '', name, 'create', words, {}, 'unknown')
        return _report(batch, words, 'unknown', False, missing=missing)
    uris = _unique(s['uri'] for s in found)
    outcome = _staged(playlist_id, [(_add(playlist_id, uris), len(uris))]) if uris else 'done'
    if outcome == 'not_sent':
        outcome = 'unknown'      # the playlist exists; its songs didn't go in
    _record(batch, login, via, playlist_id, name, 'create', words, {'name': name, 'uris': []}, outcome)
    return _report(batch, words, outcome, True, missing=missing)


def add(playlist_name, songs, *, login, via):
    """Add songs to the end of a playlist. Apple Music playlists too, but those can't be undone from Hub."""
    playlist = find(playlist_name)
    check_editable(playlist, apple=True)
    found, missing = _songs(songs)
    before = tracks(playlist['id'])
    have = {s['uri'] for s in before}
    uris = [u for u in _unique(s['uri'] for s in found) if u not in have]
    if not uris:
        raise Refused(f'Those songs are already in “{playlist["name"]}”.' if found else 'Hub found none of those songs.')
    control._spend(1)
    batch, words = uuid.uuid4().hex[:12], f'Added {len(uris)} song{"s" * (len(uris) != 1)} to “{playlist["name"]}”'
    outcome = _staged(playlist['id'], [(_add(playlist['id'], uris), len(before) + len(uris))])
    undoable = playlist['where'] == 'music'
    _record(batch, login, via, playlist['id'], playlist['name'], 'add', words,
            {'name': playlist['name'], 'uris': [s['uri'] for s in before], 'where': playlist['where']}, outcome)
    extra = {} if undoable else {'note': 'Apple Music can’t take songs out by API; remove them in the Music app.'}
    return _report(batch, words, outcome, undoable, missing=missing, **extra)


def _match(before, phrase):
    wanted = _clip(phrase, 200).lower()
    for s in before:
        if wanted in (s['name'].lower(), f'{s["name"]} {s["artist"]}'.lower(), f'{s["name"]} - {s["artist"]}'.lower(),
                      f'{s["name"]} – {s["artist"]}'.lower(), f'{s["name"]} by {s["artist"]}'.lower()):
            return s
    near = [s for s in before if wanted and wanted in s['name'].lower()]
    return near[0] if len(near) == 1 else None


def remove(playlist_name, songs, *, login, via):
    """Take songs out of a Music Assistant playlist."""
    playlist = find(playlist_name)
    check_editable(playlist)
    before = tracks(playlist['id'])
    chosen, missing = {}, []
    for phrase in songs or ():
        hit = _match(before, phrase)
        if hit and isinstance(hit['pos'], int):
            chosen[hit['pos']] = hit
        else:
            missing.append(_clip(phrase))
    if not chosen:
        raise Refused(f'None of those songs is in “{playlist["name"]}”.')
    control._spend(1)
    batch = uuid.uuid4().hex[:12]
    words = f'Took {len(chosen)} song{"s" * (len(chosen) != 1)} out of “{playlist["name"]}”'
    outcome = _staged(playlist['id'], [(_remove(playlist['id'], sorted(chosen)), len(before) - len(chosen))])
    _record(batch, login, via, playlist['id'], playlist['name'], 'remove', words,
            {'name': playlist['name'], 'uris': [s['uri'] for s in before], 'where': 'music'}, outcome)
    return _report(batch, words, outcome, True, missing=missing)


def move(playlist_name, song, position, *, login, via):
    """Move one song to a new position (1 = first) in a Music Assistant playlist."""
    playlist = find(playlist_name)
    check_editable(playlist)
    before = tracks(playlist['id'])
    hit = _match(before, song)
    if not hit:
        raise Refused(f'That song isn’t in “{playlist["name"]}”.')
    if not isinstance(position, int) or not before:
        raise Refused('Say where to move it, as a number from 1.')
    i, j = before.index(hit), max(1, min(len(before), position)) - 1
    if i == j:
        raise Refused(f'“{hit["name"]}” is already there.')
    _no_repeats(playlist['name'], [s['uri'] for s in before])
    order = [s['uri'] for s in before if s is not hit]
    order.insert(j, hit['uri'])
    k = min(i, j)
    control._spend(1)
    batch, words = uuid.uuid4().hex[:12], f'Moved “{hit["name"]}” to number {j + 1} in “{playlist["name"]}”'
    outcome = _staged(playlist['id'], [(_remove(playlist['id'], range(k + 1, len(before) + 1)), order[:k]),
                                       (_add(playlist['id'], order[k:]), order)])
    _record(batch, login, via, playlist['id'], playlist['name'], 'move', words,
            {'name': playlist['name'], 'uris': [s['uri'] for s in before], 'where': 'music'}, outcome)
    return _report(batch, words, outcome, True)


def _copy(playlist_id, name, uris):
    """Copy into a new playlist called `name`, then delete the old one once the copy is whole.
    (new id or None, outcome)."""
    try:
        new_id = _new(name)
    except NotSent:
        return None, 'not_sent'
    except MusicDown:
        return None, 'unknown'
    outcome = _staged(new_id, [(_add(new_id, uris), list(uris))]) if uris else 'done'
    if outcome != 'done':
        return new_id, 'unknown'   # both stay, so nothing is lost
    return new_id, 'done' if _steps([_delete(playlist_id)]) == 'sent' else 'unknown'


def rename(playlist_name, new_name, *, login, via):
    """Rename a Music Assistant playlist. Hub copies it to the new name and removes the old one."""
    playlist = find(playlist_name)
    check_editable(playlist)
    new_name = check_name(new_name)
    if new_name.lower() == playlist['name'].lower():
        raise Refused('Say the new name.')
    if any(p['name'].lower() == new_name.lower() for p in playlists(new_name)):
        raise Refused(f'There is already a playlist called “{new_name}”.')
    uris = [s['uri'] for s in tracks(playlist['id'])]
    _no_repeats(playlist['name'], uris)
    control._spend(1)
    batch, words = uuid.uuid4().hex[:12], f'Renamed “{playlist["name"]}” to “{new_name}”'
    new_id, outcome = _copy(playlist['id'], new_name, uris)
    _record(batch, login, via, new_id or playlist['id'], new_name, 'rename', words,
            {'name': playlist['name'], 'uris': uris, 'where': 'music'}, outcome)
    extra = {'note': 'Both playlists are there for now; check them in Music Assistant.'} if new_id and outcome == 'unknown' else {}
    return _report(batch, words, outcome, outcome == 'done', **extra)


def delete(playlist_id, *, login, via):
    """Delete a Music Assistant playlist. Only from a tap on the confirmation page; there is no Undo."""
    playlist = next((p for p in playlists() if p['id'] == str(playlist_id)), None)
    if playlist is None:
        raise Refused('That playlist isn’t in the library any more.')
    check_editable(playlist)
    before = [s['uri'] for s in tracks(playlist['id'])]
    control._spend(1)
    batch, words = uuid.uuid4().hex[:12], f'Deleted playlist “{playlist["name"]}”'
    outcome = _steps([_delete(playlist['id'])])
    outcome = 'done' if outcome == 'sent' else outcome
    _record(batch, login, via, playlist['id'], playlist['name'], 'delete', words,
            {'name': playlist['name'], 'uris': before, 'where': 'music'}, outcome)
    return _report(batch, words, outcome, False)


# ---- Record and Undo ----

def _undoable(row):
    before = json.loads(row['before'])
    kind = row['action'] in ('create', 'remove', 'move') or (row['action'] == 'add' and before.get('where') == 'music') \
        or (row['action'] == 'rename' and row['outcome'] == 'done')   # an unconfirmed rename may have left both lists
    return kind and row['playlist'] != '' and row['outcome'] != 'not_sent' and not row['undone'] and row['via'] != 'undo' \
        and time.time() - row['ts'] < UNDO_SECONDS


def _row(batch):
    with _db() as conn:
        found = conn.execute('select * from music_changes where batch = ? order by id limit 1', (str(batch or '')[:12],)).fetchone()
    return dict(found) if found else None


def summary(batch):
    row = _row(batch)
    if row is None:
        return None
    return {**_report(row['batch'], row['words'], row['outcome'], _undoable(row)), 'undone': bool(row['undone'])}


def undo(batch, *, login, via):
    """Put one playlist edit back."""
    row = _row(batch)
    if row is None or row['undone'] or row['via'] == 'undo' or row['outcome'] == 'not_sent':
        raise Refused('There is nothing left to undo there.')
    if time.time() - row['ts'] > UNDO_SECONDS:
        raise Refused('That change is more than a day old. Change it back in Music Assistant.')
    if not _undoable(row):
        raise Refused('That can’t be undone from Hub.')
    before, playlist_id = json.loads(row['before']), row['playlist']
    if row['action'] in ('add', 'remove', 'move'):
        _no_repeats(before.get('name') or row['name'], before['uris'])
    control._spend(1)
    new, words = uuid.uuid4().hex[:12], f'“{before.get("name") or row["name"]}” put back'
    if row['action'] == 'create':
        outcome = _steps([_delete(playlist_id)])
        outcome, where = ('done' if outcome == 'sent' else outcome), playlist_id
    elif row['action'] == 'rename':
        where, outcome = _copy(playlist_id, before['name'], before['uris'])
        where = where or playlist_id
    else:
        now, uris = len(tracks(playlist_id)), before['uris']
        stages = ([(_remove(playlist_id, range(1, now + 1)), [])] if now else []) + ([(_add(playlist_id, uris), uris)] if uris else [])
        where, outcome = playlist_id, _staged(playlist_id, stages)
    _record(new, login, 'undo', where, before.get('name') or row['name'], 'undo', words, {}, outcome)
    if outcome != 'not_sent':
        with _db() as conn:
            conn.execute('update music_changes set undone = 1 where id = ?', (row['id'],))
    from ...engine import friction
    friction.undone([row], via)
    return _report(new, words, outcome, False)


def recent_changes(limit=30):
    with _db() as conn:
        rows = [dict(r) for r in conn.execute('select * from music_changes order by id desc limit ?', (limit,))]
    return [{**_report(r['batch'], r['words'], r['outcome'], _undoable(r)), 'ts': r['ts'], 'via': r['via'],
             'undone': bool(r['undone'])} for r in rows]


# ---- Reminders: the Apple Music sign-in and Hub's own key both run out ----

def apple_sign_in_ends():
    """When the Apple Music sign-in in Music Assistant runs out, from the sign-in date in the private settings
    (household.json, "home": {"apple_music_signed_in": "YYYY-MM-DD"}). Music Assistant doesn't report it."""
    try:
        value = household.section('home').get('apple_music_signed_in')
        return date.fromisoformat(value) + timedelta(days=APPLE_DAYS) if isinstance(value, str) else None
    except ValueError:
        return None


def _when(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).date()
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace('Z', '+00:00')).date()
    return None


def key_ends():
    """When Hub's Music Assistant key runs out (they last a year), or None when that can't be read. Asked twice a day
    at most; a failure is remembered as well, so Today never waits on Music Assistant."""
    with _lock:
        if time.monotonic() - _expiry['at'] < EXPIRY_SECONDS:
            return _expiry['value']
    found = None
    if ready():
        try:
            rows = [r for r in _call('auth/tokens') or () if isinstance(r, dict) and r.get('expires_at')]
            named = [r for r in rows if str(r.get('name', '')).strip().lower() == 'hub']
            if named or len(rows) == 1:
                found = _when((named or rows)[0]['expires_at'])
        except (MusicDown, ValueError, TypeError, OverflowError):
            found = None
    with _lock:
        _expiry.update(value=found, at=time.monotonic())
    return found
