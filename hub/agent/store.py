"""Ask Hub's threads: each one's title and full model-message history, in Hub's database.

The server owns the conversation; the browser only ever adds a new message. Chat text is kept for
TEXT_DAYS: older turns are cut from a thread each time it is read or saved, and a thread nobody has
touched for that long is removed. Logs and the engine's record hold counts and cost, never text.

A thread is the person's who started it (their Tailscale login; '' for this Mac itself), since its answers can hold
their mail and calendar: only they can list, read, continue or delete it. Threads kept from before that rule have
no login and are the owner's (hub/people.py).
"""
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, UserPromptPart

from .. import people, store

TEXT_DAYS = 30
MAX_THREADS = 200
TITLE_LENGTH = 60
SCHEMA = """
create table if not exists ask_threads(
  id text primary key, title text not null, created real not null, updated real not null,
  history text not null default '[]', login text);
"""


def db():
    conn = store.db()
    conn.executescript(SCHEMA)
    if 'login' not in {r['name'] for r in conn.execute('pragma table_info(ask_threads)')}:
        conn.execute('alter table ask_threads add column login text')
    return conn


def _whose(login):
    """Who a thread is kept for: the login as it is stored."""
    return login.strip().lower() if isinstance(login, str) else ''


def _theirs(kept, login):
    """Whether a thread kept for `kept` is this login's: its own, or one from before threads were each person's
    when this login is the owner's."""
    return kept == _whose(login) if kept is not None else people.owner(login)


def taken(thread_id, login=None):
    """Whether this id is someone else's thread, which this login may neither read nor add to."""
    with closing(db()) as conn:
        row = conn.execute('select login from ask_threads where id=?', (thread_id,)).fetchone()
    return row is not None and not _theirs(row['login'], login)


def _starts_turn(message):
    return isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts)


def _asked_at(message):
    return next(p.timestamp for p in message.parts if isinstance(p, UserPromptPart))


def trim(history, now=None):
    """The history without turns that began more than TEXT_DAYS ago. Cuts only where the owner's message starts a turn."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=TEXT_DAYS)
    for at, message in enumerate(history):
        if _starts_turn(message) and _asked_at(message) >= cutoff:
            return list(history[at:])
    return []


def recent(history, turns):
    """(older, recent): the last `turns` turns, which the model is shown, and what came before, which it is not."""
    starts = [at for at, message in enumerate(history) if _starts_turn(message)]
    cut = starts[-turns] if len(starts) > turns else 0
    return list(history[:cut]), list(history[cut:])


def sweep(now=None):
    """Remove threads untouched for TEXT_DAYS, and each person's oldest beyond MAX_THREADS of theirs: one person's
    many chats never push out another's."""
    now = time.time() if now is None else now
    with closing(db()) as conn, conn:
        conn.execute('delete from ask_threads where updated < ?', (now - TEXT_DAYS * 86400,))
        conn.execute('delete from ask_threads where id in (select id from (select id, row_number() over'
                     ' (partition by login order by updated desc) as n from ask_threads) where n > ?)', (MAX_THREADS,))


def threads(login=None):
    """This login's threads, newest first."""
    sweep()
    with closing(db()) as conn:
        rows = conn.execute('select id, title, created, updated, login from ask_threads order by updated desc').fetchall()
    return [{key: row[key] for key in ('id', 'title', 'created', 'updated')} for row in rows if _theirs(row['login'], login)]


def history(thread_id, login=None):
    """The thread's messages, already trimmed to what may still be kept. Empty for a thread that doesn't exist or
    isn't this login's."""
    with closing(db()) as conn:
        row = conn.execute('select history, login from ask_threads where id=?', (thread_id,)).fetchone()
    return trim(ModelMessagesTypeAdapter.validate_json(row['history'])) if row and _theirs(row['login'], login) else []


def save(thread_id, messages, login=None):
    """Keep the thread for this login. A thread that is already someone else's is left as it is."""
    messages = trim(messages)
    title = next((' '.join(p.content.split())[:TITLE_LENGTH] for m in messages if _starts_turn(m) for p in m.parts
                  if isinstance(p, UserPromptPart) and isinstance(p.content, str)), 'Chat')
    if taken(thread_id, login):
        return
    now = time.time()
    with closing(db()) as conn, conn:
        conn.execute('insert into ask_threads(id, title, created, updated, history, login) values(?,?,?,?,?,?) '
                     'on conflict(id) do update set title=excluded.title, updated=excluded.updated, history=excluded.history',
                     (thread_id, title, now, now, ModelMessagesTypeAdapter.dump_json(messages).decode(), _whose(login)))


def delete(thread_id, login=None):
    """Remove the thread if it is this login's."""
    if not taken(thread_id, login):
        with closing(db()) as conn, conn:
            conn.execute('delete from ask_threads where id=?', (thread_id,))
