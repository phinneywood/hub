"""Local preschool catalog. Original message bodies are not stored."""
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from contextvars import ContextVar

from .support import config

_account = ContextVar('preschool_account', default='personal')


def current_account():
    return _account.get()


@contextmanager
def account(name):
    if name not in ('personal', 'shared'):
        raise ValueError('Invalid preschool account')
    token = _account.set(name)
    try:
        yield
    finally:
        _account.reset(token)

SCHEMA = """
create table if not exists settings(key text primary key, value text not null);
create table if not exists senders(address text primary key);
create table if not exists messages(
  id text primary key, sender text not null, subject text not null,
  sent text not null, processed real not null, has_attachments integer not null, criteria_version text);
create table if not exists items(
  id integer primary key autoincrement, message_id text not null references messages(id),
  kind text not null, title text not null, details text not null,
  date text, time text, location text, url text, uncertainty text not null,
  source_quote text not null, dismissed integer not null default 0);
create table if not exists candidates(
  id text primary key, sender text not null, subject text not null, sent text not null,
  snippet text not null, classification text not null, discovered real not null,
  state text not null default 'new', relevance_score real, usefulness_score real,
  decision_model text, decision_at real, criteria_version text, review_reason text);
create table if not exists school_calendar(
  id text primary key, date_text text not null, date_iso text, event text not null,
  start_time text not null, end_time text not null, location text not null,
  updated real not null, active integer not null default 1);
create table if not exists session_reviews(
  message_id text primary key, disposition text not null, reason text not null,
  reviewed real not null, criteria_version text not null);
create table if not exists message_audiences(
  message_id text primary key, scope text not null, reason text not null,
  policy text not null, binding_hash text not null, checked real not null);
"""


def db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = config.DATA_DIR / ("preschool-shared.db" if current_account() == 'shared' else "preschool.db")
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys=on")
    conn.executescript(SCHEMA)
    candidate_columns = {r['name'] for r in conn.execute('pragma table_info(candidates)')}
    for name, declaration in {'relevance_score':'real', 'usefulness_score':'real',
                              'decision_model':'text', 'decision_at':'real', 'criteria_version':'text',
                              'review_reason':'text'}.items():
        if name not in candidate_columns:
            try:
                conn.execute(f'alter table candidates add column {name} {declaration}')
            except sqlite3.OperationalError as exc:
                if 'duplicate column name' not in str(exc).lower():
                    raise
    columns = {r['name'] for r in conn.execute('pragma table_info(messages)')}
    if 'criteria_version' not in columns:
        try:
            conn.execute('alter table messages add column criteria_version text')
        except sqlite3.OperationalError as exc:
            if 'duplicate column name' not in str(exc).lower():
                raise
    if 'attachment_status' not in columns:
        try:
            conn.execute("alter table messages add column attachment_status text not null default ''")
        except sqlite3.OperationalError as exc:
            if 'duplicate column name' not in str(exc).lower():
                raise
    return conn


def senders():
    with closing(db()) as conn:
        return [r[0] for r in conn.execute("select address from senders order by address")]


def add_sender(address):
    with closing(db()) as conn, conn:
        conn.execute("insert or ignore into senders values (?)", (address,))


def remove_sender(address):
    with closing(db()) as conn, conn:
        conn.execute("delete from senders where address=?", (address,))


def mailbox_matches(mailbox):
    with closing(db()) as conn:
        row = conn.execute("select value from settings where key='mailbox'").fetchone()
        return row is None or row[0] == mailbox.lower()


def processed(message_id):
    with closing(db()) as conn:
        return conn.execute("select 1 from messages where id=?", (message_id,)).fetchone() is not None


def save_message(mailbox, message, items):
    """Commit the complete extraction together; never overwrite edits."""
    from .criteria import VERSION
    with closing(db()) as conn, conn:
        conn.execute("insert or ignore into settings values ('mailbox', ?)", (mailbox.lower(),))
        bound = conn.execute("select value from settings where key='mailbox'").fetchone()[0]
        if bound != mailbox.lower():
            raise ValueError("This catalog belongs to a different mailbox.")
        conn.execute("insert into messages(id, sender, subject, sent, processed, has_attachments, attachment_status, criteria_version) values (?, ?, ?, ?, ?, ?, ?, ?)",
                     (message['id'], message['sender'], message['subject'], message['date'], time.time(),
                      int(message['has_attachments']), message.get('attachment_status', ''), VERSION))
        for item in items:
            conn.execute("insert into items(message_id, kind, title, details, date, time, location, url, uncertainty, source_quote) "
                         "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (message['id'], *(item[k] for k in ('kind', 'title', 'details', 'date', 'time', 'location',
                                                            'url', 'uncertainty', 'source_quote'))))
        conn.execute("update candidates set state='processed' where id=?", (message['id'],))
        _save_audience(conn,message['id'],message.get('sharing_evidence'))


def save_session_message(mailbox,message,items,partial=False):
    """Explicit session-reviewed local import; append facts, never overwrite edits.

    The caller verifies fresh source quotes. This function makes no provider calls
    and does not invent Jev scores or change automatic processing settings.
    """
    from .criteria import VERSION
    fields=('kind','title','details','date','time','location','url','uncertainty','source_quote')
    with closing(db()) as conn,conn:
        conn.execute('begin immediate')
        conn.execute("insert or ignore into settings values ('mailbox',?)",(mailbox.lower(),))
        if conn.execute("select value from settings where key='mailbox'").fetchone()[0]!=mailbox.lower():
            raise ValueError('Session source belongs to another mailbox.')
        existing=conn.execute('select sender from messages where id=?',(message['id'],)).fetchone()
        if existing and existing['sender']!=message['sender']:raise ValueError('Session source sender changed.')
        conn.execute('insert or ignore into messages(id,sender,subject,sent,processed,has_attachments,attachment_status,criteria_version) values(?,?,?,?,?,?,?,?)',
            (message['id'],message['sender'],message['subject'],message['date'],time.time(),int(message['has_attachments']),message.get('attachment_status','Session-reviewed source.'),VERSION))
        ids=[]
        for item in items:
            values=tuple(item[field] for field in fields)
            match=conn.execute('select id from items where message_id=? and '+' and '.join(field+' is ?' for field in fields),
                               (message['id'],*values)).fetchone()
            if match:ids.append(match['id']);continue
            cursor=conn.execute('insert into items(message_id,'+','.join(fields)+') values('+','.join('?' for _ in range(10))+')',
                                (message['id'],*values))
            ids.append(cursor.lastrowid)
        conn.execute("update candidates set state=?,criteria_version=?,decision_model='interactive-session',decision_at=?,relevance_score=null,usefulness_score=null,review_reason=? where id=?",
            ('needs_review' if partial else 'processed',VERSION,time.time(),'source_unavailable' if partial else None,message['id']))
        conn.execute('insert or replace into session_reviews values(?,?,?,?,?)',
            (message['id'],'included_partial' if partial else 'included','source_review',time.time(),VERSION))
        _save_audience(conn,message['id'],message.get('sharing_evidence'))
    return ids


def all_items(include_dismissed=False):
    with closing(db()) as conn:
        rows = conn.execute("select i.*, m.subject, m.sender, m.sent, m.processed as saved_at, m.has_attachments, m.attachment_status, m.criteria_version, "
                            "a.scope as sharing_audience,a.reason as audience_reason,a.policy as audience_policy,a.binding_hash as audience_binding_hash from items i "
                            "join messages m on m.id=i.message_id "
                            "left join message_audiences a on a.message_id=m.id "
                            "where (? or i.dismissed=0) order by (i.date is null), i.date, i.id desc",
                            (int(include_dismissed),)).fetchall()
        return [dict(row) for row in rows]


def _save_audience(conn,message_id,result):
    import re
    from . import audience
    # Missing fresh evidence cannot leave an earlier class decision attached to
    # newly imported facts. Existing source records remain intact.
    if result is None:
        result=audience.classify({})
    if (not isinstance(result,dict) or set(result)!={'scope','reason','policy','binding_hash'} or
            any(not isinstance(value,str) for value in result.values()) or result['scope'] not in ('class','private','unverified') or
            not re.fullmatch(r'[a-z_]{2,80}',result['reason']) or not re.fullmatch(r'\d{1,3}',result['policy']) or
            not re.fullmatch(r'[a-f0-9]{64}',result['binding_hash'])):
        raise ValueError('Invalid deterministic sharing evidence.')
    conn.execute('insert or replace into message_audiences values(?,?,?,?,?,?)',
        (message_id,result['scope'],result['reason'],result['policy'],result['binding_hash'],time.time()))


def save_audience(message_id,result):
    with closing(db()) as conn,conn:
        if not conn.execute('select 1 from messages where id=?',(message_id,)).fetchone():
            raise ValueError('Sharing evidence requires an existing source message.')
        _save_audience(conn,message_id,result)


def get_item(item_id):
    with closing(db()) as conn:
        row = conn.execute("select * from items where id=?", (item_id,)).fetchone()
        return dict(row) if row else None


def update_item(item_id, item):
    with closing(db()) as conn, conn:
        conn.execute("update items set kind=?, title=?, details=?, date=?, time=?, location=?, uncertainty=? where id=?",
                     (*(item[k] for k in ('kind', 'title', 'details', 'date', 'time', 'location', 'uncertainty')), item_id))


def dismiss(item_id, dismissed):
    with closing(db()) as conn, conn:
        conn.execute("update items set dismissed=? where id=?", (int(dismissed), item_id))


def message_count():
    with closing(db()) as conn:
        return conn.execute("select count(*) from messages").fetchone()[0]


def messages():
    with closing(db()) as conn:
        return [dict(row) for row in conn.execute("select m.*, count(i.id) as item_count from messages m "
                                                   "left join items i on i.message_id=m.id "
                                                   "group by m.id order by m.processed desc")]


def setting(key, default=None):
    with closing(db()) as conn:
        row = conn.execute('select value from settings where key=?', (key,)).fetchone()
        return row[0] if row else default


def set_setting(key, value):
    with closing(db()) as conn, conn:
        conn.execute('insert into settings values (?, ?) on conflict(key) do update set value=excluded.value', (key, str(value)))


def add_candidates(mailbox, messages):
    with closing(db()) as conn, conn:
        conn.execute("insert or ignore into settings values ('mailbox', ?)", (mailbox.lower(),))
        if conn.execute("select value from settings where key='mailbox'").fetchone()[0] != mailbox.lower():
            raise ValueError('The Gmail mailbox changed.')
        for m in messages:
            conn.execute('insert or ignore into candidates(id, sender, subject, sent, snippet, classification, discovered) '
                         'values (?, ?, ?, ?, ?, ?, ?)',
                         (m['id'], m['sender'], m['subject'], m['sent'], m['snippet'], m['classification'], time.time()))


def candidates(state='new'):
    with closing(db()) as conn:
        return [dict(row) for row in conn.execute('select * from candidates where state=? order by discovered desc, id desc', (state,))]


def candidate_counts():
    with closing(db()) as conn:
        return {row['state']: row['n'] for row in conn.execute('select state, count(*) as n from candidates group by state')}


def reopen_stale_decisions(version):
    """Return nonterminal Jev outcomes from older evidence policies for fresh screening."""
    with closing(db()) as conn, conn:
        return conn.execute("update candidates set state='new', relevance_score=null, usefulness_score=null, "
                            "decision_model=null, decision_at=null, criteria_version=null "
                            "where state in ('ready','filtered') and (criteria_version is null or criteria_version<>?)",
                            (version,)).rowcount


def candidate(mid):
    with closing(db()) as conn:
        row = conn.execute('select * from candidates where id=?', (mid,)).fetchone()
        return dict(row) if row else None


def mark_candidate(mid, state):
    if state not in ('new', 'ready', 'dismissed', 'processed', 'needs_review', 'processing', 'filtered'):
        raise ValueError('Invalid candidate state')
    with closing(db()) as conn, conn:
        conn.execute('update candidates set state=?, review_reason=? where id=?', (state, None, mid))


def claim_candidate(mid):
    with closing(db()) as conn, conn:
        cursor = conn.execute("update candidates set state='processing', review_reason=null where id=? and state in ('new','ready')", (mid,))
        return cursor.rowcount == 1


def needs_review(mid, reason):
    allowed={'jev_uncertain','jev_unavailable','luna_failed','gmail_read_failed','source_unavailable','interrupted',
             'unsupported_attachment','unreadable_attachment','attachment_read_failed','too_many_attachments',
             'source_too_large','source_truncated',
             'off_allowlist','query_requires_review','too_many_resource_links','empty_link_source',
              'extraction_invalid','validation_failed','internal_error','changed_source'}
    if reason not in allowed:
        reason='internal_error'
    with closing(db()) as conn, conn:
        conn.execute("update candidates set state='needs_review', review_reason=? where id=?", (reason,mid))


def save_decision(mid, scores, state):
    from .criteria import VERSION
    if state not in ('ready', 'processing', 'needs_review', 'filtered'):
        raise ValueError('Invalid decision state')
    with closing(db()) as conn, conn:
        conn.execute('update candidates set state=?, relevance_score=?, usefulness_score=?, decision_model=?, decision_at=?, criteria_version=?, review_reason=? where id=?',
                     (state, scores['preschool_relevant'], scores['useful_information'], 'typesafe/jev-1.14', time.time(), VERSION,
                      'jev_uncertain' if state=='needs_review' else None,mid))


def classify_candidate(mid, classification):
    if classification not in ('likely', 'review'):
        raise ValueError('Invalid candidate classification')
    with closing(db()) as conn, conn:
        conn.execute("update candidates set classification=? where id=? and state='new'", (classification, mid))


def recover_candidates():
    with closing(db()) as conn, conn:
        conn.execute("update candidates set state='needs_review', review_reason='interrupted' where state='processing'")


def replace_school_calendar(rows, now):
    with closing(db()) as conn, conn:
        conn.execute('update school_calendar set active=0')
        for row in rows:
            conn.execute('insert into school_calendar(id,date_text,date_iso,event,start_time,end_time,location,updated,active) '
                         'values(?,?,?,?,?,?,?,?,1) on conflict(id) do update set date_text=excluded.date_text, '
                         'date_iso=excluded.date_iso,event=excluded.event,start_time=excluded.start_time,end_time=excluded.end_time, '
                         'location=excluded.location,updated=excluded.updated,active=1',
                         (*(row[k] for k in ('id','date_text','date_iso','event','start_time','end_time','location')), now))
        conn.execute("insert into settings values ('school_sync', ?) on conflict(key) do update set value=excluded.value", (str(now),))


def school_events():
    with closing(db()) as conn:
        return [dict(row) for row in conn.execute('select * from school_calendar where active=1 order by (date_iso is null), date_iso, event')]
