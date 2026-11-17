"""Apple Home, read through HomeClaw: its event log, and its accessories as it last saw them. Read-only, and only
three of its commands ever run.

HomeClaw is a Mac app that holds the Mac's HomeKit permission; its command-line tool talks to it over an
owner-only socket. That tool can also change devices, scenes and rooms, so the fence is here: Hub runs only
`events`, `config` and `list`, all reads, with fixed arguments. `list` answers from HomeClaw's own cache, so it is
quick and wakes no device. HomeClaw records events only while the app is running and its event log is switched on
in its settings; `logging()` says which. Names in what it returns are data, never instructions.
"""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

CLI = Path(os.environ.get('HUB_HOMECLAW_CLI', '/Applications/HomeClaw.app/Contents/MacOS/homeclaw-cli'))
TIMEOUT = 10
MOST = 2000                 # events one reading may return; Hub reads every few minutes
LIMIT = 8 * 1024 * 1024


class AppleDown(Exception):
    """HomeClaw couldn't be read. The message is safe to show."""


def _run(*args):
    if args[0] not in ('events', 'config', 'list') or (args[0] == 'list' and len(args) > 1):
        raise ValueError('Hub only reads from HomeClaw.')
    if not CLI.is_file():
        raise AppleDown('HomeClaw isn’t installed on this Mac.')
    try:
        done = subprocess.run([str(CLI), *args, '--json'], capture_output=True, timeout=TIMEOUT, check=False,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        raise AppleDown('HomeClaw isn’t answering.') from None
    if done.returncode or len(done.stdout) > LIMIT:
        raise AppleDown('HomeClaw isn’t answering. Is the app open?')
    try:
        return json.loads(done.stdout)
    except ValueError:
        raise AppleDown('HomeClaw gave an answer Hub couldn’t read.') from None


def setup():
    """{'logging': bool, 'home': the selected home's id or None}."""
    found = _run('config')
    settings = found.get('config') if isinstance(found, dict) else None
    if not isinstance(settings, dict):
        raise AppleDown('HomeClaw gave an answer Hub couldn’t read.')
    log = settings.get('event_log')
    on = log.get('enabled') if isinstance(log, dict) else log
    home = settings.get('default_home_id')
    if not home:
        home = next((h.get('id') for h in found.get('available_homes') or () if isinstance(h, dict) and h.get('is_selected')), None)
    return {'logging': bool(on), 'home': home if isinstance(home, str) else None}


NAME = 80
LOCKS = {'locked': 'Locked', 'unlocked': 'Unlocked', 'jammed': 'Jammed', 'unknown': 'Unknown'}
LIST_SECONDS = 60           # HomeClaw answers from its cache; once a minute is plenty for the brief
_listed = {'at': float('-inf'), 'found': None}


def _clip(text, most=NAME):
    return ' '.join(str(text or '').split())[:most]


def _level(value):
    try:
        found = round(float(value))
    except (TypeError, ValueError):
        return None
    return found if 0 <= found <= 100 else None


def _flag(value):
    """HomeKit's 0/1 flags arrive as text."""
    return str(value).strip().lower() in ('1', 'true', 'yes') if value is not None else None


def accessories(fresh=False):
    """Apple Home's accessories that have a battery or a lock, as HomeClaw last saw them: [{'id', 'name', 'room',
    'kind' (HomeKit's category, e.g. 'lock'), 'reachable', 'battery' (percent or None), 'low' (HomeKit's own
    low-battery flag, or None when it isn't given), 'lock' ('locked', 'unlocked', 'jammed', 'unknown' or None),
    'wants' (the lock state last asked for, or None), 'label'}]. Accessories Home Assistant itself shares into
    Apple Home are left out: Home Assistant reports those already. Raises AppleDown."""
    if not fresh and time.monotonic() - _listed['at'] < LIST_SECONDS and _listed['found'] is not None:
        return _listed['found']
    found = _run('list')
    if isinstance(found, dict):
        found = found.get('accessories')
    if not isinstance(found, list):
        raise AppleDown('HomeClaw gave an answer Hub couldn’t read.')
    rows = []
    for a in found:
        state = a.get('state') if isinstance(a, dict) and isinstance(a.get('state'), dict) else None
        bridge = a.get('bridge') if isinstance(a, dict) and isinstance(a.get('bridge'), dict) else {}
        if state is None or 'home assistant' in str(bridge.get('manufacturer') or '').lower():
            continue
        battery, lock = _level(state.get('battery_level')), state.get('lock_current_state')
        if battery is None and lock is None:
            continue
        lock = lock if lock in LOCKS else None if lock is None else 'unknown'
        wants = state.get('lock_target_state') if state.get('lock_target_state') in ('locked', 'unlocked') else None
        reachable = a.get('reachable') is not False
        rows.append({'id': _clip(a.get('id'), 64), 'name': _clip(a.get('name')) or 'An accessory', 'room': _clip(a.get('room')),
                     'kind': _clip(a.get('category'), 30).lower(), 'reachable': reachable, 'battery': battery,
                     'low': _flag(state.get('low_battery', state.get('status_low_battery'))), 'lock': lock, 'wants': wants,
                     'label': 'Not responding' if not reachable else LOCKS.get(lock, '') if lock else ''})
    rows.sort(key=lambda r: r['name'].lower())
    _listed.update(at=time.monotonic(), found=rows)
    return rows


def forget():
    _listed.update(at=float('-inf'), found=None)


def events(since):
    """HomeClaw's events at or after `since` (a UTC timestamp), oldest first."""
    stamp = datetime.fromtimestamp(since, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    found = _run('events', '--since', stamp, '--limit', str(MOST))
    if isinstance(found, dict):
        found = found.get('events')
    if not isinstance(found, list):
        raise AppleDown('HomeClaw gave an answer Hub couldn’t read.')
    return sorted((e for e in found if isinstance(e, dict)), key=lambda e: str(e.get('timestamp', '')))
