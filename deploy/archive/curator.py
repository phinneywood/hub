"""Choose each month's best photos from the Photos library on this Mac, for the Archive screen's "Best of the month".

Part of the archive program (python -m archive curate | apply), run by its LaunchAgents in the archive's own Python,
the only program given access to the Photos library; Hub never opens it. No photo, preview or detail about one
leaves this Mac and no model sees them: the scores are the ones Photos already computed on the Mac.

  curate          last month (the LaunchAgent runs this on the 1st)
  curate --month YYYY-MM
  curate --since YYYY-MM-DD  every month from that day to last month, for a first catch-up
                  (a plain run does this instead when the file catch-up holds that date; it is removed after)
  apply           act on the marks made in Hub: add Keep and Print picks to the month's album in Photos,
                  and save a full-size copy of each Print pick to the prints folder

What it writes, all owner-only, in the archive's folder under curator/ (PHOTO_CURATOR_DIR names another):
  months/YYYY-MM.json   the month's picks and the runners-up
  thumbs/<id>.jpg       a preview of each, at most 1024 pixels, made from the preview Photos keeps on the Mac
  marks.json            written by Hub: Keep, Skip or Print for each photo
  applied.json          what apply has already done, so each photo is added or saved once

The only changes it makes in Photos are adding photos to albums it made itself, in a folder of its own. It never
edits, moves, hides or deletes a photo, and never removes one from an album.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

COUNT = 25              # picks a month
MORE = 25               # runners-up shown under "More"
PER_DAY = 4             # at most this many picks from one day, so a busy weekend doesn't fill the month
MOMENT_SECONDS = 90     # photos this close together are one moment; only its best can be a pick
THUMB_PIXELS = 1024
FOLDER = 'Photo picks'  # the folder in Photos that holds the albums this program makes
ID = re.compile(r'[0-9A-Fa-f-]{36}')
MONTH = re.compile(r'\d{4}-\d{2}')


def home():
    from . import store
    return Path(os.environ.get('PHOTO_CURATOR_DIR') or store.home() / 'curator')


def prints_home():
    return Path(os.environ.get('PHOTO_CURATOR_PRINTS') or Path.home() / 'Pictures/Photo prints')


@dataclass
class Shot:
    """One photo as the chooser sees it."""
    id: str
    taken: dt.datetime
    score: float
    favorite: bool = False
    people: list = field(default_factory=list)
    shared: bool = False
    preview: str = ''


def album_name(month):
    """'2026-10' -> 'October 2026'."""
    return dt.date(int(month[:4]), int(month[5:]), 1).strftime('%B %Y')


def months_between(since, until):
    """Each 'YYYY-MM' from the month of `since` through the month of `until`."""
    year, month, found = since.year, since.month, []
    while (year, month) <= (until.year, until.month):
        found.append(f'{year:04d}-{month:02d}')
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return found


def last_month(today=None):
    first = (today or dt.date.today()).replace(day=1)
    return (first - dt.timedelta(days=1)).strftime('%Y-%m')


def bounds(month, since=None):
    """The month as local, aware datetimes [start, end); a catch-up's first month starts on its first day."""
    start = dt.datetime(int(month[:4]), int(month[5:]), 1).astimezone()
    end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1).astimezone()
    if since and start.date() <= since < end.date():
        start = dt.datetime.combine(since, dt.time()).astimezone()
    return start, end


def worth(shot):
    """How much the chooser wants a photo: Photos' own score, raised for a favorite and for people in it."""
    return shot.score + (0.5 if shot.favorite else 0) + (0.1 if shot.people else 0)


def moments(shots):
    """Photos grouped by moment: each one taken within MOMENT_SECONDS of the one before it."""
    groups = []
    for shot in sorted(shots, key=lambda s: s.taken):
        if groups and (shot.taken - groups[-1][-1].taken).total_seconds() <= MOMENT_SECONDS:
            groups[-1].append(shot)
        else:
            groups.append([shot])
    return groups


def choose(shots, count=COUNT, more=MORE, per_day=PER_DAY):
    """(picks, runners-up): the best photo of each moment, at most `per_day` a day while there are enough
    other days to choose from, picks in the order they were taken and runners-up best first."""
    best = sorted((max(group, key=worth) for group in moments(shots)), key=worth, reverse=True)
    picks, left, days = [], [], {}
    for shot in best:
        day = shot.taken.date()
        if len(picks) < count and days.get(day, 0) < per_day:
            picks.append(shot)
            days[day] = days.get(day, 0) + 1
        else:
            left.append(shot)
    while len(picks) < count and left:          # a quiet month: let the busy days have more
        picks.append(left.pop(0))
    return sorted(picks, key=lambda s: s.taken), left[:more]


def _entry(shot):
    return {'id': shot.id, 'taken': shot.taken.isoformat(timespec='seconds'), 'favorite': shot.favorite,
            'people': shot.people, 'shared': shot.shared, 'score': round(worth(shot), 3)}


def _write(path, value):
    """Write JSON so a reader never sees half a file, owner-only."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False, suffix='.tmp') as out:
        json.dump(value, out, indent=1)
    os.chmod(out.name, 0o600)
    os.replace(out.name, path)


def _read(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


# ---- reading the library (osxphotos), only in the curator's own environment ----

def _library():
    from . import photos
    return photos.library()


def _shot(photo):
    """A Shot for a photo worth considering, or None for screenshots, hidden photos and the like."""
    if photo.hidden or photo.intrash or photo.screenshot or getattr(photo, 'screen_recording', False):
        return None
    if photo.burst and not photo.burst_key:
        return None
    previews = [p for p in (photo.path_derivatives or []) if os.path.exists(p)]
    if not previews or not photo.date:
        return None
    score = photo.score.overall if photo.score and photo.score.overall is not None else 0.0
    people = sorted({p for p in (photo.persons or []) if p and p != '_UNKNOWN_'})
    return Shot(photo.uuid, photo.date.astimezone(), float(score), bool(photo.favorite), people,
                bool(getattr(photo, 'shared_library', False)), max(previews, key=os.path.getsize))


def _thumb(shot, folder):
    out = folder / f'{shot.id}.jpg'
    if out.exists():
        return True
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    done = subprocess.run(['/usr/bin/sips', '-s', 'format', 'jpeg', '-s', 'formatOptions', '82', '-Z', str(THUMB_PIXELS),
                           shot.preview, '--out', str(out)], capture_output=True, timeout=60)
    if done.returncode == 0 and out.exists():
        os.chmod(out, 0o600)
        return True
    return False


def scan(months, since=None, db=None, now=None):
    """Choose each month and write its file and previews. Returns {month: number of picks}."""
    db = db or _library()
    photos = db.photos(images=True, movies=False)
    here, made = home(), {}
    for month in months:
        start, end = bounds(month, since)
        shots = [s for s in map(_shot, (p for p in photos if p.date and start <= p.date < end)) if s]
        picks, more = choose(shots)
        kept = [s for s in picks + more if _thumb(s, here / 'thumbs')]
        _write(here / 'months' / f'{month}.json', {
            'month': month, 'album': album_name(month), 'from': start.date().isoformat(),
            'made': (now or dt.datetime.now().astimezone()).isoformat(timespec='seconds'), 'looked_at': len(shots),
            'picks': [_entry(s) for s in picks if s in kept], 'more': [_entry(s) for s in more if s in kept]})
        made[month] = len(picks)
    return made


# ---- acting on Hub's marks, through Photos itself (photoscript), only in the curator's own environment ----

def pending(marks, applied, months):
    """What apply still has to do: {'album': {month: [ids]}, 'print': {month: [ids]}}. Only photos that were
    picks or runners-up of a month this program chose, so a mark can never reach any other photo."""
    known = {}
    for month, chosen in months.items():
        for entry in chosen.get('picks', []) + chosen.get('more', []):
            known[entry['id']] = month
    todo = {'album': {}, 'print': {}}
    for photo, mark in sorted(marks.items()):
        month = known.get(photo)
        if not month or not isinstance(mark, dict):
            continue
        if mark.get('mark') in ('keep', 'print') and photo not in applied.get('album', []):
            todo['album'].setdefault(month, []).append(photo)
        if mark.get('mark') == 'print' and photo not in applied.get('print', []):
            todo['print'].setdefault(month, []).append(photo)
    return todo


def apply(library=None):
    """Add newly kept photos to their month's album and save newly marked prints. Returns counts."""
    here = home()
    marks = _read(here / 'marks.json', {})
    applied = _read(here / 'applied.json', {'album': [], 'print': []})
    months = {p.stem: _read(p, {}) for p in sorted((here / 'months').glob('*.json')) if MONTH.fullmatch(p.stem)}
    todo = pending(marks, applied, months)
    if not any(todo.values()):
        return {'album': 0, 'print': 0}
    if library is None:
        import photoscript
        library = photoscript
    photos = library.PhotosLibrary()
    folder = photos.folder(FOLDER, top_level=True) or photos.create_folder(FOLDER)
    added = saved = 0
    for month, ids in todo['album'].items():
        name = album_name(month)
        album = folder.album(name) or folder.create_album(name)
        album.add([library.Photo(i) for i in ids])
        applied['album'] = applied.get('album', []) + ids
        added += len(ids)
        _write(here / 'applied.json', applied)
    for month, ids in todo['print'].items():
        out = prints_home() / album_name(month)
        out.mkdir(parents=True, exist_ok=True)
        for photo in ids:
            library.Photo(photo).export(str(out), original=False, timeout=600)   # Photos fetches it from iCloud
            applied['print'] = applied.get('print', []) + [photo]
            saved += 1
            _write(here / 'applied.json', applied)
    return {'album': added, 'print': saved}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='archive', description=__doc__.splitlines()[0])
    parser.add_argument('do', choices=('scan', 'apply'))
    parser.add_argument('--month')
    parser.add_argument('--since')
    args = parser.parse_args(argv)
    if args.do == 'apply':
        print(json.dumps(apply()))
        return 0
    if args.month and not MONTH.fullmatch(args.month):
        parser.error('--month is YYYY-MM')
    waiting = home() / 'catch-up'
    if not args.month and not args.since and waiting.exists():
        args.since = waiting.read_text().strip()
    since = dt.date.fromisoformat(args.since) if args.since else None
    wanted = [args.month] if args.month else months_between(since, dt.date.fromisoformat(last_month() + '-01')) \
        if since else [last_month()]
    print(json.dumps(scan(wanted, since)))
    if waiting.exists() and since and waiting.read_text().strip() == since.isoformat():
        waiting.unlink()
    return 0


if __name__ == '__main__':
    sys.exit(main())
