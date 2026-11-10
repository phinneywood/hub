"""Narrow, read-only Home Assistant API client for the Network tool."""
import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ipaddress
from urllib.parse import urlsplit

import httpx


def _base_url(value):
    """Loopback by default. A deployment may name Home Assistant elsewhere: over verified HTTPS,
    or over plain HTTP only at a private address on the home network."""
    if not value:
        return "http://127.0.0.1:8123"
    problem = ValueError("HUB_NETWORK_HA_URL must be https://<host>[:port] or http://<private address>[:port]")
    parts = urlsplit(value)
    try:
        host, port = parts.hostname or "", parts.port
    except ValueError:
        raise problem from None
    if (parts.scheme not in {"http", "https"} or not host or ":" in host or parts.username or parts.password
            or parts.path.strip("/") or parts.query or parts.fragment):
        raise problem
    if parts.scheme == "http":
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise problem from None
        if not (address.is_private or address.is_loopback):
            raise problem
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


BASE_URL = _base_url(os.environ.get("HUB_NETWORK_HA_URL"))
TOKEN_FILE = Path(os.environ.get(
    "HUB_NETWORK_HA_TOKEN_FILE",
    Path.home() / "Library" / "Application Support" / "Hub" / "network-ha-token"))
ENTITY_ID = re.compile(r"^(sensor|binary_sensor|device_tracker)\.[a-z0-9_]{1,100}$")
MAX_STATES = 8 * 1024 * 1024
MAX_HISTORY = 5 * 1024 * 1024
MAX_HISTORY_ENTITIES = 12


class HomeAssistantError(Exception):
    """Safe, user-facing Home Assistant connection or response error."""


def configured():
    return TOKEN_FILE.exists()


def _token():
    try:
        info = TOKEN_FILE.lstat()
    except FileNotFoundError:
        raise HomeAssistantError("Home Assistant access has not been configured in Hub.") from None
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 8192):
        raise HomeAssistantError("The private Home Assistant token file has unsafe permissions.")
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise HomeAssistantError("The private Home Assistant token file could not be read.") from None
    if not 32 <= len(token) <= 8192 or any(ch.isspace() for ch in token):
        raise HomeAssistantError("The private Home Assistant token file is invalid.")
    return token


def _request(path, *, params=None, limit=MAX_STATES):
    """Make only bounded GET requests to the one configured Home Assistant API."""
    history_prefix = "/api/history/period/"
    history_timestamp = path.removeprefix(history_prefix) if path.startswith(history_prefix) else ""
    valid_history = False
    if history_timestamp:
        try:
            valid_history = datetime.fromisoformat(history_timestamp).tzinfo is not None
        except ValueError:
            pass
    if path not in {"/api/", "/api/states"} and not valid_history:
        raise HomeAssistantError("That Home Assistant API route is not available to Hub.")
    token = _token()
    url = BASE_URL + path
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", url, params=params, headers=headers) as response:
                if response.status_code == 401:
                    raise HomeAssistantError(
                        "Home Assistant rejected Hub's saved token (HTTP 401). "
                        "It may have been revoked or become invalid. Store a valid Home Assistant "
                        "long-lived access token using deploy/set-network-ha-token at its hidden prompt. "
                        "Do not paste the token into chat.")
                if response.status_code == 403:
                    raise HomeAssistantError(
                        "Home Assistant denied this read (HTTP 403). Check that Hub's Home Assistant "
                        "user is active and permitted to read the requested data. "
                        "Do not grant administrator access just to troubleshoot this.")
                if response.status_code == 429:
                    raise HomeAssistantError(
                        "Home Assistant is rate-limiting reads (HTTP 429). Wait before refreshing; "
                        "do not replace the token or restart the network to resolve rate limiting.")
                if 500 <= response.status_code <= 599:
                    raise HomeAssistantError(
                        "Home Assistant reported a server error while reading data. "
                        "Network condition remains unknown; replacing the token is not indicated by this error.")
                if response.status_code != 200:
                    raise HomeAssistantError("Home Assistant did not return the requested read-only data.")
                body = bytearray()
                for chunk in response.iter_bytes(16384):
                    body.extend(chunk)
                    if len(body) > limit:
                        raise HomeAssistantError("Home Assistant returned more data than Hub accepts.")
        return json.loads(body)
    except HomeAssistantError:
        raise
    except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError):
        raise HomeAssistantError("Home Assistant is not reachable from Hub right now.") from None


def states():
    raw = _request("/api/states")
    if not isinstance(raw, list):
        raise HomeAssistantError("Home Assistant returned an unexpected state list.")
    return [item for item in raw if isinstance(item, dict) and ENTITY_ID.fullmatch(str(item.get("entity_id", "")))
            and isinstance(item.get("attributes"), dict)]


def history(entity_ids, start, end=None, *, include_attributes=False):
    """Read a bounded set of entity histories from Home Assistant Recorder."""
    if not isinstance(entity_ids, (list, tuple)) or not 1 <= len(entity_ids) <= MAX_HISTORY_ENTITIES:
        raise HomeAssistantError("Choose between 1 and 12 Home Assistant entities for history.")
    if any(not isinstance(entity_id, str) or not ENTITY_ID.fullmatch(entity_id) for entity_id in entity_ids):
        raise HomeAssistantError("A Home Assistant entity identifier is invalid.")
    now = datetime.now(timezone.utc)
    if not isinstance(start, datetime) or start.tzinfo is None or start < now - timedelta(days=30, seconds=60) or start > now:
        raise HomeAssistantError("The Home Assistant history range is outside the allowed window.")
    if end is None:
        end = now
    if not isinstance(end, datetime) or end.tzinfo is None or end <= start or end > now + timedelta(minutes=1):
        raise HomeAssistantError("The Home Assistant history interval is invalid.")
    path = "/api/history/period/" + start.astimezone(timezone.utc).isoformat()
    params = {"filter_entity_id": ",".join(dict.fromkeys(entity_ids)),
              "end_time": end.astimezone(timezone.utc).isoformat(),
              "significant_changes_only": "0"}
    if not include_attributes:
        params.update({"minimal_response": "1", "no_attributes": "1"})
    raw = _request(path, params=params, limit=MAX_HISTORY)
    if (not isinstance(raw, list) or any(not isinstance(group, list) for group in raw)
            or any(not isinstance(row, dict) for group in raw for row in group)):
        raise HomeAssistantError("Home Assistant Recorder history is unavailable or malformed.")
    return raw
