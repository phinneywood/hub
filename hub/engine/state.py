"""What the engine remembers: trust levels, standing instructions, runs and suggestions.

Runs hold counts and cost only. Suggestions hold the short text shown in “Needs you”.
"""
import json
import time
from contextlib import closing

from .. import store
from .registry import TRUST

MAX_INSTRUCTIONS = 12
INSTRUCTION_LENGTH = 300
MAX_REQUESTS = 30
KEEP_DAYS = 60
OWN_BUDGET = ('chat',)     # run kinds with a monthly limit of their own; left out of the shared one


def trust(action):
    """The owner's level for an action, never above what the code supports."""
    with closing(store.db()) as conn:
        row = conn.execute('select level from engine_trust where action=?', (action.slug,)).fetchone()
    level = row['level'] if row and row['level'] in TRUST else action.default
    return TRUST[min(TRUST.index(level), TRUST.index(action.ceiling))]


def set_trust(action, level):
    if level not in action.levels():
        return False
    with closing(store.db()) as conn, conn:
        conn.execute('insert into engine_trust(action, level) values(?,?) '
                     'on conflict(action) do update set level=excluded.level', (action.slug, level))
    store.log('engine_trust', f'{action.slug}: {level}')
    return True


def instructions():
    with closing(store.db()) as conn:
        return [dict(r) for r in conn.execute('select id, text, created_at from engine_instructions order by id')]


def add_instruction(text):
    """Save one standing instruction. Its id, or False when it is empty or the list is full."""
    text = ' '.join(str(text).split())[:INSTRUCTION_LENGTH]
    if not text or len(instructions()) >= MAX_INSTRUCTIONS:
        return False
    with closing(store.db()) as conn, conn:
        return conn.execute('insert into engine_instructions(text, created_at) values(?,?)', (text, time.time())).lastrowid


def remove_instruction(instruction_id):
    with closing(store.db()) as conn, conn:
        conn.execute('delete from engine_instructions where id=?', (instruction_id,))


def record_run(day, kind, status, luna_calls=0, jev_calls=0, tokens_in=0, tokens_out=0, cost=0.0):
    with closing(store.db()) as conn, conn:
        conn.execute('delete from engine_runs where day < date(?, ?)', (day, f'-{KEEP_DAYS} days'))
        conn.execute('insert into engine_runs(day, kind, ts, status, luna_calls, jev_calls, tokens_in, tokens_out, cost) '
                     'values(?,?,?,?,?,?,?,?,?)',
                     (day, kind, time.time(), status, luna_calls, jev_calls, tokens_in, tokens_out, cost))


def runs(kind, limit=14):
    with closing(store.db()) as conn:
        return [dict(r) for r in conn.execute('select * from engine_runs where kind=? order by id desc limit ?', (kind, limit))]


def ran_recently(day, kind, days=7):
    """Whether this kind of run has happened at all in the days before `day`."""
    with closing(store.db()) as conn:
        return bool(conn.execute('select 1 from engine_runs where kind=? and day < ? and day >= date(?, ?) limit 1',
                                 (kind, day, day, f'-{days} days')).fetchone())


def last_run(day, kind):
    with closing(store.db()) as conn:
        row = conn.execute('select * from engine_runs where day=? and kind=? order by id desc limit 1', (day, kind)).fetchone()
    return dict(row) if row else None


def used(day, kind):
    """Model calls this kind of run made on `day`, and the cost recorded in its month by every kind that shares the limit."""
    with closing(store.db()) as conn:
        calls = conn.execute('select coalesce(sum(luna_calls),0) l, coalesce(sum(jev_calls),0) j from engine_runs '
                             'where day=? and kind=?', (day, kind)).fetchone()
        month = conn.execute('select coalesce(sum(cost),0) c from engine_runs where substr(day,1,7)=? and kind not in (%s)'
                             % ','.join('?' * len(OWN_BUDGET)), (day[:7], *OWN_BUDGET)).fetchone()
    return {'luna_calls': calls['l'], 'jev_calls': calls['j'], 'month_cost': month['c']}


def spent(day, kind):
    """What one kind of run has cost in the month of `day`, and its model calls and tokens that day."""
    with closing(store.db()) as conn:
        month = conn.execute('select coalesce(sum(cost),0) c from engine_runs where substr(day,1,7)=? and kind=?',
                             (day[:7], kind)).fetchone()
        today = conn.execute('select coalesce(sum(luna_calls),0) l, coalesce(sum(tokens_in),0) i, coalesce(sum(tokens_out),0) o, '
                             'count(*) n from engine_runs where day=? and kind=?', (day, kind)).fetchone()
    return {'month_cost': month['c'], 'calls_today': today['l'], 'tokens_in_today': today['i'],
            'tokens_out_today': today['o'], 'runs_today': today['n']}


def daily(day, days=30):
    """Per day and kind over the `days` ending on `day`: runs, runs that didn't finish, model calls and cost."""
    with closing(store.db()) as conn:
        rows = conn.execute("select day, kind, count(*) runs, sum(status != 'ok') failed, "
                            'sum(luna_calls + jev_calls) calls, sum(cost) cost from engine_runs '
                            'where day <= ? and day > date(?, ?) group by day, kind order by day',
                            (day, day, f'-{days} days'))
        return [dict(r) for r in rows]


def save_suggestions(day, items):
    with closing(store.db()) as conn, conn:
        conn.execute('delete from engine_suggestions where day < date(?, ?)', (day, '-7 days'))
        conn.execute("delete from engine_suggestions where day=? and key like 'ai-%'", (day,))
        conn.executemany('insert or ignore into engine_suggestions(day, key, payload) values(?,?,?)',
                         [(day, item['key'], json.dumps(item)) for item in items])


def add_note(day, key, tone, title, why, href, cta):
    """A line in today's “Needs you” about something Hub did by itself."""
    item = {'key': key, 'tone': tone, 'title': title, 'why': why, 'href': href, 'cta': cta}
    with closing(store.db()) as conn, conn:
        conn.execute('insert or replace into engine_suggestions(day, key, payload) values(?,?,?)', (day, key, json.dumps(item)))
    return item


def suggestions(day):
    with closing(store.db()) as conn:
        rows = conn.execute('select payload from engine_suggestions where day=? and dismissed=0 order by rowid', (day,))
        return [json.loads(r['payload']) for r in rows]


def dismiss(key):
    with closing(store.db()) as conn, conn:
        conn.execute('update engine_suggestions set dismissed=1 where key=?', (key,))


def recent_suggestions(day):
    """Titles from the last week with whether the owner waved them away, for the next run."""
    with closing(store.db()) as conn:
        rows = conn.execute("select day, payload, dismissed from engine_suggestions where day < ? and key like 'ai-%' "
                            'order by day desc limit 20', (day,))
        return [{'day': r['day'], 'title': json.loads(r['payload'])['title'], 'owner_said_not_today': bool(r['dismissed'])}
                for r in rows]


def add_request(text):
    """Something the owner asked for that Hub can't do yet. Kept for whoever builds next; does nothing by itself."""
    with closing(store.db()) as conn, conn:
        if conn.execute("select count(*) from engine_requests where status='open'").fetchone()[0] >= MAX_REQUESTS:
            return False
        conn.execute('insert into engine_requests(text, created_at) values(?,?)', (text, time.time()))
    return True


def requests():
    with closing(store.db()) as conn:
        return [dict(r) for r in conn.execute("select id, text, created_at from engine_requests where status='open' order by id")]


def close_request(request_id):
    with closing(store.db()) as conn, conn:
        conn.execute("update engine_requests set status='done' where id=?", (request_id,))
