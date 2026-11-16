"""A made-up family archive, for the archive program's tests, Hub's tests and the demo server.

Everyone and everything here is invented: Alex (a grown-up) and Sam (a child), a beach day, a furnace serviced by a
made-up company, a short ChatGPT export. Photos are stood in for (no Photos library is read), and so is macOS (no
PDFKit, Vision or iCloud): `Mac` reads the text this module writes into its own files, and draws colour washes.
"""
import datetime as dt
import importlib
import json
import os
import re
import socket
import struct
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PT = dt.timezone(dt.timedelta(hours=-7))
_connect = socket.socket.connect        # before any test's guard replaces it


def allow(path, monkeypatch):
    """Let a test reach its own stand-in service on this socket path, and nothing else (tests/conftest.py)."""
    guarded = socket.socket.connect

    def connect(self, address):
        if isinstance(address, str) and address == str(path):
            return _connect(self, address)
        return guarded(self, address)
    monkeypatch.setattr(socket.socket, 'connect', connect)


def program(name):
    """A module of the archive program (deploy/archive), or skip: the installed package carries no deploy scripts."""
    if not (ROOT / 'deploy' / 'archive' / '__init__.py').exists():
        pytest.skip('deploy/archive is not part of the installed package', allow_module_level=True)
    if str(ROOT / 'deploy') not in sys.path:
        sys.path.insert(0, str(ROOT / 'deploy'))
    return importlib.import_module(f'archive.{name}')


# ---- pictures and documents ----

def swatch(hue, w=64, h=48):
    """A plain colour wash standing in for a picture, as a PNG."""
    import colorsys
    rows = b''.join(b'\0' + bytes(int(255 * c) for x in range(w)
                                  for c in colorsys.hsv_to_rgb(hue % 1, .35 + .4 * x / w, .45 + .4 * y / h)) for y in range(h))
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))  # noqa: E731
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def pdf(*pages):
    """A PDF whose pages say these lines, in Helvetica. Each page is a list of lines."""
    objects = ['<< /Type /Catalog /Pages 2 0 R >>', None, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for lines in pages:
        text = 'BT /F1 12 Tf 72 720 Td 14 TL ' + ' '.join(
            '(' + line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ') Tj T*' for line in lines) + ' ET'
        objects.append(f'<< /Length {len(text)} >>\nstream\n{text}\nendstream')
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objects)} 0 R '
                       '/Resources << /Font << /F1 3 0 R >> >> >>')
        kids.append(f'{len(objects)} 0 R')
    objects[1] = f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {len(kids)} >>'
    out, offsets = b'%PDF-1.4\n', []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f'{number} 0 obj\n{body}\nendobj\n'.encode()
    table = len(out)
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    out += b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    return out + f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{table}\n%%EOF\n'.encode()


def pdf_text(path):
    """The pages of a PDF made by pdf(), read back the simple way."""
    data = Path(path).read_bytes().decode('latin-1')
    pages = re.findall(r'stream\n(.*?)\nendstream', data, re.S)
    return ['\n'.join(m.replace('\\(', '(').replace('\\)', ')') for m in re.findall(r'\(((?:\\.|[^\\)])*)\) Tj', p)) for p in pages]


class Mac:
    """Stands in for archive/macos.py: what a real Mac would read and draw, from the files this world wrote."""

    def __init__(self, seen=None, away=()):
        self.seen = dict(seen or {})        # file name -> the text Vision would read in it
        self.away = {str(p) for p in away}  # files only in iCloud until download() brings them
        self.asked, self.drawn = [], 0

    def pdf_pages(self, path):
        return pdf_text(path)

    def pdf_page(self, path, number, pixels, quality=0.8):
        pages = pdf_text(path)
        self.drawn += 1
        return (swatch(number / 7), 48, 64) if 1 <= number <= len(pages) else None

    def ocr_pdf_page(self, path, number):
        return self.seen.get(f'{Path(path).name}#{number}', '')

    def ocr_picture(self, path):
        return self.seen.get(Path(path).name, '')

    def picture(self, path, pixels, quality=0.8):
        self.drawn += 1
        return swatch(len(str(path)) / 13), 64, 48

    def quicklook(self, path, pixels, timeout=60):
        return swatch(.6), 48, 64

    def textutil(self, path, fmt=None, timeout=120):
        return Path(path).read_text()

    def video_frame(self, path, pixels, quality=0.8):
        return swatch(.3), 64, 48

    def sharp_picture(self, uuid, pixels=2048, quality=0.85, folder=None, timeout=90):
        """Photos exporting a photo: a bigger wash, unless this stand-in was told Photos says no."""
        self.exported = getattr(self, 'exported', []) + [uuid]
        if getattr(self, 'refuse', False):
            raise RuntimeError('Not authorized to send Apple events to Photos. (-1743)')
        if uuid in getattr(self, 'cant', ()):
            raise RuntimeError('Photos got an error: the photo could not be exported')
        return swatch(.75, 128, 96), 128, 96

    def hello_photos(self):
        return '10.0'

    def download(self, path):
        self.asked.append(Path(path).name)
        self.away.discard(str(path))
        return True

    def only_in_icloud(self, path):
        return str(path) in self.away


# ---- photos, as osxphotos would describe them ----

class Face:
    def __init__(self, name, age):
        self.name, self.age_type, self.intrash = name, age, False


class Score:
    def __init__(self, overall):
        self.overall = overall


class Place:
    def __init__(self, name):
        self.name = name


class Seen:
    """What Photos' search knows about a photo."""
    def __init__(self, **fields):
        self.__dict__.update({'labels': [], 'detected_text': [], 'age_groups': [], 'id_document_types': [], 'city': None,
                              'country': None, 'holidays': [], 'activities': []})
        self.__dict__.update(fields)


class Exif:
    def __init__(self, duration):
        self.duration = duration


class Photo:
    def __init__(self, n, when, people=(), faces=(), labels=(), place='', text=(), ids=(), ages=(), score=.5,
                 favorite=False, hidden=False, intrash=False, movie=False, screenshot=False, shared=False, title='',
                 description='', keywords=(), albums=(), derivative=None, burst=False, burst_key=True):
        self.uuid = f'{n:08X}-1111-4000-8000-000000000000'
        self.date = when
        self.persons = list(people) + ['_UNKNOWN_'] * sum(1 for f in faces if f[0] is None)
        self.face_info = [Face(name or '_UNKNOWN_', age) for name, age in faces]
        self.search_info = Seen(labels=list(labels), detected_text=list(text), id_document_types=list(ids), age_groups=list(ages))
        self.place = Place(place) if place else None
        self.score = Score(score)
        self.favorite, self.hidden, self.intrash = favorite, hidden, intrash
        self.ismovie, self.screenshot, self.shared_library = movie, screenshot, shared
        self.title, self.description, self.keywords, self.albums = title, description, list(keywords), list(albums)
        self.path = None
        self.path_derivatives = [derivative] if derivative else []
        self.original_filesize = 2_000_000
        self.exif_info = Exif(12.5 if movie else None)
        self.burst, self.burst_key = burst, burst_key
        self.date_modified = None


class Library:
    def __init__(self, photos):
        self._photos = photos

    def photos(self, images=True, movies=True):
        return list(self._photos)


def at(y, m, d, hour=12, minute=0):
    return dt.datetime(y, m, d, hour, minute, tzinfo=PT)


def photos(folder, now=None):
    """The made-up library: (Library, {name: uuid}). Previews are colour washes written into `folder`."""
    now = now or at(2026, 11, 16)
    previews = Path(folder) / 'previews'
    previews.mkdir(parents=True, exist_ok=True)
    made = []

    def shot(n, when, **more):
        preview = previews / f'{n}.jpeg'
        preview.write_bytes(swatch(n / 23))
        made.append(Photo(n, when, derivative=str(preview), **more))
        return made[-1]

    beach = dict(labels=['Beach', 'Ocean', 'Sand'], place='Seaside Cove, Example Coast', ages=['Child', 'Adult'])
    for n, minute in enumerate((0, 20, 40, 60, 80, 100)):
        both = n % 2 == 0
        shot(n + 1, at(2024, 7, 13, 10 + minute // 60, minute % 60), people=['Alex', 'Sam'] if both else ['Sam'],
             faces=[('Alex', 5), ('Sam', 2)] if both else [('Sam', 2)], score=.4 + n / 20, albums=['Summer'], **beach)
    shot(7, at(2024, 7, 13, 15), movie=True, people=['Sam'], faces=[('Sam', 2)], title='Sam and the waves', **beach)
    shot(8, at(2024, 8, 2, 11), people=['Sam'], faces=[('Sam', 2), (None, 2)], favorite=True, **beach)
    shot(9, at(2024, 8, 2, 11, 30), faces=[(None, 2)], ages=['Child'], labels=['Beach', 'Kite'], place='Seaside Cove, Example Coast')
    shot(10, at(2025, 10, 3, 9), labels=['Document'], text=['FURNACE SERVICE', 'Example Heating Co.', 'Next due 10/2026'])
    shot(11, at(2025, 4, 2, 8), labels=['Document'], ids=['Driver License'], text=['DRIVER LICENSE', 'DL 9876-5432-1'])
    shot(12, at(2025, 6, 1, 18), screenshot=True, text=['Boarding pass', 'Gate 12'])
    shot(13, at(2023, 11, 16, 17), people=['Alex', 'Sam'], faces=[('Alex', 5), ('Sam', 1)], labels=['Birthday', 'Cake'],
         place='Example City', score=.9)
    shot(14, at(2022, 11, 16, 9), people=['Alex'], faces=[('Alex', 5)], labels=['Park'], place='Example City')
    shot(15, at(1996, 6, 1, 9), people=['Alex'], faces=[('Alex', 2)], labels=['Birthday'], description='Alex turns six')
    shot(16, at(2024, 7, 13, 12), hidden=True, people=['Alex'], faces=[('Alex', 5)], **beach)
    shot(17, at(2024, 7, 13, 13), intrash=True, **beach)
    shot(18, at(2024, 7, 13, 13, 1), burst=True, burst_key=False, **beach)
    for n in range(19, 31):         # Sam, often: enough faces in the last two years to be a kid
        shot(n, now - dt.timedelta(days=30 * (n - 18)), people=['Sam'], faces=[('Sam', 2 if n % 3 else 1)], labels=['Park'])
    for n in range(31, 43):         # Alex, as a grown-up
        shot(n, now - dt.timedelta(days=29 * (n - 30)), people=['Alex'], faces=[('Alex', 5)], labels=['Kitchen'])
    return Library(made), {p.title or str(i): p.uuid for i, p in enumerate(made, 1)}


# ---- documents ----

INVOICE = ['Example Heating Co.', 'Invoice 4411', 'Furnace service: annual tune-up and filter replacement.',
           'Serviced on October 3, 2025.', 'Total: $189.00', 'Card ending 4242. Account 000123456789.']


def documents(folder):
    """The made-up Documents, Desktop and iCloud Drive: (roots, Mac stand-in). Some are things the archive must skip."""
    folder = Path(folder)
    docs, desk, cloud = folder / 'Documents', folder / 'Desktop', folder / 'iCloud Drive'
    for path, data in {
        docs / 'Home' / 'Furnace service invoice.pdf': pdf(INVOICE),
        docs / 'Home' / 'Paint.md': '# Paint\n\nThe hall is Example Grey. The kitchen is Morning Fog.\n',
        docs / 'Home' / 'Scan of warranty.pdf': pdf([], ['Second page']),
        docs / 'Taxes' / '2024 summary.csv': 'item,amount\ncharity,120\n',
        docs / 'Scans' / 'receipt.jpg': swatch(.1),
        docs / 'Scans' / 'sunset.jpg': swatch(.9),
        docs / 'Projects' / 'site' / 'package.json': '{}',
        docs / 'Projects' / 'site' / 'README.md': 'A website project, not a document.',
        docs / 'tool.py': 'print("code is not a document")',
        docs / '.secret.txt': 'hidden',
        docs / 'chatgpt-archive' / 'export' / 'notes.md': 'the ChatGPT export is read on its own',
        desk / 'Budget.numbers' / 'Index.zip': b'PK',
        cloud / 'School' / 'Field trip form.pdf': pdf(['Field trip to the aquarium', 'Bring a packed lunch']),
        cloud / 'Pictures' / 'beach.jpg': swatch(.5),
        cloud / 'Pictures' / 'label.jpg': swatch(.2),
    }.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data) if isinstance(data, bytes) else path.write_text(data)
    mac = Mac(seen={'receipt.jpg': 'HARDWARE STORE\nFurnace filter 16x25x1\nTotal 24.99',
                    'Scan of warranty.pdf#1': 'Ten year parts warranty for the furnace',
                    'label.jpg': 'Pantry shelf labels'},
              away=[cloud / 'School' / 'Field trip form.pdf', cloud / 'Pictures' / 'beach.jpg', cloud / 'Pictures' / 'label.jpg'])
    roots = {'Documents': str(docs), 'Desktop': str(desk), 'iCloud Drive': str(cloud)}
    return roots, mac


# ---- a ChatGPT export ----

def _node(ident, parent, role, text=None, kind='text', parts=None, when=0.0, attachments=None):
    message = {'id': ident, 'author': {'role': role}, 'create_time': when,
               'content': {'content_type': kind, 'parts': parts if parts is not None else [text]},
               'metadata': {'attachments': attachments} if attachments else {}}
    return {'id': ident, 'parent': parent, 'message': message}


def chatgpt(folder):
    """A short export in the split format, with a branch, ChatGPT's thinking, a voice chat, attachments and a group."""
    out = Path(folder) / 'chatgpt-archive' / 'export'
    out.mkdir(parents=True, exist_ok=True)
    t = at(2026, 3, 2).timestamp()
    first = {'conversation_id': 'c0ffee00-0000-4000-8000-000000000001', 'title': 'Furnace filter sizes', 'create_time': t,
             'update_time': t + 600, 'current_node': 'n6', 'mapping': {
                 'root': {'id': 'root', 'parent': None, 'message': None},
                 'n1': _node('n1', 'root', 'user', 'What size furnace filter fits a 16x25 return?', when=t,
                             attachments=[{'id': 'file_notes01', 'name': 'filter notes.md'}]),
                 'n2': _node('n2', 'n1', 'assistant', 'An answer about zeppelins that was left behind.', when=t + 10),
                 'n3': _node('n3', 'n1', 'assistant', kind='thoughts', parts=['Thinking about filters'], when=t + 20),
                 'n4': _node('n4', 'n3', 'assistant', kind='reasoning_recap', parts=['Thought for 3 seconds'], when=t + 21),
                 'n5': _node('n5', 'n4', 'assistant', 'A 16x25x1 filter fits. Replace it every three months.', when=t + 30),
                 'n6': _node('n6', 'n5', 'user', 'Thanks, ordering MERV 8.', when=t + 60)}}
    second = {'conversation_id': 'c0ffee00-0000-4000-8000-000000000002', 'title': 'Beach packing list', 'create_time': t + 86400,
              'update_time': t + 86400 + 300, 'current_node': 'v2', 'mapping': {
                  'v1': _node('v1', None, 'user', kind='multimodal_text', when=t + 86400, parts=[
                      {'content_type': 'audio_transcription', 'text': 'What should we pack for the beach with a kid?'},
                      {'content_type': 'image_asset_pointer', 'asset_pointer': 'file-service://file_photo01'}]),
                  'v2': _node('v2', 'v1', 'assistant', kind='multimodal_text', when=t + 86410, parts=[
                      {'content_type': 'audio_transcription', 'text': 'Pack sunscreen, towels, a kite and snacks.'}])}}
    (out / 'conversations-000.json').write_text(json.dumps([first, second]))
    (out / 'file_notes01.dat').write_text('MERV 8 is fine for most homes. Write the date on the frame.')
    (out / 'file_warranty01.dat').write_bytes(pdf(['Ten year parts warranty', 'Register within 60 days']))
    (out / 'conversation_asset_file_names.json').write_text(json.dumps({'file_notes01.dat': 'filter notes.md'}))
    (out / 'library_files.json').write_text(json.dumps([
        {'file_id': 'file_warranty01', 'file_name': 'Warranty.pdf', 'mime_type': 'application/pdf',
         'origination_thread_id': first['conversation_id']}]))
    (out / 'group_chats.json').write_text(json.dumps({'chats': [{
        'id': 'g-family-trip', 'name': 'Family trip', 'created_at': '2026-04-01T10:00:00.000000+00:00',
        'updated_at': '2026-04-02T10:00:00.000000+00:00', 'messages': [
            {'id': 'm1', 'role': 'user', 'text': 'Who is bringing the beach umbrella?', 'created_at': '2026-04-01T10:00:00.000000+00:00',
             'attachments': [{'title': 'Tide tables', 'desc': 'Low tide at noon', 'url': 'https://example.com/tides', 'type': 'link'}]},
            {'id': 'm2', 'role': 'assistant', 'text': 'Alex said they would.', 'created_at': '2026-04-01T10:05:00.000000+00:00'}]}]}))
    return out


def build(folder, monkeypatch=None, now=None):
    """The whole made-up archive indexed into `folder`/archive, with the archive program's real code and stand-ins
    for Photos and macOS. Returns (home, Mac stand-in, {name: uuid})."""
    folder = Path(folder)
    env = monkeypatch.setenv if monkeypatch else os.environ.__setitem__
    library, uuids = photos(folder / 'library', now)
    roots, mac = documents(folder / 'files')
    export = chatgpt(folder / 'files' / 'Documents')
    env('HUB_ARCHIVE_DIR', str(folder / 'archive'))
    env('HUB_ARCHIVE_ROOTS', json.dumps(roots))
    env('HUB_ARCHIVE_CHATGPT', str(export))
    store, documents_, chats_, photos_ = (program(n) for n in ('store', 'documents', 'chats', 'photos'))
    conn = store.connect()
    real = documents_.only_in_icloud
    documents_.only_in_icloud = lambda info: _away(info, mac)
    try:
        photos_.index(conn, db=library, thumb=lambda item: _thumb(store, item, mac), now=now or at(2026, 11, 16), say=lambda *_: None)
        documents_.index(conn, mac=mac, wait=0, say=lambda *_: None)
        chats_.index(conn, mac=mac, say=lambda *_: None)
    finally:
        documents_.only_in_icloud = real
    conn.close()
    return folder / 'archive', mac, uuids


def _away(info, mac):
    return any(os.stat(p).st_ino == info.st_ino for p in mac.away if os.path.exists(p))


def _thumb(store, item, mac):
    data, w, h = mac.picture(item['meta']['thumb_from'], 400)
    store.write_bytes(store.thumb_path(item['id']), data)
    return w, h
