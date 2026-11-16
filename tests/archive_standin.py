"""A stand-in for the archive program's service (deploy/archive/serve.py), for Hub's tests: the same routes and
answers, from a few made-up items held in memory, given to Hub's own client in this process (no socket: the
installed-package check allows none). Everything on Hub's side is the real code; the archive's real search and
service are tested on their own (tests/test_archive_*.py), and tests/test_app_archive.py checks the two answer
alike.

Everyone and everything here is invented (Alex and Sam, as in tests/archive_world.py).
"""
import re
import time
import urllib.parse
from pathlib import Path

import httpx

from tests.archive_world import INVOICE, pdf, swatch

STOP = {'the', 'a', 'an', 'of', 'at', 'in', 'on', 'and', 'or', 'to', 'for', 'with', 'from'}


def _photo(n, day, people=(), words='', place='', kind='photo', young=False, score=.5):
    return {'id': f'p:{n:08X}-1111-4000-8000-000000000000', 'source': 'photos', 'kind': kind, 'title': '', 'day': day,
            'at': time.mktime(time.strptime(day + ' 12', '%Y-%m-%d %H')) + n, 'place': place, 'people': list(people),
            'words': words, 'young': young, 'score': score, 'w': 64, 'h': 48, 'favorite': False, 'shared': False, 'thumb': True}


PHOTOS = ([_photo(n, '2024-07-13', ['Alex', 'Sam'] if n % 2 else ['Sam'], 'beach ocean sand', 'Seaside Cove', score=.4 + n / 20)
           for n in range(1, 7)]
          + [_photo(7, '2024-07-13', ['Sam'], 'beach ocean video', 'Seaside Cove', kind='video'),
             _photo(8, '2024-08-02', ['Sam'], 'beach kite', 'Seaside Cove', young=True),
             _photo(9, '2024-08-02', [], 'beach kite', 'Seaside Cove', young=True),
             _photo(10, '2025-10-03', [], 'document furnace service example heating'),
             _photo(13, '2023-11-16', ['Alex', 'Sam'], 'birthday cake', 'Example City', score=.9),
             _photo(14, '2022-11-16', ['Alex'], 'park', 'Example City')])
DOCUMENTS = [
    {'id': 'd:00000000000000000000aa01', 'source': 'documents', 'kind': 'pdf', 'title': 'Furnace service invoice.pdf',
     'day': '2025-10-03', 'folder': 'Documents/Home', 'pages': 1, 'local': True, 'thumb': True, 'ext': '.pdf',
     'text': [(1, '\n'.join(INVOICE))]},
    {'id': 'd:00000000000000000000aa02', 'source': 'documents', 'kind': 'text', 'title': 'Paint.md', 'day': '2026-02-11',
     'folder': 'Documents/Home', 'pages': None, 'local': True, 'thumb': True, 'ext': '.md',
     'text': [(None, 'The hall is Example Grey. The kitchen is Morning Fog.')]},
    {'id': 'd:00000000000000000000aa03', 'source': 'documents', 'kind': 'pdf', 'title': 'Field trip form.pdf', 'day': '2026-04-20',
     'folder': 'iCloud Drive/School', 'pages': 1, 'local': True, 'thumb': True, 'ext': '.pdf',
     'text': [(1, 'Field trip to the aquarium. Bring a packed lunch.')]}]
CHATS = [
    {'id': 'c:c0ffee00-0000-4000-8000-000000000001', 'source': 'chats', 'kind': 'chat', 'title': 'Furnace filter sizes',
     'day': '2026-03-02', 'attachments': ['filter notes.md', 'Warranty.pdf'], 'messages': [
         (1, 'user', 'What size furnace filter fits a 16x25 return?'),
         (2, 'assistant', 'A 16x25x1 filter fits. Replace it every three months.'), (3, 'user', 'Thanks, ordering MERV 8.')]},
    {'id': 'c:c0ffee00-0000-4000-8000-000000000002', 'source': 'chats', 'kind': 'chat', 'title': 'Beach packing list',
     'day': '2026-03-03', 'attachments': [], 'messages': [
         (1, 'user', 'What should we pack for the beach with a kid?'), (2, 'assistant', 'Pack sunscreen, towels, a kite and snacks.')]}]
PEOPLE = [{'name': 'Sam', 'photos': 10, 'kid': True, 'last': 0}, {'name': 'Alex', 'photos': 4, 'kid': False, 'last': 0}]
PUBLIC = ('id', 'source', 'kind', 'title', 'at', 'day', 'until', 'place', 'folder', 'pages', 'size', 'duration', 'w', 'h',
          'favorite', 'shared', 'local', 'thumb')
MARK, END = '\x02', '\x03'


def _words(text):
    return [w for w in re.findall(r'\w+', text.lower()) if w not in STOP]


def _text(item):
    if item['source'] == 'photos':
        return ' '.join([item['words'], item['place'], *item['people']])
    if item['source'] == 'documents':
        return ' '.join([item['title'], item['folder'], *(t for _, t in item['text'])])
    return ' '.join([item['title'], *item['attachments'], *(t for _, _, t in item['messages'])])


def _hit(item, searches):
    """(matched, page, snippet) for the first search all of whose words are in the item."""
    have = _text(item).lower()
    for search in searches:
        words = _words(search)
        if words and all(re.search(r'\b' + re.escape(w[:5]), have) for w in words):
            pieces = item.get('text') or [(n, t) for n, _, t in item.get('messages', [])] or [(None, item.get('title', ''))]
            page, body = next(((p, t) for p, t in pieces if any(w[:5] in t.lower() for w in words)), pieces[0])
            snippet = body[:200]
            for w in words:
                snippet = re.sub(r'(?i)\b(' + re.escape(w) + r')', MARK + r'\1' + END, snippet, count=1)
            return True, page, snippet
    return False, None, ''


def search(query):
    one = lambda name, default=None: (query.get(name) or [default])[-1]  # noqa: E731
    sources = [s for v in query.get('sources', ['photos,documents,chats']) for s in v.split(',') if s]
    q = query.get('q', [])
    people, anyof, young = query.get('people', []), query.get('anyof', []), one('young') == '1'
    after, before, place = one('after'), one('before'), one('place')
    months = [int(m) for v in query.get('months', []) for m in v.split(',') if m]
    kinds = [k for v in query.get('kind', []) for k in v.split(',') if k]
    limit, offset, sort = int(one('limit', 24)), int(one('offset', 0)), one('sort')
    out = {'loose': False, 'ms': 1}
    for source, items in (('photos', PHOTOS), ('documents', DOCUMENTS), ('chats', CHATS)):
        if source not in sources:
            continue
        found = []
        for item in items:
            matched, page, snippet = _hit(item, q) if q else (True, None, '')
            if not matched or (not q and source != 'photos' and not (after or before or kinds)):
                continue
            if source == 'photos' and not q and not (people or anyof or young or after or before or months or kinds or place):
                continue
            if after and item['day'] < after or before and item['day'] >= before:
                continue
            if months and int(item['day'][5:7]) not in months or kinds and item['kind'] not in kinds:
                continue
            if source == 'photos':
                if not all(p in item['people'] for p in people) or place and place.lower() not in item['place'].lower():
                    continue
                if (anyof or young) and not (set(anyof) & set(item['people']) or young and item['young']):
                    continue
            elif people and not all(p.lower() in _text(item).lower() for p in people):
                continue
            found.append((item, page, snippet))
        found.sort(key=lambda f: f[0]['day'], reverse=sort != 'oldest')
        chosen = found[offset:offset + limit]
        section = {'count': len(found), 'items': []}
        for item, page, snippet in chosen:
            shown = {k: item[k] for k in PUBLIC if k in item}
            if source == 'photos':
                shown['people'] = item['people']
            else:
                shown.update(snippet=snippet, page=page, role='user' if source == 'chats' else None)
            section['items'].append(shown)
        if source == 'photos':
            days = sorted({i['day'] for i, _, _ in found})
            section.update(days=len(days), first=days[0] if days else None, last=days[-1] if days else None)
        out[source] = section
    return out


def item(ident, q=()):
    """One item; with `q`, only the passages that hold one of its words, as the real service gives Ask."""
    words = [w for search in q for w in _words(search)]
    holds = lambda text: not words or any(w[:5] in text.lower() for w in words)  # noqa: E731
    for found in PHOTOS + DOCUMENTS + CHATS:
        if found['id'] == ident:
            shown = {k: found[k] for k in PUBLIC if k in found}
            if found['source'] == 'photos':
                shown.update(people=found['people'], albums=[], labels=found['words'].split(), keywords=[], description='')
            elif found['source'] == 'documents':
                pages = [(p, t) for p, t in found['text'] if holds(t)] or found['text'][:1]     # the title alone: the first
                shown.update(people=[], ext=found['ext'], pages_text=[{'page': p, 'role': None, 'at': None, 'text': t} for p, t in pages])
            else:
                said = [(n, r, t) for n, r, t in found['messages'] if holds(t)] or found['messages'][:1]
                shown.update(people=[], attachments=found['attachments'],
                             messages=[{'n': n, 'role': r, 'at': None, 'text': t} for n, r, t in said])
            return shown
    return None


def respond(path, query):
    """(status, content type, body, headers) for one request, as the archive's service would answer it."""
    parts = [urllib.parse.unquote(p) for p in path.strip('/').split('/') if p]
    route = parts[0] if parts else ''
    known = {i['id']: i for i in PHOTOS + DOCUMENTS + CHATS}
    if route == 'health':
        return 200, 'application/json', {'ok': True, 'ready': True}, {}
    if route == 'search':
        return 200, 'application/json', search(query), {}
    if route == 'item' and len(parts) == 2 and item(parts[1]):
        return 200, 'application/json', item(parts[1], query.get('q', [])), {}
    if route == 'people':
        return 200, 'application/json', {'people': PEOPLE}, {}
    if route == 'onthisday':
        return 200, 'application/json', {'day': '11-16', 'years': [
            {'year': 2023, 'count': 1, 'items': [{k: PHOTOS[10][k] for k in PUBLIC if k in PHOTOS[10]} | {'people': ['Alex', 'Sam']}]}]}, {}
    if route == 'status':
        return 200, 'application/json', {
            'counts': {'photos': {'items': len(PHOTOS), 'kinds': {'photo': 10, 'video': 1}, 'previews': 11, 'first': '2022-11-16',
                                  'last': '2025-10-03'},
                       'documents': {'items': 3, 'kinds': {'pdf': 2, 'text': 1}, 'previews': 3, 'in_icloud': 0, 'first': '2025-10-03',
                                     'last': '2026-04-20'},
                       'chats': {'items': 2, 'kinds': {'chat': 2}, 'previews': 0, 'first': '2026-03-02', 'last': '2026-03-03'}},
            'runs': {s: {'ok': 1763280000.0, 'items': 1, 'seconds': 1.0, 'errors': {}} for s in ('photos', 'documents', 'chats')},
            'people': 2, 'kids': 1, 'bytes': 4096}, {}
    if route in ('thumb', 'photo') and len(parts) == 2 and parts[1] in known and (route == 'thumb' or parts[1][0] == 'p'):
        return 200, 'image/png', swatch(len(parts[1]) / 9), {'X-Width': '64', 'X-Height': '48'}
    if route == 'page' and len(parts) == 3 and parts[1] in known and parts[1][0] == 'd' and parts[2] == '1':
        return 200, 'image/png', swatch(.4), {}
    if route == 'file' and len(parts) == 2 and parts[1] == DOCUMENTS[0]['id']:
        return 200, 'application/pdf', pdf(INVOICE), {'Content-Disposition': "attachment; filename*=UTF-8''Furnace%20service%20invoice.pdf"}
    return 404, 'application/json', {'error': 'Not in the archive.'}, {}


class StandIn:
    """The stand-in, answering Hub's own client in this process, with no socket (the installed-package check allows
    none). `asked` lists every request: (path, query)."""

    def __init__(self):
        self.asked = []

    def handle(self, request):
        query = urllib.parse.parse_qs(request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query)
        self.asked.append((request.url.path, query))
        if request.method != 'GET':
            return httpx.Response(405, json={'error': 'The archive only answers questions.'})
        status, kind, body, headers = respond(request.url.path, query)
        if kind == 'application/json':
            return httpx.Response(status, json=body, headers=headers)
        return httpx.Response(status, content=body, headers={'content-type': kind, **headers})


def install(monkeypatch, folder):
    """Hub's archive client, answered by the stand-in. Returns it."""
    from hub.app import archive
    stand_in = StandIn()
    marker = Path(folder) / 'archive.sock'
    marker.touch()                                   # Hub looks for the socket before it asks
    monkeypatch.setenv('HUB_ARCHIVE_SOCKET', str(marker))
    monkeypatch.setattr(archive, '_client', lambda timeout: httpx.Client(transport=httpx.MockTransport(stand_in.handle),
                                                                          base_url='http://archive', timeout=timeout))
    return stand_in
