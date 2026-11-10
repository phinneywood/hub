"""What Hub knows about its own versions. It never installs anything.

A separate, fixed deployer on this Mac owns the releases folder. It leaves a note when a checked
version is ready (`pending.json`) and another after each attempt (`last.json`). All Hub can do is
show those and, when the owner taps Deploy, leave a note asking for the pending version
(`queue/request.json`). The deployer decides whether to act on it. Without HUB_RELEASES_DIR this is off.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

SHA = re.compile(r'[0-9a-f]{40}')
MAX_SUBJECTS = 8


def folder():
    value = os.environ.get('HUB_RELEASES_DIR')
    return Path(value) if value else None


def running():
    """The version this process was started from, when it runs from the releases folder."""
    name = Path(sys.prefix).resolve().parent.name
    return name if SHA.fullmatch(name) else None


def _note(*name):
    root = folder()
    if not root:
        return None
    try:
        data = json.loads(root.joinpath(*name).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get('sha'), str) and SHA.fullmatch(data['sha']) else None


def pending():
    """A checked version that isn't the one running: {'sha', 'subjects'}. None when there's nothing to deploy."""
    note = _note('pending.json')
    if not note or note['sha'] == running() or not (folder() / note['sha'] / 'venv').is_dir():
        return None
    subjects = note.get('subjects') if isinstance(note.get('subjects'), list) else []
    return {'sha': note['sha'], 'subjects': [str(s)[:120] for s in subjects[:MAX_SUBJECTS]]}


def last():
    """The deployer's most recent attempt: {'sha', 'status'} with status 'accepted' or 'rolled_back'."""
    note = _note('last.json')
    return {'sha': note['sha'], 'status': str(note.get('status'))} if note else None


def requested():
    return _note('queue', 'request.json') is not None


def request(login):
    """Ask the deployer for the pending version. Returns False when there is none or no deployer is listening."""
    ready = pending()
    if not ready or not (folder() / 'queue').is_dir():
        return False
    target = folder() / 'queue' / 'request.json'
    scratch = folder() / 'request.tmp'  # written beside the queue so the deployer never sees half a note
    scratch.write_text(json.dumps({'sha': ready['sha'], 'requested_at': time.time(), 'by': login}))
    os.replace(scratch, target)
    return True
