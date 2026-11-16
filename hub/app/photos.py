"""The Photos screen: each month's best photos, chosen on this Mac by the photo curator, and the marks on them.

The curator (deploy/archive/curator.py) is part of the archive program, which has its own access to the Photos
library; Hub never opens the library. Hub reads what the curator wrote in its folder (the month files and their previews) and writes
one file back, the marks: Keep, Skip or Print for each photo. The curator acts on those marks by itself, adding
Keep and Print picks to the month's album in Photos and saving a full-size copy of each Print pick.

No photo, preview or detail about one goes to a model or leaves this Mac; Ask is not given any of it.
The folder is the curator's own in the archive's folder unless the private settings file names another:
    "photos": {"dir": "/path/to/the/curator/folder"}
"""
import datetime
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from .. import household

MARKS = ('keep', 'skip', 'print')
ID = re.compile(r'[0-9A-Fa-f-]{36}')
MONTH = re.compile(r'\d{4}-\d{2}')
_lock = threading.Lock()


class Refused(Exception):
    """A tap Hub won't make, with words for the person."""


def folder():
    named = household.section('photos').get('dir')
    return Path(named) if named else Path.home() / 'Library' / 'Application Support' / 'HubArchive' / 'curator'


def _read(path, default):
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def months():
    """Each month the curator has chosen, newest first."""
    try:
        names = [p.stem for p in (folder() / 'months').glob('*.json')]
    except OSError:
        return []
    return sorted((n for n in names if MONTH.fullmatch(n)), reverse=True)


def _photo(entry, marks, applied):
    mark = marks.get(entry['id'], {})
    return {'id': entry['id'], 'taken': entry.get('taken', ''), 'people': entry.get('people', [])[:6],
            'favorite': bool(entry.get('favorite')), 'shared': bool(entry.get('shared')),
            'mark': mark.get('mark', ''), 'by': mark.get('by', ''),
            'in_album': entry['id'] in applied.get('album', []), 'saved': entry['id'] in applied.get('print', [])}


def picture(month=''):
    """The Photos screen: one month (the newest unless named), and the list of months to move between."""
    found = months()
    if not found:
        return {'ok': True, 'ready': False, 'months': [], 'month': '', 'picks': [], 'more': []}
    month = month if month in found else found[0]
    chosen = _read(folder() / 'months' / f'{month}.json', {})
    marks, applied = _read(folder() / 'marks.json', {}), _read(folder() / 'applied.json', {})
    picks = [_photo(e, marks, applied) for e in chosen.get('picks', []) if ID.fullmatch(str(e.get('id', '')))]
    more = [_photo(e, marks, applied) for e in chosen.get('more', []) if ID.fullmatch(str(e.get('id', '')))]
    every = picks + more
    return {'ok': True, 'ready': True, 'months': [{'month': m, 'name': _name(m)} for m in found],
            'month': month, 'name': chosen.get('album') or _name(month), 'looked_at': chosen.get('looked_at', 0),
            'made': chosen.get('made', ''), 'picks': picks, 'more': more,
            'counts': {m: sum(p['mark'] == m for p in every) for m in MARKS},
            'waiting': sum((p['mark'] in ('keep', 'print') and not p['in_album']) or (p['mark'] == 'print' and not p['saved'])
                           for p in every)}


def _name(month):
    return datetime.date(int(month[:4]), int(month[5:]), 1).strftime('%B %Y')


def thumb(photo):
    """The path of a photo's preview, only for a photo the curator chose."""
    if not ID.fullmatch(photo or ''):
        return None
    path = folder() / 'thumbs' / f'{photo}.jpg'
    return path if path.is_file() and _chosen(photo) else None


def _chosen(photo):
    for month in months():
        chosen = _read(folder() / 'months' / f'{month}.json', {})
        if any(e.get('id') == photo for e in chosen.get('picks', []) + chosen.get('more', [])):
            return month
    return None


def mark(photo, do, login, now=None):
    """Mark one chosen photo Keep, Skip or Print, or clear its mark. Returns what to tell the person."""
    if do not in MARKS + ('clear',):
        raise Refused('Hub doesn’t know that mark.')
    if not ID.fullmatch(str(photo or '')) or not _chosen(photo):
        raise Refused('That photo isn’t one the curator chose.')
    path = folder() / 'marks.json'
    with _lock:
        marks = _read(path, {})
        if do == 'clear':
            marks.pop(photo, None)
        else:
            marks[photo] = {'mark': do, 'by': str(login or '')[:200], 'at': int(now or time.time())}
        with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False, suffix='.tmp') as out:
            json.dump(marks, out, indent=1)
        os.chmod(out.name, 0o600)
        os.replace(out.name, path)
    return {'keep': 'Kept. It goes into the month’s album in Photos.', 'skip': 'Skipped.',
            'print': 'Marked to print. A full-size copy goes to the prints folder.', 'clear': 'Mark cleared.'}[do]
