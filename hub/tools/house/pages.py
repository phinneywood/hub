"""Home's own pages: a link out to Home Assistant, the taps that change things, and Hub's record of changes.

Home Assistant has its own dashboards, so Hub doesn't copy them: /house opens Home Assistant. Changes need a
tap from a signed-in device (a program on this Mac has no Tailscale login and can't change the house).
"""
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import home, taps
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


# ---- Playlists, through Music Assistant (music.py) ----

def _playlists(request, problem='', done=''):
    from . import music
    lists, unreadable = [], ''
    if music.ready():
        try:
            lists = music.playlists()
        except music.MusicDown as error:
            unreadable = str(error)
    else:
        unreadable = 'Music Assistant isn’t set up in Hub yet.'
    rows = music.recent_changes()
    for r in rows:
        r['when'] = datetime.fromtimestamp(r['ts'], home.PACIFIC).strftime('%a %-I:%M %p')
    return render(request, "house/playlists.html", active="house", lists=lists, rows=rows, links=read.links(),
                  where=music.WHERE_WORDS, problem=problem[:200], unreadable=unreadable,
                  banner=music.summary(done) if done else None, waiting=music.proposals())


@router.get("/playlists", response_class=HTMLResponse)
def playlists(request: Request, done: str = ''):
    return _playlists(request, done=done[:12])


@router.get("/playlists/delete/{playlist_id}", response_class=HTMLResponse)
def confirm_delete(playlist_id: str, request: Request):
    """The confirmation tap before a playlist is deleted: it can't be undone."""
    from . import music
    try:
        chosen = next((p for p in music.playlists() if p['id'] == playlist_id[:40]), None)
        if chosen is None:
            raise music.Refused('That playlist isn’t in the library any more.')
        music.check_editable(chosen)
        count = len(music.tracks(chosen['id']))
    except (music.Refused, music.MusicDown) as error:
        return _playlists(request, problem=str(error))
    return render(request, "house/delete_playlist.html", active="house", playlist=chosen, count=count)


def _signed_out(request, words):
    return taps.refused(request, words, lambda: HTMLResponse(words, status_code=403), status=403)


def _result(request, result, words):
    """After a tap that changed a playlist: back to the page with its banner, or the words for a chat card."""
    said = words if result['done'] else 'Music Assistant didn’t confirm it. Check the Playlists page.'
    return taps.done(request, f'/house/playlists?done={result["batch"]}', said)


@router.post("/playlists/delete/{playlist_id}")
def delete_playlist(playlist_id: str, request: Request):
    """The confirmation page and the chat's delete card both post here."""
    from . import music
    if _login(request) is None:
        return _signed_out(request, "Delete playlists from your phone or laptop.")
    try:
        result = music.delete(playlist_id[:40], login=_login(request), via='tap')
    except (music.Refused, music.MusicDown) as error:
        return taps.refused(request, error, lambda: _playlists(request, problem=str(error)))
    return _result(request, result, 'Deleted.')


@router.post("/playlists/undo/{batch}")
def undo_playlist(batch: str, request: Request):
    from . import music
    if _login(request) is None:
        return HTMLResponse("Undo from your phone or laptop.", status_code=403)
    try:
        result = music.undo(batch, login=_login(request), via='tap')
    except (music.Refused, music.MusicDown) as error:
        return _playlists(request, problem=str(error))
    return RedirectResponse(f'/house/playlists?done={result["batch"]}', status_code=303)


@router.post("/playlists/make/{proposal_id}")
def make_playlist(proposal_id: str, request: Request):
    """The owner's tap on a playlist Ask proposed: only now is it made."""
    from . import music
    if _login(request) is None:
        return _signed_out(request, "Make playlists from your phone or laptop.")
    found = music.proposal(proposal_id)
    if found is None:
        gone = 'That playlist isn’t waiting any more. Ask again in the chat.'
        return taps.refused(request, gone, lambda: _playlists(request, problem=gone))
    try:
        result = music.create(found['name'], found['songs'], login=_login(request), via='tap')
    except (music.Refused, music.MusicDown) as error:
        return taps.refused(request, error, lambda: _playlists(request, problem=str(error)))
    music.settle(found['id'], 'made')
    return _result(request, result, 'Made. Undo is on the Playlists page for a day.')


@router.post("/playlists/skip/{proposal_id}")
def skip_playlist(proposal_id: str, request: Request):
    from . import music
    music.settle(proposal_id, 'skipped')
    return taps.done(request, '/house/playlists', 'Not made.')
