"""The app's door to errands: the screen as data, "Look again", the taps and the one setting.

Everything is for the person whose device asks (its Tailscale login): they see, tap and set only their own errands
and the household's. A device with no login (this Mac itself) sees the household's only. A tap needs a signed-in
device, as every other tap in the app does.
"""
from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ... import people
from . import picture, read, store, taps

router = APIRouter()


def _login(request):
    return request.headers.get('tailscale-user-login')


def _refuse(words, status=409):
    return JSONResponse({'error': str(words)[:300]}, status_code=status)


def _signed_out():
    return _refuse('Finish errands from your phone or laptop.', 403)


@router.get('/api/errands')
def errands_screen(request: Request):
    """The Errands screen as data, from Hub's own table. No Gmail call, no model. When the owner has opened
    something, Hub looks for proof that it was finished behind the screen, and `checking` says so."""
    taps.look()
    return picture.screen(login=_login(request))


@router.post('/api/errands/read')
def errands_read(request: Request):
    """"Look again": close what is done, then read what is new, behind the screen: the asker's own mail and the
    class guide. Within the day's limits."""
    login = _login(request)
    if login is None:
        return _signed_out()
    mine = people.accounts(login)
    read.read_soon(accounts=mine)
    return {'ok': True, 'said': 'Looking through your mail and the class guide' if mine else 'Looking through the class guide'}


@router.post('/api/errands/settings')
def errands_settings(request: Request, body: dict = Body(default={})):
    """{"compose": "mail" or "gmail"}: where this person's drafted replies open."""
    login = _login(request)
    if login is None:
        return _signed_out()
    writes = body.get('compose')
    if writes not in picture.COMPOSE:
        return _refuse('Pick your mail app or Gmail.', 400)
    said = picture.set_compose(login, writes)
    if said is None:
        return _refuse('Hub doesn’t read your Google account yet, so there are no replies to open.')
    return {'ok': True, 'said': said}


@router.post('/api/errands/{errand_id}/{do}')
def errands_tap(errand_id: int, do: str, request: Request, body: dict = Body(default={})):
    """A tap on an errand: go (a step was opened, {"choice", "step"}), list ({"choice"}), done, later, not or back."""
    login = _login(request)
    if login is None:
        return _signed_out()
    try:
        said, more = taps.tap(errand_id, do, body, login)
    except taps.Refused as error:
        return _refuse(error)
    errand = store.get(errand_id)
    return {'ok': True, 'said': said, 'card': picture.card(errand, login=login) if errand else None, **more}
