"""Where errands are kept: one row each in Hub's own database, never in Gmail.

A row holds what the owner sees: the title, one line of why, the facts, the sentence it rests on, and the drafted
ways to finish it. That text comes from mail, so it is blanked KEEP_TEXT_DAYS after the errand ends (or was made)
and the row is deleted after KEEP_DAYS, as sorted mail is (hub/tools/gmail/triage.py).

States: ready (waiting for a tap), opened (the owner opened where it gets finished; Hub is watching for proof),
done, later (hidden until a time), dismissed ("Not this"), missed (its date passed, or it waited too long), skipped
(Hub read it and nothing needed doing; no text is kept). `steps` holds the steps of the chosen way the owner has
taken, as "kind" names (mail, open, call, calendar, list).

Whose errand it is: `account` is the Google account it was read from ("default", the bridge's main account, for
mail), and only those hub/people.py lets see that account see it or tap it. The class guide's to-dos are the
household's (account ''): everyone sees them, and one person's Done closes them for all. A request with no login
(this Mac itself), or with a login Hub doesn't know, sees the household's only.
"""
import json
import time
from contextlib import closing

from ... import config, home, people, store

KEEP_TEXT_DAYS = 14
KEEP_DAYS = 60
OPEN = ('ready', 'opened', 'later')
ENDED = ('done', 'dismissed', 'missed', 'skipped')
HOUSEHOLD = ''                   # the account of an errand that is everyone's: the class guide's to-dos
MAIN = 'default'                 # the bridge's main account (/gmail/*, /calendar/events), as hub/people.py names it

SCHEMA = """
create table if not exists errands(
  id integer primary key, account text not null default '', key text not null, source text not null,
  kind text not null, title text not null default '', why text not null default '', due text,
  facts text not null default '{}', quote text not null default '', choices text not null default '[]',
  message_id text, thread_id text, sender text, sender_name text, subject text, reply_to text, received real,
  state text not null default 'ready', chose integer, steps text not null default '[]', how text,
  until real, undo text, made_day text not null,
  created_at real not null, updated_at real not null, opened_at real, ended_at real, checked_at real,
  unique(account, key));
create index if not exists errands_state on errands(state);
create table if not exists errands_tried(account text not null default '', key text not null, tries integer not null,
  at real not null, primary key(account, key));
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
    _whose(conn)
    conn.executescript(SCHEMA)
    return conn


def _whose(conn):
    """A database made before errands kept whose they are has no `account` column. Rebuild both tables in one
    transaction: the class guide's rows become the household's, every other row the main account's (all mail came from
    it then), and the old global settings become the main account's. A plain added column would have made old mail
    errands everyone's."""
    columns = [r['name'] for r in conn.execute('pragma table_info(errands)')]
    tried = [r['name'] for r in conn.execute('pragma table_info(errands_tried)')]
    if (not columns or 'account' in columns) and (not tried or 'account' in tried):
        return
    conn.execute('begin immediate')
    try:
        if columns and 'account' not in columns:
            conn.execute('alter table errands rename to errands_before')
        if tried and 'account' not in tried:
            conn.execute('alter table errands_tried rename to errands_tried_before')
        for statement in SCHEMA.split(';'):
            if statement.strip() and 'create index' not in statement:
                conn.execute(statement)
        if columns and 'account' not in columns:
            kept = ', '.join(columns)
            conn.execute(f"insert into errands(account, {kept}) select case when source = 'school' then ? else ? end, {kept}"
                         ' from errands_before', (HOUSEHOLD, MAIN))
            conn.execute('drop table errands_before')
        if tried and 'account' not in tried:
            conn.execute("insert into errands_tried(account, key, tries, at) select case when key like 'mail:%' then ? else ? end,"
                         ' key, tries, at from errands_tried_before', (MAIN, HOUSEHOLD))
            conn.execute('drop table errands_tried_before')
        for old, new in (('compose', 'compose:' + MAIN), ('account', 'mailbox:' + MAIN)):
            conn.execute('insert or ignore into errands_settings(key, value) select ?, value from errands_settings where key = ?',
                         (new, old))
            conn.execute('delete from errands_settings where key = ?', (old,))
        conn.execute('commit')
    except BaseException:
        conn.execute('rollback')
        raise


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


def sees(login, errand):
    """Whether this person may see the errand, and tap it: the household's, anyone; one read from a Google account,
    only someone hub/people.py lets see that account."""
    return errand['account'] == HOUSEHOLD or (bool(login) and people.sees(login, errand['account']))


def rows_for(login, where='1', args=()):
    """The errands this person may see, of those `where` picks."""
    return [e for e in rows(where, args) if sees(login, e)]


def accounts_of(login):
    """This person's own accounts, in a steady order (hub/people.py gives a set)."""
    return sorted(people.accounts(login)) if login else []


def mapped():
    """The accounts someone who may sign in owns: the only ones whose mail is read for errands, or watched for proof.
    An account no one owns makes errands no one could see, so it isn't read at all."""
    return {account for login in config.ALLOWED_LOGINS for account in people.accounts(login)}


def keys():
    """Every source item Hub has made an errand from, or read and found nothing to do in: (account, key) pairs, as the
    same email in two people's mailboxes is two errands."""
    with closing(db()) as conn:
        return {(r['account'], r['key']) for r in conn.execute('select account, key from errands')}


def add(key, source, made, *, account=HOUSEHOLD, state='ready', how=None, message=None, now=None):
    """Keep one new errand, read from `account`. `made` is the checked draft (draft.check); `message`: the email it
    came from ({id, thread_id, sender, name, subject, reply_to, received}). Returns its id, or None when one exists for
    the key already."""
    now = now_() if now is None else now
    message = message or {}
    with closing(db()) as conn, conn:
        cursor = conn.execute(
            'insert or ignore into errands(account, key, source, kind, title, why, due, facts, quote, choices, message_id,'
            ' thread_id, sender, sender_name, subject, reply_to, received, state, how, made_day, created_at, updated_at,'
            ' ended_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (account, key, source, made.get('kind') or 'none', made.get('title') or '', made.get('why') or '', made.get('due'),
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


def tried(key, account=HOUSEHOLD, now=None):
    """Count one failed reading of a source item (the model gave nothing usable for it). Returns the count."""
    now = now_() if now is None else now
    with closing(db()) as conn, conn:
        conn.execute('insert into errands_tried(account, key, tries, at) values(?, ?, 1, ?) on conflict(account, key) do update'
                     ' set tries=tries+1, at=excluded.at', (account, key, now))
        return conn.execute('select tries from errands_tried where account=? and key=?', (account, key)).fetchone()['tries']


def tries():
    """{(account, key): failed readings}."""
    with closing(db()) as conn:
        return {(r['account'], r['key']): r['tries'] for r in conn.execute('select account, key, tries from errands_tried')}


def setting(key, default=None):
    with closing(db()) as conn:
        row = conn.execute('select value from errands_settings where key=?', (key,)).fetchone()
    return row['value'] if row else default


def compose(account):
    """Where replies to this account's errands open: 'mail' (the device's mail app) or 'gmail'."""
    found = setting('compose:' + account, 'mail') if account else 'mail'
    return found if found in ('mail', 'gmail') else 'mail'


def mailbox(account):
    """The address of a Google account Hub reads, as its profile gave it, for the pages a tap opens; '' if not known."""
    return setting('mailbox:' + account, '') if account else ''


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
