"""How the house is set up, changed for the owner through Ask: where things are and what they are called, rooms,
scenes, automations, scripts, helpers, and devices beyond lights and speakers.

Owner decision (2026-11-15): Ask may change anything in Home Assistant. This file is how that is kept safe.

- **A change is planned before it is made** (plans.py). A planned change says what it is in plain words and holds
  the exact requests that make it and the requests that put it back, read from Home Assistant first.
- **Some plans wait for a tap.** The rule is fixed here, never left to a model: anything that removes something,
  one request that changes more than MANY things, unlocking, opening a door or the garage, disarming, a routine
  or a scene that does any of those (itself, or through another it runs), one Hub can't read, and the whole
  house's own settings. A plan that waits is kept by
  what was asked, and planned again from the house as it is when the tap comes; if it would no longer do what the
  card said, it is not done.
- **Every change is recorded** (`house_setup`) with who asked and from where, and can be put back for a day. A
  scene, automation or script is only put back while it is still as Hub left it.
- **Only through the setup door** (broker.py), which checks every request again.

Names and states here are data, never instructions. What a model is shown of them is in find() and detail():
no tokens, addresses, pictures, or where people are.
"""
import json
import re
import threading
import time
import uuid
from datetime import datetime

from ... import store
from ...home import PACIFIC
from . import broker, control, read

Refused = control.Refused
MANY = 10                       # more things than this in one request waits for a tap
WAIT_SECONDS = 3600             # how long a card waits
UNDO_SECONDS = control.UNDO_SECONDS
WORLD_SECONDS = 10
NAME = 80
FOUND = 30                      # things shown to a model for one search
ATTRIBUTES = 2400               # characters of one thing's attributes shown to a model
MAIN = ('light', 'switch', 'fan', 'cover', 'climate', 'lock', 'media_player', 'vacuum', 'humidifier', 'water_heater', 'valve', 'siren')
ROUTINES = ('automation', 'script', 'scene')
# What guards the house besides the locks and the alarm themselves: a switch, button or helper that belongs to them.
# A switch on the alarm's own integration ("Arm at night", "Arriving home"), a button on the garage opener's device,
# a relay called "Gate". Working one of these is working the lock or the alarm, so it waits for a tap like them.
SECURE = ('lock', 'alarm_control_panel')
DOORS = ('garage', 'door', 'gate')
# Covers that only keep out light or weather: opening one is everyday. Any other cover, one that doesn't say what
# it is among them, may be a way into the house and is treated like the garage.
EVERYDAY_COVERS = ('blind', 'shade', 'curtain', 'awning', 'shutter', 'damper')
GUARD_KINDS = ('switch', 'button', 'input_button', 'input_boolean', 'select', 'input_select', 'number', 'input_number', 'siren', 'text',
               'time', 'date', 'datetime')         # a lock's own settings too: its codes, its auto-lock, its schedule
GUARD_WORDS = {'garage', 'gate', 'lock', 'unlock', 'alarm', 'disarm', 'arm', 'arming', 'opener', 'security'}
NOT_A_GUARD = {'light', 'lamp', 'fan', 'heater', 'plug', 'outlet', 'speaker', 'camera', 'clock'}
# Integrations that hold every kind of device: sharing one with the alarm says nothing about a switch.
SHARED_BY_ALL = {'mqtt', 'template', 'zwave_js', 'zha', 'matter', 'homekit_controller', 'homekit', 'esphome', 'smartthings', 'tuya', 'group',
                 'hue', 'deconz', 'tasmota', 'shelly', 'knx', 'modbus', 'demo', 'homeassistant', 'switch_as_x'}
# Sensors that say a way into the house is open, or that something is wrong: hiding one hides that.
WATCHES = ('door', 'window', 'garage_door', 'lock', 'opening', 'smoke', 'gas', 'carbon_monoxide', 'moisture', 'tamper', 'safety')
# People and where they are, pictures, and what other accounts hold: a model gets the name and nothing more.
NAMES_ONLY = ('person', 'device_tracker', 'zone', 'camera', 'image', 'calendar', 'todo', 'geo_location', 'conversation', 'ai_task')
PRIVATE = re.compile(r'token|password|secret|credential|latitude|longitude|gps|location|picture|access|url|code|ssid|mac_|_mac|'
                     r'ip_address|serial|user_id|address', re.I)
# Words written outside the house (a song's title, an update's notes): a model about to change the house isn't shown them.
OUTSIDE = re.compile(r'^(media_(title|artist|album_name|album_artist|series_title|channel|playlist|content_id)|release_(summary|notes)|title)$')
# A moment as Home Assistant writes it, in UTC ("2026-01-05T19:09:55.123456+00:00"): a scene's state, last_triggered.
STAMP = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?([+-]\d\d:\d\d|Z)$')
# What a card says under its lines, by why it waits.
WHY = {'delete': 'This removes it. Undo can put it back for a day.',
       'many': 'One request, many changes. Undo puts them all back.',
       'unlock': 'A door is only unlocked or opened by your tap.',
       'security': 'It touches a lock, the garage or the alarm, so it waits for your tap.',
       'unread': 'Hub can’t read what this one does, so it waits for your tap.',
       'disable': 'It stops working everywhere in Home Assistant until it is enabled again. Undo can do that for a day.',
       'hide': 'Hub would stop showing it, and would no longer say when it is open. Undo can show it again for a day.',
       'house': 'This changes the whole house’s settings.',
       'restart': 'Home Assistant will be away for about a minute. Lights and switches keep working by hand.'}
SCHEMA = """
create table if not exists house_setup(
  id integer primary key autoincrement, batch text not null, ts real not null, login text, via text not null,
  kind text not null, target text not null, name text not null, words text not null, undo text, after text,
  outcome text not null, why text not null default '', undone integer not null default 0);
create index if not exists house_setup_batch on house_setup(batch);
create table if not exists house_setup_waiting(
  id integer primary key autoincrement, ts real not null, login text, title text not null, lines text not null,
  why text not null, plan text not null, args text not null, status text not null default 'waiting', batch text, steps text);
"""

_lock = threading.Lock()
_cache = {'world': None, 'at': float('-inf')}


class Already(Refused):
    """Nothing to change: the house is already like that. The message says so, and nothing was sent."""


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    if 'steps' not in {r['name'] for r in conn.execute('pragma table_info(house_setup_waiting)')}:
        conn.execute('alter table house_setup_waiting add column steps text')
    return conn


def _clip(text, most=NAME):
    return ' '.join(str(text or '').split())[:most]


# ---- reading: everything Home Assistant has, for finding a thing by what the owner calls it ----

def forget():
    """After a change: the next reading asks Home Assistant again, here and for the rest of Hub."""
    with _lock:
        _cache.update(world=None, at=float('-inf'))
    read.clear_cache()


def world(fresh=False):
    """Rooms, floors, groups of rooms (labels), devices and every entity, as plain dicts. Raises broker.HomeDown."""
    with _lock:
        if not fresh and _cache['world'] and time.monotonic() - _cache['at'] < WORLD_SECONDS:
            return _cache['world']
    found = _world(*broker.read('config/area_registry/list', 'config/device_registry/list', 'config/entity_registry/list',
                                'config/label_registry/list', 'config/floor_registry/list', 'get_states'))
    with _lock:
        _cache.update(world=found, at=time.monotonic())
    return found


def _rows(rows, key):
    return [r for r in rows or () if isinstance(r, dict) and isinstance(r.get(key), str)]


def _world(areas, devices, entities, labels, floors, states):
    rooms = {a['area_id']: {'id': a['area_id'], 'name': _clip(a.get('name')), 'floor': a.get('floor_id'), 'icon': a.get('icon'),
                            'labels': list(a.get('labels') or ()), 'aliases': list(a.get('aliases') or ())} for a in _rows(areas, 'area_id')}
    levels = {f['floor_id']: {'id': f['floor_id'], 'name': _clip(f.get('name')), 'level': f.get('level'), 'icon': f.get('icon'),
                              'aliases': list(f.get('aliases') or ())} for f in _rows(floors, 'floor_id')}
    groups = {g['label_id']: {'id': g['label_id'], 'name': _clip(g.get('name')), 'icon': g.get('icon'), 'color': g.get('color'),
                              'description': g.get('description')} for g in _rows(labels, 'label_id')}
    devs = {d['id']: {'id': d['id'], 'name': _clip(d.get('name_by_user') or d.get('name')), 'own_name': d.get('name_by_user'),
                      'area': d.get('area_id'), 'labels': list(d.get('labels') or ()), 'things': [], 'off': d.get('disabled_by')}
            for d in _rows(devices, 'id')}
    entries = {e['entity_id']: e for e in _rows(entities, 'entity_id')}
    now = {s['entity_id']: s for s in _rows(states, 'entity_id')}
    things = {}
    for entity in sorted({**entries, **now}):
        entry, item = entries.get(entity, {}), now.get(entity, {})
        attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
        device = entry.get('device_id') if entry.get('device_id') in devs else None
        area = entry.get('area_id') or (devs[device]['area'] if device else None)
        if area not in rooms:       # as the rest of Hub reads it: light.kitchen_strip is in the kitchen. A routine is in no room.
            area = None if entity.partition('.')[0] in ROUTINES else \
                max((a for a in rooms if entity.partition('.')[2].startswith(a + '_')), key=len, default=None)
        # Whether it is named after its device ("Floor lamp", or "Floor lamp Signal" for one of its features). Older
        # Home Assistants say so; newer ones don't, and it shows in the name itself.
        follows = entry.get('has_entity_name')
        if follows is None and device:
            follows = not entry.get('name') and (not entry.get('original_name')
                                                 or attrs.get('friendly_name') == f'{devs[device]["name"]} {entry["original_name"]}')
        things[entity] = {
            'id': entity, 'domain': entity.partition('.')[0], 'area': area, 'own_area': entry.get('area_id'), 'device': device,
            'name': _clip(entry.get('name') or attrs.get('friendly_name') or entry.get('original_name')
                          or entity.partition('.')[2].replace('_', ' ')),
            'own_name': entry.get('name'), 'state': str(item.get('state') or 'unknown')[:60], 'attrs': attrs,
            # The device's own main feature takes the device's name: renaming it means renaming the device.
            'is_device': bool(device and follows and not entry.get('name') and not entry.get('original_name')),
            'named_alone': not follows,
            'hidden': bool(entry.get('hidden_by')), 'disabled': bool(entry.get('disabled_by')) or entity not in now,
            'by': entry.get('hidden_by') or entry.get('disabled_by'), 'minor': bool(entry.get('entity_category')),
            'off': entry.get('disabled_by'), 'icon': entry.get('icon'), 'platform': entry.get('platform'),
            'labels': list(entry.get('labels') or ()), 'class': attrs.get('device_class') or entry.get('original_device_class'),
            'config': attrs.get('id') if isinstance(attrs.get('id'), str) else None}
        if device:
            devs[device]['things'].append(entity)
    way_in = lambda t: t['domain'] in SECURE or (t['domain'] == 'cover' and t['class'] not in EVERYDAY_COVERS)      # noqa: E731
    beside = {t['device'] for t in things.values() if t['device'] and way_in(t)}
    alarms = {t['platform'] for t in things.values() if t['domain'] == 'alarm_control_panel' and t['platform']} - SHARED_BY_ALL
    for t in things.values():
        named = words_of(t['name'])
        t['guards'] = t['domain'] in GUARD_KINDS and bool(t['device'] in beside or t['platform'] in alarms
                                                         or (named & GUARD_WORDS and not named & NOT_A_GUARD))
        t['watches'] = way_in(t) or t['guards'] or (t['domain'] == 'binary_sensor' and t['class'] in WATCHES)
    return {'rooms': rooms, 'floors': levels, 'groups': groups, 'devices': devs, 'things': things}


def room_name(area, w):
    return w['rooms'][area]['name'] if area in w['rooms'] else ''


def called(thing, w):
    """A thing's name for a sentence. Where several things of its kind share the name (five rooms have a
    "Ceiling"), its room is said with it."""
    same = [t for t in w['things'].values() if t['domain'] == thing['domain'] and t['name'] == thing['name'] and not t['minor']]
    return f'{thing["name"]} ({room_name(thing["area"], w)})' if len(same) > 1 and thing['area'] else thing['name']


# ---- finding ----

FILLER = {'the', 'a', 'an', 'in', 'on', 'of', 'at', 'my', 'our', 'all', 'every', 'room'}
SAID_AS = {'light': {'light', 'lamp'}, 'fan': {'fan'}, 'switch': {'switch', 'plug', 'outlet'}, 'lock': {'lock'},
           'climate': {'thermostat', 'heating', 'heat', 'climate'}, 'media_player': {'speaker'}, 'vacuum': {'vacuum'},
           'automation': {'automation'}, 'scene': {'scene'}, 'script': {'script', 'routine'}, 'alarm_control_panel': {'alarm'},
           'cover': {'cover'}}


def _plainly(text):
    return ' '.join(str(text or '').lower().replace('’', "'").split()).removeprefix('the ')


def _one(word):
    """A word without its plural: "lights" and "light" are the same word here."""
    return word[:-1] if len(word) > 3 and word.endswith('s') and not word.endswith('ss') else word


def words_of(text):
    return {_one(w) for w in re.sub(r"[^a-z0-9']+", ' ', _plainly(text)).replace("'", '').split()}


def _answers_to(thing, w):
    """The exact things a person might call it: its name, and its name with its room."""
    name, room = _plainly(thing['name']), _plainly(room_name(thing['area'], w))
    names = {name, thing['id']}
    if room:
        names |= {f'{room} {name}', f'{name} in {room}', f'{name} in the {room}'}
    return names


def _kind_said(thing):
    """A thing's kind as a person says it: a light, a fan, a speaker."""
    said = {'media_player': 'speaker', 'climate': 'thermostat', 'alarm_control_panel': 'alarm', 'binary_sensor': 'sensor'}
    return said.get(thing['domain'], thing['domain'].replace('input_', '').replace('_', ' '))


def which(things, w):
    """The refusal when a name fits several things: their names with their rooms, for the model to ask with. Two
    that would read the same (a fan and its own light, both "Guest fan") are told apart by what they are."""
    listed = [f'{t["name"]} ({room_name(t["area"], w)})' if t['area'] else t['name'] for t in things[:8]]
    listed = [f'{name}, the {_kind_said(t)}' if listed.count(name) > 1 else name for name, t in zip(listed, things)]
    return Refused('Which one? ' + ', '.join(listed) + ('…' if len(things) > 8 else ''))


def _one_device(found, asked):
    """Several things that are one device under one name (a fan and its light): the one the words mean, else its
    main one. None when they are different devices."""
    if len({t['device'] for t in found}) != 1 or not found[0]['device'] or not all(t['is_device'] for t in found):
        return None
    said = [t for t in found if asked & SAID_AS.get(t['domain'], set())] or found
    return min(said, key=lambda t: MAIN.index(t['domain']) if t['domain'] in MAIN else len(MAIN))


def match(said, w, kinds=None):
    """The things the owner named, as world()'s dicts. One thing by its id, its name, or its name with its room
    ("kitchen ceiling", "the fan in the office", "thermostat"); or every thing of a kind in a room ("kitchen
    lights"). A name that fits several things is refused with their names, never guessed. Raises Refused."""
    raw, text = str(said or '').strip(), _plainly(said)
    if raw in w['things'] and (not kinds or w['things'][raw]['domain'] in kinds):
        return [w['things'][raw]]
    asked = words_of(text) - FILLER
    if not asked:
        raise Refused('Say which thing.')
    pool = [t for t in w['things'].values() if not kinds or t['domain'] in kinds]
    for group in ([t for t in pool if not (t['minor'] or t['hidden'] or t['disabled'])], pool):
        exact = [t for t in group if text in _answers_to(t, w)]
        if exact:
            if len(exact) > 1 and not _one_device(exact, asked):
                raise which(exact, w)
            return [_one_device(exact, asked)] if len(exact) > 1 else exact
        fits = []
        for t in group:
            own, room = words_of(t['name']), words_of(room_name(t['area'], w)) - FILLER
            if asked <= own | room | SAID_AS.get(t['domain'], set()) and asked & own:
                fits.append((len(own - asked), t))
        if fits:
            least = min(extra for extra, _ in fits)
            best = [t for extra, t in fits if extra == least]
            if len(best) > 1 and not _one_device(best, asked):
                raise which(best, w)
            return [_one_device(best, asked)] if len(best) > 1 else best
        # No name in it: a room and a kind ("kitchen lights"), or with `kinds` given just the room.
        together = [t for t in group if t['area'] and asked & (words_of(room_name(t['area'], w)) - FILLER)
                    and asked <= (words_of(room_name(t['area'], w)) - FILLER) | SAID_AS.get(t['domain'], set())
                    and (kinds or asked & SAID_AS.get(t['domain'], set()))]
        if together:
            return together
    raise Refused(f'Hub found nothing called “{_clip(said, 60)}” in Home Assistant.')


def room(said, w):
    """The room (area) the owner named, by its id, name or other names; else by all the words said. Raises Refused."""
    text = _plainly(said)
    if str(said or '').strip() in w['rooms']:
        return w['rooms'][str(said).strip()]
    exact = [r for r in w['rooms'].values() if text in (_plainly(r['name']), *(_plainly(a) for a in r['aliases']))]
    asked = words_of(text) - FILLER
    loose = exact or ([r for r in w['rooms'].values() if asked and asked <= words_of(r['name'])] if asked else [])
    if len(loose) == 1:
        return loose[0]
    rooms = ', '.join(sorted(r['name'] for r in (loose or w['rooms'].values()))[:24])
    raise Refused(f'Which room? {rooms}' if loose else f'There is no room called “{_clip(said, 60)}”. The rooms are: {rooms}.')


def _local(value):
    """A moment as the household's clock says it ("Jan 5, 11:09 AM"), so a model never reads out UTC; anything that
    isn't one, as it is."""
    if isinstance(value, str) and STAMP.match(value):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(PACIFIC).strftime('%b %-d, %-I:%M %p')
        except ValueError:
            pass
    return value


def _shown(value, most=160):
    """One attribute as a model may see it: short, and plain data."""
    if isinstance(value, str):
        return _local(value)[:most]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_shown(v, 60) for v in list(value)[:16]]
    return str(value)[:most]


def _attributes(thing):
    """A thing's attributes for a model: nothing that names a token, an address, a picture or a place, and not much."""
    out, size = {}, 0
    for key, value in sorted(thing['attrs'].items(), key=lambda kv: len(str(kv[1]))):
        if PRIVATE.search(key) or OUTSIDE.match(key) or key in ('friendly_name', 'icon', 'id'):
            continue
        shown = _shown(value)
        size += len(key) + len(json.dumps(shown, default=str))
        if size > ATTRIBUTES:
            break
        out[key] = shown
    return out


def _brief(thing, w):
    out = {'id': thing['id'], 'name': thing['name'], 'kind': thing['domain']}
    if thing['area']:
        out['room'] = room_name(thing['area'], w)
    if thing['domain'] not in NAMES_ONLY:
        out['state'] = 'off in Home Assistant' if thing['disabled'] else _local(thing['state'])
    if thing['hidden']:
        out['hidden'] = True
    return out


def find(said=None, kind=None):
    """What a model is shown when it looks for things: at most FOUND, each with its id, name, kind, room and state.
    `kind`: a Home Assistant domain ("light", "automation", "climate"), or "room", "floor" or "group"."""
    w = world()
    asked = words_of(said) - FILLER if said else set()
    if kind == 'dashboard':
        hits = [r for r in dashboards() if asked <= words_of(r['name'])]
        return {'found': [{'name': r['name'], 'kind': 'dashboard', **({'views': r['views']} if r['views'] is not None else {'made_by': 'Home Assistant itself'})}
                          for r in hits[:30]]}
    if kind == 'integration':
        hits = [r for r in integrations() if asked <= words_of(r['name']) | words_of(r['kind'])]
        return {'found': [{'name': r['name'], 'kind': 'integration', 'of': r['kind'], 'state': r['state']} for r in hits[:60]]}
    if kind in ('room', 'floor', 'group'):
        rows = {'room': w['rooms'], 'floor': w['floors'], 'group': w['groups']}[kind].values()
        hits = [r for r in rows if asked <= words_of(r['name'])]
        return {'found': [dict({'name': r['name'], 'kind': kind, 'id': r['id']},
                               **({'floor': w['floors'][r['floor']]['name']} if kind == 'room' and r.get('floor') in w['floors'] else {}),
                               **({'groups': [w['groups'][g]['name'] for g in r['labels'] if g in w['groups']]}
                                  if kind == 'room' and r.get('labels') else {})) for r in sorted(hits, key=lambda r: r['name'].lower())][:60]}
    pool = [t for t in w['things'].values() if (not kind or t['domain'] == kind) and (kind or not t['minor'] or asked)]
    hits = []
    for t in pool:
        own, where = words_of(t['name']) | words_of(t['id'].replace('.', ' ').replace('_', ' ')), words_of(room_name(t['area'], w))
        if asked <= own | where | SAID_AS.get(t['domain'], set()):
            hits.append((not asked & own, t['minor'], t['hidden'] or t['disabled'], t['name'].lower(), t))
    hits.sort(key=lambda h: h[:4])
    return {'found': [_brief(h[4], w) for h in hits[:60 if kind and not asked else FOUND]],
            **({'more': len(hits) - FOUND} if len(hits) > FOUND and (asked or not kind) else {})}


def board(path):
    """What one dashboard shows, as Home Assistant keeps it, or None when it keeps nothing for it (a dashboard Home
    Assistant draws by itself). Raises broker.HomeDown."""
    try:
        (found,) = broker.setup_read({'type': 'lovelace/config', 'url_path': path})
    except broker.Rejected:
        return None
    return found if isinstance(found, dict) else None


def dashboards():
    """The dashboards a person made in Home Assistant: [{'id', 'path', 'name', 'icon', 'sidebar', 'admin', 'views'}].
    `views` is the titles of its views, or None for one Home Assistant draws by itself or keeps in its files."""
    (rows,) = broker.setup_read({'type': 'lovelace/dashboards/list'})
    out = []
    for r in rows or ():
        if not isinstance(r, dict) or not isinstance(r.get('id'), str) or not isinstance(r.get('url_path'), str):
            continue
        kept = board(r['url_path']) if r.get('mode') == 'storage' and broker.DASHBOARD.fullmatch(r['url_path']) else None
        views = kept.get('views') if isinstance(kept, dict) and isinstance(kept.get('views'), list) else None
        out.append({'id': r['id'], 'path': r['url_path'], 'name': _clip(r.get('title')) or r['url_path'], 'icon': r.get('icon'),
                    'sidebar': r.get('show_in_sidebar') is not False, 'admin': bool(r.get('require_admin')), 'config': kept if views is not None else None,
                    'views': [_clip(v.get('title')) or f'View {n}' for n, v in enumerate(views, 1) if isinstance(v, dict)] if views is not None else None})
    return sorted(out, key=lambda r: r['name'].lower())


MAIL = re.compile(r'\S+@\S+')


def integrations():
    """The integrations Home Assistant has set up, for finding one to reload: [{'id', 'name', 'kind', 'state'}]. A
    title that holds an account's address is shown without it. Raises broker.HomeDown."""
    (rows,) = broker.setup_read({'type': 'config_entries/get'})
    out = []
    for r in rows or ():
        if not isinstance(r, dict) or not isinstance(r.get('entry_id'), str) or not isinstance(r.get('domain'), str):
            continue
        kind = r['domain'].replace('_', ' ')
        title = _clip(MAIL.sub('', str(r.get('title') or '')).strip(' ()-:')) or kind
        state = 'off' if r.get('disabled_by') else {'loaded': 'working', 'setup_error': 'not working', 'setup_retry': 'trying to connect',
                                                      'not_loaded': 'not started', 'failed_unload': 'stuck'}.get(str(r.get('state')), 'unknown')
        out.append({'id': r['entry_id'], 'name': title, 'kind': kind, 'state': state, 'reloads': bool(r.get('supports_unload', True))})
    return sorted(out, key=lambda r: (r['kind'], r['name'].lower()))


def uses(thing):
    """The automations, scripts and scenes that refer to this thing, by name, for a card that removes it. Fails soft."""
    kind = thing['domain'] if thing['domain'] in ROUTINES else 'entity'
    try:
        (found,) = broker.setup_read({'type': 'search/related', 'item_type': kind, 'item_id': thing['id']})
    except (broker.HomeDown, ValueError):
        return []
    w = world()
    ids = [e for k in ROUTINES for e in (found or {}).get(k) or () if isinstance(e, str) and e != thing['id']]
    return [w['things'][e]['name'] if e in w['things'] else e for e in ids][:8]


def detail(said):
    """One thing in full, for a model about to change it: a device with its state and settings, a routine with
    what it does, a room with what is in it. Raises Refused."""
    w = world()
    text = str(said or '').strip()
    place = next((r for r in w['rooms'].values() if _plainly(text) in (r['id'], _plainly(r['name']))), None)
    if place and text not in w['things']:
        inside = [t for t in w['things'].values() if t['area'] == place['id'] and not t['minor'] and not t['hidden'] and not t['disabled']]
        return {'room': place['name'], 'id': place['id'],
                'floor': w['floors'][place['floor']]['name'] if place['floor'] in w['floors'] else None,
                'groups': [w['groups'][g]['name'] for g in place['labels'] if g in w['groups']],
                'things': [_brief(t, w) for t in sorted(inside, key=lambda t: (t['domain'], t['name'].lower()))[:60]]}
    (thing,) = match(text, w)[:1]
    out = _brief(thing, w)
    if thing['domain'] in NAMES_ONLY:
        return out
    out['attributes'] = _attributes(thing)
    if thing['device']:
        device = w['devices'][thing['device']]
        out['device'] = {'name': device['name'], 'also': [w['things'][e]['id'] for e in device['things'] if e != thing['id']][:20]}
    if thing['domain'] in ROUTINES:
        key = thing['id'].partition('.')[2] if thing['domain'] == 'script' else thing['config']
        config = broker.setup_config(thing['domain'], key) if key else None
        out['config' if config else 'note'] = config or 'Written by hand in Home Assistant’s files: it can be run, but not read or changed from here.'
    used = uses(thing)
    if used:
        out['used_by'] = used
    return out


# ---- a planned change ----

def change(kind, target, name, words, steps, undo=None, asks=None, after=None):
    """One planned change. `steps` and `undo` are requests for the setup door, as plain data:
      {'ws': {...command...}, 'keep': ['room', 'area_id']}    keep: remember a field of the answer as $room
      {'config': 'scene', 'id': '...', 'body': {...} or None} None removes it
      {'call': 'climate.set_temperature', 'data': {...}}
      {'reload': '<an integration's entry id>'}
      {'board': {...a dashboard command...}, 'keep': ['board', 'id']}
    `asks`: why it must wait for a tap (a key of WHY), or None. `after`: {'config', 'id', 'body'} as written, so
    Undo can tell whether it is still as Hub left it."""
    return {'kind': kind, 'target': str(target)[:200], 'name': _clip(name), 'words': _clip(words, 300), 'steps': steps,
            'undo': undo, 'asks': asks, 'after': after}


def _filled(value, kept, later=()):
    """A request with each "$name" replaced by what an earlier answer gave. `later`: names a step of the same list
    will give when it runs, left as they are; any other name with nothing behind it is a KeyError."""
    if isinstance(value, str) and value.startswith('$'):
        if value[1:] in kept:
            return kept[value[1:]]
        if value[1:] in later:
            return value
        raise KeyError(value)
    if isinstance(value, list):
        return [_filled(v, kept, later) for v in value]
    if isinstance(value, dict):
        return {k: v if k == 'keep' else _filled(v, kept, later) for k, v in value.items()}
    return value


def _send(step, kept):
    sent = _filled({k: v for k, v in step.items() if k != 'keep'}, kept)
    if 'ws' in sent:
        answer = broker.setup_write(sent['ws'])
    elif 'reload' in sent:
        answer = broker.setup_reload(sent['reload'])
    elif 'board' in sent:
        answer = broker.setup_dashboard(sent['board'])
    elif 'config' in sent:
        answer = broker.setup_save(sent['config'], sent['id'], sent.get('body'))
    else:
        domain, _, service = sent['call'].partition('.')
        answer = broker.setup_call(domain, service, sent.get('data') or {})
    if step.get('keep') and isinstance(answer, dict) and isinstance(answer.get(step['keep'][1]), str):
        kept[step['keep'][0]] = answer[step['keep'][1]]


def _run(steps, kept):
    """('done' | 'not_sent' | 'unknown', why). 'not_sent': certainly nothing changed, and why says Home Assistant's
    own short reason when it refused."""
    for n, step in enumerate(steps):
        try:
            _send(step, kept)
        except broker.Rejected as error:
            return ('not_sent' if n == 0 else 'unknown'), error.code
        except broker.NotSent:
            return ('not_sent' if n == 0 else 'unknown'), ''
        except broker.HomeDown:
            return 'unknown', ''
        except (ValueError, KeyError):
            return ('not_sent' if n == 0 else 'unknown'), 'not allowed'
    return 'done', ''


def _report(batch, rows):
    return {'batch': batch, 'done': [r['words'] for r in rows if r['outcome'] == 'done'],
            'unknown': [r['words'] for r in rows if r['outcome'] == 'unknown'],
            'not_sent': [r['words'] for r in rows if r['outcome'] == 'not_sent'],
            'why': next((r['why'] for r in rows if r.get('why')), ''), 'silent': [],
            'undo': any(r['undoable'] and r['outcome'] != 'not_sent' for r in rows)}


def asks(changes):
    """Why these changes must wait for a tap, or None. The whole rule."""
    return next((c['asks'] for c in changes if c['asks']), None) or ('many' if len(changes) > MANY else None)


def carry_out(changes, *, login, via):
    """Make the planned changes now, record each, and report as control.change() does."""
    if not changes:
        raise Refused('Nothing to change.')
    control._spend(len(changes), taps=via == 'app')
    batch, now, rows, kept = 's' + uuid.uuid4().hex[:11], time.time(), [], {}
    for c in changes:
        outcome, why = _run(c['steps'], kept)
        try:
            undo = json.dumps(_filled(c['undo'], kept, {s['keep'][0] for s in c['undo'] if s.get('keep')})) \
                if c['undo'] and outcome != 'not_sent' else None
        except KeyError:
            undo = None             # the answer that named the new thing never came: nothing to point Undo at
        rows.append({'words': c['words'], 'outcome': outcome, 'why': why, 'undoable': undo is not None})
        with _db() as conn:
            conn.execute('insert into house_setup(batch, ts, login, via, kind, target, name, words, undo, after, outcome, why) '
                         'values (?,?,?,?,?,?,?,?,?,?,?,?)', (batch, now, login, via, c['kind'], c['target'], c['name'], c['words'], undo,
                                                              json.dumps(c['after']) if c['after'] else None, outcome, why))
    forget()
    return _report(batch, rows)


# ---- plans that wait for a tap ----

def fingerprint(changes):
    """The exact requests a plan would send, so that a plan made again at the tap is done only if it is the very one
    the card showed: the same words can say something else once rooms are renamed. A new routine's or scene's id
    comes from the clock, so it is left out."""
    fresh = {s['id'] for c in changes if c['target'] == 'new' for s in c['steps'] if isinstance(s, dict) and 'config' in s}

    def plain(value):
        if isinstance(value, str):
            return '$new' if value in fresh else value
        if isinstance(value, list):
            return [plain(v) for v in value]
        return {k: plain(v) for k, v in value.items()} if isinstance(value, dict) else value

    return json.dumps([plain(c['steps']) for c in changes], sort_keys=True)


def wait(title, changes, plan, args, *, login):
    """Keep a plan for the owner's tap; returns the card. Nothing is sent to Home Assistant."""
    why = asks(changes) or 'many'
    lines = [c['words'] for c in changes][:24]
    with _db() as conn:
        conn.execute("update house_setup_waiting set status = 'replaced' where status = 'waiting' and login is ?", (login,))
        number = conn.execute('insert into house_setup_waiting(ts, login, title, lines, why, plan, args, steps) values (?,?,?,?,?,?,?,?)',
                              (time.time(), login, _clip(title, 160), json.dumps(lines), why, plan, json.dumps(args),
                               fingerprint(changes))).lastrowid
    return {'kind': 'confirm', 'id': number, 'title': _clip(title, 160), 'lines': lines, 'why': WHY[why],
            'do': {'delete': 'Remove', 'disable': 'Disable', 'restart': 'Restart'}.get(why, 'Do it')}


def waiting(number):
    """One card's state for the app: 'waiting', 'done', 'dismissed', 'replaced' or 'expired', with its batch once done."""
    with _db() as conn:
        conn.execute("update house_setup_waiting set status = 'expired' where status = 'waiting' and ts < ?", (time.time() - WAIT_SECONDS,))
        row = conn.execute('select * from house_setup_waiting where id = ?', (int(number),)).fetchone()
    return dict(row) if row else None


def settle(number, status, batch=None):
    with _db() as conn:
        return conn.execute("update house_setup_waiting set status = ?, batch = ? where id = ? and status = 'waiting'",
                            (status, batch, int(number))).rowcount == 1


def confirm(number, *, login):
    """The owner's tap on a card: plan it again from the house as it is now and, if it still does what the card
    said, do it. Returns the report. Raises Refused."""
    from . import plans
    row = waiting(number)
    if row is None or row['status'] != 'waiting':
        raise Refused({'expired': 'That card waited more than an hour. Ask again.', 'done': 'That is already done.'}.get(
            row and row['status'], 'That card isn’t waiting any more. Ask again.'))
    todo = None
    try:
        if row['plan'] == 'undo':           # a card from Undo: what to put back, as Undo would have
            todo, changes = _putting_back(json.loads(row['args'])['batch'])
        else:
            changes = plans.PLANS[row['plan']](world(fresh=True), **json.loads(row['args']))
    except Already as error:
        settle(number, 'done')
        raise Refused(str(error)) from None
    except broker.HomeDown as error:
        raise Refused(str(error)) from None
    if [c['words'] for c in changes][:24] != json.loads(row['lines']) or fingerprint(changes) != row['steps']:
        settle(number, 'replaced')
        raise Refused('The house has changed since that card was made, so nothing was done. Ask again.')
    if not settle(number, 'working'):
        raise Refused('That card isn’t waiting any more. Ask again.')
    try:
        result = _put_back(todo, changes, login=login, via='app') if row['plan'] == 'undo' else carry_out(changes, login=login, via='app')
    except Refused:                 # nothing was sent (too many changes this minute): the card goes on waiting
        with _db() as conn:
            conn.execute("update house_setup_waiting set status = 'waiting' where id = ? and status = 'working'", (int(number),))
        raise
    with _db() as conn:
        conn.execute("update house_setup_waiting set status = 'done', batch = ? where id = ?", (result['batch'], int(number)))
    return result


# ---- the record, and putting things back ----

def _batch(batch):
    with _db() as conn:
        return [dict(r) for r in conn.execute('select * from house_setup where batch = ? order by id', (str(batch or '')[:12],))]


def _undoable(r, now=None):
    return bool(r['undo']) and not r['undone'] and r['via'] != 'undo' and r['outcome'] != 'not_sent' \
        and (now or time.time()) - r['ts'] < UNDO_SECONDS


def summary(batch):
    rows = _batch(batch)
    if not rows:
        return None
    found = _report(rows[0]['batch'], [dict(r, undoable=_undoable(r)) for r in rows])
    found['undone'] = all(r['undone'] for r in rows)
    return found


def _same(now, then):
    """Whether a scene, automation or script is still as Hub wrote it. Its id is Home Assistant's to add or leave out."""
    plain = lambda body: json.dumps({k: v for k, v in (body or {}).items() if k != 'id'}, sort_keys=True)   # noqa: E731
    return now is not None and plain(now) == plain(then)


def _putting_back(batch):
    """(the records of a batch still to put back, newest first; the changes that put them back, planned like any
    other, each with why it would wait for a tap). Raises Refused."""
    from . import plans
    rows = [r for r in _batch(batch) if not r['undone'] and r['via'] != 'undo' and r['outcome'] != 'not_sent']
    if not rows:
        raise Refused('There is nothing left to undo there.')
    if time.time() - rows[0]['ts'] > UNDO_SECONDS:
        raise Refused('That change is more than a day old. Change it back in Home Assistant.')
    todo = [r for r in reversed(rows) if r['undo']]
    if not todo:
        raise Refused('That can’t be undone from Hub.')
    for r in todo:
        after = json.loads(r['after']) if r['after'] else None
        try:
            if after and not _same(board(after['id']) if after['config'] == 'dashboard' else broker.setup_config(after['config'], after['id']), after['body']):
                raise Refused(f'{r["name"]} was changed again after Hub changed it, so Hub left it alone. Change it in Home Assistant.')
        except broker.HomeDown as error:
            raise Refused(str(error)) from None
    w = world(fresh=True)
    changes = []
    for r in todo:
        steps = json.loads(r['undo'])
        call = steps[0]['call'] if len(steps) == 1 and 'call' in steps[0] else None
        words = plans._did(*call.partition('.')[::2], r['name'], steps[0].get('data') or {}) if call else f'{r["name"]} put back'
        changes.append(change(r['kind'], r['target'], r['name'], words, steps, asks=plans.back_asks(steps, w)))
    return todo, changes


def undo(batch, *, login, via):
    """Put a batch back, newest change first. Returns the report. Undo is never a way round the tap: putting back
    what would unlock, open or disarm, or bring back something that does, returns {'card': ...} instead, the card
    that waits for the owner's tap like any other, and nothing is sent. Raises Refused."""
    todo, changes = _putting_back(batch)
    why = next((c['asks'] for c in changes if c['asks']), None)
    if why:
        name = changes[0]['name'] if len(changes) == 1 else f'these {len(changes)}'
        return {'card': wait(f'Put back {name}?', changes, 'undo', {'batch': str(batch)[:12]}, login=login)}
    return _put_back(todo, changes, login=login, via=via)


def _put_back(todo, changes, *, login, via):
    control._spend(len(todo), taps=via == 'app')
    new, now, out, kept = 's' + uuid.uuid4().hex[:11], time.time(), [], {}
    for r, c in zip(todo, changes):
        outcome, why = _run(c['steps'], kept)
        words = f'{r["name"]} put back'
        out.append({'words': words, 'outcome': outcome, 'why': why, 'undoable': False})
        with _db() as conn:
            conn.execute('insert into house_setup(batch, ts, login, via, kind, target, name, words, outcome, why) values (?,?,?,?,?,?,?,?,?,?)',
                         (new, now, login, 'undo', r['kind'], r['target'], r['name'], words, outcome, why))
            if outcome != 'not_sent':
                conn.execute('update house_setup set undone = 1 where id = ?', (r['id'],))
    forget()
    return _report(new, out)


def recent(limit=30):
    """The latest setup changes, newest first, one entry per batch, as control.recent() gives house changes."""
    with _db() as conn:
        rows = conn.execute('select batch, min(ts) as ts, login, via, max(id) as last, group_concat(distinct kind) as kinds '
                            'from house_setup group by batch order by last desc limit ?', (limit,)).fetchall()
    return [{**summary(r['batch']), 'ts': r['ts'], 'login': r['login'], 'via': r['via'], 'kinds': (r['kinds'] or '').split(',')}
            for r in rows]


def touched(since):
    """(entity, when) for each device or routine a setup change reached since then, so Activity shows Hub's own
    line for it and not Home Assistant's as well."""
    with _db() as conn:
        return [(r['target'], r['ts']) for r in conn.execute(
            "select target, ts from house_setup where ts >= ? and outcome != 'not_sent' and target like '%.%'", (since,))]
