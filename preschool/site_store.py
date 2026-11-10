"""Published class-topic pages, separate from either mailbox's source catalog."""
import json
import os
import re
import sqlite3
import time
from contextlib import closing

from .support import config

SCHEMA = """
create table if not exists pages(
  slug text primary key, title text not null, body text not null,
  revision integer not null, updated real not null, source_keys text not null);
create table if not exists page_history(
  slug text not null, revision integer not null, title text not null,
  body text not null, updated real not null, source_keys text not null,
  primary key(slug,revision));
create table if not exists class_timetables(
  slug text primary key, payload text not null, revision integer not null, updated real not null);
create table if not exists timetable_history(
  slug text not null, revision integer not null, payload text not null, updated real not null,
  primary key(slug,revision));
create table if not exists family_previews(
  revision integer primary key, fingerprint text not null, updated real not null,
  payload text not null);
create table if not exists family_calendar(
  uid text primary key, sequence integer not null, created real not null,
  updated real not null, payload text not null);
create table if not exists family_event_keys(
  source_key text primary key, uid text not null);
"""


class RevisionConflict(ValueError):
    pass


def db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = config.DATA_DIR / 'preschool-site.db'
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def record(row):
    if row is None:
        return None
    value = dict(row)
    value['source_keys'] = json.loads(value['source_keys'])
    return value


def pages():
    with closing(db()) as conn:
        return {row['slug']: record(row) for row in conn.execute('select * from pages')}


def history(slug=None):
    with closing(db()) as conn:
        return [record(row) for row in conn.execute(
            'select * from page_history where (? is null or slug=?) order by updated desc, revision desc', (slug, slug))]


def save(slug, title, body, source_keys, expected_revision):
    if not re.fullmatch(r'[a-z-]{2,40}', slug) or not title.strip() or len(title) > 200:
        raise ValueError('Choose a valid class topic.')
    if not body.strip() or len(body) > 20_000:
        raise ValueError('Enter page text, up to 20,000 characters.')
    keys = sorted(set(source_keys))
    if len(keys) > 500 or any(not re.fullmatch(r'[a-f0-9]{64}', key) for key in keys):
        raise ValueError('The supporting source references are invalid.')
    with closing(db()) as conn, conn:
        conn.execute('begin immediate')
        current = conn.execute('select revision from pages where slug=?', (slug,)).fetchone()
        revision = current['revision'] if current else 0
        if revision != expected_revision:
            raise RevisionConflict('This page changed while you were editing. Reopen the editor before saving.')
        values = (slug, title.strip(), body.strip(), revision + 1, time.time(), json.dumps(keys))
        conn.execute('insert into page_history(slug,title,body,revision,updated,source_keys) values(?,?,?,?,?,?)', values)
        conn.execute('insert into pages values(?,?,?,?,?,?) on conflict(slug) do update set '
                     'title=excluded.title,body=excluded.body,revision=excluded.revision,updated=excluded.updated,source_keys=excluded.source_keys', values)
    return revision + 1


def seed_topics(topics):
    """Explicit deployment operation; never overwrite an already maintained page."""
    from .website import page_text
    count=0
    for topic in topics:
        if topic['article'] or not topic['supporting_facts']:
            continue
        try:
            save(topic['slug'],topic['title'],page_text(topic),
                 [source['view_key'] for source in topic['sources']],expected_revision=0)
        except RevisionConflict:
            continue
        count+=1
    return count


def timetables():
    with closing(db()) as conn:
        return [{**dict(row),'payload':json.loads(row['payload'])} for row in conn.execute('select * from class_timetables order by slug')]


def save_timetable(slug,payload,expected_revision):
    from .schedule import validate
    validate(payload)
    if not re.fullmatch(r'[a-z-]{2,40}',slug):raise ValueError('Invalid class timetable name.')
    with closing(db()) as conn,conn:
        conn.execute('begin immediate')
        row=conn.execute('select revision from class_timetables where slug=?',(slug,)).fetchone()
        revision=row['revision'] if row else 0
        if revision!=expected_revision:raise RevisionConflict('The class timetable changed; review it before saving.')
        stamp=time.time();serialized=json.dumps(payload)
        conn.execute('insert into timetable_history values(?,?,?,?)',(slug,revision+1,serialized,stamp))
        conn.execute('insert into class_timetables values(?,?,?,?) on conflict(slug) do update set payload=excluded.payload,revision=excluded.revision,updated=excluded.updated',
                     (slug,serialized,revision+1,stamp))
    return revision+1
