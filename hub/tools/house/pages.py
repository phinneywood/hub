"""Home's own pages: a link out to Home Assistant, the taps that change things, and Hub's record of changes.

Home Assistant has its own dashboards, so Hub doesn't copy them: /house opens Home Assistant. Changes need a
tap from a signed-in device (a program on this Mac has no Tailscale login and can't change the house).
"""
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import home
from ...templating import render
from . import control, read

router = APIRouter()
BACK = ('/today', '/house/changes')


def _login(request):
    return request.headers.get("tailscale-user-login")


def _back(back, batch=None):
    where = back if back in BACK else '/today'
    return RedirectResponse(f'{where}?home={batch}' if batch else where, status_code=303)


@router.get("")
def house():
    return RedirectResponse(read.links().get('assistant_url') or '/house/changes', status_code=302)


@router.get("/changes", response_class=HTMLResponse)
def changes(request: Request):
    return _changes(request)


def _changes(request, problem=''):
    rows = control.recent()
    for r in rows:
        r['when'] = datetime.fromtimestamp(r['ts'], home.PACIFIC).strftime('%a %-I:%M %p')
    return render(request, "house/changes.html", active="house", rows=rows, links=read.links(),
                  problem=problem[:200])


@router.post("/do")
def do(request: Request, target: str = Form(...), action: str = Form(...), back: str = Form('/today')):
    """One tap from Today: “All lights off” (target lights-on) or a speaker's Pause."""
    if _login(request) is None:
        return HTMLResponse("Change the house from your phone or laptop.", status_code=403)
    try:
        found = read.snapshot(fresh=True)
        if target == 'lights-on':
            targets = [d['id'] for r in found['rooms'] for d in r['devices'] if d['kind'] == 'light' and d['on']]
        else:
            targets = [target]
        result = control.change(targets, action, login=_login(request), via='today')
    except (control.Refused, read.broker.HomeDown) as error:
        return _changes(request, problem=str(error))
    return _back(back, result['batch'])


@router.post("/undo/{batch}")
def undo(batch: str, request: Request, back: str = Form('/today')):
    if _login(request) is None:
        return HTMLResponse("Undo from your phone or laptop.", status_code=403)
    try:
        result = control.undo(batch, login=_login(request), via='tap')
    except control.Refused as error:
        return _changes(request, problem=str(error))
    return _back(back, result['batch'])
