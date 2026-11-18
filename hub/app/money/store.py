"""Where Money is kept: money.db in Hub's private folder, owner-only, and never anywhere else.

One file of its own, apart from hub.db, so the household's money can be looked at, backed up or let go of on its
own. The secret that reads a member's banks (SimpleFIN's access address) is not in it: it lives in a second
owner-only file, `money-access.json`, read only by simplefin.py, never returned to a page, logged or given to a
model.

Tables:
- links: a member's connection to SimpleFIN Bridge (who connected it, when it was last read, what it said).
- accounts: every account, from a connection, a statement file or typed in by hand; whose it is and who sees it.
- balances: one balance an account a day, for net worth over time.
- txns: transactions, with the category Hub gave each and how it decided.
- rules: "this payee is groceries", from a member's correction; they beat Hub's own guesses.
- budgets, goals, plan: what the household told Hub.
- changes: every change a member made, with what was there before, for Undo.
- settings: small switches ("Money in Ask", when the last reading ran).
"""
import json
import os
import sqlite3
import time
from contextlib import closing

from ... import config

FILE = 'money.db'
ACCESS = 'money-access.json'

SCHEMA = """
create table if not exists money_links(
  id integer primary key, login text not null, kind text not null default 'simplefin', name text not null default '',
  created_at real not null, read_at real, tried_at real, ok integer, said text, reads text not null default '[]',
  gone integer not null default 0);
create table if not exists money_accounts(
  id text primary key, link integer, source text not null, owner text not null default '', share text not null default 'shared',
  name text not null, bank text not null default '', kind text not null default 'other', kind_set integer not null default 0,
  currency text not null default 'USD', balance real, available real, balance_at real, ends text,
  in_budget integer not null default 1, closed integer not null default 0, rate real,
  created_at real not null, updated_at real not null);
create table if not exists money_balances(
  account text not null, day text not null, balance real not null, primary key(account, day));
create table if not exists money_txns(
  id integer primary key, account text not null, ext text not null, day text not null, at real,
  amount real not null, payee text not null, words text not null default '', pending integer not null default 0,
  category text, how text, pair integer, note text, seen_at real not null, unique(account, ext));
create index if not exists money_txns_day on money_txns(day);
create table if not exists money_rules(
  id integer primary key, words text not null unique, category text not null, login text, made_at real not null);
create table if not exists money_budgets(
  category text primary key, monthly real not null, login text, set_at real not null);
create table if not exists money_goals(
  id integer primary key, name text not null, target real not null, by_day text, saved real not null default 0,
  account text, monthly real, owner text not null default '', private integer not null default 0,
  made_at real not null, done_at real);
create table if not exists money_bills(
  id integer primary key, name text not null, amount real not null, every text not null, next_day text not null,
  category text not null default 'other', login text, made_at real not null);
create table if not exists money_plan(key text primary key, value text not null);
create table if not exists money_changes(
  id integer primary key, batch text not null, at real not null, login text, via text not null, said text not null,
  undo text not null, undone_at real);
create table if not exists money_settings(key text primary key, value text not null);
"""


def now():
    """The one clock Money reads; tests set it."""
    return time.time()


def _open():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = config.DATA_DIR / FILE
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    if path.stat().st_mode & 0o077:
        path.chmod(0o600)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


_db = _open     # the dry-run layer (hub/golden/dryrun.py) points this at a scratch copy


def scratch(folder):
    """An opener for a copy of money.db in `folder`, for the dry-run layer: reads see the household's money,
    and nothing written there reaches the real file."""
    copy = folder / 'money.sqlite3'
    with closing(_open()) as real, closing(sqlite3.connect(copy)) as spare:
        real.backup(spare)

    def opened():
        conn = sqlite3.connect(copy, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    return opened


def db():
    return _db()


def rows(sql, args=()):
    with closing(db()) as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def one(sql, args=()):
    found = rows(sql, args)
    return found[0] if found else None


def run(sql, args=()):
    with closing(db()) as conn, conn:
        return conn.execute(sql, args).lastrowid


# ---- small switches ----

def setting(key, default=None):
    row = one('select value from money_settings where key=?', (key,))
    if row is None:
        return default
    try:
        return json.loads(row['value'])
    except ValueError:
        return default


def put(key, value):
    if value is None:
        run('delete from money_settings where key=?', (key,))
    else:
        run('insert into money_settings(key, value) values(?,?) on conflict(key) do update set value=excluded.value',
            (key, json.dumps(value)))


# ---- the plan's assumptions, one value a key ----

def plan(key, default=None):
    row = one('select value from money_plan where key=?', (key,))
    if row is None:
        return default
    try:
        return json.loads(row['value'])
    except ValueError:
        return default


def plan_all():
    found = {}
    for row in rows('select key, value from money_plan'):
        try:
            found[row['key']] = json.loads(row['value'])
        except ValueError:
            continue
    return found


# ---- the secret that reads a member's banks: its own owner-only file, never in money.db ----

def _access_path():
    return config.DATA_DIR / ACCESS


def _access_all():
    try:
        found = json.loads(_access_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def access(link):
    """The access address for one connection, or None. For simplefin.py only."""
    value = _access_all().get(str(link))
    return value if isinstance(value, str) else None


def keep_access(link, url):
    found = _access_all()
    if url is None:
        found.pop(str(link), None)
    else:
        found[str(link)] = url
    config.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _access_path()
    spare = path.with_name(path.name + '.new')
    fd = os.open(spare, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(found, out)
    os.replace(spare, path)
    path.chmod(0o600)
