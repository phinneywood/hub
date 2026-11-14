"""The Checks page in Settings: what the live checks say now, each one's history, and a Run button.

A run started here goes in its own process (`python -m hub.live run`), so a slow check never holds a page.
Owner-only like every page; the button is same-origin only. The button never allows the `undo` check: that
one runs only from the morning job or the command line, when the owner has switched it on.
"""
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config
from ..templating import render
from . import checks, runner

router = APIRouter()
SAID = {'started': 'Started. It takes about a minute; refresh to see it.', 'busy': 'A run is already going.'}


def start(trigger, *extra):
    """Start a run in its own process. False when one is already going."""
    if runner.running():
        return False
    subprocess.Popen([sys.executable, '-m', 'hub.live', 'run', '--trigger', trigger, *extra],
                     cwd=str(Path(__file__).resolve().parents[2]), env={**os.environ, 'HUB_DATA_DIR': str(config.DATA_DIR)},
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def morning():
    """With the morning job: a run, when the owner has switched daily checks on in the private settings."""
    chosen = checks.settings()
    if chosen.get('daily') is not True:
        return False
    return start('morning', *(['--with-undo'] if chosen.get('undo') is True else []))


@router.get('', response_class=HTMLResponse)
def page(request: Request, said: str = ''):
    history = runner.history()
    failing = {f['id']: f for f in runner.failing()}
    rows = [{'check': c, 'history': history.get(c.id, []), 'failing': failing.get(c.id),
             'last': (history.get(c.id) or [None])[-1]} for c in checks.CHECKS]
    chosen = checks.settings()
    return render(request, 'live.html', active='settings', rows=rows, latest=runner.latest(), running=runner.running(),
                  said=SAID.get(said, ''), daily=chosen.get('daily') is True, undo=chosen.get('undo') is True,
                  keep_days=runner.KEEP_DAYS)


@router.post('/run')
def run_now():
    return RedirectResponse('/checks?said=' + ('started' if start('manual') else 'busy'), status_code=303)
