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


# ---- Automatic deploys and Undo (the deployer's `auto`, home-ops docs/HUB-DEPLOYER.md) ----

UNDO_HOURS = 24             # the deployer honours an Undo this long after a deploy


def _current():
    """The version the releases folder's `current` points at, or None."""
    try:
        name = os.readlink(folder() / 'current')
    except (OSError, TypeError):
        return None
    return name if SHA.fullmatch(name) else None


def _age(note):
    """Seconds since a note's `at` (an ISO time), or None."""
    from datetime import datetime
    try:
        return time.time() - datetime.fromisoformat(note['at']).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def undoable():
    """The last deploy while Undo can still put back the version from before it: {'sha', 'previous', 'at', 'auto'}.
    None unless it was accepted less than UNDO_HOURS ago, is still running, wasn't itself an Undo, and the version
    from before is still here. The deployer checks all of it again. A receipt without `how` comes from a deployer
    that predates Undo, which would refuse it."""
    note = _note('last.json')
    previous = note and note.get('previous')
    if not note or note.get('status') != 'accepted' or note.get('how') not in ('tap', 'auto') or _current() != note['sha'] \
            or not isinstance(previous, str) or not SHA.fullmatch(previous) or not (folder() / previous / 'venv').is_dir():
        return None
    age = _age(note)
    if age is None or not 0 <= age < UNDO_HOURS * 3600:
        return None
    return {'sha': note['sha'], 'previous': previous, 'at': note['at'], 'auto': note.get('how') == 'auto',
            'until': time.time() - age + UNDO_HOURS * 3600}


def undo(login):
    """Ask the deployer for the version from before the last deploy. False when there is none to go back to, no deployer
    is listening, or a request is already waiting."""
    found = undoable()
    if not found or not (folder() / 'queue').is_dir() or requested():
        return False
    target = folder() / 'queue' / 'request.json'
    scratch = folder() / 'request.tmp'
    scratch.write_text(json.dumps({'sha': found['previous'], 'undo': True, 'requested_at': time.time(), 'by': login}))
    os.replace(scratch, target)
    return True


def tried():
    """What the deployer's last automatic try did, within a day: {'sha', 'status', 'error', 'at'}, or None."""
    note = _note('auto.json')
    age = _age(note) if note else None
    if not note or note.get('status') in (None, 'started') or age is None or age > UNDO_HOURS * 3600:
        return None
    error = str(note.get('error') or '').strip().splitlines()
    return {'sha': note['sha'], 'status': str(note['status']), 'error': error[0].rstrip(':. ')[:160] if error else '', 'at': note['at']}


def _what(sha):
    """What a release is, in a few words: its entry in versions.json when that entry was written for it (it follows the
    version before), else its first commit subject that isn't a merge."""
    from ..app import versions
    top, last = (versions.entries() or [{}])[0], _note('last.json')
    if top.get('sha') is None and top.get('title') and last and last['sha'] == sha and top.get('from') == last.get('previous'):
        return top['title']
    note = _note('pending.json')
    subjects = [str(s) for s in (note.get('subjects') or ()) if not str(s).startswith('Merge ')] if note and note['sha'] == sha else []
    return subjects[0][:120] if subjects else 'A new version'


def notice():
    """What to tell the owner about the last automatic deploy: (title, words), or None when there is nothing to say."""
    note = tried()
    if not note:
        return None
    if note['status'] == 'accepted':
        return 'Hub updated itself', f'{_what(note["sha"])}. If something is off, Undo is on Versions for a day.'
    if note['status'] == 'rolled_back':
        return 'A Hub update didn’t hold', 'A check failed after the switch, so the version from before is running again.'
    if note['status'] == 'recovery_needed':
        return 'Hub may be down after an update', 'Going back to the version from before didn’t finish. Look at Hub.'
    if note['status'] == 'checks_failed':
        return 'A Hub update wasn’t deployed', 'Its checks on GitHub failed, so nothing changed.'
    return 'A Hub update is waiting for you', (note['error'] or 'It couldn’t be staged') + '. Nothing changed.'


def tell():
    """Started by the deployer after an automatic deploy: the notice on the owner's phone, through Home Assistant's
    notify to the phone named in the private settings (`"deploy": {"notify": "mobile_app_..."}`). Today shows the
    same from the deployer's notes by itself. Returns what happened, in words for the deployer's log."""
    said = notice()
    if not said:
        return 'Nothing to tell.'
    from .. import household
    from ..tools.house import broker
    try:
        phone = household.section('deploy').get('notify')
    except ValueError:
        phone = None
    if not isinstance(phone, str) or not phone.startswith('mobile_app_'):
        return 'Shown on Today; no phone is named in Hub’s settings.'
    try:
        broker.setup_call('notify', phone, {'title': said[0], 'message': said[1]})
    except (ValueError, broker.HomeDown, broker.NotSent, broker.Rejected) as error:
        return f'Shown on Today; the phone notice failed ({type(error).__name__}).'
    return 'Told the owner on their phone and on Today.'


if __name__ == '__main__':      # python -m hub.engine.release tell
    if sys.argv[1:] != ['tell']:
        sys.exit('usage: python -m hub.engine.release tell')
    print(tell())
