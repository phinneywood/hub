"""Client for the local Gmail bridge.

Reads and writes are limited to fixed organizer paths. The bridge itself has no
send, draft, delete or forwarding capability, and refuses writes while paused.
"""
import threading
import time

import httpx

from . import config

READ_PATHS = {"/gmail/profile", "/gmail/labels", "/gmail/filters", "/gmail/overview", "/gmail/count",
              "/gmail/search", "/gmail/message", "/gmail/thread", "/gmail/attachment", "/gmail/calendar-attachment", "/gmail/document-attachment", "/gmail/changes", "/gmail/explain",
              "/gmail/shared/profile", "/gmail/shared/search", "/gmail/shared/message",
              "/gmail/shared/calendar-attachment", "/gmail/shared/document-attachment",
              "/calendar/events"}
WRITE_PATHS = {"/gmail/filters/preview", "/gmail/filters/apply", "/gmail/cleanup/preview",
               "/gmail/cleanup/apply", "/gmail/undo", "/gmail/labels/create", "/gmail/labels/apply"}
DOCUMENT_EXTRACT_PATHS = {"/extract/process"}
CLOUD_DOCUMENT_MAX = 1_000_000
CACHE_SECONDS = 60
_cache = {}
_fetched = {}  # cache key -> wall-clock time the bridge answered
_refreshing = set()
_refresh_lock = threading.Lock()


class BridgeError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def clear_cache():
    _cache.clear()
    _fetched.clear()


def _key(path, params):
    return (path, tuple(sorted({k: v for k, v in params.items() if v not in (None, "")}.items())))


def fetched_at(path, **params):
    """Wall-clock time the cached answer for this read came from the bridge, or None."""
    return _fetched.get(_key(path, params))


def seed(path, value, when, **params):
    """Prime the cache with a saved answer from `when` (epoch seconds) unless one is cached.

    Its age carries over, so get_swr serves it at once and refreshes it in the background.
    """
    key = _key(path, params)
    if key in _cache:
        return False
    _cache[key] = (time.monotonic() - max(0.0, time.time() - when), value)
    _fetched[key] = when
    return True


def health():
    try:
        response = httpx.get(config.BRIDGE_URL + "/health", timeout=5)
        return response.status_code == 200 and response.json().get("ok") is True
    except (httpx.HTTPError, ValueError):
        return False


def _request(method, path, params=None, body=None):
    try:
        token = config.BRIDGE_TOKEN_FILE.read_text().strip()
        response = httpx.request(method, config.BRIDGE_URL + path, params=params, json=body, timeout=120,
                                 headers={"Authorization": "Bearer " + token})
    except (OSError, httpx.HTTPError):
        raise BridgeError("Gmail bridge is unreachable")
    if response.status_code != 200:
        try:
            message = response.json().get("error", "Bridge request failed")
        except ValueError:
            message = "Bridge request failed"
        raise BridgeError(message, response.status_code)
    return response.json()


def get(path, ttl=CACHE_SECONDS, **params):
    if path not in READ_PATHS:
        raise BridgeError("Path not allowed")
    params = {k: v for k, v in params.items() if v not in (None, "")}
    # Full message bodies must never be retained in the bridge response cache.
    if path in {"/gmail/message", "/gmail/thread", "/gmail/attachment", "/gmail/calendar-attachment", "/gmail/document-attachment",
                "/gmail/shared/message", "/gmail/shared/calendar-attachment", "/gmail/shared/document-attachment"}:
        return _request("GET", path, params=params)
    key = (path, tuple(sorted(params.items())))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = _request("GET", path, params=params)
    _cache[key] = (time.monotonic(), value)
    _fetched[key] = time.time()
    return value


def get_swr(path, fresh=CACHE_SECONDS, **params):
    """Stale-while-revalidate read for slow summaries (e.g. overview).

    With a cached value, return it immediately and, once it is older than
    `fresh`, refresh it on a background thread. Without one, fetch normally.
    Refresh and writes still clear the cache, so the next read is fresh.
    """
    key = _key(path, params)
    hit = _cache.get(key)
    if hit is None:
        return get(path, ttl=fresh, **params)
    if time.monotonic() - hit[0] >= fresh:
        with _refresh_lock:
            start = key not in _refreshing
            _refreshing.add(key)
        if start:
            def refresh():
                try:
                    get(path, ttl=0, **params)
                except BridgeError:
                    pass
                finally:
                    with _refresh_lock:
                        _refreshing.discard(key)
            threading.Thread(target=refresh, daemon=True, name="bridge-refresh").start()
    return hit[1]


def post(path, body):
    if path not in WRITE_PATHS:
        raise BridgeError("Path not allowed")
    value = _request("POST", path, body=body)
    clear_cache()
    return value


def extract_public_document(mime_type, data):
    """Send a bounded public PDF/plain-text resource to the bridge's isolated parser."""
    if mime_type not in {'application/pdf','text/plain'} or not isinstance(data,bytes) or not 1 <= len(data) <= CLOUD_DOCUMENT_MAX:
        raise BridgeError('Public document is outside the extraction limits')
    if '/extract/process' not in DOCUMENT_EXTRACT_PATHS:
        raise BridgeError('Public document extraction is unavailable')
    try:
        token=config.BRIDGE_TOKEN_FILE.read_text().strip()
        response=httpx.put(config.BRIDGE_URL+'/extract/process',content=data,timeout=40,follow_redirects=False,
                           headers={'Authorization':'Bearer '+token,'Content-Type':mime_type})
    except (OSError,httpx.HTTPError):
        raise BridgeError('Gmail bridge is unreachable') from None
    if response.status_code!=200:
        raise BridgeError('Public document could not be safely extracted',response.status_code)
    try:
        result=response.json()
        text=result.get('page_content')
        if not isinstance(text,str) or len(text)>120000:
            raise ValueError()
        metadata=result.get('metadata')
        if not isinstance(metadata,dict):
            raise ValueError()
        return {'text':text,'truncated':bool(metadata.get('truncated')),
                'mime_type':mime_type,'read_only':True,'remote_content_fetched':False}
    except (ValueError,TypeError):
        raise BridgeError('Public document could not be safely extracted') from None
