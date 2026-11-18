"""Dashboards: one page per category, made of blocks each person can hide and put in their own order.

Owner's rule (2026-11-17): every category in Hub works the same way. Ask knows it and answers about it; what needs a
person shows as a card in Ask or Today; and the category has one page of its own, a dashboard the person can arrange.
This is that page, written once for every category, so a new one is a module here and nothing else in the app.

A board is a module with:

- `ID`, `NAME`, `WHAT`: its address (`/app/#board/<ID>`), its name and one line on what it shows.
- `sees(login)`: whether this login may open it. A board says whose it is; the wall never shows one.
- `picture(login, today)`: `{'say', 'sub', 'blocks'}`. `blocks` is a list of dicts with an `id`, a `title` and a
  `kind` the app knows how to draw (see `KINDS`); the person's own order and hidden blocks are applied here.
- `line(login, today)`: one short sentence for Today, or None.
- `facts(login, today)`: what Ask's model may be given when the person asks about it, as plain values.

What a person arranges (order, hidden) is theirs alone, kept in Hub's own database by login. A tick on a block's
checklist is the household's, as the list it belongs to is. Nothing here reaches outside Hub: each board reads what
Hub already reads for its older page or its Ask lookup.
"""
import json
import logging
import time
from contextlib import closing

from ... import home, store

log = logging.getLogger(__name__)

# What the app knows how to draw (web/app/src/boards.tsx). Anything else is left out, so a block a newer board
# sends never breaks an older app.
KINDS = {'hero', 'tiles', 'rows', 'checks', 'line', 'bars', 'note'}
ID_CHARS = 60
SCHEMA = """
create table if not exists app_board_layout(
  login text not null, board text not null, layout text not null, ts real not null, primary key(login, board));
create table if not exists app_board_ticks(
  board text not null, item text not null, login text, ts real not null, primary key(board, item));
create table if not exists app_board_settings(
  board text not null, key text not null, value text not null, login text, ts real not null, primary key(board, key));
"""


class Refused(Exception):
    """A tap that can't be done as asked; the words are for the person."""


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _boards():
    from . import calendar, mail, network, pregnancy, spend
    return {b.ID: b for b in (pregnancy, calendar, network, mail, spend)}


def board(board_id):
    return _boards().get(str(board_id or '')[:ID_CHARS])


def _sees(module, login):
    try:
        return bool(module.sees(login))
    except Exception:  # noqa: BLE001 - a board that can't say whose it is is nobody's
        log.warning('boards: %s could not say who sees it', module.ID)
        return False


def listing(login, today=None):
    """The boards this login may open, each with its name, what it shows and its line for today."""
    today = today or home.today()
    found = []
    for module in _boards().values():
        if not _sees(module, login):
            continue
        found.append({'id': module.ID, 'name': module.NAME, 'what': module.WHAT, 'line': _line(module, login, today)})
    return found


def _line(module, login, today):
    try:
        return module.line(login, today)
    except Exception:  # noqa: BLE001 - Today still opens without one board's line
        log.warning('boards: %s line failed', module.ID)
        return None


def lines(login, today=None):
    """Today's part: one line per board this person may open that has something to say."""
    return [{'id': b['id'], 'name': b['name'], **b['line']} for b in listing(login, today) if b['line']]


# ---- the person's arrangement ----

def layout(login, board_id):
    """{'order': [block ids], 'hidden': [block ids]} as this person left the board; empty for anyone new."""
    if not login:
        return {'order': [], 'hidden': []}
    with closing(_db()) as conn:
        row = conn.execute('select layout from app_board_layout where login=? and board=?', (login, board_id)).fetchone()
    try:
        kept = json.loads(row['layout']) if row else {}
    except ValueError:
        kept = {}
    return {'order': [str(x) for x in kept.get('order') or []][:40], 'hidden': [str(x) for x in kept.get('hidden') or []][:40]}


def arrange(login, board_id, order, hidden):
    """Keep this person's order and hidden blocks for a board. Only ids the board has are kept."""
    if not login:
        raise Refused('Arrange it from your phone or laptop.')
    module = board(board_id)
    if module is None or not _sees(module, login):
        raise Refused('That dashboard isn’t yours to change.')
    known = {b['id'] for b in module.picture(login, home.today())['blocks']}
    clean = lambda ids: list(dict.fromkeys(str(x)[:ID_CHARS] for x in ids or () if str(x)[:ID_CHARS] in known))
    kept = {'order': clean(order), 'hidden': clean(hidden)}
    with closing(_db()) as conn, conn:
        conn.execute('insert into app_board_layout(login, board, layout, ts) values(?,?,?,?) '
                     'on conflict(login, board) do update set layout=excluded.layout, ts=excluded.ts',
                     (login, module.ID, json.dumps(kept), time.time()))
    return kept


def _arranged(blocks, kept):
    """The blocks in the person's order (any new block where the board puts it), split into shown and hidden."""
    by_id = {b['id']: b for b in blocks}
    ids = [i for i in kept['order'] if i in by_id]
    ids += [b['id'] for b in blocks if b['id'] not in ids]        # a block the person never placed (a new one) comes last
    ordered = [by_id[i] for i in ids]
    hidden = set(kept['hidden'])
    return [b for b in ordered if b['id'] not in hidden], [{'id': b['id'], 'title': b['title']} for b in ordered if b['id'] in hidden]


def picture(board_id, login, today=None):
    """One board as data for the app, arranged as this person left it; None when it isn't theirs to open."""
    module = board(board_id)
    if module is None or not _sees(module, login):
        return None
    today = today or home.today()
    found = module.picture(login, today)
    blocks = [b for b in found['blocks'] if b.get('kind') in KINDS and b.get('id')]
    shown, hidden = _arranged(blocks, layout(login, module.ID))
    return {'ok': True, 'id': module.ID, 'name': module.NAME, 'say': found['say'], 'sub': found.get('sub', ''),
            'blocks': shown, 'hidden': hidden, 'settings': found.get('settings', []), 'at': time.time()}


def facts(board_id, login, today=None):
    """What Ask may be given about a board, for this person; a line saying it isn't theirs otherwise."""
    module = board(board_id)
    if module is None or not _sees(module, login):
        return {'note': 'That dashboard isn’t this person’s to read.'}
    return module.facts(login, today or home.today())


# ---- the household's ticks and settings, shared by everyone who sees the board ----

def ticks(board_id):
    with closing(_db()) as conn:
        return {r['item'] for r in conn.execute('select item from app_board_ticks where board=?', (board_id,))}


def tick(login, board_id, item, done):
    if not login:
        raise Refused('Tick it from your phone or laptop.')
    module = board(board_id)
    if module is None or not _sees(module, login):
        raise Refused('That dashboard isn’t yours to change.')
    item = str(item or '')[:ID_CHARS]
    if item not in getattr(module, 'TICKABLE', lambda: set())():
        raise Refused('Hub couldn’t find that on the list.')
    with closing(_db()) as conn, conn:
        if done:
            conn.execute('insert or replace into app_board_ticks(board, item, login, ts) values(?,?,?,?)', (module.ID, item, login, time.time()))
        else:
            conn.execute('delete from app_board_ticks where board=? and item=?', (module.ID, item))
    return 'Ticked' if done else 'Put back'


def setting(board_id, key, default=None):
    with closing(_db()) as conn:
        row = conn.execute('select value from app_board_settings where board=? and key=?', (board_id, key)).fetchone()
    return row['value'] if row else default


def change(login, board_id, key, value):
    """A board's own setting, such as a date, kept for the household. The board checks and words it."""
    if not login:
        raise Refused('Change it from your phone or laptop.')
    module = board(board_id)
    if module is None or not _sees(module, login) or not hasattr(module, 'set_value'):
        raise Refused('That dashboard isn’t yours to change.')
    kept, said = module.set_value(str(key or '')[:ID_CHARS], value)       # raises Refused
    with closing(_db()) as conn, conn:
        if kept is None:
            conn.execute('delete from app_board_settings where board=? and key=?', (module.ID, key))
        else:
            conn.execute('insert or replace into app_board_settings(board, key, value, login, ts) values(?,?,?,?,?)',
                         (module.ID, key, str(kept), login, time.time()))
    return said
