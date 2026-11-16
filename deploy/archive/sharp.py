"""Sharp photos for the full-screen viewer. Most photos keep only a small preview on this Mac (about 480 pixels; the
originals are in iCloud). Photos itself can export any of them, bringing the original from iCloud when needed; the
archive makes that a 2048-pixel picture and keeps it in an owner-only cache of at most 2 GB, the least recently
opened going first, so a photo opened again shows at once. Exporting changes nothing in the library.

It needs the owner's one-time yes to the archive working Photos (asked by `python -m archive photos-access` at the
Mac, the same question the curator's albums need). Without it, or without the network or the time, the viewer keeps
the best picture the Mac has; after a refusal it doesn't ask Photos again for ten minutes. A picture that takes longer
than a viewer waits is still made, and kept for next time.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as Late

from . import macos, store

PIXELS = 2048
MOST = 2 * 1024 ** 3            # the cache's size, at most
KEEP = int(MOST * 0.9)          # and what a trim brings it down to
WAIT = 12                       # seconds a viewer waits for one before it is shown the best picture on the Mac
BACK_OFF = 600                  # after Photos said no, this long before it is asked again
_fetching = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sharp')      # Photos exports one at a time anyway
_pending, _lock = {}, threading.Lock()
_refused = {'at': float('-inf')}


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


def _fetch(item, uuid, mac):
    try:
        made = mac.sharp_picture(uuid, PIXELS, folder=store.owner_folder(store.home() / 'exporting'))
    except Exception:  # noqa: BLE001 - not allowed, not answering, or not in iCloud: wait a while before asking again
        _refused['at'] = time.monotonic()
        made = None
    finally:
        with _lock:
            _pending.pop(item, None)
    if made:
        store.write_bytes(path(item), made[0])
        trim()
        return made[0]
    return None


def get(item, uuid, wait=WAIT, mac=None):
    """A sharp picture of one photo, from the cache or from Photos, or None to show the Mac's own instead."""
    mac = mac or macos
    kept = cached(item)
    if kept is not None or time.monotonic() - _refused['at'] < BACK_OFF:
        return kept
    with _lock:
        future = _pending.get(item)
        if future is None:
            future = _pending[item] = _fetching.submit(_fetch, item, uuid, mac)
    try:
        return future.result(timeout=wait)
    except Late:
        return None             # it is still coming, and is kept for the next time it is opened
