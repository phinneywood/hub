"""What was asked and answered, and what Hub remembers. In Hub's own database, never in Git.

A turn's words are kept for 90 days, so a person can scroll back through their one conversation (`thread.py`). A turn Luna answered (`private.py`, on a Hub
without Ask's own model) is marked so.

What Hub remembers is the engine's one list of standing notes (`hub/engine/state.py`), the same list the older
chat saved to with a tap, so there is one memory: a note saved either way reaches Ask, steers the morning
review, and can keep speakers together as a zone (`hub/tools/house/zones.py`).
"""
import json
import time

from ... import store

TEXT_DAYS = 90                  # turns stay readable this long; the running summary carries the conversation past it (thread.py)
RECENT_SECONDS = 30 * 60        # how far back a follow-up ("there too") can reach
RECENT_TURNS = 6
SHOWN_TURNS = 40                # what the app shows of the one conversation when it opens, and each time it scrolls back
USUAL_WINDOW = 90               # minutes either side of now that count as "around this time"
USUAL_DAYS = 2                  # asked on at least this many days to be offered
USUAL_MOST = 3
SCHEMA = """
create table if not exists app_ask(
  id integer primary key autoincrement, ts real not null, login text, asked text not null, answer text not null,
  cards text not null default '[]', private integer not null default 0, ms integer not null default 0,
  status text not null default 'ok');
create index if not exists app_ask_login on app_ask(login, ts);
"""
# Added 2026-11-18: which tools a turn used and how each came out ("music_library:read", "find:not_done"), and what
# kind of failure ended it ("rate_limit"). Names and outcome words only, never what was said or found.
COLUMNS = (('tools', "text not null default '[]'"), ('problem', "text not null default ''"))
# How a turn ended. ok: answered or done. stuck: tools were used and none of them did the job. unanswered: the model
# neither spoke nor acted. failed: an error ended it. refused: Hub wouldn't run it (no key, a limit).
STATUSES = ('ok', 'stuck', 'unanswered', 'failed', 'refused')


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    have = {row[1] for row in conn.execute('pragma table_info(app_ask)')}
    missing = [(name, kind) for name, kind in COLUMNS if name not in have]
    for name, kind in missing:          # a database from before these columns
        conn.execute(f'alter table app_ask add column {name} {kind}')
    if missing:
        conn.commit()
    return conn


def _turn(row):
    return {'id': row['id'], 'at': row['ts'], 'asked': row['asked'], 'answer': row['answer'], 'cards': json.loads(row['cards']),
            'private': bool(row['private']), 'ms': row['ms'], 'status': row['status'],
            'tools': json.loads(row['tools']) if 'tools' in row.keys() and row['tools'] else [],
            'problem': row['problem'] if 'problem' in row.keys() else ''}


def save(login, asked, answer, cards=(), private=False, ms=0, status='ok', at=None, tools=(), problem=''):
    """Keep one turn; returns its number. Older than TEXT_DAYS goes. `at`: when it was asked, for tests. `tools`:
    "name:outcome" for each tool the turn used; `problem`: the kind of failure that ended it."""
    with _db() as conn:
        conn.execute('delete from app_ask where ts < ?', (time.time() - TEXT_DAYS * 86400,))
        return conn.execute('insert into app_ask(ts, login, asked, answer, cards, private, ms, status, tools, problem) '
                            'values (?,?,?,?,?,?,?,?,?,?)',
                            (at or time.time(), login, asked[:2000], answer[:4000], json.dumps(list(cards)), int(private), int(ms),
                             status, json.dumps([str(t)[:60] for t in tools][:20]), str(problem or '')[:60])).lastrowid


def health(days=7, now=None):
    """How Ask's turns ended over the last `days`, for the System page and the live check: counts by status, the
    tools that most often didn't do the job, and the kinds of failure. Counts and names only, never words."""
    now = now or time.time()
    with _db() as conn:
        rows = conn.execute("select status, tools, problem from app_ask where ts > ? and asked != ''",
                            (now - days * 86400,)).fetchall()
    by_status, misses, problems = {}, {}, {}
    for row in rows:
        by_status[row['status']] = by_status.get(row['status'], 0) + 1
        for used in json.loads(row['tools'] or '[]'):
            name, _, outcome = str(used).partition(':')
            if outcome in MISSES:
                misses[name] = misses.get(name, 0) + 1
        if row['problem']:
            problems[row['problem']] = problems.get(row['problem'], 0) + 1
    return {'days': days, 'turns': len(rows), 'by_status': by_status, 'tool_misses': misses, 'problems': problems}


MISSES = ('not_done', 'refused', 'error', 'unavailable', 'retry')


def usual(login, now=None):
    """What this person tends to ask around this time of day, most often first: requests that changed the house
    or the music, asked within an hour and a half of now on at least two different days this month, and not
    already asked in the last two hours. Their own words, as they last typed them. No model is involved."""
    from datetime import datetime

    from ... import home
    now = now or time.time()
    minute = lambda ts: (lambda t: t.hour * 60 + t.minute)(datetime.fromtimestamp(ts, home.PACIFIC))  # noqa: E731
    day = lambda ts: datetime.fromtimestamp(ts, home.PACIFIC).date()  # noqa: E731
    with _db() as conn:
        rows = conn.execute("select ts, asked from app_ask where login is ? and status = 'ok' and private = 0 and cards != '[]' "
                            'and ts > ? order by ts', (login, now - TEXT_DAYS * 86400)).fetchall()
    found = {}
    for row in rows:
        apart = abs(minute(row['ts']) - minute(now))
        said = ' '.join(row['asked'].lower().split()).strip(' .!?')
        entry = found.setdefault(said, {'days': set(), 'last': 0.0, 'asked': row['asked']})
        entry.update(last=row['ts'], asked=row['asked'])
        if min(apart, 1440 - apart) <= USUAL_WINDOW:
            entry['days'].add(day(row['ts']))
    often = [e for e in found.values() if len(e['days']) >= USUAL_DAYS and now - e['last'] > 2 * 3600]
    return [e['asked'] for e in sorted(often, key=lambda e: (-len(e['days']), -e['last']))[:USUAL_MOST]]


def recent(login, seconds=RECENT_SECONDS, limit=RECENT_TURNS):
    """This person's last turns, oldest first."""
    with _db() as conn:
        rows = conn.execute('select * from app_ask where login is ? and ts > ? order by id desc limit ?',
                            (login, time.time() - seconds, limit)).fetchall()
    return [_turn(r) for r in reversed(rows)]


class NotKept(Exception):
    """Nothing was remembered. The message is safe to show. `again`: other words might be kept."""

    def __init__(self, message, again=False):
        super().__init__(message)
        self.again = again


def remember(note, said, zone=(), *, login):
    """Keep one thing the owner asked Hub to remember, at once; returns its number. Raises NotKept.

    Only ever the owner's own words: their latest message must ask for something to be remembered, and the note
    must be made of that message's words (the engine's own test, `notes.grounded`), so nothing a song title, a
    device name or a model made up can become a standing note. `zone`: speakers that play together from now on."""
    from ...engine import judge, notes, state
    from ...tools.house import zones
    text = ' '.join(str(note or '').split())
    if not text:
        raise NotKept('Say what to remember.', again=True)
    if len(text) > notes.NOTE_LENGTH:
        raise NotKept(f'A note is one short sentence, under {notes.NOTE_LENGTH} characters.', again=True)
    if judge.scrub(text) != text:
        raise NotKept('A note can’t hold links or email addresses.')
    if not notes.ASKED.search(str(said or '')):
        raise NotKept('The owner’s latest message doesn’t ask Hub to remember anything, so nothing was kept.')
    if not notes.grounded(text, said):
        raise NotKept('A note must say what the owner said, in their own words.', again=True)
    number = state.add_instruction(text, login)
    if not number:
        raise NotKept(f'Hub remembers {state.MAX_INSTRUCTIONS} things at most. Forget one under System first.')
    if len(set(zone)) > 1:
        zones.add(number, zone)
    store.log('ask_note', 'saved')
    return number


def remembered(login):
    """What this person asked Hub to remember. Each person's notes are their own."""
    from ...engine import state
    return [{'id': i['id'], 'note': i['text']} for i in state.instructions(login)]


def forget(note_id, login):
    """Forget one of this person's notes; False when it isn't theirs. A zone kept beside it goes with it."""
    from ...engine import state
    return state.remove_instruction(int(note_id), login)
