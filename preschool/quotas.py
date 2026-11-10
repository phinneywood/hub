"""Atomic reservations against the existing shared standing daily counters.

No activation, quota increase, reset, refund or source-system write. Reservations
are conservative: failed/uncertain calls remain counted across both accounts.
"""
from contextlib import closing
from datetime import date
import sqlite3

from . import store

LIMITS = {'extraction': ('auto_count_', 3), 'decision': ('jev_count_', 50)}


def _legacy(prefix, day, limit):
    total = 0
    for account in ('personal', 'shared'):
        path = store.config.DATA_DIR / ('preschool-shared.db' if account == 'shared' else 'preschool.db')
        if not path.exists():
            continue
        try:
            if path.is_symlink():
                raise ValueError()
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                row = conn.execute('select value from settings where key=?', (prefix + day,)).fetchone()
            count = int(row[0]) if row else 0
            if count < 0:
                raise ValueError()
            total += count
        except (sqlite3.Error, TypeError, ValueError):
            return limit
    return total


def _count(kind, day, reserve):
    if kind not in LIMITS or not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError('Invalid standing quota reservation.')
    prefix, limit = LIMITS[kind]
    key = 'global_' + prefix + day
    with store.account('shared'), closing(store.db()) as conn, conn:
        conn.execute('begin immediate')
        if not conn.execute('select 1 from settings where key=?', (key,)).fetchone():
            conn.execute('insert into settings values (?,?)', (key, str(_legacy(prefix, day, limit))))
        try:
            count = int(conn.execute('select value from settings where key=?', (key,)).fetchone()[0])
            if count < 0:
                raise ValueError()
        except (TypeError, ValueError):
            count = limit
        if reserve:
            if count >= limit:
                raise ValueError('The existing standing daily quota is exhausted.')
            count += 1
            conn.execute('update settings set value=? where key=?', (str(count), key))
        return count


def current(kind, day):
    return _count(kind, day, False)


def reserve(kind, day):
    return _count(kind, day, True)
