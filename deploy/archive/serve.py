"""Hub's way in: HTTP on a Unix socket in the archive's own folder, which only the owner can open.

Every route reads; none changes anything. No request can name a file: an item is asked for by its id, which must
already be in the index, and the index says where its file is. A photo is sent as a picture made from what Photos
keeps on this Mac, never as its original; a document's own bytes only for a document that is on this Mac.

  GET /health                      {"ok": true, "ready": whether there is an index}
  GET /status                      what the index holds and each source's last run, as counts
  GET /search?q=&sources=&...      what matched, source by source (search.py)
  GET /item/{id}                   one item in full: its facts and its text (with q=, only the passages that match)
  GET /people                      the people Photos knows by name, and who looks like a kid
  GET /onthisday?day=MM-DD         photos from this day in earlier years
  GET /thumb/{id}                  its 400-pixel preview
  GET /photo/{id}?size=1600        a photo at most `size` pixels, from the biggest picture of it on this Mac;
                                   with sharp=1, a 2048-pixel one from Photos itself when it may (sharp.py)
  GET /page/{id}/{n}?size=1600     page n of a PDF, or a picture document, drawn
  GET /file/{id}                   a document's own bytes

Every route takes shared=1, which Hub adds for a login that may see Shared Library photos only: then only those
photos are counted, listed or sent, and anything else is "not in the archive".

Nothing is logged about what was asked: not the words searched for, not the items opened.
"""
import datetime as dt
import http.server
import json
import mimetypes
import os
import re
import socketserver
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from . import macos, photos, search, sharp, store

ID = re.compile(r'[pdcg]:[A-Za-z0-9_-]{6,80}')
DAY = re.compile(r'\d{4}-\d{2}-\d{2}')
KINDS = {'photo', 'video', 'pdf', 'word', 'text', 'sheet', 'slides', 'image', 'chat'}
SORTS = {'newest', 'oldest', 'relevance', 'best'}
SOCKET_PATH_MOST = 103          # macOS's limit on a socket's path
DRAWING = threading.BoundedSemaphore(3)     # pictures made at once; the rest wait their turn


class NotFound(Exception):
    pass


class Bad(Exception):
    pass


def _one(query, name, default=None):
    values = query.get(name)
    return values[-1] if values else default


def _number(query, name, default, low, high):
    try:
        value = int(_one(query, name, default))
    except (TypeError, ValueError):
        raise Bad(f'{name} must be a number') from None
    if not low <= value <= high:
        raise Bad(f'{name} must be from {low} to {high}')
    return value


def _list(query, name, allowed=None):
    found = [v.strip() for value in query.get(name, []) for v in value.split(',') if v.strip()]
    if allowed is not None and not set(found) <= allowed:
        raise Bad(f'{name} holds something unknown')
    return found


def search_args(query):
    """The search a query string asks for, checked."""
    args = {'q': [q[:300] for q in query.get('q', [])][:6],
            'sources': _list(query, 'sources', set(search.SOURCES)) or list(search.SOURCES),
            'kind': _list(query, 'kind', KINDS), 'people': [p[:80] for p in query.get('people', [])][:8],
            'anyof': [p[:80] for p in query.get('anyof', [])][:40], 'young': _one(query, 'young') == '1',
            'place': (_one(query, 'place') or '')[:80] or None, 'favorite': _one(query, 'favorite') == '1',
            'months': [int(m) for m in _list(query, 'months') if m.isdigit() and 1 <= int(m) <= 12],
            'sort': _one(query, 'sort'), 'limit': _number(query, 'limit', 24, 1, 200),
            'offset': _number(query, 'offset', 0, 0, 100000), 'prefix': _one(query, 'prefix') == '1',
            'shared': _one(query, 'shared') == '1'}
    for edge in ('after', 'before'):
        value = _one(query, edge)
        if value and not DAY.fullmatch(value):
            raise Bad(f'{edge} is YYYY-MM-DD')
        args[edge] = value
    if args['sort'] is not None and args['sort'] not in SORTS:
        raise Bad('sort is newest, oldest, relevance or best')
    return args


def _row(conn, ident, source=None, shared=False):
    """An item by its id, or NotFound; with `shared`, only a photo in the Shared Library counts as found."""
    if not ID.fullmatch(ident or ''):
        raise NotFound()
    row = conn.execute('select * from items where id = ?', (ident,)).fetchone()
    if row is None or (source and row['source'] != source) or (shared and not (row['source'] == 'photos' and row['shared'])):
        raise NotFound()
    return row


def _picture(data):
    """A picture's content type, from its first bytes."""
    return 'image/png' if data[:4] == b'\x89PNG' else 'image/jpeg'


def _open(path):
    """A file's bytes, never through a link put where the file was."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as file:
        return file.read()


def answer(path, query, conn):
    """(status, content type, body, extra headers) for one request. `conn` is a read-only view of the index."""
    parts = [urllib.parse.unquote(p) for p in path.strip('/').split('/') if p]
    route = parts[0] if parts else ''
    shared = _one(query, 'shared') == '1'          # Hub asks this for a login that may see Shared Library photos only
    if route == 'search' and len(parts) == 1:
        started = time.monotonic()
        found = search.search(conn, **search_args(query))
        found['ms'] = int((time.monotonic() - started) * 1000)
        return 200, 'application/json', found, {}
    if route == 'item' and len(parts) == 2:
        found = search.item(conn, parts[1] if ID.fullmatch(parts[1]) else '', q=[w[:300] for w in query.get('q', [])][:6], shared=shared)
        if found is None:
            raise NotFound()
        return 200, 'application/json', found, {}
    if route == 'people' and len(parts) == 1:
        return 200, 'application/json', {'people': search.people(conn, shared)}, {}
    if route == 'onthisday' and len(parts) == 1:
        today = dt.date.today()
        day = _one(query, 'day') or today.strftime('%m-%d')
        if not re.fullmatch(r'\d{2}-\d{2}', day):
            raise Bad('day is MM-DD')
        year = _number(query, 'year', today.year, 1900, 3000)
        return 200, 'application/json', {'day': day, 'years': search.on_this_day(conn, day, year, shared=shared)}, {}
    if route == 'status' and len(parts) == 1:
        return 200, 'application/json', {'counts': search.counts(conn, shared), 'runs': store.status(conn),
                                         'people': conn.execute('select count(*) from people').fetchone()[0],
                                         'kids': conn.execute('select count(*) from people where kid = 1').fetchone()[0],
                                         'sharp': sharp.state(),
                                         'bytes': store.database().stat().st_size}, {}
    if route == 'thumb' and len(parts) == 2:
        row = _row(conn, parts[1], shared=shared)
        where = store.thumb_path(row['id'])
        if not where.exists() and row['source'] == 'photos':
            with DRAWING:
                photos.make_thumb({'id': row['id'], 'meta': json.loads(row['meta'] or '{}')})
        try:
            data = _open(where)
        except FileNotFoundError:
            raise NotFound() from None
        return 200, _picture(data), data, {}
    if route == 'photo' and len(parts) == 2:
        row = _row(conn, parts[1], 'photos', shared=shared)
        size = _number(query, 'size', 1600, 200, 2400)
        if _one(query, 'sharp') == '1' and row['kind'] == 'photo':
            got = sharp.get(row['id'], row['id'][2:])          # from Photos itself, by way of iCloud, when it may
            if got:
                return 200, _picture(got), got, {'X-Sharp': '1'}
        if not row['path']:
            raise NotFound()
        with DRAWING:
            made = macos.picture(row['path'], size, 0.82)
        if not made:
            raise NotFound()
        return 200, _picture(made[0]), made[0], {'X-Width': str(made[1]), 'X-Height': str(made[2]), 'X-Sharp': '0'}
    if route == 'page' and len(parts) == 3:
        row = _row(conn, parts[1], 'documents', shared=shared)
        number = int(parts[2]) if parts[2].isdigit() else 0
        size = _number(query, 'size', 1600, 200, 2400)
        if not row['local'] or number < 1:
            raise NotFound()
        with DRAWING:
            if row['kind'] == 'pdf':
                made = macos.pdf_page(row['path'], number, size, 0.82)
            elif row['kind'] == 'image' and number == 1:
                made = macos.picture(row['path'], size, 0.82)
            else:
                made = None
        if not made:
            raise NotFound()
        return 200, _picture(made[0]), made[0], {'X-Width': str(made[1]), 'X-Height': str(made[2])}
    if route == 'file' and len(parts) == 2:
        row = _row(conn, parts[1], 'documents', shared=shared)
        file = Path(row['path'] or '')
        if not row['local'] or not file.is_file():
            raise NotFound()
        try:
            data = _open(file)
        except OSError:
            raise NotFound() from None
        name = file.name
        kind = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        disposition = "attachment; filename*=UTF-8''" + urllib.parse.quote(name)
        return 200, kind, data, {'Content-Disposition': disposition}
    raise NotFound()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'HubArchive'
    sys_version = ''
    protocol_version = 'HTTP/1.1'
    timeout = 60

    def address_string(self):
        return 'hub'

    def log_message(self, form, *args):      # what was asked is never written down
        return

    def _send(self, status, kind, body, headers=None):
        data = json.dumps(body, ensure_ascii=False).encode() if kind == 'application/json' else body
        self.send_response(status)
        self.send_header('Content-Type', kind + ('; charset=utf-8' if kind == 'application/json' else ''))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        if self.close_connection:
            self.send_header('Connection', 'close')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 - the name http.server calls
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query, max_num_fields=200)
        if url.path == '/health':
            return self._send(200, 'application/json', {'ok': True, 'ready': store.database().exists()})
        if not store.database().exists():
            return self._send(503, 'application/json', {'error': 'The archive has not been indexed yet.'})
        conn = None
        try:
            conn = store.connect(read_only=True)
            status, kind, body, headers = answer(url.path, query, conn)
            self._send(status, kind, body, headers)
        except NotFound:
            self._send(404, 'application/json', {'error': 'Not in the archive.'})
        except Bad as error:
            self._send(400, 'application/json', {'error': str(error)})
        except Exception as error:  # noqa: BLE001 - the kind of failure, never what was asked
            print(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {type(error).__name__}', file=sys.stderr, flush=True)
            self._send(500, 'application/json', {'error': type(error).__name__})
        finally:
            if conn is not None:
                conn.close()

    def do_POST(self):  # noqa: N802
        self.close_connection = True            # whatever was sent with it is not read
        self._send(405, 'application/json', {'error': 'The archive only answers questions.'})

    do_PUT = do_DELETE = do_PATCH = do_POST


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        kind = sys.exc_info()[0]
        if kind and not issubclass(kind, (BrokenPipeError, ConnectionResetError)):
            print(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {kind.__name__}', file=sys.stderr, flush=True)


def serve(path=None):
    """Answer on the socket until stopped."""
    path = Path(path or store.socket_path())
    if len(str(path)) > SOCKET_PATH_MOST:
        raise SystemExit(f'The socket path is longer than macOS allows ({SOCKET_PATH_MOST} characters).')
    store.owner_folder(path.parent)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    before = os.umask(0o177)
    try:
        server = Server(str(path), Handler)
    finally:
        os.umask(before)
    os.chmod(path, 0o600)
    print(f'{time.strftime("%Y-%m-%d %H:%M:%S")} serving', file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return server
