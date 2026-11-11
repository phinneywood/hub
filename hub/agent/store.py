"""Ask Hub's threads: each one's title and full model-message history, in Hub's database.

The server owns the conversation; the browser only ever adds a new message. Chat text is kept for
TEXT_DAYS: older turns are cut from a thread each time it is read or saved, and a thread nobody has
touched for that long is removed. Logs and the engine's record hold counts and cost, never text.
"""
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, UserPromptPart

from .. import store

TEXT_DAYS = 30
MAX_THREADS = 200
TITLE_LENGTH = 60
SCHEMA = """
create table if not exists ask_threads(
  id text primary key, title text not null, created real not null, updated real not null,
  history text not null default '[]');
"""


def db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


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
    """Remove threads untouched for TEXT_DAYS, and the oldest beyond MAX_THREADS."""
    now = time.time() if now is None else now
    with closing(db()) as conn, conn:
        conn.execute('delete from ask_threads where updated < ?', (now - TEXT_DAYS * 86400,))
        conn.execute('delete from ask_threads where id not in (select id from ask_threads order by updated desc limit ?)',
                     (MAX_THREADS,))


def threads():
    sweep()
    with closing(db()) as conn:
        return [dict(r) for r in conn.execute('select id, title, created, updated from ask_threads order by updated desc')]


def history(thread_id):
    """The thread's messages, already trimmed to what may still be kept. Empty for a thread that doesn't exist."""
    with closing(db()) as conn:
        row = conn.execute('select history from ask_threads where id=?', (thread_id,)).fetchone()
    return trim(ModelMessagesTypeAdapter.validate_json(row['history'])) if row else []


def save(thread_id, messages):
    messages = trim(messages)
    title = next((' '.join(p.content.split())[:TITLE_LENGTH] for m in messages if _starts_turn(m) for p in m.parts
                  if isinstance(p, UserPromptPart) and isinstance(p.content, str)), 'Chat')
    now = time.time()
    with closing(db()) as conn, conn:
        conn.execute('insert into ask_threads(id, title, created, updated, history) values(?,?,?,?,?) '
                     'on conflict(id) do update set title=excluded.title, updated=excluded.updated, history=excluded.history',
                     (thread_id, title, now, now, ModelMessagesTypeAdapter.dump_json(messages).decode()))


def delete(thread_id):
    with closing(db()) as conn, conn:
        conn.execute('delete from ask_threads where id=?', (thread_id,))
