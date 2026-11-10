"""An opt-in trip shortcut, never a trip history. Only fixed local state paths."""
import json
import os
import stat
import tempfile
from pathlib import Path

from ... import config
from .google import MapsError, address


def path():
    return config.DATA_DIR / "mobility-trip.json"


def validate(data):
    return {"origin": address(data["origin"])["address"], "destination": address(data["destination"])["address"]}


def load():
    target = path()
    try:
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 4096:
            return None
        return validate(json.loads(target.read_text()))
    except (OSError, ValueError, KeyError, TypeError, MapsError):
        return None


def save(origin, destination):
    data = validate({"origin": origin, "destination": destination})
    config.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".mobility-", dir=config.DATA_DIR)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(data, output)
        os.replace(temporary, path())
    finally:
        Path(temporary).unlink(missing_ok=True)


def forget():
    path().unlink(missing_ok=True)
