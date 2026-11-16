"""What Ask can do: lights, scenes, music, remembering, passing a message to Kitchen, reading mail, the calendar
and school, and searching the family archive.

Few tools, each doing a whole job. Every change goes through hub/tools/house/control.py, like a tap in the app:
the same allowlist, record and Undo, counted against Ask's own per-minute limit. A tool says what it did in a
plain sentence (`turn.said`), so a request that was simply carried out needs no second trip to the model; a tool
that needs the model again (nothing matched, a name it couldn't place) sets `turn.again`.
"""
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic_ai import RunContext, Tool

from ...tools.house import broker, control, music as library, nowplaying, read, zones
from .. import levels, model
from .. import scenes as kept_scenes
from ..words import listed as _listed
from ..words import sentence, spoken
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
    strangers: bool = False                      # words written by others (mail, the calendar, the archive) are in view
    found: dict = field(default_factory=dict)    # what search found, by the short names the model reads them by
    withheld: dict = field(default_factory=dict)  # what is kept of a sentence the model must never see (photos)

    def kept(self):
        """What Hub said, as it is kept for later requests: a sentence about photos only says photos were shown."""
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


def _may_change(turn):
    """Ask changes the house only for someone signed in, and never while the owner has Home control set to
    "Suggest only" (the same setting that held the older chat back)."""
    from ...engine import catalog, state
    if turn.login is None:
        raise NotNow('Change the house from your phone or laptop.')
    if turn.strangers:      # what strangers wrote must never be what moved the house
        raise NotNow('Hub doesn’t change the house in a request that read mail, the calendar or the archive. Ask for it on its own.')
    if state.trust(catalog.action('house.control')) == 'suggest':
        raise NotNow('Home control is set to “Suggest only” on the Engine page, so Hub changed nothing.')


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
        theirs = next((s for s in found['scenes'] if s['name'].lower() == said), None)
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


def kitchen(ctx: RunContext[Turn]) -> dict:
    """Pass the owner's message, word for word, to Kitchen, which answers about cooking, recipes, meal ideas,
    groceries, the shopping list, food on hand and the shop's cart. Call it once and say nothing else."""
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


# ---- the family archive (hub/app/archive.py) ----

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
    if shown['count'] or asked:
        # Hub speaks for photos when they are what was asked for, or all that was found; else they wait on the card
        lead = asked or not lines
        about = (archive.understand(turn.text)['about'] if len(turn.text) <= 80 else '') or (phrases[0] if phrases else 'Photos')
        sentence = archive.photo_sentence(about if lead else 'Photos', shown)
        if lead:
            turn.withheld[len(turn.said)] = 'Hub showed photos.'
            turn.said.append(sentence)
        turn.cards.append({'kind': 'photos', 'title': sentence, 'count': shown['count'],
                           'items': [{'id': p['id'], 'kind': p['kind'], 'day': p['day']} for p in shown['items']],
                           'search': turn.text[:300]})
    if lines:
        cards.append({'kind': 'found', 'items': sorted(lines, key=lambda l: l['day'] or '', reverse=True)[:CARD_MOST],
                      'count': len(lines), 'search': turn.text[:300]})
    turn.cards.extend(cards)
    turn.again = bool(lines) or not shown['count'] or 'archive' in told   # the model answers what it can read, or says nothing matched
    return told


def read_found(ctx: RunContext[Turn], ref: str) -> dict:
    """Read one result search found, in full, by its ref ("m1", "e2", "d1"). Mail and calendar always; documents and
    ChatGPT history only when the owner allows it. Read-only."""
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
        found = archive.item(ident, turn.login)
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


ALL = (lights, scene, music, remember, kitchen, look_up, search, Tool(read_found, name='read'))      # `read` is the house's reader here
