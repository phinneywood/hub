"""Where the archive keeps what it derives, and the index itself: one SQLite file with full-text search.

Everything here is owner-only (folders 0700, files 0600) and stays on this Mac. The sources are only ever read;
the index holds what was read from them, and removing a row or a preview never touches an original.

The index (archive.db, WAL, so Hub's searches never wait on a nightly run):
  items         one row per photo, video, document or conversation: what it is, when, where, and a few facts
  parts         its searchable text: a head (title, file name, place, people, labels...) and a body (a page's
                text in about 1,000 characters, one message, the text Photos found in a photo)
  fts           FTS5 over parts (porter stemming, accents ignored), kept in step by triggers
  people        the people Photos knows by name, how many photos each is in, and whether they look like a kid
  item_people   which photos each person is in
  looked        pictures read for text that held none, so they aren't read again until they change
  status        each source's last run: counts, time taken, the kinds of error met
"""
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

SCHEMA = """
create table if not exists items(
  id text primary key, source text not null, kind text not null, title text not null default '',
  at real, day text, until real, place text not null default '', folder text not null default '',
  pages integer, size integer, duration real, w integer, h integer,
  favorite integer not null default 0, shared integer not null default 0, screenshot integer not null default 0,
  young integer not null default 0, local integer not null default 1, score real, thumb integer not null default 0,
  path text, sig text, meta text not null default '{}', seen real);
create index if not exists items_when on items(source, at);
create index if not exists items_day on items(source, day);
create table if not exists people(
  id integer primary key, name text not null unique collate nocase, photos integer not null default 0,
  kid integer not null default 0, last real);
create table if not exists item_people(item text not null, person integer not null, primary key(item, person)) without rowid;
create index if not exists item_people_person on item_people(person, item);
create table if not exists parts(
  id integer primary key, item text not null, n integer not null default 0, page integer, role text, at real,
  head text not null default '', body text not null default '');
create index if not exists parts_item on parts(item, n);
create virtual table if not exists fts using fts5(head, body, content='parts', content_rowid='id',
  tokenize='porter unicode61 remove_diacritics 2');
create trigger if not exists parts_added after insert on parts begin
  insert into fts(rowid, head, body) values (new.id, new.head, new.body);
end;
create trigger if not exists parts_removed after delete on parts begin
  insert into fts(fts, rowid, head, body) values ('delete', old.id, old.head, old.body);
end;
create table if not exists looked(id text primary key, sig text not null);
create table if not exists status(
  source text primary key, items integer not null default 0, started real, finished real, ok real, seconds real,
  errors text not null default '{}', note text not null default '{}');
"""
ITEM_FIELDS = ('id', 'source', 'kind', 'title', 'at', 'day', 'until', 'place', 'folder', 'pages', 'size', 'duration',
               'w', 'h', 'favorite', 'shared', 'screenshot', 'young', 'local', 'score', 'thumb', 'path', 'sig', 'meta',
               'seen')


def home():
    """The archive's own folder."""
    return Path(os.environ.get('HUB_ARCHIVE_DIR') or Path.home() / 'Library' / 'Application Support' / 'HubArchive')


def database():
    return home() / 'archive.db'


def socket_path():
    return home() / 'archive.sock'


def owner_folder(path):
    """Make a folder only the owner can open, and any folders above it that are missing, and return it."""
    path = Path(path)
    missing = []
    for above in (path, *path.parents):
        if above.exists():
            break
        missing.append(above)
    for folder in reversed(missing):        # one at a time: mkdir(parents=True) would leave the upper ones 0755
        folder.mkdir(mode=0o700, exist_ok=True)
        folder.chmod(0o700)
    if path.stat().st_mode & 0o077:
        path.chmod(0o700)
    return path


def write_bytes(path, data):
    """Write a file so a reader never sees half of it, owner-only."""
    path = Path(path)
    owner_folder(path.parent)
    with tempfile.NamedTemporaryFile('wb', dir=path.parent, delete=False, suffix='.tmp') as out:
        out.write(data)
    os.chmod(out.name, 0o600)
    os.replace(out.name, path)


def write_json(path, value):
    write_bytes(path, json.dumps(value, indent=1, ensure_ascii=False).encode())


def read_json(path, default):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def thumb_path(item):
    """Where an item's preview is kept: spread over 256 folders so none holds tens of thousands of files."""
    spread = hashlib.sha1(item.encode()).hexdigest()[:2]
    return home() / 'thumbs' / spread / (item.replace(':', '-') + '.jpg')


def connect(read_only=False):
    """The index. Read-only connections are what the service answers from; they never create anything."""
    path = database()
    if read_only:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10, check_same_thread=False)
    else:
        owner_folder(path.parent)
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        if path.stat().st_mode & 0o077:
            path.chmod(0o600)
        conn = sqlite3.connect(path, timeout=60)
        conn.execute('pragma journal_mode=wal')
        conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    conn.execute('pragma busy_timeout=10000')
    return conn


def known(conn, source):
    """{id: sig} for every item of a source the index holds, to leave unchanged ones alone."""
    return {r['id']: r['sig'] for r in conn.execute('select id, sig from items where source = ?', (source,))}


def put(conn, item, parts=(), people=None):
    """Add or replace one item with its searchable parts and, for a photo, the people in it."""
    row = {name: item.get(name) for name in ITEM_FIELDS}
    row['meta'] = json.dumps(item.get('meta') or {}, ensure_ascii=False)
    for flag in ('favorite', 'shared', 'screenshot', 'young', 'thumb'):
        row[flag] = int(bool(row[flag]))
    row['local'] = int(item.get('local', True))
    row['title'] = row['title'] or ''
    row['place'] = row['place'] or ''
    row['folder'] = row['folder'] or ''
    names = ', '.join(ITEM_FIELDS)
    conn.execute(f'insert or replace into items({names}) values ({", ".join("?" * len(ITEM_FIELDS))})',
                 tuple(row[name] for name in ITEM_FIELDS))
    conn.execute('delete from parts where item = ?', (item['id'],))
    conn.executemany('insert into parts(item, n, page, role, at, head, body) values (?,?,?,?,?,?,?)',
                     [(item['id'], n, p.get('page'), p.get('role'), p.get('at'), p.get('head') or '', p.get('body') or '')
                      for n, p in enumerate(parts)])
    if people is not None:
        conn.execute('delete from item_people where item = ?', (item['id'],))
        for name in dict.fromkeys(people):
            conn.execute('insert or ignore into people(name) values (?)', (name,))
            conn.execute('insert or ignore into item_people(item, person) select ?, id from people where name = ?',
                         (item['id'], name))


def seen(conn, ids, when):
    """Mark unchanged items as still there."""
    conn.executemany('update items set seen = ? where id = ?', [(when, i) for i in ids])


def forget(conn, ids):
    """Remove items the sources no longer have: their rows and previews, never anything of the sources'."""
    for item in ids:
        conn.execute('delete from parts where item = ?', (item,))
        conn.execute('delete from item_people where item = ?', (item,))
        conn.execute('delete from items where id = ?', (item,))
        try:
            thumb_path(item).unlink()
        except FileNotFoundError:
            pass


def set_status(conn, source, **values):
    conn.execute('insert or ignore into status(source) values (?)', (source,))
    for name, value in values.items():
        if name in ('errors', 'note'):
            value = json.dumps(value, ensure_ascii=False)
        conn.execute(f'update status set {name} = ? where source = ?', (value, source))


def status(conn):
    """Each source's last run, as counts and times."""
    out = {}
    for row in conn.execute('select * from status order by source'):
        out[row['source']] = {'items': row['items'], 'started': row['started'], 'finished': row['finished'],
                              'ok': row['ok'], 'seconds': row['seconds'], 'errors': json.loads(row['errors'] or '{}'),
                              'note': json.loads(row['note'] or '{}')}
    return out
