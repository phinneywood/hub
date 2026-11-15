"""The Changes screen: everything Hub changed in the house and in the music library, newest first, each with its
Undo for a day, and the playlists Ask proposed, waiting for a tap. For the app (web/app/src/changes.tsx).

It reads the change records the older pages read (hub/tools/house/control.py and music.py) and makes the same
calls on a tap; a house change is undone through the app's own /app/api/undo.
"""
from ..tools.house import control, music

VIA = {'ask': 'from Ask', 'app': 'from the app', 'today': 'from Today', 'tap': 'from a tap', 'undo': 'Undo',
       'card': 'from Ask’s music card', 'bar': 'from the mini-player', 'music': 'from Music', 'recipe': 'from a routine'}


class Refused(Exception):
    """A tap that can't be done as asked; the words are for the owner."""


def _row(r, where):
    return {'batch': r['batch'], 'what': ', '.join(r['done'] + r['unknown'] + r['not_sent']), 'at': r['ts'],
            'via': VIA.get(r['via'], r['via'] or ''), 'unsure': r['unknown'], 'not_done': r['not_sent'],
            'undo': bool(r['undo']) and not r['undone'], 'undone': r['undone'], 'where': where}


def picture():
    """The Changes screen as data. Reads Hub's own records; asks neither Home Assistant nor Music Assistant."""
    rows = [_row(r, 'house') for r in control.recent()] + [_row(r, 'music') for r in music.recent_changes()]
    return {'changes': sorted(rows, key=lambda r: -r['at'])[:40],
            'waiting': [{'id': p['id'], 'name': p['name'], 'songs': p['songs']} for p in music.proposals()]}


def tap(do, key, login):
    """A playlist tap: make or skip one Ask proposed, or undo a playlist change. Returns the sentence to show."""
    key = str(key or '')[:40]
    try:
        if do == 'make':
            found = music.proposal(key)
            if found is None:
                raise Refused('That playlist isn’t waiting any more. Ask again.')
            result = music.create(found['name'], found['songs'], login=login, via='app')
            music.settle(found['id'], 'made')
            return '; '.join(result['done'] + result['unknown']) or 'Made.'
        if do == 'skip':
            music.settle(key, 'skipped')
            return 'Not made.'
        if do == 'undo':
            result = music.undo(key, login=login, via='app')
            return '; '.join(result['done'] + result['unknown']) or 'Put back.'
    except (music.Refused, music.MusicDown) as error:
        raise Refused(error) from None
    raise Refused('Hub couldn’t read that request.')
