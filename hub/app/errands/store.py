"""Where errands are kept: one row each in Hub's own database, never in Gmail.

A row holds what the owner sees: the title, one line of why, the facts, the sentence it rests on, and the drafted
ways to finish it. That text comes from mail, so it is blanked KEEP_TEXT_DAYS after the errand ends (or was made)
and the row is deleted after KEEP_DAYS, as sorted mail is (hub/tools/gmail/triage.py).

States: ready (waiting for a tap), opened (the owner opened where it gets finished; Hub is watching for proof),
done, later (hidden until a time), dismissed ("Not this"), missed (its date passed, or it waited too long), skipped
(Hub read it and nothing needed doing; no text is kept). `steps` holds the steps of the chosen way the owner has
taken, as "kind" names (mail, open, call, calendar, list).
"""
import json
import time
from contextlib import closing

from ... import home, store

KEEP_TEXT_DAYS = 14
KEEP_DAYS = 60
OPEN = ('ready', 'opened', 'later')
ENDED = ('done', 'dismissed', 'missed', 'skipped')

SCHEMA = """
create table if not exists errands(
  id integer primary key, key text not null unique, source text not null, kind text not null,
  title text not null default '', why text not null default '', due text, facts text not null default '{}',
  quote text not null default '', choices text not null default '[]',
  message_id text, thread_id text, sender text, sender_name text, subject text, reply_to text, received real,
  state text not null default 'ready', chose integer, steps text not null default '[]', how text,
  until real, undo text, made_day text not null,
  created_at real not null, updated_at real not null, opened_at real, ended_at real, checked_at real);
create index if not exists errands_state on errands(state);
create table if not exists errands_tried(key text primary key, tries integer not null, at real not null);
create table if not exists errands_settings(key text primary key, value text not null);
"""
JSON_FIELDS = ('facts', 'choices', 'steps', 'undo')


def now():
    """The one clock errands read; tests set it."""
    return time.time()


def now_():
    return now()


def db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _row(row):
    found = dict(row)
    for name in JSON_FIELDS:
        try:
            found[name] = json.loads(found[name]) if found[name] else None
        except ValueError:
            found[name] = None
    found['facts'] = found['facts'] or {}
    found['choices'] = found['choices'] or []
    found['steps'] = found['steps'] or []
    return found


def rows(where='1', args=()):
    with closing(db()) as conn:
        return [_row(r) for r in conn.execute(f'select * from errands where {where}', args)]


def get(errand_id):
    found = rows('id=?', (int(errand_id),))
    return found[0] if found else None


def keys():
    """Every source item Hub has made an errand from, or read and found nothing to do in."""
    with closing(db()) as conn:
        return {r['key'] for r in conn.execute('select key from errands')}


def add(key, source, made, *, state='ready', how=None, message=None, now=None):
    """Keep one new errand. `made` is the checked draft (draft.check); `message`: the email it came from
    ({id, thread_id, sender, name, subject, reply_to, received}). Returns its id, or None when one exists for the key
    already."""
    now = now_() if now is None else now
    message = message or {}
    with closing(db()) as conn, conn:
        cursor = conn.execute(
            'insert or ignore into errands(key, source, kind, title, why, due, facts, quote, choices, message_id, thread_id,'
            ' sender, sender_name, subject, reply_to, received, state, how, made_day, created_at, updated_at, ended_at)'
            ' values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (key, source, made.get('kind') or 'none', made.get('title') or '', made.get('why') or '', made.get('due'),
             json.dumps(made.get('facts') or {}), made.get('quote') or '', json.dumps(made.get('choices') or []),
             message.get('id'), message.get('thread_id'), message.get('sender'), message.get('name'), message.get('subject'),
             message.get('reply_to') or None, message.get('received'), state, how, home.today().isoformat(), now, now,
             now if state in ENDED else None))
        return cursor.lastrowid if cursor.rowcount else None


def update(errand_id, **fields):
    """Change one errand. JSON fields are written as JSON; updated_at is set."""
    if not fields:
        return
    fields = {name: json.dumps(value) if name in JSON_FIELDS and value is not None else value for name, value in fields.items()}
    fields['updated_at'] = now_()
    with closing(db()) as conn, conn:
        conn.execute(f'update errands set {", ".join(k + "=?" for k in fields)} where id=?', (*fields.values(), int(errand_id)))


def tried(key, now=None):
    """Count one failed reading of a source item (the model gave nothing usable for it). Returns the count."""
    now = now_() if now is None else now
    with closing(db()) as conn, conn:
        conn.execute('insert into errands_tried(key, tries, at) values(?, 1, ?) on conflict(key) do update'
                     ' set tries=tries+1, at=excluded.at', (key, now))
        return conn.execute('select tries from errands_tried where key=?', (key,)).fetchone()['tries']


def tries():
    with closing(db()) as conn:
        return {r['key']: r['tries'] for r in conn.execute('select key, tries from errands_tried')}


def setting(key, default=None):
    with closing(db()) as conn:
        row = conn.execute('select value from errands_settings where key=?', (key,)).fetchone()
    return row['value'] if row else default


def put_setting(key, value):
    with closing(db()) as conn, conn:
        conn.execute('insert into errands_settings(key, value) values(?, ?) on conflict(key) do update set value=excluded.value',
                     (key, str(value)))


def prune(now=None):
    """Let go of old text and old rows, as sorted mail does."""
    now = now_() if now is None else now
    text_before, gone_before = now - KEEP_TEXT_DAYS * 86400, now - KEEP_DAYS * 86400
    with closing(db()) as conn, conn:
        conn.execute("update errands set title='', why='', quote='', choices='[]', facts='{}', sender_name='', subject='',"
                     " reply_to=null, undo=null where coalesce(ended_at, created_at) < ? and (title != '' or why != ''"
                     " or quote != '' or choices != '[]' or facts != '{}' or subject != '') and state in (%s)"
                     % ','.join('?' * len(ENDED)), (text_before, *ENDED))
        conn.execute('delete from errands where created_at < ?', (gone_before,))
        conn.execute('delete from errands_tried where at < ?', (gone_before,))
