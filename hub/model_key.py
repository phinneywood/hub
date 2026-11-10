"""Where Hub's model key comes from. Shared by Hub's and Preschool's model routes.

1Password first: a read-only service account limited to one vault, whose token is the one
secret kept in the macOS Keychain (owner-approved exception, 2026-11-09), so Hub can read the key
unattended and after a restart. `op` gets the token only in its own environment, never as an
argument, and nothing is logged. If that isn't set up or doesn't answer, the older Keychain item
is used until the owner removes it. Values are never printed or written anywhere.
"""
import os
import subprocess
import time
from pathlib import Path

LEGACY_SERVICE = "hub-openrouter"
TOKEN_SERVICE = "hub-1password-token"
REFERENCE = os.environ.get("HUB_OPENROUTER_REF", "op://Hub/OpenRouter/credential")
OP = Path(os.environ.get("HUB_OP_PATH", Path.home() / ".local/bin/op"))
CACHE_SECONDS = 300
_cache = {"value": None, "source": None, "at": float("-inf")}


def _keychain(service):
    try:
        out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service, "-a", "hub", "-w"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _exists(service):
    """Whether the item is there. Asks without `-w`, so the value is never read."""
    try:
        out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service, "-a", "hub"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0


def legacy_present():
    """Whether the older Keychain copy of the key still exists, for the reminder to remove it."""
    return _exists(LEGACY_SERVICE)


def _onepassword():
    token = _keychain(TOKEN_SERVICE)
    if not token:
        return None
    try:
        out = subprocess.run([str(OP), "read", "--no-newline", REFERENCE], capture_output=True, text=True, timeout=20,
                             env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "OP_SERVICE_ACCOUNT_TOKEN": token})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def read(fresh=False):
    """The key, or None. Cached for a few minutes so a page view doesn't reach 1Password each time."""
    if fresh or time.monotonic() - _cache["at"] >= CACHE_SECONDS or not _cache["value"]:
        value, source = _onepassword(), "1password"
        if not value:
            value, source = _keychain(LEGACY_SERVICE), "keychain"
        _cache.update(value=value, source=source if value else None, at=time.monotonic())
    return _cache["value"]


def source(fresh=False):
    """'1password', 'keychain' or None. For the status page; never the value."""
    read(fresh)
    return _cache["source"]
