"""The app's door to errands: the screen as data, "Look again", the taps and the one setting.

Looking is free for any device on the tailnet; a tap needs a signed-in device, as every other tap in the app does.
"""
from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from . import picture, read, taps

router = APIRouter()


def _login(request):
    return request.headers.get('tailscale-user-login')


def _refuse(words, status=409):
    return JSONResponse({'error': str(words)[:300]}, status_code=status)


def _signed_out():
    return _refuse('Finish errands from your phone or laptop.', 403)


@router.get('/api/errands')
def errands_screen():
    """The Errands screen as data, from Hub's own table. No Gmail call, no model. When the owner has opened
    something, Hub looks for proof that it was finished behind the screen, and `checking` says so."""
    taps.look()
    return picture.screen()


@router.post('/api/errands/read')
def errands_read(request: Request):
    """"Look again": close what is done, then read what is new, behind the screen. Within the day's limits."""
    if _login(request) is None:
        return _signed_out()
    read.read_soon()
    return {'ok': True, 'said': 'Looking through your mail and the class guide'}


@router.post('/api/errands/settings')
def errands_settings(request: Request, body: dict = Body(default={})):
    """{"compose": "mail" or "gmail"}: where a drafted reply opens."""
    from . import store
    if _login(request) is None:
        return _signed_out()
    writes = body.get('compose')
    if writes not in picture.COMPOSE:
        return _refuse('Pick your mail app or Gmail.', 400)
    store.put_setting('compose', writes)
    return {'ok': True, 'said': 'Replies open in Gmail' if writes == 'gmail' else 'Replies open in your mail app'}


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
    from . import store
    errand = store.get(errand_id)
    return {'ok': True, 'said': said, 'card': picture.card(errand) if errand else None, **more}
