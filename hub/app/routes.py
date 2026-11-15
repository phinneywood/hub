"""The app (web/app, built into hub/static/app): its page, the house as data, the live feed and the taps.

Every tap goes through hub/tools/house/control.py: the same allowlist, limit, record and Undo as a request typed
to Ask. A change needs a signed-in device; a program on this Mac has no Tailscale login and can only look.
"""
import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, Body, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from ..tools.house import control, nowplaying, read
from . import levels, model
from . import scenes as kept_scenes
from .feed import feed

router = APIRouter()
BUILT = Path(__file__).resolve().parent.parent / 'static' / 'app'
BEAT = 5                # seconds of nothing new on the live feed before a beat, so the app can tell the line is alive
PICKS_SECONDS = 300
_picks = {'at': float('-inf'), 'found': []}

PAGE = """<!doctype html>
<html lang="en" data-theme="dusk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="#141C33" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#E4EAF1" media="(prefers-color-scheme: light)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Hub</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="preload" href="/static/app/fonts/Jost.ttf" as="font" type="font/ttf" crossorigin>
<link rel="stylesheet" href="/static/app/app.css?v={css}">
</head>
<body>
<div id="app"></div>
<script type="module" src="/static/app/app.js?v={js}"></script>
</body>
</html>
"""


def versions():
    """Short hashes of the built files, so a new build is fetched and an old one can be kept for good."""
    try:
        built = json.loads((BUILT / 'build.json').read_text())['built']
        return {name: str(built[key])[:12] for name, key in (('js', 'app.js'), ('css', 'app.css'))}
    except (OSError, ValueError, KeyError, TypeError):
        return {'js': '0', 'css': '0'}


def _login(request):
    return request.headers.get('tailscale-user-login')


def _refuse(words, status=409):
    return JSONResponse({'error': str(words)[:300]}, status_code=status)


def _signed_out():
    return _refuse('Change the house from your phone or laptop.', 403)


def _said(result):
    """A tap's answer: what was done in plain words, and the batch for its Undo."""
    words = result['done'] + [f'{w} (not confirmed)' for w in result['unknown']]
    said = '; '.join(words) or 'Nothing changed.'
    if result.get('silent'):
        said = 'Sent, but the speaker isn’t playing.'
    feed.poke()
    return {'ok': bool(result['done']) and not result['not_sent'], 'said': said[:300],
            'batch': result['batch'] if result['undo'] else None}


def _change(request, work):
    """Run one change for a signed-in device and answer in the app's shape."""
    if _login(request) is None:
        return _signed_out()
    try:
        return _said(work(_login(request)))
    except (control.Refused, read.broker.HomeDown) as error:
        return _refuse(error)
    except (ValueError, TypeError):
        return _refuse('Hub couldn’t read that request.', 400)


@router.get('')
def bare():
    """The app lives at /app/ (with the slash), so the worker that keeps it on the phone covers it."""
    return RedirectResponse('/app/', status_code=308)


@router.get('/', response_class=HTMLResponse)
def page():
    return HTMLResponse(PAGE.format(**versions()))


@router.get('/sw.js')
def worker():
    """Keeps the app's own files on the phone, so it opens at once and can say when it can't reach home.
    Served from here so it covers /app/ and nothing else of Hub."""
    return FileResponse(BUILT / 'sw.js', media_type='text/javascript')


@router.get('/api/home')
def home(request: Request):
    login = _login(request)
    links = read.opens()
    return {**model.now(), 'signed_in': login is not None, 'initial': (login or 'H')[:1].upper(), 'versions': versions(),
            'links': {'ha': links.get('ha_url'), 'music': links.get('music_url')}}


@router.get('/api/live')
async def live(request: Request):
    """The house as it changes: one `house` event per new picture, for as long as the app stays open, and a
    `beat` whenever nothing has changed for a few seconds. An app that hears neither knows it has lost Hub."""
    loop = asyncio.get_running_loop()
    queue = feed.listen(loop)

    async def events():
        try:
            first = feed.last() or json.dumps(await loop.run_in_executor(None, model.now), separators=(',', ':'), sort_keys=True)
            yield f'event: house\ndata: {first}\n\n'
            while True:
                try:
                    text = await asyncio.wait_for(queue.get(), BEAT)
                except asyncio.TimeoutError:
                    yield 'event: beat\ndata: 1\n\n'
                    continue
                yield f'event: house\ndata: {text}\n\n'
        finally:
            feed.leave(queue)

    return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})


def _room_lights(room_id):
    found = read.snapshot(fresh=True)
    room = next((r for r in found['rooms'] if model.slug(r['name']) == room_id), None)
    if room is None:
        raise control.Refused('That room isn’t in Home Assistant any more.')
    return [d for d in room['devices'] if d['kind'] == 'light' and not d['offline']]


@router.post('/api/room/{room_id}')
def room(room_id: str, request: Request, body: dict = Body(...)):
    """A room's lights together: {"on": true|false}, or {"level": 1..100} for the ones that dim."""
    def work(login):
        lights = _room_lights(room_id[:80])
        if 'level' in body:
            level = int(body['level'])
            if level <= 0:
                targets, action, value = [d['id'] for d in lights if d['on']], 'off', None
            else:
                lit = [d for d in lights if d['on']] or lights
                targets, action, value = [d['id'] for d in lit if d.get('dims')], 'brightness', level
                if not targets:         # nothing here dims: a level means on
                    targets, action, value = [d['id'] for d in lights if not d['on']], 'on', None
        elif body.get('on') is True:
            # Each dark lamp comes back as bright as it last was; one Hub has never seen lit is simply switched on.
            again = {}
            for d in lights:
                if not d['on']:
                    again.setdefault(levels.last(d['id']) if d.get('dims') else None, []).append(d['id'])
            if not again:
                raise control.Refused('Those lights are already like that.')
            return control.several([(ids, 'brightness' if level else 'on', level) for level, ids in again.items()],
                                   login=login, via='app')
        elif body.get('on') is False:
            targets, action, value = [d['id'] for d in lights if d['on']], 'off', None
        else:
            raise control.Refused('Say on, off or a level.')
        if not targets:
            raise control.Refused('Those lights are already like that.')
        return control.change(targets, action, value=value, login=login, via='app', at_once=True)
    return _change(request, work)


@router.post('/api/device/{entity}')
def device(entity: str, request: Request, body: dict = Body(...)):
    """One light, plug or fan: {"on": true|false}, or {"level": 1..100} for a light."""
    def work(login):
        if 'level' in body and int(body['level']) > 0:
            return control.change([entity[:140]], 'brightness', value=int(body['level']), login=login, via='app', at_once=True)
        on = body.get('on') if 'level' not in body else False
        if on not in (True, False):
            raise control.Refused('Say on, off or a level.')
        return control.change([entity[:140]], 'on' if on else 'off', login=login, via='app', at_once=True)
    return _change(request, work)


@router.post('/api/scene/{scene}')
def scene(scene: str, request: Request):
    """A scene Hub keeps, a Home Assistant scene, or "All off": every light indoors that is on."""
    def work(login):
        if scene.startswith(kept_scenes.PREFIX):
            return kept_scenes.run(scene, login=login)
        if scene == model.ALL_OFF:
            targets = model.indoor_lights_on(read.snapshot(fresh=True), read.floors())
            if not targets:
                raise control.Refused('Every light indoors is already off.')
            return control.change(targets, 'off', login=login, via='app', at_once=True)
        return control.change([scene[:140]], 'activate', login=login, via='app')
    return _change(request, work)


@router.post('/api/scenes')
def keep_scene(request: Request, body: dict = Body(...)):
    """Keep the lights of some rooms as they are now, under a name: {"name", "rooms": [room ids], "rest_off": bool}."""
    if _login(request) is None:
        return _signed_out()
    try:
        found = read.snapshot(fresh=True)
        wanted = {str(r) for r in body.get('rooms') or ()}
        names = [r['name'] for r in found['rooms'] if model.slug(r['name']) in wanted]
        name = kept_scenes.keep(body.get('name'), names, bool(body.get('rest_off')), login=_login(request))
    except (control.Refused, read.broker.HomeDown) as error:
        return _refuse(error)
    feed.poke()
    return {'ok': True, 'said': f'{name} is kept'}


@router.post('/api/scenes/{scene}/forget')
def forget_scene(scene: str, request: Request):
    if _login(request) is None:
        return _signed_out()
    try:
        kept_scenes.forget(scene[:40])
    except control.Refused as error:
        return _refuse(error)
    feed.poke()
    return {'ok': True, 'said': 'Forgotten'}


@router.post('/api/music/{speaker}')
def music(speaker: str, request: Request, body: dict = Body(...)):
    """One tap on what is playing: play, pause, next, previous, a volume, or a room in or out."""
    def work(login):
        value = body.get('value')
        return nowplaying.tap(speaker[:140], str(body.get('action'))[:20], login=login, target=str(body.get('target') or '')[:140] or None,
                              value=int(value) if value is not None else None, via='app')
    return _change(request, work)


@router.get('/api/picks')
def picks():
    """A few things to play instead: the library's playlists. Empty when Music Assistant isn't set up or answering."""
    from ..tools.house import music as library
    if time.monotonic() - _picks['at'] > PICKS_SECONDS:
        found = []
        if library.ready():
            try:
                found = [{'name': p['name'], 'kind': 'playlist', 'uri': p['uri']} for p in library.playlists()
                         if read.broker.playable(p['uri'], 'playlist')][:12]
            except library.MusicDown:
                found = []
        _picks.update(at=time.monotonic() if found else float('-inf'), found=found)
    return {'picks': _picks['found']}


@router.post('/api/play/{speaker}')
def play(speaker: str, request: Request, body: dict = Body(...)):
    """Play one of the picks where music is on now: {"uri", "kind", "name"}."""
    def work(login):
        return control.change([speaker[:140]], 'play_media', query=str(body.get('uri') or '')[:300], kind=body.get('kind'),
                              label=str(body.get('name') or '')[:80], login=login, via='app')
    return _change(request, work)


@router.post('/api/undo/{batch}')
def undo(batch: str, request: Request):
    return _change(request, lambda login: control.undo(batch, login=login, via='app', at_once=True))
