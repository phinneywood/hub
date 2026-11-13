"""What waits for the owner's tap, shown as a card under Ask's answer, with its buttons.

A control that leaves something waiting (a note, a recipe, a playlist to make, a playlist to delete) puts a
small `card` in its answer: its kind, the proposal's id, and the words to show. The chat page draws it with
buttons that post to the same signed-in Hub routes the Notes, Recipes and Playlists pages use, asking for a
JSON answer. Nothing is decided here: a card only shows, and the route still checks the tap. `status()` lets
a card shown again later (a reopened chat) say what became of it.
"""
import time

KINDS = ('note', 'recipe', 'playlist', 'delete')
WAIT_SECONDS = 24 * 3600
# kind -> (module that keeps it, table, words for each final status)
TABLES = {'note': ('hub.engine.notes', 'ask_note_proposals'), 'recipe': ('hub.engine.recipes', 'recipe_proposals'),
          'playlist': ('hub.tools.house.music', 'music_proposals')}


def card(kind, proposal_id, text, lines=()):
    """What a control puts in its answer for the page to draw. The model sees it too, as data."""
    assert kind in KINDS
    return {'kind': kind, 'id': str(proposal_id), 'text': str(text), 'lines': [str(line) for line in lines]}


def status(kind, proposal_id):
    """'waiting', 'expired', 'gone', or how it ended ('saved', 'skipped', 'made', 'deleted')."""
    import importlib
    proposal_id = str(proposal_id)[:40]
    if kind == 'delete':
        from ..tools.house import music
        try:
            return 'waiting' if any(p['id'] == proposal_id for p in music.playlists()) else 'deleted'
        except (music.MusicDown, ValueError):
            return 'gone'
    if kind not in TABLES:
        return 'gone'
    module, table = TABLES[kind]
    with importlib.import_module(module)._db() as conn:
        row = conn.execute(f'select status, ts from {table} where id = ?', (proposal_id[:12],)).fetchone()
    if row is None:
        return 'gone'
    if row['status'] == 'waiting' and time.time() - row['ts'] > WAIT_SECONDS:
        return 'expired'
    return row['status']
