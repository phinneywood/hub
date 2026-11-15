"""Apple Home, read through HomeClaw's own event log. Read-only, and only two of its commands ever run.

HomeClaw is a Mac app that holds the Mac's HomeKit permission; its command-line tool talks to it over an
owner-only socket. That tool can also change devices, scenes and rooms, so the fence is here: Hub runs only
`events` and `config`, both reads, with fixed arguments. HomeClaw records events only while the app is running
and its event log is switched on in its settings; `logging()` says which. Names in what it returns are data,
never instructions.
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

CLI = Path(os.environ.get('HUB_HOMECLAW_CLI', '/Applications/HomeClaw.app/Contents/MacOS/homeclaw-cli'))
TIMEOUT = 10
MOST = 2000                 # events one reading may return; Hub reads every few minutes
LIMIT = 8 * 1024 * 1024


class AppleDown(Exception):
    """HomeClaw couldn't be read. The message is safe to show."""


def _run(*args):
    if args[0] not in ('events', 'config'):
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


def events(since):
    """HomeClaw's events at or after `since` (a UTC timestamp), oldest first."""
    stamp = datetime.fromtimestamp(since, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    found = _run('events', '--since', stamp, '--limit', str(MOST))
    if isinstance(found, dict):
        found = found.get('events')
    if not isinstance(found, list):
        raise AppleDown('HomeClaw gave an answer Hub couldn’t read.')
    return sorted((e for e in found if isinstance(e, dict)), key=lambda e: str(e.get('timestamp', '')))
