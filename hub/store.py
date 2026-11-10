"""Local SQLite store: the plan (decisions) and the app's own activity log. No email content.

Sorted mail has its own tables in tools/gmail/triage.py, with their own retention.
"""
import json
import sqlite3
import time
from contextlib import closing

from . import config

SCHEMA = """
create table if not exists plan_items(
  id integer primary key autoincrement,
  sender text not null unique,
  decision text not null,
  status text not null default 'planned',
  stage text,
  count integer,
  count_exact integer not null default 1,
  filter_change_id text,
  preview_id text,
  change_ids text not null default '[]',
  archived integer not null default 0,
  archived_prior integer not null default 0,
  undo_queue text not null default '[]',
  error text,
  spec text,
  label_id text,
  label_created integer not null default 0,
  created_at real,
  updated_at real);
create table if not exists ai_blocked(sender text primary key);
create table if not exists inbox_history(
  day text primary key, total integer, unread integer);
create table if not exists activity(
  id integer primary key autoincrement, ts real, action text, detail text);
create table if not exists today_snoozed(key text primary key, until text not null);
create table if not exists engine_trust(action text primary key, level text not null);
create table if not exists engine_instructions(
  id integer primary key autoincrement, text text not null, created_at real not null);
create table if not exists engine_runs(
  id integer primary key autoincrement, day text not null, kind text not null, ts real not null,
  status text not null, luna_calls integer not null default 0, jev_calls integer not null default 0,
  tokens_in integer not null default 0, tokens_out integer not null default 0, cost real not null default 0);
create table if not exists engine_suggestions(
  day text not null, key text not null, payload text not null, dismissed integer not null default 0,
  primary key(day, key));
create table if not exists engine_requests(
  id integer primary key autoincrement, text text not null, status text not null default 'open',
  created_at real not null);
"""
ACTIONS = ("filter", "filter_archive", "archive", "custom", "sweep")
JSON_FIELDS = ("change_ids", "undo_queue", "spec")
NEW_COLUMNS = {"spec": "text", "label_id": "text", "label_created": "integer not null default 0", "display_name": "text"}


def db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(config.DATA_DIR / "hub.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    have = {r["name"] for r in conn.execute("pragma table_info(plan_items)")}
    for name, kind in NEW_COLUMNS.items():
        if name not in have:
            conn.execute(f"alter table plan_items add column {name} {kind}")
    return conn


def _row(row):
    item = dict(row)
    for field in JSON_FIELDS:
        item[field] = json.loads(item[field]) if item[field] is not None else None
    return item


def items():
    with closing(db()) as conn:
        return [_row(r) for r in conn.execute("select * from plan_items order by id")]


def get(item_id):
    with closing(db()) as conn:
        row = conn.execute("select * from plan_items where id=?", (item_id,)).fetchone()
    return _row(row) if row else None


def get_by_sender(sender):
    with closing(db()) as conn:
        row = conn.execute("select * from plan_items where sender=?", (sender,)).fetchone()
    return _row(row) if row else None


def set_decision(sender, decision, count=None, exact=True, spec=None, display_name=None):
    """Insert or replace a decision that hasn't been applied yet. Returns False if it can't change."""
    now = time.time()
    name = (display_name or "").strip()[:80] or None
    with closing(db()) as conn, conn:
        row = conn.execute("select status from plan_items where sender=?", (sender,)).fetchone()
        if row and row["status"] not in ("planned",):
            return False
        conn.execute(
            "insert into plan_items(sender, decision, count, count_exact, spec, display_name, created_at, updated_at) "
            "values(?,?,?,?,?,?,?,?) "
            "on conflict(sender) do update set decision=excluded.decision, count=excluded.count, "
            "count_exact=excluded.count_exact, spec=excluded.spec, "
            "display_name=coalesce(excluded.display_name, plan_items.display_name), updated_at=excluded.updated_at",
            (sender, decision, count, int(exact), json.dumps(spec) if spec is not None else None, name, now, now))
    return True


def remove(item_id):
    with closing(db()) as conn, conn:
        cursor = conn.execute("delete from plan_items where id=? and status='planned'", (item_id,))
    return cursor.rowcount == 1


def update(item_id, **fields):
    for field in JSON_FIELDS:
        if field in fields:
            fields[field] = json.dumps(fields[field])
    fields["updated_at"] = time.time()
    names = ", ".join(f"{k}=?" for k in fields)
    with closing(db()) as conn, conn:
        conn.execute(f"update plan_items set {names} where id=?", (*fields.values(), item_id))


def reset_later():
    with closing(db()) as conn, conn:
        conn.execute("delete from plan_items where decision='later' and status='planned'")


def decided_senders():
    return {i["sender"] for i in items()}


def plan_count():
    return sum(1 for i in items() if i["decision"] in ACTIONS and i["status"] == "planned")


def ai_blocked(sender):
    with closing(db()) as conn:
        return conn.execute("select 1 from ai_blocked where lower(sender)=?", (sender.lower(),)).fetchone() is not None


def set_ai_blocked(sender, blocked):
    with closing(db()) as conn, conn:
        if blocked:
            conn.execute("insert or ignore into ai_blocked(sender) values(?)", (sender.lower(),))
        else:
            conn.execute("delete from ai_blocked where lower(sender)=?", (sender.lower(),))


def log(action, detail=""):
    with closing(db()) as conn, conn:
        conn.execute("insert into activity(ts, action, detail) values(?,?,?)", (time.time(), action, detail[:500]))


def recent_activity(limit=40):
    with closing(db()) as conn:
        return [dict(r) for r in conn.execute("select * from activity order by id desc limit ?", (limit,))]


def record_inbox(day, total, unread):
    """One snapshot per local day (latest wins). Counts only; no email content."""
    if not isinstance(total, int):
        return
    with closing(db()) as conn, conn:
        conn.execute("insert into inbox_history(day, total, unread) values(?,?,?) on conflict(day) do update set "
                     "total=excluded.total, unread=excluded.unread", (day, total, unread if isinstance(unread, int) else None))


def inbox_history(limit=60):
    with closing(db()) as conn:
        rows = conn.execute("select day, total, unread from inbox_history order by day desc limit ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def snooze(key, day, until=None):
    """Hide a home recommendation through local day `until` (ISO date; today unless given)."""
    with closing(db()) as conn, conn:
        conn.execute("delete from today_snoozed where until < ?", (day,))
        conn.execute("insert into today_snoozed(key, until) values(?,?) on conflict(key) do update set until=excluded.until",
                     (key[:200], until or day))


def snoozed(day):
    with closing(db()) as conn:
        return {r["key"] for r in conn.execute("select key from today_snoozed where until >= ?", (day,))}
