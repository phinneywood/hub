"""What Ask can do: lights, scenes, music, how the house is set up, any other device, remembering, passing a
message to Kitchen, reading mail, the calendar and school, and searching the family archive.

Few tools, each doing a whole job. Lights, scenes and music go through hub/tools/house/control.py, like a tap in
the app: the same allowlist, record and Undo, counted against Ask's own per-minute limit. Everything else in
Home Assistant (owner decision, 2026-11-15: Ask may change anything there) goes through hub/tools/house/admin.py:
planned first, recorded with the way back, and left on a card for the owner's tap where its rule says so.

A tool says what it did in a plain sentence (`turn.said`), so a request that was simply carried out needs no
second trip to the model; a tool that needs the model again (nothing matched, a name it couldn't place, something
it read) sets `turn.again`.

The family archive (`search`, `read`; owner decision, 2026-11-16): the model is given mail and calendar results,
and the passages of documents and ChatGPT history that match what was asked, with card, account and ID-like numbers
masked, unless the owner switched those off on the Archive screen. Photos are shown by Hub on a card it writes
itself; the model is told nothing about them, not even whether any were found, and it never decides whether the
model goes again. A request that read anything written by others changes nothing in the house.
"""
import re
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic_ai import RunContext
from typing_extensions import TypedDict

from ...tools.house import admin, broker, control, music as library, nowplaying, plans, read, zones
from .. import levels, model
from .. import scenes as kept_scenes
from ..words import are, counted, number, sentence, spoken
from ..words import listed as _listed
from . import private, turns

EVERYWHERE = ('everywhere', 'all', 'all lights', 'all the lights', 'every light', 'the house', 'house', 'whole house',
              'the whole house', 'inside', 'indoors')
OUTSIDE = ('outside', 'outdoors')
SEARCHES = 3            # how many different words Hub tries for one request to play something
VIA = 'ask'


@dataclass
class Turn:
    """One request from the owner: their words, who they are, and what Hub has done and said so far."""
    text: str
    login: str | None = None
    said: list = field(default_factory=list)     # what Hub tells the owner was done, a sentence per tool
    cards: list = field(default_factory=list)    # what the app draws under the answer
    again: bool = False                          # a tool needs the model to look at its answer and go on
    strangers: bool = False                      # words written by others (mail, the calendar, school, the archive) are in view
    found: dict = field(default_factory=dict)    # what search found, by the short names the model reads them by
    searched: list = field(default_factory=list)  # what search looked for: read gives back the passages that hold it
    withheld: dict = field(default_factory=dict)  # what is kept of a sentence the model must never see (photos)

    def kept(self):
        """What Hub said, as it is kept for later requests: a sentence written from photos is kept as a fixed line."""
        return [self.withheld.get(n, told) for n, told in enumerate(self.said)]


def _say(turn, told, card=None):
    """Hub tells the owner this itself; the model has nothing to add."""
    turn.said.append(told)
    if card:
        turn.cards.append(card)
    return {'done': told}


def _again(turn, problem):
    """A tool couldn't do it as asked; the model may try again or explain. Nothing was changed."""
    turn.again = True
    return {'not_done': str(problem)[:400]}


class NotNow(Exception):
    """Hub may not change the house for this request. The message is for the owner, as it is."""


HELD = {'house.control': 'Home control', 'house.setup': 'Changing the house’s setup', 'house.security': 'Locks, the garage and the alarm'}


def _may_change(turn, action='house.control'):
    """Ask changes the house only for someone signed in, and never while the owner has that kind of change set to
    "Suggest only" on the Engine page (the same setting that held the older chat back)."""
    from ...engine import catalog, state
    if turn.login is None:
        raise NotNow('Change the house from your phone or laptop.')
    if turn.strangers:      # what others wrote must never be what moved the house, or rewrote how it is set up
        raise NotNow('Hub changes the house in a request of its own, never alongside mail, the calendar, school or the '
                     'archive. Ask for it by itself.')
    if state.trust(catalog.action(action)) == 'suggest':
        raise NotNow(f'{HELD[action]} is set to “Suggest only” on the Engine page, so Hub changed nothing.')


def _outcome(turn, result, told, card=None):
    """What to tell the owner after a change: `told` when the house took it, else what happened instead."""
    from ..feed import feed
    feed.poke()
    if result.get('silent'):
        return _say(turn, 'Hub sent that, but the speaker isn’t playing. Check Music Assistant.', card)
    if result['done']:
        if card is not None and result.get('undo'):
            card = dict(card, batch=result['batch'])
        return _say(turn, told, card)
    if result['unknown']:
        return _say(turn, 'Hub sent that, but the house didn’t confirm it.', card)
    return _say(turn, 'The house couldn’t be reached, so nothing was changed.')


# ---- lights ----

def _lamp(said, by_room):
    """One lamp named with its room, "kitchen ceiling" or "ceiling in the kitchen", as (what to call it, [lamp]),
    or None. Several rooms have a lamp called Ceiling; this is how one of them is meant."""
    for room in sorted(by_room, key=len, reverse=True):
        name = room.lower()
        if said.startswith(name + ' '):
            rest = said[len(name) + 1:]
        elif said.endswith((' in the ' + name, ' in ' + name)):
            rest = said.rsplit(' in ', 1)[0]
        else:
            continue
        rest = rest.removesuffix(' lights').removesuffix(' light').strip()
        if rest in ('', 'light', 'lights', 'lamp', 'lamps'):
            continue                                    # "kitchen lights" is the room
        lamps = [d for d in by_room[room] if d['name'].lower() == rest] or [d for d in by_room[room] if rest in d['name'].lower()]
        if len(lamps) == 1:
            return f'{lamps[0]["name"]} in {spoken(room)}', lamps
    return None


def _where(names, found, floors):
    """{what the owner would call it: [its lights]} for the places named. Raises control.Refused."""
    picture = model.home(found, floors)
    outdoors = model.outdoors(found, floors)
    by_room = {r['name']: [d for d in r['devices'] if d['kind'] == 'light' and not d['offline']] for r in found['rooms']}
    out = {}
    for name in names or ():
        said = ' '.join(str(name).lower().split()).removeprefix('the ')
        floor = next((f for f in picture['floors'] if f['name'] and f['name'].lower() == said), None)
        if said in EVERYWHERE:
            out['Every light indoors'] = [d for room, lights in by_room.items() if room not in outdoors for d in lights]
        elif said in OUTSIDE:
            out['Outside'] = [d for room, lights in by_room.items() if room in outdoors for d in lights]
        elif floor:
            rooms = {picture['rooms'][r]['name'] for r in floor['rooms']}
            out[floor['name']] = [d for room in rooms for d in by_room.get(room, ())]
        elif _lamp(said, by_room):
            called, lamps = _lamp(said, by_room)
            out[called] = lamps
        else:
            ids = control.resolve(name, ('light',), found)
            rooms = control.rooms_named(name, found, loose=True)
            devices = [d for lights in by_room.values() for d in lights if d['id'] in ids]
            out[_listed([spoken(r) for r in rooms]) if rooms else (devices[0]['name'] if len(devices) == 1 else str(name))] = devices
    if not any(out.values()):
        raise control.Refused('Hub found no lights there.')
    return out


def lights(ctx: RunContext[Turn], where: list[str], change: Literal['on', 'off', 'dim'], level: int | None = None) -> dict:
    """Switch or dim lights, now. `where`: one or more of the rooms or floors named in <house>, one lamp as its
    room then its name ("kitchen ceiling"), "everywhere" for every light indoors, or "outside". `change`: on
    (each lamp comes back as bright as it last was), off, or dim with `level` from 1 to 100 (the lamps that are
    lit; a dark room comes on at that level)."""
    turn = ctx.deps
    try:
        _may_change(turn)
        found, floors = read.snapshot(fresh=True), read.floors()
        places = _where(where, found, floors)
        devices = list({d['id']: d for lamps in places.values() for d in lamps}.values())
        if change == 'off':
            changes, words = [([d['id'] for d in devices if d['on']], 'off', None)], 'off'
        elif change == 'on':
            again = {}
            for d in devices:
                if not d['on']:
                    again.setdefault(levels.last(d['id']) if d.get('dims') else None, []).append(d['id'])
            changes, words = [(ids, 'brightness' if lvl else 'on', lvl) for lvl, ids in again.items()], 'on'
        else:
            if level is None or not 1 <= int(level) <= 100:
                return _again(turn, 'Say how bright, from 1 to 100.')
            # In each place: the lamps that are lit, or all of them when it is dark. Lamps that can't dim are left.
            dims = [d['id'] for lamps in places.values() for d in ([x for x in lamps if x['on']] or lamps) if d.get('dims')]
            if not dims:
                return _again(turn, 'Those lights only switch on and off; they can’t be dimmed.')
            changes, words = [(list(dict.fromkeys(dims)), 'brightness', int(level))], f'at {int(level)}%'
        changes = [c for c in changes if c[0]]
        names, are = sentence(_listed(places)), 'is' if len(places) == 1 and ' and ' not in next(iter(places)) else 'are'
        if not changes:
            return _say(turn, f'{names} {are} already {words}.')
        result = control.several(changes, login=turn.login, via=VIA, taps=False)
    except NotNow as error:
        return _say(turn, str(error))
    except control.Refused as error:
        return _again(turn, error)
    except broker.HomeDown:
        return _say(turn, 'The house isn’t answering, so nothing was changed.')
    touched = {entity for ids, _, _ in changes for entity in ids}       # the card draws the rooms that changed, no others
    rooms = list(dict.fromkeys(model.slug(d['room']) for d in devices if d['id'] in touched))
    return _outcome(turn, result, f'{names} {are} {words}.', {'kind': 'rooms', 'rooms': rooms})


def _scene_like(said, scenes):
    """The one scene whose name holds every word said: "wind down" for a scene called "Upstairs: Wind down"."""
    asked = admin.words_of(said) - admin.FILLER
    near = [s for s in scenes if asked and asked <= admin.words_of(s['name'])]
    return near[0] if len(near) == 1 else None


def scene(ctx: RunContext[Turn], name: str) -> dict:
    """Run one of the scenes named in <house>, such as "All off", now."""
    turn = ctx.deps
    try:
        _may_change(turn)
        said = ' '.join(str(name).lower().split())
        found = read.snapshot(fresh=True)
        if said in ('all off', 'all lights off', 'everything off'):
            targets = model.indoor_lights_on(found, read.floors())
            if not targets:
                return _say(turn, 'Every light indoors is already off.')
            return _outcome(turn, control.several([(targets, 'off', None)], login=turn.login, via=VIA, taps=False),
                            'Every light indoors is off.', {'kind': 'scene'})
        mine = next((s for s in kept_scenes.kept() if s['name'].lower() == said), None)
        if mine:
            return _outcome(turn, kept_scenes.run(mine['id'], login=turn.login, via=VIA), f'{mine["name"]} is on.', {'kind': 'scene'})
        theirs = next((s for s in found['scenes'] if s['name'].lower() == said), None) or _scene_like(said, found['scenes'])
        if theirs and (theirs.get('sets') is None or set(theirs['sets']) - {'light', 'fan', 'media_player'}):
            # A scene that holds more than lights, fans and speakers (a lock, the alarm, a switch or a helper that
            # may set something off), or doesn't say what it holds, goes the careful way: planned, and on a card
            # for the owner's tap if setting it would unlock, open or disarm.
            return _setup(turn, 'act', {'action': 'scene.turn_on', 'things': [theirs['id']]}, 'house.security')
        if theirs:
            return _outcome(turn, control.change([theirs['id']], 'activate', login=turn.login, via=VIA), f'{theirs["name"]} is on.')
    except NotNow as error:
        return _say(turn, str(error))
    except control.Refused as error:
        return _again(turn, error)
    except broker.HomeDown:
        return _say(turn, 'The house isn’t answering, so nothing was changed.')
    return _again(turn, f'There is no scene called “{str(name)[:60]}”.')


# ---- music ----

def _speakers(names, found):
    """Speaker entity ids for the rooms or speakers named, in order. Raises control.Refused."""
    rooms = [r['name'] for r in found['rooms'] if r['name'] != read.OTHER_ROOM]
    rooms += [s['room'] for s in found['speakers'] if s['room'] != read.OTHER_ROOM and s['room'] not in rooms]
    homes = {s['id']: model._home_of(s, rooms) for s in found['speakers'] if not s['offline']}
    out = []
    for name in names or ():
        said = ' '.join(str(name).lower().split()).removeprefix('the ')
        here = [s for s, room in homes.items() if room and room.lower() == said]
        out += here[:1] or control.resolve(name, ('media_player',), found)[:1]
    return list(dict.fromkeys(out))


def _called(entity, found):
    """A speaker as the owner would say it: its room ("the kitchen") when it has one, else its own name."""
    rooms = [r['name'] for r in found['rooms'] if r['name'] != read.OTHER_ROOM]
    rooms += [s['room'] for s in found['speakers'] if s['room'] != read.OTHER_ROOM and s['room'] not in rooms]
    speaker = next(s for s in found['speakers'] if s['id'] == entity)
    room = model._home_of(speaker, rooms)
    return spoken(room) if room else speaker['name']


def _playing(found):
    """The group that is playing or paused: what "it" means in "pause it" or "play it in the kitchen too"."""
    groups = model.streams(found)
    return groups[0] if groups and groups[0]['state'] in ('playing', 'paused', 'buffering') else None


def _card(first, players):
    """The card under the answer: the speakers this touched, the first and the ones that play with it."""
    with_it = [first] if isinstance(first, str) else list(first)
    with_it += [m for e in list(with_it) for m in players.get(e, {}).get('members', ())]
    return {'kind': 'music', 'speakers': [e for e in dict.fromkeys(with_it) if e in players]}


def _pick(what, kind):
    """The first of the owner's words that finds something to play, or (None, the reasons)."""
    reasons = []
    for words in list(what or ())[:SEARCHES]:
        try:
            return library.pick(words, kind), reasons
        except control.Refused as error:
            reasons.append(str(error))
    return None, reasons


def music(ctx: RunContext[Turn], do: Literal['play', 'pause', 'resume', 'next', 'previous', 'volume', 'join', 'leave'],
          rooms: list[str] | None = None, what: list[str] | None = None,
          kind: Literal['playlist', 'album', 'artist', 'track', 'radio'] | None = None, volume: int | None = None) -> dict:
    """Music on the speakers, now.
    play: `what` is one to three short searches, best first, and Hub plays the first that finds something: one of
    the owner's playlists by name, an artist, an album, a song, or a genre or mood in two or three words ("coffee
    shop jazz"). Turn a description into things that can be searched: for an era or an occasion, artists or songs
    you know from it. `kind` limits what is played. `rooms`: where, by the room names in <house>; several rooms
    play in sync. Leave `rooms` out to change what is playing where it is playing. `volume` is 0 to 100; left out,
    music starting from silence starts softly.
    join / leave: `rooms` start or stop playing what is on ("in the kitchen as well", "not in the bedroom").
    pause, resume, next, previous: what is playing. volume: `volume` in `rooms`, or everywhere it plays."""
    turn = ctx.deps
    try:
        _may_change(turn)
        found = read.snapshot(fresh=True)
        players = {d['id']: d for d in found['speakers']}
        now = _playing(found)
        asked = _speakers(rooms, found)
        if do == 'play':
            if not library.ready():
                return _say(turn, 'Music Assistant isn’t set up in Hub, so Hub can’t look up what to play.')
            targets = asked or ([now['leader']] if now else [])
            if not targets:
                return _again(turn, 'Nothing is playing, so Hub needs to know where: ask the owner which room.')
            picked, reasons = _pick(what, kind)
            if picked is None:
                return _again(turn, ' '.join(reasons) or 'Say what to play.')
            leader = targets[0]
            mates = zones.mates(leader, players)
            together = list(dict.fromkeys([*targets[1:], *mates]))
            wanted = [leader, *together]
            alone = bool(asked) and any(m not in wanted for m in players[leader]['members'])    # named rooms: only there
            result = control.change([leader], 'play_media', login=turn.login, via=VIA, value=volume, alone=alone,
                                    query=picked['uri'], label=picked['name'], kind=picked['kind'], together=together)
            card = {'kind': 'music', 'speakers': [e for e in wanted if e in players]} if asked else _card(leader, players)
            where = _listed([_called(e, found) for e in card['speakers']])
            loud = f' at {int(volume)}%' if volume is not None else ''
            return _outcome(turn, result, f'Playing {picked["name"]} in {where}{loud}.', card)
        if do in ('join', 'leave'):
            if not now:
                return _again(turn, 'Nothing is playing to join or leave.')
            if not asked:
                return _again(turn, 'Say which room.')
            results = [nowplaying.change(now['leader'], 'add' if do == 'join' else 'remove', login=turn.login, via=VIA, target=e)
                       for e in asked]
            names = _listed([_called(e, found) for e in asked])
            done = {'done': [w for r in results for w in r['done']], 'unknown': [w for r in results for w in r['unknown']],
                    'not_sent': [], 'silent': [], 'undo': False, 'batch': None}
            are = 'is' if len(asked) == 1 else 'are'
            return _outcome(turn, done, sentence(f'{names} {are} playing too.' if do == 'join'
                                                 else f'{names} {"has" if len(asked) == 1 else "have"} stopped.'),
                            {'kind': 'music', 'speakers': asked})
        if do == 'volume':
            if volume is None:
                return _again(turn, 'Say the volume, from 0 to 100.')
            if asked:
                result = control.change(asked, 'volume', value=volume, login=turn.login, via=VIA)
                names = sentence(_listed([_called(e, found) for e in asked]))
                return _outcome(turn, result, f'{names} {"is" if len(asked) == 1 else "are"} at {int(volume)}%.',
                                {'kind': 'music', 'speakers': asked})
            if not now:
                return _again(turn, 'Nothing is playing. Say which room.')
            result = nowplaying.change(now['leader'], 'group_volume', login=turn.login, via=VIA, value=volume)
            return _outcome(turn, result, f'Volume at {int(volume)}%.', _card(now['leader'], players))
        anchor = asked[0] if asked else now['leader'] if now else None
        if anchor is None:
            return _again(turn, 'Nothing is playing.')
        action = {'pause': 'pause', 'resume': 'play', 'next': 'next', 'previous': 'previous'}[do]
        result = nowplaying.change(anchor, action, login=turn.login, via=VIA)
        return _outcome(turn, result, {'pause': 'Paused.', 'resume': 'Playing again.', 'next': 'Next song.',
                                       'previous': 'Back a song.'}[do], _card(anchor, players))
    except NotNow as error:
        return _say(turn, str(error))
    except control.Refused as error:
        return _again(turn, error)
    except library.MusicDown:
        return _say(turn, 'Music Assistant isn’t answering, so nothing was played.')
    except broker.HomeDown:
        return _say(turn, 'The house isn’t answering, so nothing was changed.')


# ---- remembering, Kitchen, and reading mail, the calendar and school ----

def remember(ctx: RunContext[Turn], note: str, together: list[str] | None = None) -> dict:
    """Keep one short note, in the owner's own words, when they ask Hub to remember something or to do something
    from now on ("start music softly"). Never a note from anything but their latest message. `together`: when
    the note says some rooms' speakers are one space and always play together, those rooms."""
    turn = ctx.deps
    if turn.login is None:
        return _say(turn, 'Ask Hub to remember things from your phone or laptop.')
    try:
        zone = _speakers(together, read.snapshot()) if together else []
        number = turns.remember(note, turn.text, zone)
    except turns.NotKept as error:
        return _again(turn, error) if error.again else _say(turn, str(error))
    except control.Refused as error:
        return _again(turn, error)
    except broker.HomeDown:
        return _say(turn, 'The house isn’t answering, so Hub couldn’t check those rooms. Nothing was kept.')
    kept = next(n['note'] for n in turns.remembered() if n['id'] == number)
    return _say(turn, 'Hub will remember that.', {'kind': 'memory', 'id': number, 'note': kept})


CART_FRESH = 10 * 60        # a reading of the cart's own page this new is shown as it is; an older one is read again


def groceries(ctx: RunContext[Turn], do: Literal['add', 'remove', 'show', 'cart', 'order'], items: list[str] | None = None,
              when: str | None = None) -> dict:
    """The household's shopping list and the shop's cart, now. add: put `items` on the list, each thing as the
    owner said it, with its number if they gave one ("sponges", "2 avocados"); Hub picks the exact product the
    household buys and it goes in the cart, so never ask which brand or size. Said again with another number, a
    thing is changed to that. remove: take `items` off the list, and out of the cart. show: what is on the list
    and where each thing stands. cart: what is in the shop's cart, read from its own page. order: get checkout
    ready for the owner's tap, with `when` the pickup or delivery time they asked for, in their words. Nothing is
    ever ordered without their tap on the card."""
    from .. import kitchen as shop
    turn = ctx.deps
    if turn.login is None:
        return _say(turn, 'Change the list from your phone or laptop.')
    try:
        if do == 'add':
            found = shop.add(items, turn.login, source='ask')
            new, old = [i['name'] for i in found['added']], [i['name'] for i in found['already']]
            picks = sum(i['state'] == 'pick' for i in found['added'])
            told = [sentence(f'{_listed(new)} {are(new)} on the list.')] if new else []
            if old:
                told.append(sentence(f'{_listed(old)} {"were" if are(old) == "are" else "was"} already there.' if new
                                     else f'{_listed(old)} {are(old)} already on the list.'))
            for item in found['changed']:               # said again with another number: the same thing, changed
                told.append(sentence(f'{item["name"]} changed to {number(item["asked"] or item["qty"]).lower()}.'))
            if picks:
                told.append('Hub hasn’t bought it before: pick one below.' if len(new) == 1
                            else f'{counted(picks, "thing")} {"needs" if picks == 1 else "need"} a product picked below.')
            card = {'kind': 'groceries', 'items': [i['id'] for i in found['added'] + found['already'] + found['changed']]}
            return _say(turn, ' '.join(told) or 'Say what to add.', card if card['items'] else None)
        if do == 'remove':
            on_list = shop.picture()
            if not on_list['ok']:
                raise shop.Old(on_list['said']) if on_list['why'] == 'old' else shop.Refused(on_list['said'])
            going = shop.named(items, on_list['items'])
            if not going:
                return _say(turn, 'Hub didn’t find that on the list.', {'kind': 'groceries', 'all': True})
            gone = [shop.remove(i['id'], turn.login) for i in going]
            names = [i['name'] for i in gone]
            told = sentence(f'{_listed(names)} {are(names)} off the list')
            return _say(turn, told + (', and coming out of the cart.' if any(i['state'] == 'removing' for i in gone) else '.'))
        if do == 'show':
            on_list = shop.picture()
            if not on_list['ok']:
                raise shop.Old(on_list['said']) if on_list['why'] == 'old' else shop.Refused(on_list['said'])
            things = on_list['items']
            if not things:
                return _say(turn, 'The list is empty.')
            carted, picks = sum(i['state'] == 'cart' for i in things), sum(i['state'] in ('pick', 'unavailable') for i in things)
            told = f'{counted(len(things), "thing")} {"is" if len(things) == 1 else "are"} on the list'
            if carted == len(things):
                told += ', in the cart' if carted == 1 else ', all in the cart'
            else:
                parts = [f'{number(carted)} in the cart'] if carted else []
                parts += [f'{number(picks)} {"needs" if picks == 1 else "need"} a pick'] if picks else []
                told += ': ' + ', '.join(parts) if parts else ''
            return _say(turn, told + '.', {'kind': 'groceries', 'all': True})
        if do == 'cart':
            on_list = shop.picture()
            if not on_list['ok']:
                raise shop.Old(on_list['said']) if on_list['why'] == 'old' else shop.Refused(on_list['said'])
            seen = on_list['cart']
            if seen and seen['at'] and time.time() - seen['at'] < CART_FRESH:
                lines = sum(line['qty'] for line in seen['lines'])
                return _say(turn, (f'{counted(lines, "thing")} {"is" if lines == 1 else "are"} in the cart.' if lines else 'The cart is empty.'),
                            {'kind': 'groceries', 'cart': True})
            shop.tap('read', {}, turn.login)
            return _say(turn, 'Reading the cart. This takes a minute.', {'kind': 'groceries', 'cart': True, 'since': time.time()})
        after = shop.order(when or '', turn.login)
        return _say(turn, 'Reading checkout. This takes a minute.',
                    {'kind': 'kitchen', 'after': after} if isinstance(after, int) else None)
    except shop.Old:                    # a Kitchen from before it kept the list itself: its assistant still takes the words
        return kitchen(ctx)
    except shop.Refused as error:
        return _say(turn, str(error))


def kitchen(ctx: RunContext[Turn]) -> dict:
    """Pass the owner's message, word for word, to the kitchen assistant, which answers about cooking, recipes,
    meal ideas, what to make from what is on hand, past orders and prices, and can plan the ingredients a recipe
    still needs. It takes a minute or two. Call it once and say nothing else. For the shopping list and the cart
    themselves, use groceries."""
    from ...engine import catalog
    from ...tools.kitchen import client
    turn = ctx.deps
    if turn.login is None:
        return _say(turn, 'Ask Kitchen from your phone or laptop.')
    try:
        sent = catalog.kitchen_send(turn.text, turn.login)
    except client.KitchenDown:
        return _say(turn, 'Kitchen isn’t answering right now.')
    return _say(turn, 'Kitchen is on it.', {'kind': 'kitchen', 'after': sent['message']})


def look_up(ctx: RunContext[Turn], topics: list[Literal['mail', 'calendar', 'school', 'today']]) -> dict:
    """Read the owner's mail, their calendar, their child's school (the class guide) or what needs them today, to
    answer a question about them. Read-only: Hub can't send, file, delete or book anything from here."""
    turn = ctx.deps
    turn.again = turn.strangers = True
    try:
        return {'data': private.data(turn.text, set(topics) or {'today'})}
    except Exception:  # noqa: BLE001 - a reader that failed is an answer the model can pass on
        return {'not_done': 'Hub couldn’t read that just now.'}


# ---- how the house is set up, and every other device (hub/tools/house/admin.py) ----

SECURITY = ('lock', 'alarm_control_panel')
OPENING = {'unlock': 'Unlock', 'open': 'Open', 'open_cover': 'Open', 'toggle': 'Open', 'set_cover_position': 'Open', 'alarm_disarm': 'Disarm',
           'alarm_trigger': 'Set off', 'open_valve': 'Open', 'set_valve_position': 'Open'}


def find(ctx: RunContext[Turn], words: str | None = None, kind: str | None = None) -> dict:
    """Look in Home Assistant for things by name, when <house> doesn't already name what you need: devices,
    rooms, scenes, automations, scripts, helpers. `words`: part of a name or a room. `kind`: a domain such as
    "climate", "lock", "cover", "fan", "switch", "automation", "script", "scene", "input_boolean", or "room",
    "floor", "group", "integration" or "dashboard". Returns names and ids for the other tools. Read-only."""
    turn = ctx.deps
    turn.again = True
    try:
        return admin.find(words, kind)
    except broker.HomeDown:
        return {'not_done': 'The house isn’t answering.'}


def details(ctx: RunContext[Turn], thing: str) -> dict:
    """Everything about one thing, by its name or id: a device's state and what it can be set to, an automation's,
    script's or scene's config as Home Assistant keeps it, or what is in a room. Read an automation or script
    before changing it. Read-only."""
    turn = ctx.deps
    turn.again = True
    try:
        return admin.detail(thing)
    except control.Refused as error:
        return {'not_done': str(error)[:400]}
    except broker.HomeDown:
        return {'not_done': 'The house isn’t answering.'}


def _question(plan, args, changes, why):
    """The card's question, from what would change."""
    name = changes[0]['name']
    if why == 'many':
        return f'Make these {len(changes)} changes?'
    if why == 'delete':
        return f'Remove {name}?' if len(changes) == 1 else f'Remove these {len(changes)}?'
    if why == 'disable':
        return f'Disable {name}?' if len(changes) == 1 else f'Disable these {len(changes)}?'
    if why == 'hide':
        return f'Hide {name}?' if len(changes) == 1 else f'Hide these {len(changes)}?'
    if plan == 'arrange':           # a new name, a new room, or the return of something that guards the house
        was, renamed, new = changes[0]['words'].partition(' renamed ')
        return f'Rename {was} to {new}?' if renamed else f'Enable {name}?' if 'enabled again' in changes[0]['words'] else f'Move {name}?'
    if why == 'unlock':
        verb = OPENING.get(str(args.get('action', '')).partition('.')[2], 'Open')
        return f'{verb} {name}?' if len(changes) == 1 else f'{verb} these {len(changes)}?'
    if why in ('security', 'unread'):
        domain, _, service = str(args.get('action', '')).partition('.')
        words = changes[0]['words']
        if args.get('do') in ('on', 'off') or (domain in ('automation', 'homeassistant') and 'switched o' in words):
            verb = 'Switch on' if words.endswith('switched on') else 'Switch off'
        elif args.get('do') == 'save':
            verb = 'Save'
        elif domain == 'scene':
            verb = 'Set'
        elif args.get('do') == 'run' or domain in ('script', 'automation') or (domain == 'homeassistant' and changes[0]['kind'] in ('script', 'scene')):
            verb = 'Run'
        else:                       # a switch, a button or a helper that belongs to the locks or the alarm, or sets them off
            verb = {'turn_on': 'Switch on', 'turn_off': 'Switch off', 'press': 'Press'}.get(service, 'Change')
        return f'{verb} {name}?' if len(changes) == 1 else f'{verb} these {len(changes)}?'
    return 'Restart Home Assistant?' if why == 'restart' else 'Change the house’s settings?'


def _setup(turn, plan, args, action='house.setup'):
    """Plan one change to the house's setup and make it now, or leave it on a card for the owner's tap.

    Nothing is changed on a turn that has mail, the calendar or school in view: words written by strangers must
    never be what moved a lock or rewrote an automation."""
    args = {k: v for k, v in args.items() if v is not None}
    try:
        _may_change(turn, action)
        changes = plans.PLANS[plan](admin.world(fresh=True), **args)
        why = admin.asks(changes)
        if why:
            card = admin.wait(_question(plan, args, changes, why), changes, plan, args, login=turn.login)
            return _say(turn, 'That one waits for your tap.', card)
        result = admin.carry_out(changes, login=turn.login, via=VIA)
    except NotNow as error:
        return _say(turn, str(error))
    except admin.Already as error:
        return _say(turn, str(error))
    except control.Refused as error:
        return _again(turn, error)
    except broker.HomeDown:
        return _say(turn, 'The house isn’t answering, so nothing was changed.')
    from ..feed import feed
    feed.poke()
    done, missed = result['done'], result['unknown'] + result['not_sent']
    if not done and result['not_sent'] and result['why']:      # Home Assistant's own no: the model may be able to put it right
        return _again(turn, f'Home Assistant said no ({result["why"]}), so nothing was changed.')
    if not done:
        return _say(turn, 'Hub sent that, but the house didn’t confirm it.' if result['unknown']
                    else 'The house couldn’t be reached, so nothing was changed.')
    told = sentence(done[0]) + '.' if len(done) == 1 else f'{len(done)} changes made.'
    if missed:
        told += f' {len(missed)} didn’t take.'
    card = {'kind': 'setup', 'lines': done if len(done) > 1 else [], 'missed': missed}
    return _say(turn, told, dict(card, batch=result['batch']) if result['undo'] else card)


def arrange(ctx: RunContext[Turn], things: list[str], room: str | None = None, name: str | None = None,
            hidden: bool | None = None, enabled: bool | None = None, icon: str | None = None) -> dict:
    """Change where things are, what they are called or how they show in Home Assistant, now. `things`: names as
    in <house> ("living room floor lamp", "kitchen lights") or ids. `room`: move them there ("none" takes them out
    of every room). `name`: a new name, for one thing. `hidden`: hide a thing from Hub and Home Assistant's own
    screens, or show it again. `enabled`: false disables a thing in Home Assistant altogether (it waits for the
    owner's tap), true enables it again. `icon`: one of Home Assistant's, such as "mdi:floor-lamp"."""
    return _setup(ctx.deps, 'arrange', {'things': things, 'room': room, 'name': name, 'hidden': hidden, 'enabled': enabled, 'icon': icon})


def rooms(ctx: RunContext[Turn], kind: Literal['room', 'floor', 'group'], do: Literal['add', 'rename', 'set', 'remove'], name: str,
          new_name: str | None = None, floor: str | None = None, rooms: list[str] | None = None, level: int | None = None) -> dict:
    """Add, rename, set or remove a room, a floor, or a group of rooms that make one open space (a group is reached
    by its name like a room: "the open plan"). add a room with its `floor`. A group: add with `rooms` makes it, or
    puts those rooms in a group that exists; remove with `rooms` takes those rooms out of it; set with `rooms`
    says all of them at once. set: a room's `floor`, a floor's `level` (0 the ground floor, 1 above, -1 below).
    Removing a room, a floor or a whole group waits for the owner's tap."""
    return _setup(ctx.deps, 'place', {'kind': kind, 'do': do, 'name': name, 'new_name': new_name, 'floor': floor, 'rooms': rooms,
                                      'level': level})


class Kept(TypedDict, total=False):
    """One thing in a scene: a light, plug or fan, or a room for all its lights."""
    thing: str
    on: bool
    level: int
    colour: str


def scene_setup(ctx: RunContext[Turn], do: Literal['save', 'rename', 'remove'], name: str, set: list[Kept] | None = None,     # noqa: A002
                as_now: list[str] | None = None, leave: list[str] | None = None, new_name: str | None = None) -> dict:
    """Make or change one of Home Assistant's scenes (to run one, use scene). save makes the scene, or changes
    the one of that name and leaves the rest of it as it was. `set`: what it should set, each a light, plug, fan
    or a room's lights, with on, off or a level from 1 to 100, and a colour by name if wanted. `as_now`: rooms or
    lights to keep exactly as they are at this moment. `leave`: things to take out of the scene."""
    return _setup(ctx.deps, 'scene', {'do': do, 'name': name, 'set': set, 'capture': as_now, 'leave': leave, 'new_name': new_name})


def routine(ctx: RunContext[Turn], kind: Literal['automation', 'script'], do: Literal['save', 'rename', 'remove', 'on', 'off', 'run'],
            name: str, config: dict | None = None, new_name: str | None = None) -> dict:
    """Automations and scripts in Home Assistant. on / off switch an automation; run runs one now. save makes one,
    or replaces the one of that name: `config` is the whole thing as Home Assistant writes it. An automation:
    alias, description (one plain sentence saying what it does), triggers, conditions, actions, mode. A script:
    alias, description, sequence, mode. Under entity_id and area_id give ids, or names exactly as <house> has them
    ("bedroom floor lamp", "Kitchen"): Hub looks each id up and refuses a name that fits two things. To change one,
    read it with details first and send the whole config back with the change made. Clock times are the house's
    local time. One that unlocks or opens a door waits for the owner's tap, and so does removing one."""
    return _setup(ctx.deps, 'routine', {'kind': kind, 'do': do, 'name': name, 'config': config, 'new_name': new_name})


def device(ctx: RunContext[Turn], action: str, things: list[str] | None = None, data: dict | None = None) -> dict:
    """Any device that isn't a plain light, scene or music request, by Home Assistant service. `action` is
    "domain.service", `things` the names or ids it is for, `data` the service's own fields.
    Thermostat: climate.set_temperature {"temperature": 68}, climate.set_hvac_mode {"hvac_mode": "heat"}.
    Fans: fan.turn_on, fan.turn_off, fan.set_percentage {"percentage": 50}. Plugs: switch.turn_on.
    A light's colour or a slow fade: light.turn_on {"color_temp_kelvin": 2700, "transition": 5}.
    Locks: lock.lock, lock.unlock. Garage, doors and blinds: cover.open_cover, cover.close_cover,
    cover.set_cover_position {"position": 40}. Helpers: input_boolean.turn_on, input_number.set_value {"value": 3},
    input_select.select_option {"option": "Away"}. Also vacuum, humidifier, alarm_control_panel, button.press,
    timer.start, a notice to a household phone (notify.mobile_app_<phone> {"message": "..."}, in the owner's own
    words), and homeassistant.restart. Unlocking, opening a door or the garage, disarming and restarting wait for
    the owner's tap, and so does anything that belongs to a lock or the alarm."""
    domain = str(action or '').partition('.')[0]
    if domain in ('notify', 'persistent_notification', 'tts'):
        from ...engine import judge, notes
        said = ' '.join(str(part) for part in ((data or {}).get('title'), (data or {}).get('message')) if part)
        if judge.scrub(said) != said:
            return _again(ctx.deps, 'A notice can’t hold links or email addresses.')
        if not notes.grounded(str((data or {}).get('message') or ''), ctx.deps.text):
            return _again(ctx.deps, 'A notice says what the owner asked for, in their own words: use the words of their message.')
    doors = domain == 'cover' and any(word in ' '.join(things or ()).lower() for word in ('garage', 'door', 'gate'))
    return _setup(ctx.deps, 'act', {'action': action, 'things': things, 'data': data},
                  'house.security' if domain in SECURITY or doors else 'house.control')


def integration(ctx: RunContext[Turn], name: str) -> dict:
    """Reload one of Home Assistant's integrations (Hue, Sonos, a thermostat's own) when its devices have stopped
    answering: it reconnects, and nothing about how it is set up changes. `name`: its title, or what it is ("hue").
    Look with find and kind "integration" to see which there are and which are not working."""
    return _setup(ctx.deps, 'integration', {'name': name})


def dashboard(ctx: RunContext[Turn], do: Literal['add', 'add_view', 'remove_view', 'rename', 'remove'], name: str,
              rooms: list[str] | None = None, things: list[str] | None = None, view: str | None = None, new_name: str | None = None) -> dict:
    """Home Assistant's own dashboards (its screens, not Hub's). add makes a dashboard called `name` showing a view
    for each of `rooms` (what is in the room, as tiles), or one view of `things` titled `view`. add_view adds such
    a view to the dashboard `name`; remove_view takes the view titled `view` out. rename with `new_name`; remove.
    Removing a dashboard or a view waits for the owner's tap. Look with find and kind "dashboard" to see which
    there are and their views."""
    return _setup(ctx.deps, 'dashboard', {'do': do, 'name': name, 'rooms': rooms, 'things': things, 'view': view, 'new_name': new_name})


def setting(ctx: RunContext[Turn], do: Literal['add', 'change', 'remove'],
            kind: Literal['input_boolean', 'input_number', 'input_select', 'input_text', 'input_datetime', 'input_button', 'timer',
                          'counter', 'house'],
            name: str | None = None, fields: dict | None = None, new_name: str | None = None) -> dict:
    """Helpers that automations can use, and the house's own settings. A helper is an input_boolean (a switch),
    input_number (`fields`: min, max, step), input_select (options), input_text, input_datetime (has_date,
    has_time), input_button, timer (duration) or counter: add, change or remove one by `name`.
    kind "house" with do "change" sets the whole house's own `fields`: location_name, unit_system ("metric" or
    "us_customary"), time_zone, currency, country, language. That waits for the owner's tap."""
    if kind == 'house':
        return _setup(ctx.deps, 'house', {'fields': fields or {}})
    return _setup(ctx.deps, 'helper', {'do': do, 'kind': kind, 'name': name or '', 'fields': fields, 'new_name': new_name})


# ---- the family archive (hub/app/archive.py) ----

ARCHIVE_KEPT = 'Hub answered from the archive itself.'
PHOTOS_TOLD = ('Any photos are shown to the owner on a card Hub draws and describes itself. You are told nothing about '
               'photos: never say what they show, how many there are, or whether there are any.')
LOCKED = {'documents': 'Matches are on the card for the owner to open. You can’t read documents.',
          'chats': 'Matches are on the card for the owner to open. You can’t read ChatGPT history.'}
CARD_MOST = 6
PHOTOS_SHOWN = 12
NUMBER = re.compile(r'(?<![\w/])(?:\d[ -]?){5,}\d(?![\w/])')
DATE = re.compile(r'\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}')


def mask(text):
    """Card, account and other ID-like numbers hidden, all but their last four digits (two for a short one)."""
    def hide(found):
        digits = re.sub(r'\D', '', found.group())
        if DATE.fullmatch(found.group().strip()):
            return found.group()
        keep = 4 if len(digits) >= 10 else 2
        return '•' * (len(digits) - keep) + digits[-keep:]
    return NUMBER.sub(hide, str(text or ''))


def _date(value):
    return value if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) else None


def _plain(snippet):
    return ' '.join(str(snippet or '').replace('\x02', '').replace('\x03', '').split())


def search(ctx: RunContext[Turn], searches: list[str], sources: list[Literal['photos', 'documents', 'mail', 'calendar', 'chats']] | None = None,
           people: list[str] | None = None, place: str | None = None, after: str | None = None, before: str | None = None,
           newest_first: bool = False) -> dict:
    """Search the family archive: photos and videos, documents (PDFs, scans, notes), mail, calendar history and the
    owner's ChatGPT history. `searches`: two to four ways of saying what to look for, a few words each, best first
    ("furnace service", "HVAC maintenance", "heating tune-up"); several phrasings and synonyms find more. `sources`:
    where to look; leave it out to look everywhere. `people`: names of people, or "kids", for photos. `place`: a place
    name, for photos. `after`, `before`: dates as YYYY-MM-DD. `newest_first`: for "when did we last ...". Returns
    what you may read: mail and calendar results with a ref each; documents and ChatGPT history only when the owner
    allows Ask to read them. Read one result in full with read."""
    from .. import archive
    turn = ctx.deps
    allowed = archive.sources_for(turn.login)
    wanted = [s for s in (sources or allowed) if s in allowed]
    phrases = [' '.join(str(s).split())[:120] for s in (searches or []) if str(s).strip()][:4]
    turn.searched = list(dict.fromkeys(turn.searched + phrases))[:6]
    named = [str(p)[:80] for p in (people or [])][:6]
    kids = any(n.lower() in archive.KIDS or n.lower() in ('the kids', 'our kids') for n in named)
    meaning = {'terms': ' '.join(phrases[:1]), 'sources': wanted, 'kind': [], 'people': [n for n in named if n.lower() not in
               archive.KIDS and n.lower() not in ('the kids', 'our kids')], 'kids': kids, 'after': _date(after),
               'before': _date(before), 'months': [], 'sort': 'newest' if newest_first else None,
               'place': str(place or '')[:80] or None}
    told, cards, lines = {}, [], []
    shown = {'count': 0, 'days': 0, 'items': []}
    switches = archive.switches()
    try:
        known = archive.people(turn.login) if (meaning['people'] or kids) else []
        names = {p['name'].lower(): p['name'] for p in known}
        meaning['people'] = [names.get(n.lower(), n) for n in meaning['people']]
        kid_names = [p['name'] for p in known if p['kid']]
        if 'photos' in wanted:
            shown = archive.local(meaning, ['photos'], turn.login, limit=PHOTOS_SHOWN, sort='best', searches=phrases,
                                  kids=kid_names).get('photos') or shown
            told['photos'] = PHOTOS_TOLD
        rest = [s for s in wanted if s in ('documents', 'chats')]
        found = archive.local(meaning, rest, turn.login, limit=8, searches=phrases, kids=kid_names) if rest and phrases else {}
    except archive.Away as away:
        found, told['archive'] = {}, str(away)
    for source in ('documents', 'chats'):
        if source not in wanted:
            continue
        items = (found.get(source) or {}).get('items', [])
        for n, item in enumerate(items, 1):
            ref = f'{source[0]}{n}'
            turn.found[ref] = item['id']
            lines.append({'id': item['id'], 'source': source, 'title': item['title'], 'day': item.get('day'),
                          'sub': item.get('folder') or ('ChatGPT' if source == 'chats' else ''), 'page': item.get('page')})
        if not switches.get(source):
            told[source] = LOCKED[source] if items else 'Nothing matched.'
        else:
            told[source] = [{'ref': ref, 'title': mask(i['title']), 'date': i.get('day'), 'where': i.get('folder') or None,
                             'snippet': mask(_plain(i.get('snippet')))} for ref, i in zip([f'{source[0]}{n}' for n in range(1, len(items) + 1)], items)]
            turn.strangers = turn.strangers or bool(items)
    if 'mail' in wanted and (phrases or meaning['people']):
        try:
            mails = archive.mail(meaning, phrases or None)
        except Exception:  # noqa: BLE001 - mail that can't be searched is an answer the model can pass on
            mails, told['mail'] = [], 'Mail couldn’t be searched just now.'
        from ... import store
        readable = []
        for n, m in enumerate(mails, 1):
            ref = f'm{n}'
            turn.found[ref] = m['id']
            lines.append({'id': m['id'], 'source': 'mail', 'title': m['subject'] or '(No subject)', 'day': m['day'], 'sub': m['from']})
            if not store.ai_blocked(m['address']):          # a sender with AI switched off is on the card, never sent
                readable.append({'ref': ref, 'from': m['from'], 'subject': m['subject'], 'date': m['day'], 'snippet': m['snippet']})
        told.setdefault('mail', readable or 'Nothing the model may read matched.' if mails else 'Nothing matched.')
        turn.strangers = turn.strangers or bool(readable)
    if 'calendar' in wanted and (phrases or meaning['after']):
        events = archive.calendar(meaning, phrases or None)
        for n, e in enumerate(events, 1):
            turn.found[f'e{n}'] = e['id']
            lines.append({'id': e['id'], 'source': 'calendar', 'title': e['title'], 'day': e['day'], 'sub': e['location']})
        told['calendar'] = [{'ref': f'e{n}', 'title': e['title'], 'date': e['day'], 'where': e['location'] or None}
                            for n, e in enumerate(events, 1)] or 'Nothing matched.'
        turn.strangers = turn.strangers or bool(events)
    asked = 'photos' in archive.understand(turn.text)['sources'] or wanted == ['photos']
    hub_says = None
    if 'archive' in told and not lines:
        hub_says = told['archive']
    elif asked or (not lines and shown['count']):      # photos lead, or are all there is: Hub's sentence is the answer
        about = (archive.understand(turn.text)['about'] if len(turn.text) <= 80 else '') or (phrases[0] if phrases else 'Photos')
        hub_says = archive.photo_sentence(about, shown)
    elif not lines:         # nothing at all: Hub says so itself
        hub_says = 'Nothing in the archive matched that.'
    if hub_says:
        turn.withheld[len(turn.said)] = ARCHIVE_KEPT      # later requests never learn from it whether photos were found
        turn.said.append(hub_says)
    if shown['count']:
        turn.cards.append({'kind': 'photos', 'title': hub_says if hub_says and asked else archive.photo_sentence(None, shown),
                           'count': shown['count'], 'items': [{'id': p['id'], 'kind': p['kind'], 'day': p['day']} for p in shown['items']],
                           'search': turn.text[:300]})
    if lines:
        cards.append({'kind': 'found', 'items': sorted(lines, key=lambda l: l['day'] or '', reverse=True)[:CARD_MOST],
                      'count': len(lines), 'search': turn.text[:300]})
    turn.cards.extend(cards)
    turn.again = bool(lines)        # only what the model may hear about decides a second trip; photos never do
    return told


def read_found(ctx: RunContext[Turn], ref: str) -> dict:
    """Read one result search found, by its ref ("m1", "e2", "d1"): an email or an event in full; of a document or
    a conversation, the passages that hold what was searched for (when the owner allows it). Read-only."""
    from ... import store
    from .. import archive
    turn = ctx.deps
    turn.again = True
    ident = turn.found.get(str(ref).strip())
    if not ident:
        return {'not_done': 'There is no such result. Use a ref that search gave.'}
    try:
        if ident.startswith('m:'):
            message = archive.mail_message(ident)
            if message is None or store.ai_blocked(message['address']):
                return {'not_done': 'That email is from a sender Ask may not read.'}
            turn.strangers = True
            return {'from': message['from'], 'subject': message['subject'], 'date': message['day'],
                    'text': mask(message['text'][:8000]), 'attachments': [a['name'] for a in message['attachments']]}
        if ident.startswith('e:'):
            event = archive.calendar_event(ident)
            turn.strangers = True
            return event or {'not_done': 'That event is gone.'}
        source = 'documents' if ident.startswith('d:') else 'chats'
        if not archive.switches().get(source):
            return {'not_done': LOCKED[source]}
        found = archive.item(ident, turn.login, passages=turn.searched)      # the passages that hold what was asked for
        if found is None:
            return {'not_done': 'That one is gone.'}
        turn.strangers = True
        pieces = found.get('pages_text') or found.get('messages') or []
        text = '\n\n'.join((f'[page {p["page"]}] ' if p.get('page') and source == 'documents' else
                              f'[{p.get("role")}] ' if source == 'chats' else '') + p['text'] for p in pieces)
        return {'title': mask(found['title']), 'date': found.get('day'), 'text': mask(text[:12000]), 'cut': len(text) > 12000}
    except archive.Away as away:
        return {'not_done': str(away)}
    except Exception:  # noqa: BLE001 - a reader that failed is an answer the model can pass on
        return {'not_done': 'Hub couldn’t read that just now.'}


read_found.__name__ = 'read'      # what the model calls it: in this module `read` is the house's reader


ALL = (lights, scene, music, remember, groceries, kitchen, look_up, find, details, arrange, rooms, scene_setup, routine, device, setting,
       integration, dashboard, search, read_found)
