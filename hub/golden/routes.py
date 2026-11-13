"""The Golden tests page in Settings: the latest live result, the scenarios, and a button for a live dry run.

A run started here goes in its own process (`python -m hub.golden live`), so the dry-run layer never stands
in front of the running Hub's own Ask chats. Owner-only like every page; the button is same-origin only.
"""
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config, home
from ..templating import render
from . import runner, scenarios

router = APIRouter()


@router.get('', response_class=HTMLResponse)
def page(request: Request, said: str = ''):
    from ..engine import state
    return render(request, 'golden.html', active='settings', latest=runner.latest(), running=runner.running(),
                  refusal=runner.refusal(), scenarios=scenarios.SCENARIOS, cap=runner.RUN_COST_CAP,
                  month_cap=runner.MONTH_COST_CAP, said={'started': 'Started. It takes a minute or two; refresh to see it.'}.get(said, ''),
                  spent=state.spent(home.today().isoformat(), runner.KIND)['month_cost'])


@router.post('/run')
def start():
    if runner.refusal() is None:
        subprocess.Popen([sys.executable, '-m', 'hub.golden', 'live'], cwd=str(Path(__file__).resolve().parents[2]),
                         env={**os.environ, 'HUB_DATA_DIR': str(config.DATA_DIR)}, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return RedirectResponse('/golden?said=started', status_code=303)
