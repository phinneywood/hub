"""How bright each lamp was the last time it was lit, so a room switched back on comes back as it was.

Home Assistant turns a dimmer on at whatever its maker chose, often full. Hub notes each lit lamp's brightness
whenever it reads the house, and a room's tap asks for that again. Kept in Hub's own database; a lamp Hub has
never seen lit is simply switched on.
"""
import threading

from .. import store

SCHEMA = """
create table if not exists app_levels(entity text primary key, level integer not null);
"""
_lock = threading.Lock()
_known = None       # {entity: percent}, read from the database once


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _load():
    global _known
    if _known is None:
        with _db() as conn:
            _known = {r['entity']: r['level'] for r in conn.execute('select entity, level from app_levels')}
    return _known


def forget():
    """For tests: start from what the database holds."""
    global _known
    with _lock:
        _known = None


def note(found):
    """Remember the brightness of every lamp that is lit in this reading (read.snapshot())."""
    with _lock:
        known = _load()
        new = {d['id']: int(d['level']) for r in found['rooms'] for d in r['devices']
               if d['kind'] == 'light' and d['on'] and d['level'] and known.get(d['id']) != int(d['level'])}
        if new:
            known.update(new)
            with _db() as conn:
                conn.executemany('insert or replace into app_levels(entity, level) values (?, ?)', list(new.items()))


def last(entity):
    """The brightness (1 to 100) this lamp was last lit at, or None."""
    with _lock:
        return _load().get(entity)
