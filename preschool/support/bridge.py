"""Client for the local Gmail bridge.

Reads and writes are limited to fixed organizer paths. The bridge itself has no
send, draft, delete or forwarding capability, and refuses writes while paused.
"""
import time

import httpx

from . import config

READ_PATHS = {"/gmail/profile", "/gmail/labels", "/gmail/filters", "/gmail/overview", "/gmail/count",
              "/gmail/search", "/gmail/message", "/gmail/attachment", "/gmail/calendar-attachment", "/gmail/document-attachment", "/gmail/changes", "/gmail/explain",
              "/gmail/shared/profile", "/gmail/shared/search", "/gmail/shared/message",
              "/gmail/shared/calendar-attachment", "/gmail/shared/document-attachment"}
WRITE_PATHS = frozenset()  # Preschool has no mailbox mutation authority.
DOCUMENT_EXTRACT_PATHS = {"/extract/process"}
CLOUD_DOCUMENT_MAX = 1_000_000
CACHE_SECONDS = 60
_cache = {}


class BridgeError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def clear_cache():
    _cache.clear()


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
    if path in {"/gmail/message", "/gmail/attachment", "/gmail/calendar-attachment", "/gmail/document-attachment",
                "/gmail/shared/message", "/gmail/shared/calendar-attachment", "/gmail/shared/document-attachment"}:
        return _request("GET", path, params=params)
    key = (path, tuple(sorted(params.items())))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = _request("GET", path, params=params)
    _cache[key] = (time.monotonic(), value)
    return value


def post(path, body):
    if path not in WRITE_PATHS:
        raise BridgeError("Path not allowed")
    value = _request("POST", path, body=body)
    _cache.clear()
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
