"""Photos and videos, read from the Photos library's own database with osxphotos, never through Photos itself.

Every photo and video in the owner's library and the shared library, except hidden ones, ones in Recently Deleted
and the extra shots of a burst. For each: when and where it was taken, who Photos knows in it, what Photos sees in
it (its own labels and the text it read in it), titles, captions, keywords, albums, favorite, its score, and a
400-pixel preview made from the preview Photos keeps on this Mac.

What Photos read in a photo of an ID document is left out: those photos stay findable by everything else.

Nothing about a photo (the picture, a preview, a label, a person, a place, a date, even how many) goes to a model.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from . import macos, store
from .text import words

THUMB = 400
PICTURES = ('.jpeg', '.jpg', '.heic', '.heif', '.png', '.tif', '.tiff')
BABY, CHILD = 1, 2          # Photos' own face age types: on this kind of library they match its Baby and Child groups
KNOWN_AGES = (1, 2, 3, 4, 5)
KID_DAYS = 730              # a person is a kid when their faces of the last two years are mostly a baby's or a child's
KID_SHARE = 0.5
KID_FACES = 10
UNKNOWN = '_UNKNOWN_'


def library():
    import osxphotos
    path = os.environ.get('HUB_ARCHIVE_LIBRARY') or str(Path.home() / 'Pictures' / 'Photos Library.photoslibrary')
    store.readable(Path(path) / 'database' / 'Photos.sqlite')          # without Full Disk Access, say so plainly
    return osxphotos.PhotosDB(dbfile=path)


def _named(name):
    return bool(name) and name != UNKNOWN


def _search(photo):
    try:
        return photo.search_info
    except Exception:  # noqa: BLE001 - an older library has no search information
        return None


def _field(info, name):
    try:
        value = getattr(info, name) if info is not None else None
    except Exception:  # noqa: BLE001
        return []
    if not value:
        return []
    return [value] if isinstance(value, str) else [v for v in value if v]


def _local(paths):
    return [p for p in paths or () if p and os.path.exists(p)]


def _faces(photo):
    return [f for f in (getattr(photo, 'face_info', None) or []) if not getattr(f, 'intrash', False)]


def describe(photo):
    """(item, parts, people) for one photo or video, or None for one the archive leaves out."""
    if photo.hidden or photo.intrash or not photo.date:
        return None
    if getattr(photo, 'burst', False) and not getattr(photo, 'burst_key', True):
        return None
    info = _search(photo)
    movie = bool(photo.ismovie)
    people = sorted({p for p in (photo.persons or []) if _named(p)})
    faces = _faces(photo)
    unnamed = [f for f in faces if not _named(getattr(f, 'name', None))]
    groups = set(_field(info, 'age_groups'))
    young = any(getattr(f, 'age_type', None) in (BABY, CHILD) for f in unnamed) or bool(unnamed and groups & {'Baby', 'Child'})
    place = getattr(photo.place, 'name', '') if getattr(photo, 'place', None) else ''
    ids = bool(_field(info, 'id_document_types'))
    seen_text = '' if ids else '\n'.join(_field(info, 'detected_text'))
    labels = _field(info, 'labels')
    albums = sorted(set(photo.albums or []))
    kinds = [w for w, on in (('video', movie), ('screenshot', getattr(photo, 'screenshot', False)),
                             ('selfie', getattr(photo, 'selfie', False)), ('panorama', getattr(photo, 'panorama', False)),
                             ('portrait', getattr(photo, 'portrait', False)), ('live photo', getattr(photo, 'live_photo', False)),
                             ('favorite', photo.favorite)) if on]
    head = words(photo.title, photo.description, photo.keywords, labels, place, *(_field(info, name) for name in (
        'place_names', 'neighborhoods', 'city', 'state', 'country', 'landmarks', 'venues', 'venue_types', 'bodies_of_water',
        'holidays', 'activities', 'events', 'human_actions', 'pets', 'document_types', 'media_types')),
        sorted(groups), people, albums, kinds)
    derivatives = sorted(_local(photo.path_derivatives), key=os.path.getsize)
    original = photo.path if photo.path and photo.path.lower().endswith(PICTURES) and os.path.exists(photo.path) else None
    biggest = original or (max(derivatives, key=os.path.getsize) if derivatives else None)
    duration = None
    if movie:
        try:
            duration = float(photo.exif_info.duration) if photo.exif_info and photo.exif_info.duration else None
        except (AttributeError, TypeError, ValueError):
            duration = None
    score = photo.score.overall if getattr(photo, 'score', None) and photo.score.overall is not None else None
    when = photo.date
    item = {'id': 'p:' + photo.uuid, 'source': 'photos', 'kind': 'video' if movie else 'photo', 'title': photo.title or '',
            'at': when.timestamp(), 'day': when.date().isoformat(), 'place': place or '',
            'size': getattr(photo, 'original_filesize', None), 'duration': duration, 'favorite': photo.favorite,
            'shared': getattr(photo, 'shared_library', False), 'screenshot': getattr(photo, 'screenshot', False),
            'young': young, 'score': score, 'path': biggest,
            'meta': {'albums': albums, 'labels': labels, 'keywords': sorted(set(photo.keywords or [])),
                     'description': photo.description or '', 'thumb_from': derivatives[0] if derivatives else biggest,
                     'edited': str(getattr(photo, 'date_modified', '') or '')}}
    return item, [{'head': head, 'body': seen_text}], people


def signature(item, parts, people):
    """What a photo looked like when it was indexed, to leave it alone next time if nothing changed."""
    value = {k: v for k, v in item.items() if k not in ('sig', 'seen', 'thumb', 'w', 'h')}
    return hashlib.sha1(json.dumps([value, parts, people], sort_keys=True, default=str).encode()).hexdigest()


def make_thumb(item):
    """The 400-pixel preview of a photo, from the smallest preview Photos keeps that will do. (w, h) or None."""
    source = item['meta'].get('thumb_from')
    if not source:
        return None
    made = macos.picture(source, THUMB, quality=0.72)
    if made is None:
        return None
    data, w, h = made
    store.write_bytes(store.thumb_path(item['id']), data)
    return w, h


def kids(faces, now=None):
    """The named people who look like kids: their faces over the last two years they were photographed are mostly
    a baby's or a child's, and they were photographed in the last two years. `faces`: {name: [(when, age type)]}."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = set()
    for name, seen in faces.items():
        if not seen:
            continue
        last = max(when for when, _ in seen)
        if (now - last).days > KID_DAYS:
            continue
        recent = [age for when, age in seen if (last - when).days <= KID_DAYS and age in KNOWN_AGES]
        if len(recent) >= KID_FACES and sum(age in (BABY, CHILD) for age in recent) / len(recent) >= KID_SHARE:
            out.add(name)
    return out


def index(conn, db=None, thumb=make_thumb, now=None, say=print):
    """Bring the photos in the index up to date. Returns counts. Raises if the library can't be read, so that
    nothing is removed from the index on a run that saw nothing."""
    started = now or dt.datetime.now(dt.timezone.utc)
    stamp = started.timestamp()
    db = db or library()
    every = db.photos(images=True, movies=True)
    had = store.known(conn, 'photos')
    if had and not every:
        raise RuntimeError('the library answered with no photos at all')
    counts = {'items': 0, 'new': 0, 'changed': 0, 'thumbs': 0, 'no_preview': 0, 'videos': 0, 'shared': 0}
    errors = {}
    faces, keep = {}, set()
    for photo in every:
        try:
            described = describe(photo)
        except Exception as error:  # noqa: BLE001 - one odd photo never stops the rest
            errors[type(error).__name__] = errors.get(type(error).__name__, 0) + 1
            continue
        if described is None:
            continue
        item, parts, people = described
        keep.add(item['id'])
        counts['items'] += 1
        counts['videos'] += item['kind'] == 'video'
        counts['shared'] += bool(item['shared'])
        for face in _faces(photo):
            if _named(getattr(face, 'name', None)):
                faces.setdefault(face.name, []).append((photo.date, getattr(face, 'age_type', None)))
        item['sig'] = signature(item, parts, people)
        item['seen'] = stamp
        old = had.get(item['id'])
        has_thumb = store.thumb_path(item['id']).exists()
        if old == item['sig'] and has_thumb:
            continue
        if old != item['sig']:
            counts['new' if old is None else 'changed'] += 1
        made = None
        try:
            made = thumb(item)
        except Exception as error:  # noqa: BLE001
            errors['preview ' + type(error).__name__] = errors.get('preview ' + type(error).__name__, 0) + 1
        if made:
            item['thumb'], (item['w'], item['h']) = 1, made
            counts['thumbs'] += 1
        else:
            counts['no_preview'] += 1
        store.put(conn, item, parts, people)
        if (counts['new'] + counts['changed']) % 500 == 499:
            conn.commit()
            say(f'photos: {counts["items"]} read, {counts["thumbs"]} previews made')
    store.seen(conn, [i for i in keep if had.get(i) is not None], stamp)
    gone = [i for i in had if i not in keep]
    store.forget(conn, gone)
    counts['removed'] = len(gone)
    _people(conn, faces, started)
    counts['people'] = conn.execute('select count(*) from people where photos > 0').fetchone()[0]
    counts['kids'] = conn.execute('select count(*) from people where kid = 1').fetchone()[0]
    conn.commit()
    return counts, errors


def _people(conn, faces, now):
    """Count each person's photos, note when they were last photographed, and whether they look like a kid."""
    young = kids(faces, now)
    conn.execute('update people set photos = (select count(*) from item_people where person = people.id)')
    conn.execute('delete from people where photos = 0')
    for row in conn.execute('select id, name from people').fetchall():
        seen = faces.get(row['name']) or []
        last = max((when for when, _ in seen), default=None)
        conn.execute('update people set kid = ?, last = ? where id = ?',
                     (int(row['name'] in young), last.timestamp() if last else None, row['id']))
