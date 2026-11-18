"""Every change a member makes to Money, with the way back.

A budget, a category, a rule, a goal, a bill, an account's name or kind, the plan's assumptions: each is Hub's own
record (nothing at a bank ever changes), kept here with what was there before. Undo puts it back, for a day, and
only for the member who made it. Its batch starts with "m", so the app's one Undo route can send it here.
"""
import json
import secrets
from contextlib import closing

from . import share, store

UNDO_SECONDS = 24 * 3600
TABLES = {'money_budgets': 'category', 'money_rules': 'id', 'money_goals': 'id', 'money_plan': 'key', 'money_settings': 'key',
          'money_accounts': 'id', 'money_txns': 'id', 'money_bills': 'id'}


class Refused(Exception):
    """Undo can't happen. The message is safe to show."""


def snap(table, value, cols=None):
    """What a row is now (only `cols`, when given), so it can be put back: {'t', 'v', 'row'}; 'row' None when there
    is no such row yet."""
    column = TABLES[table]
    row = store.one(f'select * from {table} where {column}=?', (value,))
    if row is not None and cols:
        row = {c: row[c] for c in cols}
    return {'t': table, 'v': value, 'row': row, 'cols': list(cols or ())}


def record(login, via, said, snaps):
    """Keep one change and its way back; returns its batch."""
    batch = 'm' + secrets.token_hex(6)[:11]
    store.run('insert into money_changes(batch, at, login, via, said, undo) values(?,?,?,?,?,?)',
              (batch, store.now(), share.key(login), via, said[:300], json.dumps(snaps, default=str)))
    return batch


def _put_back(item):
    table, column = item['t'], TABLES[item['t']]
    with closing(store.db()) as conn, conn:
        if item['row'] is None:
            conn.execute(f'delete from {table} where {column}=?', (item['v'],))
        elif item['cols']:
            sets = ', '.join(f'{c}=?' for c in item['cols'])
            conn.execute(f'update {table} set {sets} where {column}=?', (*(item['row'][c] for c in item['cols']), item['v']))
        else:
            names = list(item['row'])
            conn.execute(f'insert or replace into {table}({", ".join(names)}) values({", ".join("?" * len(names))})',
                         tuple(item['row'][n] for n in names))


def undo(batch, login):
    """Put one change back. Returns what was said about it. Raises Refused."""
    row = store.one('select * from money_changes where batch=?', (batch,))
    if row is None:
        raise Refused('Hub doesn’t have that change any more.')
    if row['undone_at']:
        raise Refused('That was put back already.')
    if row['login'] != share.key(login):
        raise Refused('Only whoever made that change can undo it.')
    if store.now() - row['at'] > UNDO_SECONDS:
        raise Refused('That change is more than a day old. Change it again instead.')
    for item in reversed(json.loads(row['undo'])):
        _put_back(item)
    store.run('update money_changes set undone_at=? where id=?', (store.now(), row['id']))
    return row['said']


def recent(login, limit=12):
    """This member's changes from the last day, newest first, for the screen."""
    rows = store.rows('select batch, at, said, undone_at from money_changes where login=? and at>? order by at desc limit ?',
                      (share.key(login), store.now() - UNDO_SECONDS, limit))
    return [{'batch': r['batch'], 'at': r['at'], 'said': r['said'], 'undone': bool(r['undone_at'])} for r in rows]
