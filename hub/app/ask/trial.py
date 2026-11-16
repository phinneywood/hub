"""A trial of Ask on the real house with nothing sent: the live check that the real model uses Ask's setup and
list tools the way Hub's own tests assume.

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

from ... import config, home
from ...engine import state
from ...tools.house import admin, broker
from . import brain, route, turns

LOGIN = 'live-check@hub.invalid'
KIND = 'checks'                 # these calls in the engine's record of runs, apart from the owner's own asking
CAP = 0.20                      # dollars one trial may cost; it stops asking once it has spent this
FILE = 'ask-trial.json'


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
            'automation': routine, 'automation_said': routine and _named(routine, w), 'locks': locks, 'locks_said': locks and _named(locks, w)}


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


def requests(who):
    """(a short name, what is asked, the check of what Ask did), for each request this house can be asked."""
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


async def _all(asked, who, ledger, started):
    out = []
    for name, text, check in asked:
        if _spent() - started >= CAP:
            out.append({'id': name, 'asked': text, 'ok': None, 'why': 'Left out: the trial had spent its limit.'})
            continue
        before = len(ledger.changes)
        seen = await _ask(text)
        seen['changes'] = ledger.changes[before:]
        why = 'Ask couldn’t finish the request.' if seen['status'] != 'ok' else check(seen, who)
        out.append({'id': name, 'asked': text, 'ok': not why, 'why': why, **seen})
    return out


def _keep(rows, cost):
    """The private record of this trial, for whoever looks into a failure. Owner-only; it names the house's things."""
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = config.DATA_DIR / FILE
        path.write_text(json.dumps({'at': time.time(), 'cost': round(cost, 4), 'requests': rows}, indent=1, default=str))
        os.chmod(path, 0o600)
    except OSError:
        pass


def run():
    """Put the requests to Ask with the real model, nothing sent. Returns {'ran', 'failed': [names], 'left_out':
    [names], 'cost'}. Raises Stopped when it can't run here."""
    from ...golden import dryrun
    try:
        route.build()
    except route.NotSetUp as error:
        raise Stopped(str(error)) from None
    try:
        who = cast(admin.world(fresh=True))
    except broker.HomeDown:
        raise Stopped('The house can’t be read, so there is nothing to ask about.') from None
    asked = requests(who)
    keep, save = brain.KIND, turns.save
    started = _spent()
    try:
        brain.KIND = KIND                           # counted apart from the owner's own asking, and against no budget of theirs
        turns.save = lambda *args, **more: 0        # no line in anyone's history of asking
        with dryrun.layer() as ledger:
            rows = asyncio.run(_all(asked, who, ledger, _spent()))
    finally:
        brain.KIND, turns.save = keep, save
    cost = max(0.0, _spent() - started)
    _keep(rows, cost)
    return {'ran': sum(r['ok'] is not None for r in rows), 'failed': [r['id'] for r in rows if r['ok'] is False],
            'left_out': [r['id'] for r in rows if r['ok'] is None], 'cost': cost}
