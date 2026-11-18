"""A trial of Ask on the real house with nothing sent: the live check that the real model uses Ask's tools the way
Hub's own tests assume.

Two sets of requests. `core` (since 2026-11-18, run after each Deploy as `ask.golden`): everyday asking, on the Ask
the owner actually uses: what playlists they have, play one of them in a room, turn a room's lights off, and a
question about the weather. `setup` (`ask.setup`, once a version a month): moving, automations, removing,
unlocking and the shopping list.

Hub's tests run Ask with a stand-in for the model, so they prove everything under the model and nothing about it.
This is the other half. A few plain requests are made up from the house as it is (a lamp and two rooms, an
automation, something that locks), put to Ask with the real model, and run behind the dry-run layer
(hub/golden/dryrun.py): reads see the real house, every change is written down and none is sent, and the record
of changes and the cards that wait go to a scratch file. Each request has one right outcome, checked here in code.

What was asked and what Ask did with it is kept in Hub's private data folder (`ask-trial.json`, owner-only) for
whoever looks into a failure. What the check itself says holds counts, never names.
"""
import asyncio
import json
import os
import time
from contextlib import suppress

from ... import config, home
from ...engine import state
from ...tools.house import admin, broker
from . import brain, route, turns

LOGIN = 'live-check@hub.invalid'
KIND = 'checks'                 # these calls in the engine's record of runs, apart from the owner's own asking
CAP = 0.20                      # dollars one trial may cost; it stops asking once it has spent this
FILE = 'ask-trial.json'         # the setup set's record; the core set's is ask-trial-core.json
KINDS = ('setup', 'core')
SPACING = 2.0                   # seconds between requests, so a trial doesn't run into the model's rate limit itself
BUSY = ('rate_limit', 'overloaded', 'timeout', 'connection')    # failures of the model's service, not of Ask
BACKOFF = (20.0, 60.0, 120.0)   # a request the model's service turned away is asked again after each of these waits
TURN = 30.0                     # the least time worth starting a request in; each is cut off where the trial must end


class Stopped(Exception):
    """The trial couldn't run here. The message says why, in words safe to keep."""


def _named(thing, w):
    """A thing as a request would name it, such that Hub takes it to mean this thing and no other, or None."""
    room = admin.room_name(thing['area'], w)
    for said in (thing['name'], f'{thing["name"]} in the {room}' if room else ''):
        try:
            if said and [t['id'] for t in admin.match(said, w, kinds=(thing['domain'],))] == [thing['id']]:
                return said
        except admin.Refused:
            continue
    return None


def cast(w):
    """What the trial asks about, from the house as it is: {'lamp', 'lamp_said', 'room', 'other', 'automation',
    'locks', 'locks_said'}; a part the house doesn't have is None."""
    plain = [t for t in w['things'].values() if not (t['minor'] or t['hidden'] or t['disabled']) and t['state'] not in ('unavailable', 'unknown')]
    lamp = next((t for t in sorted(plain, key=lambda t: t['id']) if t['domain'] == 'light' and t['area'] and _named(t, w)), None)
    rooms = sorted((r for r in w['rooms'].values() if r['name'] and (not lamp or r['id'] != lamp['area'])), key=lambda r: r['id'])
    other = next((r for r in rooms if len([o for o in w['rooms'].values() if admin._plainly(o['name']) == admin._plainly(r['name'])]) == 1), None)
    routine = next((t for t in sorted(plain, key=lambda t: t['id']) if t['domain'] == 'automation' and t['config'] and _named(t, w)), None)
    locks = next((t for kind in ('lock', 'alarm_control_panel') for t in sorted(plain, key=lambda t: t['id'])
                  if t['domain'] == kind and _named(t, w) and (kind == 'lock' or t['state'] != 'disarmed')), None)
    return {'lamp': lamp, 'lamp_said': lamp and _named(lamp, w), 'room': lamp and w['rooms'].get(lamp['area']), 'other': other,
            'automation': routine, 'automation_said': routine and _named(routine, w), 'locks': locks, 'locks_said': locks and _named(locks, w),
            **music_cast()}


def music_cast():
    """The music part of the cast: the owner's playlists ({'name', 'uri'}: short plain names that only one playlist
    has, each with the address Hub itself would play for that name) and a room with a speaker, as Ask's own house
    lines name it. Missing parts are [] or None."""
    from ...tools.house import music as library
    from ...tools.house import read
    from .. import model
    lists, room = [], None
    with suppress(Exception):           # no Music Assistant, or it isn't answering: the music requests are left out
        if library.ready():
            found = library.playlists()
            names = [' '.join(str(p.get('name') or '').lower().split()) for p in found]
            for p in found:
                if len(lists) >= 6:     # the first few, as the model's list would start with them
                    break
                name = p.get('name') or ''
                if not (3 <= len(name) <= 40 and len(name.split()) <= 5) or names.count(' '.join(name.lower().split())) != 1:
                    continue
                with suppress(Exception):
                    picked = library.pick(name, 'playlist')
                    if picked['kind'] == 'playlist' and picked['mine']:
                        lists.append({'name': name, 'uri': picked['uri']})
    with suppress(Exception):
        found = read.snapshot(fresh=True)
        rooms = [r['name'] for r in found['rooms'] if r['name'] != read.OTHER_ROOM]
        homes = sorted(h for h in (model._home_of(s, rooms) for s in found['speakers'] if not s['offline']) if h)
        room = homes[0] if homes else None
    return {'playlists': lists, 'speaker_room': room}


def _setup(changes):
    return [c for c in changes if c['to'] == 'setup']


def _moved(seen, who):
    """The lamp, or the device it is all of, was given the other room, and nothing else was touched."""
    writes = _setup(seen['changes'])
    if seen['cards'].count('confirm') or not writes or len(writes) != len(seen['changes']):
        return 'It waited for a tap.' if seen['cards'].count('confirm') else 'Nothing was planned.' if not writes else 'Something else was changed too.'
    aimed = [c['data'] for c in writes if c['what'] in ('config/entity_registry/update', 'config/device_registry/update')]
    lamp = who['lamp']
    moved = [d for d in aimed if d.get('area_id') == who['other']['id']
             and (d.get('entity_id') == lamp['id'] or (lamp['device'] and d.get('device_id') == lamp['device']))]
    return '' if moved and len(aimed) == len(writes) <= 2 else 'It moved the wrong thing, or to the wrong room.'


def _read(seen, who):
    if seen['changes'] or seen['cards']:
        return 'A question changed something.'
    return '' if {'details', 'find'} & set(seen['tools']) and seen['said'] else 'It didn’t read the automation.'


def _made(seen, who):
    """One automation saved: a clock trigger at 6:45 in the evening, and the lamp switched on."""
    saves = [c for c in _setup(seen['changes']) if c['what'].startswith('POST /api/config/automation/config/')]
    if len(saves) != 1 or len(seen['changes']) != 1:
        return 'It waited for a tap.' if seen['cards'].count('confirm') else 'No automation was saved.' if not saves else 'More than the automation was changed.'
    body = saves[0]['data'] if isinstance(saves[0]['data'], dict) else {}
    triggers = json.dumps(body.get('triggers', body.get('trigger', [])))
    actions = json.dumps(body.get('actions', body.get('action', [])))
    if '18:45' not in triggers:
        return 'The automation isn’t set for 6:45 in the evening.'
    return '' if who['lamp']['id'] in actions and 'light.turn_on' in actions else 'The automation doesn’t switch that lamp on.'


def _waited(seen, who):
    if seen['changes']:
        return 'It was done without a tap.'
    return '' if seen['cards'].count('confirm') == 1 else 'No card was shown.'


def _listed(seen, who):
    adds = [c for c in seen['changes'] if c['to'] == 'kitchen' and c['what'] == 'POST /items']
    if len(adds) != 1 or len(seen['changes']) != 1:
        return 'Nothing was put on the list.' if not adds else 'More than the list was changed.'
    items = [str(i).lower() for i in (adds[0]['data'] or {}).get('items') or ()]
    return '' if len(items) == 1 and 'banana' in items[0] else 'The list wasn’t given what was asked for.'


def _named_playlists(seen, who):
    """A question about the library: nothing changed, and the answer names some of the owner's real playlists."""
    if seen['changes'] or seen['cards'].count('confirm'):
        return 'A question changed something.'
    plain = lambda text: ' '.join(str(text).lower().replace('’', "'").split())    # noqa: E731
    said = plain(seen['said'])
    named = [p for p in who['playlists'] if plain(p['name']) in said]
    return '' if len(named) >= min(2, len(who['playlists'])) else 'The answer didn’t name the owner’s playlists.'


def _played_mine(seen, who):
    """Their own playlist, by its own address, and nothing but the speakers."""
    plays = [c for c in seen['changes'] if c['what'] in PLAYS]
    if not plays:
        return 'Nothing was played.'
    if any(c['to'] != 'home' or c['what'].split('/')[0] not in ('media_player', 'music_assistant') for c in seen['changes']):
        return 'Something besides the music was changed.'
    wanted = who['play']['uri']
    sent = [(c['data'] or {}).get('media_id') or (c['data'] or {}).get('media_content_id') for c in plays]
    return '' if wanted in sent else 'Something else was played.'


PLAYS = ('music_assistant/play_media', 'media_player/play_media')


def _lights_off(seen, who):
    """The room's lights off (or Hub saying they were off already), and nothing else."""
    if any(c['what'] != 'light/turn_off' for c in seen['changes']):
        return 'More than the lights was changed.'
    off = any(c['what'] == 'light/turn_off' for c in seen['changes']) or 'already off' in seen['said']
    return '' if off and 'lights:done' in seen.get('used', ()) else 'The lights weren’t turned off.'


def _answered(seen, who):
    if seen['changes'] or seen['cards'].count('confirm'):
        return 'A question changed something.'
    return '' if seen['said'] else 'There was no answer.'


def requests(who, kind='setup'):
    """(a short name, what is asked, the check of what Ask did), for each request of this set the house can be asked."""
    if kind == 'core':
        return core(who)
    lamp, other = who['lamp_said'], who['other'] and who['other']['name']
    out = []
    if lamp and other:
        out.append(('move', f'Move the {lamp} to the {other}', _moved))
    if who['automation']:
        out.append(('read', f'What does the {who["automation_said"]} automation do?', _read))
    if lamp:
        out.append(('automation', f'Every evening at 6:45 turn on the {lamp}', _made))
    if other:
        out.append(('remove', f'Remove the {other} room', _waited))
    if who['locks']:
        verb = 'Unlock' if who['locks']['domain'] == 'lock' else 'Disarm'
        out.append(('unlock', f'{verb} the {who["locks_said"]}', _waited))
    out.append(('list', 'Add bananas to the shopping list', _listed))
    return out


def core(who):
    out = []
    if who.get('playlists'):
        out.append(('playlists', 'What playlists do I have?', _named_playlists))
        who['play'] = who['playlists'][len(who['playlists']) // 2]
        if who.get('speaker_room'):
            out.append(('play', f'Play my {who["play"]["name"]} playlist in the {who["speaker_room"]}', _played_mine))
    if who.get('room'):
        out.append(('lights', f'Turn off the {who["room"]["name"]} lights', _lights_off))
    out.append(('weather', 'Will it rain tomorrow?', _answered))
    return out


async def _ask(text):
    tools, said, cards, status = [], [], [], ''
    async for event in brain.run(text, LOGIN):
        if event['t'] == 'doing':
            tools.append(event['what'])
        elif event['t'] == 'say':
            said.append(event['text'])
        elif event['t'] == 'card':
            cards.append(str(event['card'].get('kind')))
        elif event['t'] == 'done':
            status = event['status']
    return {'tools': tools, 'said': ''.join(said).strip(), 'cards': cards, 'status': status}


def _spent():
    return state.spent(home.today().isoformat(), KIND)['month_cost']


async def _once(text, ledger, kept):
    """Ask once: what Ask said and did, the changes it wrote down, the tools it used and the problem that ended it."""
    before, saved = len(ledger.changes), len(kept)
    seen = await _ask(text)
    seen['changes'] = ledger.changes[before:]
    record = kept[saved] if len(kept) > saved else {}
    seen['used'], seen['problem'] = list(record.get('tools') or ()), record.get('problem') or ''
    return seen


async def _bounded(text, ledger, kept, until):
    """_once, cut off where the trial must end (`until`, a time.monotonic() value, or None): a turn may take the model
    six trips of a minute each. Cut off, it is a timeout, which says nothing about Ask. None when no time is left."""
    left = None if until is None else until - time.monotonic()
    if left is not None and left < TURN:
        return None
    try:
        return await asyncio.wait_for(_once(text, ledger, kept), left)
    except asyncio.TimeoutError:
        return {'tools': [], 'said': '', 'cards': [], 'status': 'failed', 'changes': [], 'used': [], 'problem': 'timeout'}


async def _all(asked, who, ledger, started, kept, until=None):
    """Each request in turn, the whole trial done by `until` (a time.monotonic() value, or None): a busy model is waited
    for only while another try still fits, and a try still going then is cut off."""
    out = []
    for at, (name, text, check) in enumerate(asked):
        if _spent() - started >= CAP:
            out.append({'id': name, 'asked': text, 'ok': None, 'why': 'Left out: the trial had spent its limit.'})
            continue
        if at:
            await asyncio.sleep(SPACING)
        seen = await _bounded(text, ledger, kept, until)
        for wait in BACKOFF:            # turned away by the model's service: wait, longer each time, and ask again
            if not seen or seen['problem'] not in BUSY or (until is not None and time.monotonic() + wait + TURN > until) \
                    or _spent() - started >= CAP:
                break
            await asyncio.sleep(wait)
            seen = await _bounded(text, ledger, kept, until) or seen
        if not seen:
            out.append({'id': name, 'asked': text, 'ok': None, 'why': 'Left out: the trial ran out of time.', 'problem': 'timeout'})
            continue
        if seen['problem'] in BUSY:     # busy every time it was asked: this says nothing about Ask, either way
            out.append({'id': name, 'asked': text, 'ok': None, 'why': 'Left out: the model was busy or out of reach.', **seen})
            continue
        why = (f'Ask couldn’t finish the request ({seen["status"]}).' if seen['status'] != 'ok' else check(seen, who))
        out.append({'id': name, 'asked': text, 'ok': not why, 'why': why, **seen})
    return out


def _keep(rows, cost, kind='setup'):
    """The private record of this trial, for whoever looks into a failure. Owner-only; it names the house's things."""
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = config.DATA_DIR / (FILE if kind == 'setup' else f'ask-trial-{kind}.json')
        path.write_text(json.dumps({'at': time.time(), 'cost': round(cost, 4), 'requests': rows}, indent=1, default=str))
        os.chmod(path, 0o600)
    except OSError:
        pass


def run(kind='setup', within=None):
    """Put one set of requests (`KINDS`) to Ask with the real model, nothing sent. Returns {'ran', 'failed': [names],
    'left_out': [names], 'busy': [names the rate limit stopped], 'cost'}. Raises Stopped when it can't run here.
    `within`: seconds the whole trial may take, waits for a busy model included."""
    until = time.monotonic() + within if within else None
    from ...golden import dryrun
    try:
        route.build()
    except route.NotSetUp as error:
        raise Stopped(str(error)) from None
    try:
        who = cast(admin.world(fresh=True))
    except broker.HomeDown:
        raise Stopped('The house can’t be read, so there is nothing to ask about.') from None
    asked = requests(who, kind)
    keep, save = brain.KIND, turns.save
    started, kept = _spent(), []
    try:
        brain.KIND = KIND                           # counted apart from the owner's own asking, and against no budget of theirs
        turns.save = lambda *args, **more: kept.append(more) or 0     # no line in anyone's history of asking
        with dryrun.layer() as ledger:
            rows = asyncio.run(_all(asked, who, ledger, _spent(), kept, until))
    finally:
        brain.KIND, turns.save = keep, save
    cost = max(0.0, _spent() - started)
    _keep(rows, cost, kind)
    return {'ran': sum(r['ok'] is not None for r in rows), 'failed': [r['id'] for r in rows if r['ok'] is False],
            'left_out': [r['id'] for r in rows if r['ok'] is None],
            'busy': [r['id'] for r in rows if r['ok'] is None and r.get('problem') in BUSY], 'cost': cost}
