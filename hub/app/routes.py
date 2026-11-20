"""The app (web/app, built into hub/static/app): its page, the house as data, the live feed and the taps.

Every tap goes through hub/tools/house/control.py: the same allowlist, limit, record and Undo as a request typed
to Ask. A change needs a signed-in device; a program on this Mac has no Tailscale login and can only look.
"""
import asyncio
import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, Body, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse

from .. import bridge
from ..tools.house import admin, control, nowplaying, read, zones
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
<meta name="theme-color" content="#0F1322" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#F5EFE4" media="(prefers-color-scheme: light)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Hub</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="preload" href="/static/app/fonts/Jost.ttf" as="font" type="font/ttf" crossorigin>
<link rel="preload" href="/static/app/fonts/Fraunces.woff2" as="font" type="font/woff2" crossorigin>
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
    """A tap's answer: what was done in plain words, and the batch for its Undo. An Undo that would open the house
    answers with the card that waits for the owner's tap instead, and says nothing was done yet."""
    if 'card' in result:
        return JSONResponse({'error': 'That waits for your tap.', 'card': result['card']}, status_code=409)
    words = result['done'] + [f'{w} (not confirmed)' for w in result['unknown']]
    said = '; '.join(words) or 'Nothing changed.'
    if result.get('silent'):
        said = 'Sent, but the speaker isn’t playing.'
    feed.poke()
    return {'ok': bool(result['done']) and not result['not_sent'], 'said': said[:300],
            'batch': result['batch'] if result['undo'] else None,
            **({'timing': result['timing']} if 'timing' in result else {})}      # the light show's, once it moved


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
    from .. import caretaker
    return {**model.now(), 'signed_in': login is not None, 'initial': (login or 'H')[:1].upper(), 'versions': versions(),
            'links': {'ha': links.get('ha_url'), 'music': links.get('music_url')}, 'update': _update_ready(),
            'mail': not _not_theirs(request), 'care': caretaker.top()}


def _update_ready():
    """Whether a checked version is waiting for Deploy, so System can say so."""
    from ..engine import release
    return release.folder() is not None and release.pending() is not None


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
    """Play one of the picks where music is on now: {"uri", "kind", "name"}. With {"here": true}, in that speaker's room
    only, as Ask plays in a named room: its zone comes along, it leaves a group it shares with other rooms, and music
    playing anywhere else plays on."""
    def work(login):
        anchor, mates, alone = speaker[:140], [], False
        if body.get('here'):
            players = {d['id']: d for d in read.snapshot(fresh=True)['speakers']}
            if anchor not in players:
                raise control.Refused('That speaker isn’t in Home Assistant any more.')
            mates = zones.mates(anchor, players)
            alone = any(m not in (anchor, *mates) for m in players[anchor]['members'])
        return control.change([anchor], 'play_media', query=str(body.get('uri') or '')[:300], kind=body.get('kind'),
                              label=str(body.get('name') or '')[:80], login=login, via='app', alone=alone, together=mates)
    return _change(request, work)


@router.post('/api/show/{do}')
def show(do: str, request: Request, body: dict = Body(default={})):
    """The light show's card: "end" ends the show; "timing" sets its lights {"value": 150} milliseconds earlier (or,
    below 0, later) and answers with the timing as set. "start" is a tap on one of Ask's choices for a show,
    {"handle": "#1a2b3c"}, or on one of the playlists Hub offers (/api/picks), {"uri": ...}: only something a lookup
    found lately, never words (tools/house/lightshow.py)."""
    from ..tools.house import lightshow
    from ..tools.house import music as library

    def work(login):
        if do == 'start':
            handle, uri = str(body.get('handle') or '')[:20], str(body.get('uri') or '')[:300]
            found = library.recall(handle) if handle.startswith('#') else None
            if not found and uri:       # one of the playlists the light show sheet offers, as /api/picks gave it
                found = next(({**p, 'artist': ''} for p in picks()['picks'] if p['uri'] == uri), None)
            if not found and uri:
                raise control.Refused('Hub can’t find that playlist any more.')
            if not found:
                raise control.Refused('Hub doesn’t remember that choice any more. Ask for the light show again.')
            shown = f'{found["name"]} – {found["artist"]}' if found['artist'] and found['kind'] != 'playlist' else found['name']
            return lightshow.start(found['uri'], found['kind'], shown, login=login, via='app')
        if do == 'end':
            return lightshow.end(login=login, via='app')
        if do == 'timing':
            return lightshow.set_timing(body.get('value'), login=login, via='app')
        raise control.Refused('The light show card can’t do that.')
    return _change(request, work)


# ---- Versions (hub/app/versions.py): what each release changed ----

@router.get('/api/versions')
def versions_list():
    """Each release, newest first, in plain words, and which one is running."""
    from . import versions as releases
    return releases.picture()


@router.post('/api/deploy')
def deploy(request: Request):
    """Ask the deployer for the checked version, like Deploy on the older Engine page. A signed-in device only."""
    from .. import store
    from ..engine import release
    login = _login(request)
    if login is None:
        return _refuse('Deploy from your phone or laptop.', 403)
    if not release.request(login):
        return _refuse('There’s no checked version waiting.')
    store.log('engine_deploy', 'asked')
    return {'ok': True, 'said': 'Updating. This takes about a minute.'}


@router.post('/api/deploy/undo')
def deploy_undo(request: Request):
    """Ask the deployer for the version from before the last deploy, within a day of it. A signed-in device only."""
    from .. import store
    from ..engine import release
    login = _login(request)
    if login is None:
        return _refuse('Undo from your phone or laptop.', 403)
    if release.requested():
        return _refuse('Hub is already updating. Undo once it is done.')
    if not release.undo(login):
        return _refuse('There’s no recent update to undo.')
    store.log('engine_deploy', 'undo')
    return {'ok': True, 'said': 'Going back. This takes about a minute.'}


# ---- Engine (hub/app/engine.py): what the owner asked for, what Hub may do, what it spent ----

@router.get('/api/engine')
def engine_picture(request: Request):
    """The Engine screen as data, for the person asking. Reads what Hub keeps; calls no model."""
    from . import engine
    return engine.picture(_login(request))


@router.post('/api/engine/tell')
def engine_tell(request: Request, body: dict = Body(...)):
    """One line the owner typed: {"text": ...}. Sorting it may make one zero-retention model call, as on the older page."""
    from . import engine
    if _login(request) is None:
        return _refuse('Tell Hub from your phone or laptop.', 403)
    said, ok = engine.tell(str(body.get('text') or ''), _login(request))
    return {'ok': ok, 'said': said} if ok else _refuse(said)


@router.post('/api/engine/{what}')
def engine_tap(what: str, request: Request, body: dict = Body(...)):
    """A tap on the Engine screen: {"key": ..., "value": ...}."""
    from . import engine
    if _login(request) is None:
        return _refuse('Change this from your phone or laptop.', 403)
    try:
        return {'ok': True, 'said': engine.do(what, body.get('key'), body.get('value'), _login(request))}
    except engine.Refused as error:
        return _refuse(error)
    except (ValueError, TypeError):
        return _refuse('Hub couldn’t read that request.', 400)


# ---- Today (hub/app/today.py): what needs a person, then the day ----

@router.get('/api/today')
def day(request: Request, wall: bool = False):
    """The Today screen as data, for the person asking (people.py): the household's things and their own accounts'.
    With ?wall=1, as the kitchen wall shows it: the household's things only, whoever asks. Reads what Hub keeps and
    the house; the brief's written note, while it is on, is Ask's model's (ask/note.py)."""
    from . import today
    return today.picture(wall=wall, login=_login(request))


@router.get('/api/brief', response_class=PlainTextResponse)
def brief_aloud(request: Request, wall: bool = False):
    """The brief as a few sentences, for a Shortcut to read aloud: the asker's own, or with ?wall=1 the household's."""
    from . import brief, today
    return brief.spoken(today.picture(wall=wall, login=_login(request)))


@router.post('/api/today/tick')
def tick(request: Request, body: dict = Body(...)):
    """A need's circle: {"key": ...}. With {"back": true}, put it back."""
    from . import today
    if _login(request) is None:
        return _signed_out()
    key = str(body.get('key') or '')
    if not key:
        return _refuse('Nothing to tick.')
    if not today.yours(key, _login(request)):
        return _refuse('That one isn’t yours to tick.')
    if body.get('back'):
        return {'ok': bool(today.untick(key)), 'said': 'Put back'}
    said = today.tick(key)
    return {'ok': True, 'said': said, 'key': key} if said else _refuse('That one is already gone.')


# ---- Ask (hub/app/ask): say it, and Hub does it or answers ----

_asking, _asking_lock = set(), __import__('threading').Lock()


@router.get('/api/ask')
def ask_status(request: Request, before: int | None = None):
    """Whether Ask can run, this person's one conversation (its last turns, or the turns before turn `before` when
    the app scrolls back), what they tend to ask around now, and what Hub remembers. (The model is given less:
    `brain.earlier`.)"""
    from .ask import brain, morning, thread, turns
    why, login = brain.warm() or brain.limits(), _login(request)
    if before is None:          # the day's morning message is the first thing in the conversation (no model)
        morning.message(login)
    shown = [{**{k: v for k, v in turn.items() if k != 'shown'}, 'answer': turn.get('shown') or turn['answer']}   # as this person saw it
             for turn in thread.history(login, before, turns.SHOWN_TURNS)]
    return {'ready': why is None, 'why': why, 'recent': shown, 'more': len(shown) == turns.SHOWN_TURNS,
            'usual': turns.usual(login) if why is None else [], 'remembers': turns.remembered(login),
            'morning': morning.made(login)}


@router.post('/api/ask/morning/opened')
def ask_morning_opened(request: Request, body: dict = Body(...)):
    """A tap on a morning item's button: counted as acted on, for this person (hub/app/ask/morning.py)."""
    from .ask import morning
    login = _login(request)
    if login is None:
        return _signed_out()
    return {'ok': morning.opened(login, str(body.get('key') or ''))}


@router.post('/api/ask/fresh')
def ask_fresh(request: Request):
    """Start this person's conversation over: what the model is given starts from now. The messages stay."""
    from .ask import thread
    login = _login(request)
    if login is None:
        return _signed_out()
    thread.start_fresh(login)
    return {'ok': True, 'said': 'Starting fresh'}


@router.post('/api/ask')
async def ask(request: Request, body: dict = Body(...)):
    """One request, answered as it happens: a stream of small events (words, a card to draw, how it ended).
    A caller that doesn't ask for a stream (a Shortcut on the owner's phone, for Siri) gets the whole answer at the
    end instead, as {"said": ...}."""
    from .ask import brain
    login = _login(request)
    if login is None:
        return _refuse('Ask from your phone or laptop.', 403)
    text = str(body.get('text') or '')[:brain.MESSAGE_LENGTH]
    with _asking_lock:
        if login in _asking:
            return _refuse('Hub is still on your last request.')
        _asking.add(login)

    async def events():
        try:
            async for event in brain.run(text, login):
                yield 'data: ' + json.dumps(event, separators=(',', ':')) + '\n\n'
        finally:
            with _asking_lock:
                _asking.discard(login)

    if 'text/event-stream' in request.headers.get('accept', ''):
        return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})
    said, end = [], {}
    try:
        async for event in brain.run(text, login):
            if event['t'] == 'say':
                said.append(event['text'])
            elif event['t'] == 'done':
                end = event
    finally:
        with _asking_lock:
            _asking.discard(login)
    return {'ok': end.get('status') == 'ok', 'said': ' '.join(''.join(said).split())}


@router.post('/api/memory/{note}/forget')
def forget_note(note: int, request: Request):
    from .ask import turns
    if _login(request) is None:
        return _signed_out()
    if not turns.forget(note, _login(request)):
        return _refuse('That note isn’t there any more.', 404)
    return {'ok': True, 'said': 'Forgotten'}


@router.post('/api/undo/{batch}')
def undo(batch: str, request: Request):
    from .errands import asked as errand_asked
    if batch.startswith('m'):       # a change to Hub's own record of the household's money (hub/app/money/changes.py)
        from .money.routes import money_undo
        return money_undo(batch, request)
    if batch.startswith('s'):       # a change to how the house is set up (hub/tools/house/admin.py)
        return _change(request, lambda login: admin.undo(batch, login=login, via='app'))
    if batch.startswith(errand_asked.BATCH):      # errands Ask changed, or where replies open (hub/app/errands/asked.py)
        if _login(request) is None:
            return _signed_out()
        try:
            return {'ok': True, 'said': errand_asked.undo(batch, _login(request))}
        except errand_asked.taps.Refused as error:
            return _refuse(error)
    return _change(request, lambda login: control.undo(batch, login=login, via='app', at_once=True))


# ---- Career desk (hub/app/career): one person's plan, field notes, writing and evidence ----

@router.get('/api/career')
def career_picture(request: Request):
    """The Career screen as data, for the person signed in only. Reads their desk's files; calls no model."""
    from .career import desk
    return desk.picture(_login(request))


@router.get('/api/career/piece/{kind}/{name}')
def career_piece(kind: str, name: str, request: Request):
    """One field note, piece, evidence sheet or the market, to read."""
    from .career import desk
    try:
        return desk.piece(_login(request), kind[:20], name[:130])
    except desk.NoDesk as error:
        return _refuse(error, 404)


@router.get('/api/career/job/{number}')
def career_job(number: int, request: Request):
    """What became of one job of the desk's, for the card that follows it."""
    from .career import desk, worker
    try:
        return worker.job(_login(request), number)
    except desk.NoDesk as error:
        return _refuse(error, 404)


@router.post('/api/career/{do}')
def career_tap(do: str, request: Request, body: dict = Body(default={})):
    """A tap on the desk: tick a step {"key", "back"?}, set a piece's status {"id", "status"}, or let Hub's model read
    the desk or not {"on"}. Asking the desk to research or draft is Ask's (hub/app/ask/tools.py `career`)."""
    from .career import desk
    login = _login(request)
    if login is None:
        return _refuse('Change your desk from your phone or laptop.', 403)
    try:
        if do == 'tick':
            back = bool(body.get('back'))
            desk.tick(login, str(body.get('key') or '')[:20], not back)
            return {'ok': True, 'said': 'Opened again' if back else 'Done'}
        if do == 'status':
            title = desk.set_status(login, str(body.get('id') or '')[:130], str(body.get('status') or ''))
            return {'ok': True, 'said': f'{title}: {body.get("status")}'[:200]}
        if do == 'model':
            return {'ok': True, 'said': desk.set_model_reads(login, bool(body.get('on')))}
    except (desk.NoDesk, desk.Refused) as error:
        return _refuse(error)
    return _refuse('Hub couldn’t read that request.', 404)


# ---- Cards that wait for a tap (hub/tools/house/admin.py): removing something, unlocking, the whole house ----

@router.get('/api/setup/{number}')
def setup_card(number: int):
    """What became of one card, for an app that was closed while it waited."""
    row = admin.waiting(number)
    return {'status': row['status'], 'batch': row['batch']} if row else {'status': 'gone', 'batch': None}


@router.post('/api/setup/{number}/{choice}')
def setup_tap(number: int, choice: str, request: Request):
    """The owner's tap on a card: `confirm` does what the card says, planned again from the house as it is now;
    `dismiss` puts the card away. Only a signed-in device can do either, and no tool of Ask's can."""
    if choice == 'confirm':
        return _change(request, lambda login: admin.confirm(number, login=login))
    if choice != 'dismiss':
        return _refuse('Hub couldn’t read that request.', 404)
    if _login(request) is None:
        return _signed_out()
    admin.settle(number, 'dismissed')
    return {'ok': True, 'said': 'Left as it was'}


# ---- House health (hub/caretaker.py): what the caretaker watches, fixed and hands to you ----

@router.get('/api/health')
def health_picture():
    """The House health screen as data, from the caretaker's folder. Reads only."""
    from .. import caretaker
    return caretaker.picture()


@router.get('/api/health/top')
def health_top():
    """Is the caretaker watching, and how many things need the owner: for the top button and System."""
    from .. import caretaker
    return caretaker.top()


@router.get('/api/health/note')
def health_note():
    """The night in a headline and lines, 9 PM until now, until noon; null otherwise."""
    from .. import caretaker
    return caretaker.note()


@router.post('/api/health/{do}')
def health_ask(do: str, request: Request, body: dict = Body(default={})):
    """A tap on House health or on Ask's caretaker card, {"target": ...}: fix, restart, done, ignore, pause, resume
    or drill. Hub checks it and leaves it in the caretaker's queue; the caretaker checks it again and answers in its
    journal within a minute. A tap is the owner's own, so "Suggest only" (which holds back what Ask asks by itself)
    doesn't apply."""
    from .. import caretaker
    login = _login(request)
    if login is None:
        return _refuse('Ask the caretaker from your phone or laptop.', 403)
    try:
        said, rid = caretaker.ask(do[:12], body.get('target', ''), login)
    except caretaker.Refused as error:
        return _refuse(error)
    except (ValueError, TypeError, OSError):
        return _refuse('Hub couldn’t pass that on.', 400)
    return {'ok': True, 'said': said, 'request': rid}


# ---- Activity (hub/app/activity.py): what happened in the house, from Home Assistant, Apple Home and Hub ----

@router.get('/api/activity')
def activity_picture(kinds: str = '', sources: str = '', before: float | None = None):
    """What happened in the house, newest first: `kinds` and `sources` are comma lists, `before` a time for more."""
    from . import activity
    return activity.picture(kinds=[k for k in kinds.split(',') if k] or activity.SHOWN,
                            sources=[s for s in sources.split(',') if s] or tuple(activity.SOURCES), before=before)


# ---- Changes (hub/app/changes.py): what Hub changed in the house and the music library ----

@router.get('/api/changes')
def changes_picture():
    """Every change Hub made, newest first, with Undo for a day, and playlists waiting for a tap."""
    from . import changes
    return changes.picture()


@router.post('/api/playlist/{do}')
def playlist_tap(do: str, request: Request, body: dict = Body(default={})):
    """A playlist tap: make, skip or undo, with {"key": ...}."""
    from . import changes
    if _login(request) is None:
        return _refuse('Change playlists from your phone or laptop.', 403)
    try:
        said = changes.tap(do, body.get('key'), _login(request))
    except changes.Refused as error:
        return _refuse(error)
    feed.poke()
    return {'ok': True, 'said': said[:300]}


# ---- Kitchen (hub/app/kitchen.py): the list, each thing's product, the cart and what waits for a tap ----

@router.get('/api/kitchen')
def kitchen_picture():
    """The Kitchen screen as data, from the kitchen service. No model."""
    from . import kitchen
    return kitchen.picture()


@router.post('/api/kitchen/{do}')
def kitchen_tap(do: str, request: Request, body: dict = Body(default={})):
    """A tap on the list: add, remove, pick, qty, keep, fill, read, order, auto or reminders. Each can end in the shop's
    cart, so each needs a signed-in device; the kitchen service checks the login again."""
    from . import kitchen
    if _login(request) is None:
        return _refuse('Change the list from your phone or laptop.', 403)
    try:
        said, more = kitchen.tap(do[:20], body, _login(request))
    except kitchen.Refused as error:
        return _refuse(error)
    except (ValueError, TypeError):
        return _refuse('Hub couldn’t read that request.', 400)
    return {'ok': True, 'said': said, **more}


# ---- Mail (hub/app/mail.py): what needs the owner, then what Hub sorted ----
# The mail Hub reads is one person's Google account (the bridge's main one), so all of it, and every tap on it, is
# for a login that may see that account (hub/people.py). Anyone else is told theirs isn't connected, the same answer
# whatever they ask about, so nothing is learned of the owner's mail.

NOT_CONNECTED = 'Your Google account isn’t connected to Hub, so there’s no mail of yours here.'


def _not_theirs(request):
    from .. import people
    return not people.sees(_login(request), people.DEFAULT)


@router.get('/api/mail')
def mail_picture(request: Request):
    """The Mail screen as data, from Hub's own sorting tables. No Gmail call, no model."""
    from . import mail
    if _not_theirs(request):
        return {'ok': True, 'connected': False}
    return mail.picture()


@router.get('/api/mail-settings')
def mail_settings(request: Request):
    """The Mail settings screen as data: levels, the charge alert, senders sorted by hand, the plan, the rules."""
    from . import mailrules
    if _not_theirs(request):
        return {'ok': True, 'connected': False}
    return mailrules.picture()


@router.get('/api/mail-settings/quiet')
def mail_settings_quiet(request: Request):
    """The next loud sender to decide about (the older Quiet page's card), for the owner only."""
    from .. import bridge
    from . import mailrules
    if _not_theirs(request):
        return {'ok': True, 'connected': False}
    try:
        return {'ok': True, **mailrules.quiet()}
    except bridge.BridgeError:
        return _refuse('Gmail isn’t answering just now.', 503)


@router.post('/api/mail-settings/{do}')
def mail_settings_tap(do: str, request: Request, body: dict = Body(default={})):
    """A tap on Mail settings, {"key": ..., "value": ...}: through the older Mail pages' own handlers."""
    from . import mailrules
    if _login(request) is None:
        return _refuse('Change mail from your phone or laptop.', 403)
    if _not_theirs(request):
        return _refuse(NOT_CONNECTED, 403)
    try:
        return {'ok': True, 'said': mailrules.tap(do, body.get('key'), body.get('value'))}
    except mailrules.Refused as error:
        return _refuse(error)
    except (ValueError, TypeError):
        return _refuse('Hub couldn’t read that request.', 400)


@router.post('/api/mail/{do}')
def mail_tap(do: str, request: Request, body: dict = Body(default={})):
    """A tap on the Mail screen: done, back, answered, fine, sort, file or undo, with {"key": ...}."""
    from . import mail
    if _login(request) is None:
        return _refuse('Change mail from your phone or laptop.', 403)
    if _not_theirs(request):
        return _refuse(NOT_CONNECTED, 403)
    ok, said = mail.tap(do, body.get('key'))
    return {'ok': True, 'said': said} if ok else _refuse(said)


# ---- Ask's mail changes (hub/app/ask/mailchange.py): archive, label, read, each only after the person's tap ----

@router.get('/api/mail/change/{number}')
def mail_change_card(number: int, request: Request):
    """What became of one card, for an app that was closed while it waited. Only for the person it is for."""
    from .ask import mailchange
    row = mailchange.card(number)
    if row is None or row['login'] != _login(request):
        return {'status': 'gone', 'batch': None}
    return {'status': row['status'], 'batch': None, 'undo': row['status'] == 'done'}


@router.post('/api/mail/change/{number}/{choice}')
def mail_change_tap(number: int, choice: str, request: Request):
    """The person's tap on Ask's mail card: `confirm` makes the change, `dismiss` puts the card away, `undo` puts a
    done change back. Only a signed-in device of the person who asked can do any of these, and no tool of Ask's can."""
    from .ask import mailchange
    taps = {'confirm': mailchange.confirm, 'dismiss': mailchange.dismiss, 'undo': mailchange.undo}
    if choice not in taps:
        return _refuse('Hub couldn’t read that request.', 404)
    if _login(request) is None:
        return _refuse('Change mail from your phone or laptop.', 403)
    if _not_theirs(request):
        return _refuse(NOT_CONNECTED, 403)
    try:
        said = taps[choice](number, _login(request))
    except mailchange.Refused as error:
        return _refuse(error)
    return {'ok': True, 'said': said, 'batch': None, 'undo': choice == 'confirm'}


# ---- Dashboards (hub/app/boards/): one page per category, arranged by each person ----

@router.get('/api/boards')
def boards_list(request: Request):
    """The dashboards this person may open, each with its line for today."""
    from . import boards
    return {'ok': True, 'boards': boards.listing(_login(request))}


@router.get('/api/board/{board_id}')
def board_picture(board_id: str, request: Request):
    """One dashboard, in this person's order, with the blocks they hid listed apart."""
    from . import boards
    found = boards.picture(board_id, _login(request))
    if found is None:
        return JSONResponse({'ok': False, 'error': 'That dashboard isn’t here, or isn’t yours.'}, status_code=404)
    return found


@router.post('/api/board/{board_id}/{do}')
def board_tap(board_id: str, do: str, request: Request, body: dict = Body(default={})):
    """A tap on a dashboard: `layout` ({"order", "hidden"}), `tick` ({"item", "done"}) or `set` ({"key", "value"})."""
    from . import boards
    login = _login(request)
    if login is None:
        return _refuse('Change it from your phone or laptop.', 403)
    try:
        if do == 'layout':
            boards.arrange(login, board_id, body.get('order'), body.get('hidden'))
            return {'ok': True, 'said': 'Kept'}
        if do == 'tick':
            return {'ok': True, 'said': boards.tick(login, board_id, body.get('item'), bool(body.get('done')))}
        if do == 'set':
            return {'ok': True, 'said': boards.change(login, board_id, body.get('key'), body.get('value'))}
    except boards.Refused as error:
        return _refuse(error, 403 if 'yours' in str(error) else 409)
    except (ValueError, TypeError):
        return _refuse('Hub couldn’t read that request.', 400)
    return _refuse('Hub couldn’t read that request.', 400)


# ---- Errands (hub/app/errands/): what Hub got ready for the owner's tap ----

from .errands.routes import router as errands_router  # noqa: E402

router.include_router(errands_router)


# ---- Money (hub/app/money/): the household's accounts, read and never moved; members only ----

from .money.routes import router as money_router  # noqa: E402

router.include_router(money_router)


# ---- Photos (hub/app/photos.py): each month's best photos, chosen on this Mac by the photo curator ----

@router.get('/api/photos')
def photos_picture(request: Request, month: str = ''):
    """The Photos screen as data, from the curator's folder. Photos are private: a signed-in device only."""
    from . import photos
    if _login(request) is None:
        return _refuse('Open Photos on your phone or laptop.', 403)
    return photos.picture(month, _login(request))


@router.get('/api/photos/thumb/{photo}')
def photos_thumb(photo: str, request: Request):
    """One chosen photo's preview (Hub's no-store rule keeps it off the device)."""
    from . import photos
    if _login(request) is None:
        return _refuse('Open Photos on your phone or laptop.', 403)
    path = photos.thumb(photo, _login(request))
    if path is None:
        return _refuse('No such photo.', 404)
    with path.open('rb') as head:
        png = head.read(4) == b'\x89PNG'
    return FileResponse(path, media_type='image/png' if png else 'image/jpeg')


@router.post('/api/photos/{do}')
def photos_tap(do: str, request: Request, body: dict = Body(default={})):
    """Mark a chosen photo, {"key": photo}: keep, skip, print or clear."""
    from . import photos
    login = _login(request)
    if login is None:
        return _refuse('Mark photos from your phone or laptop.', 403)
    try:
        return {'ok': True, 'said': photos.mark(body.get('key'), do, login)}
    except photos.Refused as error:
        return _refuse(error)


# ---- Archive (hub/app/archive.py): photos, documents, ChatGPT history, mail and calendar history, in one search ----

def _archive(request, work):
    """Run one archive read for a signed-in device: the archive is private, like Photos."""
    from . import archive
    login = _login(request)
    if login is None:
        return _refuse('Open the archive on your phone or laptop.', 403)
    try:
        return work(archive, login)
    except archive.Away as away:
        return _refuse(away, 503)
    except archive.Refused as error:
        return _refuse(error)
    except bridge.BridgeError:
        return _refuse('Mail isn’t answering right now.', 503)


ACTIVE = re.compile(r'html|xhtml|svg|xml|javascript|ecmascript', re.I)
ACTIVE_NAMES = re.compile(r'\.(html?|xhtml|svg|xml|m?js)(["\';]|$)', re.I)


def _bytes(found, name=None):
    """A picture or a file from the archive, kept off the device by Hub's no-store rule. Nothing from the archive
    can run as a page of Hub's: every answer is sandboxed, and a document that a browser would run (a web page, an
    SVG, XML, a script) is sent as plain bytes, to be saved."""
    if found is None:
        return _refuse('Not in the archive.', 404)
    data, kind, disposition, sharp = found
    headers = {'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': 'sandbox'}
    if sharp:
        headers['X-Sharp'] = sharp
    if name == 'file':
        if disposition:
            headers['Content-Disposition'] = disposition
        if ACTIVE.search(kind or '') or ACTIVE_NAMES.search(disposition or ''):
            kind = 'application/octet-stream'
    else:
        kind = 'image/png' if data[:4] == b'\x89PNG' else 'image/jpeg'
    return Response(data, media_type=kind, headers=headers)


@router.get('/api/archive')
def archive_landing(request: Request):
    """The Archive screen before a search: what it holds, on this day, the people, and what Ask may read."""
    return _archive(request, lambda archive, login: archive.landing(login))


@router.post('/api/archive/search')
def archive_search(request: Request, body: dict = Body(default={})):
    """What matched, source by source, read without a model: {"q", "source", "offset"}. Mail comes from
    /api/archive/mail. Both are asked with POST so the words searched for are never in Hub's request log."""
    q, source, offset = str(body.get('q') or '')[:300], str(body.get('source') or ''), body.get('offset') or 0
    offset = max(0, min(int(offset), 20000)) if isinstance(offset, int) else 0
    return _archive(request, lambda archive, login: archive.search(q, login, source or None, offset))


@router.post('/api/archive/mail')
def archive_mail(request: Request, body: dict = Body(default={})):
    """Mail that matches, newest first, ten at most, searched live in Gmail through the bridge: {"q"}."""
    return _archive(request, lambda archive, login: {'mail': archive.mail_search(str(body.get('q') or '')[:300], login)})


@router.get('/api/archive/item/{ident}')
def archive_item(ident: str, request: Request):
    """One result in full: a photo's facts, a document's text, a conversation, an event or an email."""
    def work(archive, login):
        found = archive.item(ident, login)
        return found if found is not None else _refuse('Not in the archive.', 404)
    return _archive(request, work)


@router.get('/api/archive/attachment/{ident}/{part}')
def archive_attachment(ident: str, part: str, request: Request):
    """The text of a PDF or text file attached to an email, read by the bridge's isolated parser."""
    def work(archive, login):
        found = archive.mail_attachment(ident, part, login)
        return found if found is not None else _refuse('Not in the archive.', 404)
    return _archive(request, work)


@router.get('/api/archive/thumb/{ident}')
def archive_thumb(ident: str, request: Request):
    return _archive(request, lambda archive, login: _bytes(archive.picture(f'/thumb/{ident}', archive.scope(login))
                                                           if archive.ITEM.fullmatch(ident) and ident[0] in 'pd' else None))


@router.get('/api/archive/photo/{ident}')
def archive_photo(ident: str, request: Request, size: int = 1600, sharp: int = 0):
    """A photo for the full-screen viewer: the Mac's best picture of it, or with sharp=1 a 2048-pixel one from
    iCloud when the archive may ask Photos for it (it says which in X-Sharp)."""
    asked = [('size', str(max(200, min(size, 2400))))] + ([('sharp', '1')] if sharp else [])
    return _archive(request, lambda archive, login: _bytes(archive.picture(f'/photo/{ident}', asked + archive.scope(login))
                                                           if archive.ITEM.fullmatch(ident) and ident[0] == 'p' else None))


@router.get('/api/archive/page/{ident}/{number}')
def archive_page(ident: str, number: int, request: Request, size: int = 1600):
    return _archive(request, lambda archive, login: _bytes(archive.picture(f'/page/{ident}/{max(1, min(number, 10000))}',
                                                                           [('size', str(max(200, min(size, 2400))))] + archive.scope(login))
                                                           if archive.ITEM.fullmatch(ident) and ident[0] == 'd' else None))


@router.get('/api/archive/file/{ident}')
def archive_file(ident: str, request: Request):
    """A document's own bytes, to keep: documents only, never a photo's original."""
    return _archive(request, lambda archive, login: _bytes(archive.picture(f'/file/{ident}', archive.scope(login))
                                                           if archive.ITEM.fullmatch(ident) and ident[0] == 'd' else None, 'file'))


@router.post('/api/archive/kid')
def archive_kid(request: Request, body: dict = Body(default={})):
    """{"name": ..., "kid": true or false}: whether someone is one of the kids, for "the kids" in a search."""
    return _archive(request, lambda archive, login: {'ok': True, 'said': archive.set_kid(body.get('name'), bool(body.get('kid')), login)})


@router.post('/api/archive/switch')
def archive_switch(request: Request, body: dict = Body(default={})):
    """{"name": "documents" or "chats", "on": true or false}: what Ask's model may read. The owner's login only."""
    return _archive(request, lambda archive, login: {'ok': True, 'said': archive.switch(body.get('name'), bool(body.get('on')), login),
                                                      'switches': archive.switches()})
