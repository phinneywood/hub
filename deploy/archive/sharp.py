"""Sharp photos for the full-screen viewer. Most photos keep only a small preview on this Mac (about 480 pixels; the
originals are in iCloud). Photos itself can export any of them, bringing the original from iCloud when needed; the
archive makes that a 2048-pixel picture and keeps it in an owner-only cache of at most 2 GB, the least recently
opened going first, so a photo opened again shows at once. Exporting changes nothing in the library.

Photos exports one at a time, so the newest request goes first, at most three wait their turn (older ones are let
go: nobody is looking at them any more), and only one viewer waits for its picture; the others are shown the Mac's own
at once and get the sharp one the next time. It needs the owner's one-time yes to the archive working Photos (asked
by `python -m archive photos-access` at the Mac, which keeps the answer: store.photos_answer). Until that yes, Photos
is never asked, and every viewer is shown the Mac's own picture. Photos saying the archive may not do that pauses sharp
photos for ten minutes; a photo Photos can't export is left alone for a day while the others carry on.
"""
import os
import threading
import time
from collections import deque

from . import macos, store

PIXELS = 2048
MOST = 2 * 1024 ** 3            # the cache's size, at most
KEEP = int(MOST * 0.9)          # and what a trim brings it down to
WAIT = 12                       # seconds the one waiting viewer waits before it is shown the best picture on the Mac
QUEUED = 3                      # exports waiting their turn, at most; the oldest beyond that are let go
BACK_OFF = 600                  # after Photos said the archive may not work it, this long before it is asked again
FAILED_FOR = 86400              # and a photo it couldn't export, this long
NOT_ALLOWED = ('-1743', 'not authorized', 'not allowed')
_queue, _lock, _ready = deque(), threading.Lock(), threading.Condition()
_done = {}                      # item -> threading.Event, for the one request waiting on it
_waiting = threading.Lock()     # held by the one request that waits
_refused = {'at': float('-inf')}
_failed = {}                    # item -> when Photos couldn't export it
_worker = {'thread': None, 'mac': None}


def folder():
    return store.home() / 'sharp'


def path(item):
    return store.sharp_path(item)


def cached(item):
    """A sharp picture already kept, marked as opened now, or None."""
    where = path(item)
    try:
        data = where.read_bytes()
    except OSError:
        return None
    os.utime(where)
    return data


def trim():
    """Let the least recently opened pictures go until the cache is under its size. Returns how many went."""
    found = []
    for file in folder().glob('*/*.jpg'):
        try:
            info = file.stat()
        except OSError:
            continue
        found.append((info.st_mtime, info.st_size, file))
    total = sum(size for _, size, _ in found)
    if total <= MOST:
        return 0
    gone = 0
    for _, size, file in sorted(found):
        if total <= KEEP:
            break
        file.unlink(missing_ok=True)
        total -= size
        gone += 1
    return gone


def state():
    """'waiting' until the owner has answered at the Mac, 'not allowed' while Photos' no stands, else 'allowed'."""
    answer = store.photos_answer()
    if answer == 'no' or time.monotonic() - _refused['at'] < BACK_OFF:
        return 'not allowed'
    return 'allowed' if answer == 'yes' else 'waiting'


def _may_ask():
    """Whether Photos may be asked now: the owner said yes at the Mac; or Photos said no, which macOS repeats at
    once without asking anyone, and enough time has passed to hear whether the owner has changed it since."""
    return store.photos_answer() in ('yes', 'no') and time.monotonic() - _refused['at'] >= BACK_OFF


def _export(item, uuid, mac):
    try:
        made = mac.sharp_picture(uuid, PIXELS, folder=store.owner_folder(store.home() / 'exporting'))
    except Exception as error:  # noqa: BLE001 - not allowed pauses every photo; anything else, this photo only
        if any(word in str(error).lower() for word in NOT_ALLOWED):
            _refused['at'] = time.monotonic()
            store.set_photos_answer('no')
        else:
            _failed[item] = time.time()
        return
    if store.photos_answer() != 'yes':
        store.set_photos_answer('yes')          # Photos answered: the owner has switched it back on
    if made:
        store.write_bytes(path(item), made[0])
        trim()
    else:
        _failed[item] = time.time()


def _work():
    while True:
        with _ready:
            while not _queue:
                _ready.wait()
            item, uuid = _queue.pop()                    # the newest request first
            mac = _worker['mac']
        if cached(item) is None and _may_ask():
            _export(item, uuid, mac)
        with _ready:
            event = _done.pop(item, None)
        if event:
            event.set()


def _ask(item, uuid, mac):
    """Put one export in the queue, newest last, letting the oldest go beyond QUEUED. Returns its event."""
    with _ready:
        _worker['mac'] = mac
        if _worker['thread'] is None:
            _worker['thread'] = threading.Thread(target=_work, name='sharp', daemon=True)
            _worker['thread'].start()
        if (item, uuid) in _queue:
            _queue.remove((item, uuid))
        _queue.append((item, uuid))
        while len(_queue) > QUEUED:
            gone, _ = _queue.popleft()
            event = _done.pop(gone, None)
            if event:
                event.set()
        event = _done.setdefault(item, threading.Event())
        _ready.notify()
        return event


def get(item, uuid, wait=WAIT, mac=None):
    """A sharp picture of one photo, from the cache or from Photos, or None to show the Mac's own instead."""
    mac = mac or macos
    kept = cached(item)
    if kept is not None or not _may_ask() or time.time() - _failed.get(item, 0) < FAILED_FOR:
        return kept
    event = _ask(item, uuid, mac)
    if not _waiting.acquire(blocking=False):
        return None                     # another viewer is waiting already: this one gets the Mac's picture now
    try:
        event.wait(wait)
    finally:
        _waiting.release()
    return cached(item)
