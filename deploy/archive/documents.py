"""Documents in Documents, Desktop, Downloads and iCloud Drive: their names, folders, dates, text and a first-page
preview. Only documents: PDFs, Word and other text documents, spreadsheets, slides, and pictures that hold text.
Never source code, and never anything inside a software project, a library or a bundle.

Text comes from macOS itself: PDFKit for PDFs, with Vision reading the pages that are only pictures (a scan, at most
30 pages a document); textutil for Word, RTF and OpenDocument; a plain read for text, Markdown and CSV; a parser of
its own for web pages, which never fetch anything. Numbers, Keynote, Pages and Office spreadsheets and slides are
found by name and folder, with a preview. A picture is kept only when Vision finds text in it.

iCloud Drive keeps many files only in iCloud. The archive asks iCloud for the documents among them (up to 25 MB
each, pictures up to 10 MB, and 3 GB a run at most) and reads each once it is on this Mac; until then it is found by
name, folder and date. Downloading is all it asks: it never evicts, moves or changes a file, here or in iCloud.
"""
import datetime as dt
import hashlib
import html.parser
import json
import os
import re
import stat
import time
from pathlib import Path

from . import macos, store
from .text import MOST, chunks, clean, words

KINDS = {'.pdf': 'pdf', '.doc': 'word', '.docx': 'word', '.rtf': 'word', '.rtfd': 'word', '.odt': 'word', '.pages': 'word',
         '.txt': 'text', '.text': 'text', '.md': 'text', '.markdown': 'text', '.html': 'text', '.htm': 'text',
         '.csv': 'sheet', '.tsv': 'sheet', '.xls': 'sheet', '.xlsx': 'sheet', '.numbers': 'sheet', '.ods': 'sheet',
         '.key': 'slides', '.ppt': 'slides', '.pptx': 'slides', '.odp': 'slides',
         '.jpg': 'image', '.jpeg': 'image', '.png': 'image', '.heic': 'image', '.heif': 'image', '.tif': 'image',
         '.tiff': 'image', '.webp': 'image'}
TEXTUTIL = ('.doc', '.docx', '.rtf', '.rtfd', '.odt')
PLAIN = ('.txt', '.text', '.md', '.markdown', '.csv', '.tsv')
WEB = ('.html', '.htm')
PACKAGES = ('.pages', '.numbers', '.key', '.rtfd')                 # documents that are folders on disk
SKIP_DIRS = {'.git', 'node_modules', '.venv', 'venv', 'Library', 'Developer', '__pycache__', '.Trash', 'site-packages'}
PROJECT = {'.git', 'package.json', 'pyproject.toml', 'setup.py', 'Cargo.toml', 'go.mod', 'Package.swift', 'Gemfile',
           'composer.json', 'pom.xml', 'build.gradle', 'CMakeLists.txt', 'requirements.txt'}
BUNDLES = ('.app', '.photoslibrary', '.bundle', '.framework', '.pkg', '.musiclibrary', '.tvlibrary', '.imovielibrary',
           '.fcpbundle', '.xcodeproj', '.xcworkspace', '.logicx', '.band', '.sparsebundle', '.plugin', '.kext')
BIGGEST = 100 * 1024 * 1024        # larger files are left out altogether
FETCH_DOCUMENT = 25 * 1024 * 1024  # an iCloud-only document this size or less is fetched to be read
FETCH_PICTURE = 10 * 1024 * 1024
FETCH_BUDGET = 3 * 1024 ** 3       # bytes asked of iCloud in one run, at most
WAIT_SECONDS = 15 * 60             # how long a run waits for what it asked iCloud for; the rest is read next time
OCR_PAGES = 30
TEXT_READ = 4 * 1024 * 1024        # bytes read of a plain text file
FOUND_TEXT = re.compile(r'\w{3,}')
SF_DATALESS = 0x40000000           # the file's contents are only in iCloud
THUMB = 400
READER = 2                         # raised when reading improves, so every document is read again once


def roots():
    """[(label, folder)] the archive reads documents from. HUB_ARCHIVE_ROOTS (JSON) names others, for tests."""
    named = os.environ.get('HUB_ARCHIVE_ROOTS')
    if named:
        return [(label, Path(path)) for label, path in json.loads(named).items()]
    home = Path.home()
    return [('Documents', home / 'Documents'), ('Desktop', home / 'Desktop'), ('Downloads', home / 'Downloads'),
            ('iCloud Drive', home / 'Library' / 'Mobile Documents' / 'com~apple~CloudDocs')]


def skipped_inside():
    """Folders the archive reads in their own way and leaves out here: the ChatGPT export."""
    from . import chats
    return {chats.root().parent}


def only_in_icloud(info):
    return bool(getattr(info, 'st_flags', 0) & SF_DATALESS)


def item_id(path):
    return 'd:' + hashlib.sha1(str(path).encode()).hexdigest()[:24]


def walk(label, root, leave_out=()):
    """Every document under `root`: (path, its stat, label, folder relative to the root)."""
    leave_out = {Path(p) for p in leave_out}
    for folder, dirs, files in os.walk(root):
        here = Path(folder)
        if here in leave_out or here != root and PROJECT & set(dirs + files):
            dirs[:] = []
            continue
        keep = []
        for name in dirs:
            lower = name.lower()
            if name.startswith('.') or name in SKIP_DIRS or lower.endswith(BUNDLES):
                continue
            if lower.endswith(PACKAGES):
                files.append(name)          # a Pages, Numbers or Keynote document is a folder on disk
                continue
            keep.append(name)
        dirs[:] = sorted(keep)
        for name in sorted(files):
            if name.startswith('.') or Path(name).suffix.lower() not in KINDS:
                continue
            path = here / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode):
                continue
            yield path, info, label, str(here.relative_to(root))


def _size(path, info):
    if stat.S_ISDIR(info.st_mode):
        return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
    return info.st_size


class _Web(html.parser.HTMLParser):
    """A web page's words, without its scripts and styles. Nothing is fetched."""
    SKIP = {'script', 'style', 'noscript', 'template', 'svg'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.hidden += 1
        elif tag in ('p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'section', 'article'):
            self.out.append('\n')

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.out.append(data)


def _plain(path):
    with open(path, 'rb') as file:
        data = file.read(TEXT_READ)
    for encoding in ('utf-8-sig', 'utf-16') if data[:2] in (b'\xff\xfe', b'\xfe\xff') else ('utf-8-sig',):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1')


def read_text(path, kind, ext, mac=macos):
    """[(page or None, text)] for one document on this Mac, and how many pages Vision read. Raises on a file
    macOS can't open."""
    if kind == 'pdf':
        pages, read = mac.pdf_pages(path), 0
        out = []
        for number, page in enumerate(pages, 1):
            if len(FOUND_TEXT.findall(page)) < 3 and read < OCR_PAGES:     # a scanned page: read its picture
                page, read = mac.ocr_pdf_page(path, number), read + 1
            out.append((number, page))
        return out, read
    if kind == 'image':
        return [(None, mac.ocr_picture(path))], 1
    if ext in TEXTUTIL:
        return [(None, mac.textutil(path))], 0
    if ext in WEB:
        parser = _Web()
        parser.feed(_plain(path))
        return [(None, ''.join(parser.out))], 0
    if ext in PLAIN:
        return [(None, _plain(path))], 0
    return [], 0                                    # Numbers, Keynote, Pages, Office spreadsheets and slides: by name


def preview(path, kind, ext, mac=macos):
    """The first page's preview as (JPEG bytes, w, h), or None."""
    if kind == 'pdf':
        return mac.pdf_page(path, 1, THUMB, 0.72)
    if kind == 'image':
        return mac.picture(path, THUMB, 0.72)
    if ext in WEB:
        return None                                 # Quick Look would draw it with a browser: not here
    return mac.quicklook(path, THUMB)


def describe(path, info, label, relative, mac=macos, read=True):
    """(item, parts) for one document, with its text and first-page preview when it is on this Mac and `read`.
    None for a picture without text."""
    ext = Path(path.name).suffix.lower()
    kind = KINDS[ext]
    local = not only_in_icloud(info)
    folder = label if relative in ('', '.') else f'{label}/{relative}'
    when = dt.datetime.fromtimestamp(info.st_mtime).astimezone()
    item = {'id': item_id(path), 'source': 'documents', 'kind': kind, 'title': path.name, 'at': info.st_mtime,
            'day': when.date().isoformat(), 'folder': folder, 'size': _size(path, info), 'local': local,
            'path': str(path), 'meta': {'ext': ext, 'created': getattr(info, 'st_birthtime', None)}}
    head = words(Path(path.name).stem.replace('_', ' '), relative.replace('/', ' · ') if relative not in ('', '.') else '', label)
    parts = []
    if local and read:
        pages, ocr = read_text(path, kind, ext, mac)
        kept = 0
        for page, text in pages:
            for piece in chunks(text):
                if kept + len(piece) > MOST:
                    break
                parts.append({'page': page, 'head': head, 'body': piece})
                kept += len(piece)
        if kind == 'pdf':
            item['pages'] = len(pages)
        item['meta'].update(ocr=ocr, text=bool(parts))
        if kind == 'image' and len(FOUND_TEXT.findall(' '.join(p['body'] for p in parts))) < 3:
            return None
        made = preview(path, kind, ext, mac)
        if made:
            data, item['w'], item['h'] = made
            store.write_bytes(store.thumb_path(item['id']), data)
            item['thumb'] = 1
    return item, parts or [{'head': head, 'body': ''}]


def base(info, path):
    """A file's size and time of change: what says it is the same file whether or not it is on this Mac."""
    return f'{_size(path, info)}:{info.st_mtime_ns}'


def signature(info, path):
    return f'{base(info, path)}:r{READER}:{int(only_in_icloud(info))}'


def _wanted(info, kind):
    """Whether an iCloud-only file is worth fetching to read it."""
    return 0 < info.st_size <= (FETCH_PICTURE if kind == 'image' else FETCH_DOCUMENT)


def _kind(path):
    return KINDS[Path(path.name).suffix.lower()]


def index(conn, mac=macos, now=None, wait=WAIT_SECONDS, say=print):
    """Bring the documents in the index up to date. Returns (counts, errors)."""
    stamp = now or time.time()
    had = store.known(conn, 'documents')
    looked = dict(conn.execute('select id, sig from looked').fetchall())    # pictures read before that held no text
    counts = {'new': 0, 'changed': 0, 'in_icloud': 0, 'asked_icloud': 0, 'asked_bytes': 0, 'came_down': 0,
              'came_down_bytes': 0, 'pictures_without_text': 0, 'ocr_pages': 0, 'unread': 0}
    errors, keep, fetch = {}, set(), []

    def take(path, info, label, relative):
        """Index one file as it is now, unless it is the same as when it was last indexed."""
        ident, sig = item_id(path), signature(info, path)
        if had.get(ident) == sig:
            keep.add(ident)
            return
        if looked.get(ident) == f'{base(info, path)}:r{READER}':
            return
        try:
            described = describe(path, info, label, relative, mac)
        except Exception as error:  # noqa: BLE001 - one unreadable file never stops the rest
            problem = f'{Path(path.name).suffix.lower()} {type(error).__name__}'
            errors[problem] = errors.get(problem, 0) + 1
            if _kind(path) == 'image':
                return                                  # tried again next run
            described = describe(path, info, label, relative, mac, read=False)
            described[0]['meta']['unread'] = True
            counts['unread'] += 1
            sig += ':unread'                            # read again next run
        if described is None:
            counts['pictures_without_text'] += 1
            conn.execute('insert or replace into looked(id, sig) values (?, ?)', (ident, f'{base(info, path)}:r{READER}'))
            return
        item, parts = described
        item['sig'], item['seen'] = sig, stamp
        counts['new' if ident not in had else 'changed'] += 1
        counts['ocr_pages'] += item['meta'].get('ocr', 0) if item['kind'] == 'pdf' else 0
        store.put(conn, item, parts)
        keep.add(ident)
        if (counts['new'] + counts['changed']) % 100 == 99:
            conn.commit()

    for label, root in roots():
        try:
            if not store.readable(root):
                continue
        except store.NoAccess:
            errors['NoAccess'] = errors.get('NoAccess', 0) + 1          # this root, and what it held, stay as they were
            keep.update(i for i, s in had.items() if conn.execute('select 1 from items where id = ? and folder like ?',
                                                                  (i, label + '%')).fetchone())
            continue
        for path, info, lab, relative in walk(label, root, skipped_inside()):
            if _size_quick(info) > BIGGEST:
                continue
            kind = _kind(path)
            if only_in_icloud(info):
                counts['in_icloud'] += 1
                ident = item_id(path)
                if had.get(ident) == f'{base(info, path)}:r{READER}:0':
                    keep.add(ident)                     # read while it was here, and since left to iCloud: keep it
                    continue
                if looked.get(ident) == f'{base(info, path)}:r{READER}':
                    continue
                if _wanted(info, kind):
                    fetch.append((kind == 'image', -info.st_mtime, path, info, lab, relative))
                    continue
                if kind == 'image':
                    continue                            # a picture is kept only for its text, which needs it here
            take(path, info, lab, relative)
    # Ask iCloud for what is worth reading: documents first, newest first, within the run's budget.
    asked, budget = [], FETCH_BUDGET
    for _, _, path, info, label, relative in sorted(fetch, key=lambda f: (f[0], f[1])):
        if info.st_size <= budget and mac.download(path):
            budget -= info.st_size
            asked.append((path, label, relative))
            counts['asked_icloud'] += 1
            counts['asked_bytes'] += info.st_size
        elif _kind(path) != 'image':
            take(path, info, label, relative)           # by name until it can be fetched
    deadline = time.monotonic() + wait
    while asked:
        waiting = []
        for path, label, relative in asked:
            try:
                info = path.lstat()
            except OSError:
                continue
            if only_in_icloud(info):
                waiting.append((path, label, relative))
                continue
            counts['came_down'] += 1
            counts['came_down_bytes'] += info.st_size
            take(path, info, label, relative)
        conn.commit()
        asked = waiting
        if not asked or time.monotonic() > deadline:
            break
        say(f'documents: waiting on iCloud for {len(asked)} files')
        time.sleep(5)
    for path, label, relative in asked:                 # not here yet: found by name until the next run reads it
        try:
            info = path.lstat()
        except OSError:
            continue
        if _kind(path) != 'image':
            take(path, info, label, relative)
    counts['still_in_icloud'] = len(asked)
    store.seen(conn, [i for i in keep if i in had], stamp)
    gone = [i for i in had if i not in keep]
    store.forget(conn, gone)
    counts['removed'] = len(gone)
    counts['items'] = len(keep)
    counts['with_text'] = conn.execute("select count(distinct item) from parts where body != '' and item like 'd:%'").fetchone()[0]
    counts['name_only'] = counts['items'] - counts['with_text']
    conn.commit()
    return counts, errors


def _size_quick(info):
    return info.st_size if not stat.S_ISDIR(info.st_mode) else 0
