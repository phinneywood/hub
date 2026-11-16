"""What each kind of setup change is, as requests for Home Assistant and the requests that put it back.

Every function here takes the house as admin.world() read it and what was asked, and returns planned changes
(admin.change). Nothing is sent from this file. A plan that removes something, unlocks or opens a door, or
touches the whole house says so (`asks`), and admin.py then keeps it for the owner's tap.

The names a model passes in are looked up here (admin.match, admin.room): a model never chooses an id that Hub
has not found in Home Assistant itself.
"""
import copy
import json
import re
import time

from . import admin, broker, control, read
from .admin import Already, Refused, change

HAND = 'was written by hand in Home Assistant’s files, so it can be run but not changed from here'
SCENE_KINDS = ('light', 'switch', 'fan')
DOORS = admin.DOORS
NO_ROOM = ('none', 'no room', 'nowhere', '')
OPENS = {'lock': ('unlock', 'open'), 'alarm_control_panel': ('alarm_disarm', 'alarm_trigger'),
         'cover': ('open_cover', 'toggle', 'set_cover_position'), 'valve': ('open_valve', 'toggle', 'set_valve_position')}
# The states a scene can hold these in that count as opening, and the device actions that never do.
WIDE_OPEN = {'lock': ('unlocked', 'open', 'opening'), 'alarm_control_panel': ('disarmed', 'triggered'), 'cover': ('open', 'opening'),
             'valve': ('open', 'opening')}
SHUTS = ('lock', 'close', 'close_tilt', 'stop', 'arm_home', 'arm_away', 'arm_night', 'arm_vacation', 'arm_custom_bypass')
DEEP = 4                    # a routine that runs a routine that runs a routine: this far Hub follows, then it asks
# Things whose whole purpose is to set automations off. Working one is looked into: what does it trigger?
SET_OFF = ('input_boolean', 'input_button', 'input_select', 'input_number', 'input_text', 'input_datetime', 'button', 'switch', 'select',
           'number', 'counter', 'timer')
# The kinds of step a routine's actions can hold. A call is read for what it reaches, a scene for what it sets, a
# device's own action for its device; the quiet ones only wait, decide or keep a value, and the containers hold
# steps that are read one by one. Any other kind of step (an event another routine may act on, or one Hub doesn't
# know) is one Hub can't read, so the routine waits for a tap.
CALLS = ('action', 'service', 'service_template')
QUIET = ('delay', 'wait_template', 'wait_for_trigger', 'condition', 'variables', 'stop', 'set_conversation_response')
CONTAINERS = ('choose', 'if', 'repeat', 'parallel', 'sequence')
PLAIN_CALL = re.compile(r'[a-z0-9_]+\.[a-z0-9_]+')
# Services that send what Hub can't read: a code learned from a remote may open the gate as well as change a channel.
OPAQUE = {('remote', 'send_command')}
# Things made from a template run whatever their owner wrote when they are worked; Hub can't see that from here.
TEMPLATED = ('button', 'switch', 'select', 'number', 'light', 'fan', 'lock', 'cover', 'valve', 'vacuum', 'alarm_control_panel', 'siren')
# Integrations whose own scenes only ever set their own lights, though Home Assistant doesn't list what they hold.
LIGHT_SCENES = read.LIGHT_SCENES


def _ws(kind, keep=None, **fields):
    return {'ws': {'type': kind, **fields}, **({'keep': keep} if keep else {})}


def _listed(items, most=4):
    items = [i for i in items if i]
    shown = items[:most] + ([f'{len(items) - most} more'] if len(items) > most else [])
    return shown[0] if len(shown) == 1 else ', '.join(shown[:-1]) + ' and ' + shown[-1] if shown else ''


def _things(names, w, kinds=None):
    """The things named, each once, in the order said."""
    if isinstance(names, str):
        names = [names]
    found = {}
    for name in names or ():
        for thing in admin.match(name, w, kinds):
            found.setdefault(thing['id'], thing)
    if not found:
        raise Refused('Say which thing.')
    return list(found.values())


def _named(rows, said, what):
    """One of `rows` (rooms, floors, groups, helpers) by its name or id. Raises Refused."""
    text, raw = admin._plainly(said), str(said or '').strip()
    exact = [r for r in rows if raw == r['id'] or text == admin._plainly(r['name'])]
    asked = admin.words_of(text) - admin.FILLER
    loose = exact or [r for r in rows if asked and asked <= admin.words_of(r['name'])]
    if len(loose) == 1:
        return loose[0]
    names = ', '.join(sorted(r['name'] for r in (loose or rows))[:20]) or 'none yet'
    raise Refused(f'Which {what}? {names}' if loose else f'There is no {what} called “{admin._clip(said, 60)}”. There are: {names}.')


# ---- where things are and what they are called ----

def _sole(thing, w):
    """Whether a thing is all its device is for: its one main feature. Then the device goes where it goes."""
    device = w['devices'].get(thing['device'])
    if not device:
        return False
    mains = [e for e in device['things'] if w['things'][e]['domain'] in admin.MAIN and not w['things'][e]['minor']]
    return thing['is_device'] or mains == [thing['id']]


def _move(thing, target, w, moved):
    new = target['id'] if target else None
    device = w['devices'].get(thing['device'])
    now = thing['own_area'] or (device['area'] if device else None)
    was, to = admin.room_name(now, w), target['name'] if target else ''
    if now == new:
        raise Already(f'{thing["name"]} is already in {to}.' if to else f'{thing["name"]} is in no room already.')
    words = f'{thing["name"]} moved from {was} to {to}' if was and to else f'{thing["name"]} put in {to}' if to \
        else f'{thing["name"]} taken out of {was}'
    # A routine can work every lock or door in a room ("open the office"), so a way in that changes rooms waits.
    if _sole(thing, w):
        if device['id'] in moved:
            return []
        moved.add(device['id'])
        steps = [_ws('config/device_registry/update', device_id=device['id'], area_id=new)]
        undo = [_ws('config/device_registry/update', device_id=device['id'], area_id=device['area'])]
        if thing['own_area']:       # it had a room of its own: from now on it goes where its device goes
            steps.append(_ws('config/entity_registry/update', entity_id=thing['id'], area_id=None))
            undo.append(_ws('config/entity_registry/update', entity_id=thing['id'], area_id=thing['own_area']))
        guards = any(_keeps_out(w['things'][e], w) for e in device['things'] if e in w['things'])
        return [change('place', thing['id'], thing['name'], words, steps, undo, asks='security' if guards else None)]
    return [change('place', thing['id'], thing['name'], words, [_ws('config/entity_registry/update', entity_id=thing['id'], area_id=new)],
                   [_ws('config/entity_registry/update', entity_id=thing['id'], area_id=thing['own_area'])],
                   asks='security' if _keeps_out(thing, w) else None)]


def _rename(thing, name, w):
    new = admin._clip(name)
    if not new:
        raise Refused('Say the new name.')
    if thing['name'] == new:
        raise Already(f'It is already called {new}.')
    device = w['devices'].get(thing['device'])
    steps, undo = [], []
    if not thing['is_device']:
        steps.append(_ws('config/entity_registry/update', entity_id=thing['id'], name=new))
        undo.append(_ws('config/entity_registry/update', entity_id=thing['id'], name=thing['own_name']))
    if device and (thing['is_device'] or (_sole(thing, w) and thing['named_alone'])):
        steps.append(_ws('config/device_registry/update', device_id=device['id'], name_by_user=new))
        undo.append(_ws('config/device_registry/update', device_id=device['id'], name_by_user=device['own_name']))
    # What guards the house is partly known by its name ("Gate"), so a new name for it waits for a tap.
    return [change('name', thing['id'], new, f'{thing["name"]} renamed {new}', steps, undo, asks='security' if _keeps_out(thing, w) else None)]


def _keeps_out(thing, w):
    """Whether a thing is a way into the house or guards one: a lock, the alarm, a door, the garage or a gate (any
    cover but a blind and its kind), a valve, or a switch, button or helper that belongs to them."""
    return thing['guards'] or thing['domain'] in admin.SECURE or thing['domain'] == 'valve' \
        or (thing['domain'] == 'cover' and _door(thing['id'], w))


def _guarded_room(area, w):
    """Whether a room holds a way into the house or what guards one. A routine can work a room by its id, its
    group or its floor, so moving such a room into a group or onto a floor waits for a tap."""
    return any(t['area'] == area and _keeps_out(t, w) for t in w['things'].values())


def _hide(thing, hidden):
    if thing['hidden'] == bool(hidden):
        raise Already(f'{thing["name"]} is already {"hidden" if hidden else "shown"}.')
    return [change('name', thing['id'], thing['name'], f'{thing["name"]} {"hidden" if hidden else "shown again"}',
                   [_ws('config/entity_registry/update', entity_id=thing['id'], hidden_by='user' if hidden else None)],
                   [_ws('config/entity_registry/update', entity_id=thing['id'], hidden_by='user' if thing['hidden'] else None)],
                   asks='hide' if hidden and thing['watches'] else None)]


def _enable(thing, enabled, w, done):
    """Switch a thing off or back on in Home Assistant itself. A thing that is all its device is for takes the
    device with it. Switching off waits for a tap: until it is enabled again nothing can use it."""
    device = w['devices'].get(thing['device']) if _sole(thing, w) else None
    was = (device['off'] if device else None) or thing['off']
    if bool(was) != bool(enabled):
        raise Already(f'{thing["name"]} is already {"enabled" if enabled else "disabled"}.')
    if device and device['id'] in done:
        return []
    key = ('config/device_registry/update', 'device_id', device['id']) if device else ('config/entity_registry/update', 'entity_id', thing['id'])
    done.add(device['id'] if device else thing['id'])
    step = lambda by: [_ws(key[0], **{key[1]: key[2]}, disabled_by=by)]      # noqa: E731
    if enabled:                     # a routine that was disabled comes back to life: one Hub can't read now, so it asks
        return [change('name', thing['id'], thing['name'], f'{thing["name"]} enabled again; it appears within a minute', step(None), step('user'),
                       asks='unread' if thing['domain'] in ('automation', 'script') else None)]
    used = admin.uses(thing)
    return [change('name', thing['id'], thing['name'], f'{thing["name"]} disabled' + (f'; {_listed(used)} use it' if used else ''),
                   step('user'), step(None), asks='disable')]


def _icon(thing, icon):
    new = str(icon or '').strip().lower()
    new = new if new.startswith('mdi:') else 'mdi:' + new.replace(' ', '-')
    if not broker.ICON.fullmatch(new):
        raise Refused('An icon is one of Home Assistant’s, such as mdi:floor-lamp or mdi:ceiling-fan.')
    if thing['icon'] == new:
        raise Already(f'{thing["name"]} already has that icon.')
    return [change('name', thing['id'], thing['name'], f'{thing["name"]} given the icon {new.removeprefix("mdi:").replace("-", " ")}',
                   [_ws('config/entity_registry/update', entity_id=thing['id'], icon=new)],
                   [_ws('config/entity_registry/update', entity_id=thing['id'], icon=thing['icon'])])]


def arrange(w, things, room=None, name=None, hidden=None, enabled=None, icon=None):
    """Move things to a room, rename one, hide and show them, disable and enable them, or give them an icon."""
    found = _things(things, w)
    if name is not None and len(found) != 1:
        raise Refused('A name is for one thing. Say which.')
    if room is None and name is None and hidden is None and enabled is None and icon is None:
        raise Refused('Say what to change: its room, its name, its icon, or whether it is hidden or disabled.')
    target = None if room is None or admin._plainly(room) in NO_ROOM else admin.room(room, w)
    changes, already, moved, switched = [], [], set(), set()
    for thing in found:
        for plan in ((lambda: _move(thing, target, w, moved)) if room is not None else None,
                     (lambda: _retitle(thing, name, w)) if name is not None else None,
                     (lambda: _hide(thing, hidden)) if hidden is not None else None,
                     (lambda: _enable(thing, enabled, w, switched)) if enabled is not None else None,
                     (lambda: _icon(thing, icon)) if icon is not None else None):
            try:
                changes += plan() if plan else []
            except Already as note:
                already.append(str(note))
    if not changes:
        raise Already(' '.join(already) or 'Nothing to change.')
    return changes


def _retitle(thing, name, w):
    """A new name. A scene, automation or script is renamed where it is kept, so its own page agrees."""
    if thing['domain'] == 'scene':
        return scene(w, 'rename', thing['id'], new_name=name)
    if thing['domain'] in ('automation', 'script'):
        return routine(w, thing['domain'], 'rename', thing['id'], new_name=name)
    return _rename(thing, name, w)


# ---- rooms, floors, and groups of rooms ----

def _room_steps(w, do, name, new_name, floor):
    rooms = list(w['rooms'].values())
    level = _named(list(w['floors'].values()), floor, 'floor') if floor else None
    if do == 'add':
        if any(admin._plainly(r['name']) == admin._plainly(name) for r in rooms):
            raise Already(f'There is already a room called {admin._clip(name)}.')
        fields = {'name': admin._clip(name), **({'floor_id': level['id']} if level else {})}
        return [change('room', 'new', fields['name'], f'Room “{fields["name"]}” added' + (f' on {level["name"]}' if level else ''),
                       [_ws('config/area_registry/create', keep=['room', 'area_id'], **fields)],
                       [_ws('config/area_registry/delete', area_id='$room')])]
    here = admin.room(name, w)
    if do == 'rename':
        new = admin._clip(new_name)
        if not new or new == here['name']:
            raise Refused('Say the new name.') if not new else Already(f'It is already called {new}.')
        return [change('room', here['id'], new, f'Room {here["name"]} renamed {new}',
                       [_ws('config/area_registry/update', area_id=here['id'], name=new)],
                       [_ws('config/area_registry/update', area_id=here['id'], name=here['name'])])]
    if do == 'set':
        if level is None:
            raise Refused('Say which floor the room is on.')
        if here['floor'] == level['id']:
            raise Already(f'{here["name"]} is already on {level["name"]}.')
        return [change('room', here['id'], here['name'], f'{here["name"]} moved to {level["name"]}',
                       [_ws('config/area_registry/update', area_id=here['id'], floor_id=level['id'])],
                       [_ws('config/area_registry/update', area_id=here['id'], floor_id=here['floor'])],
                       asks='security' if _guarded_room(here['id'], w) else None)]
    devices = [d['id'] for d in w['devices'].values() if d['area'] == here['id']]
    things = [t['id'] for t in w['things'].values() if t['own_area'] == here['id']]
    count = len({t['id'] for t in w['things'].values() if t['area'] == here['id'] and not t['minor']})
    again = {k: v for k, v in (('name', here['name']), ('floor_id', here['floor']), ('icon', here['icon']),
                                ('labels', here['labels']), ('aliases', here['aliases'])) if v}
    undo = [_ws('config/area_registry/create', keep=['room', 'area_id'], **again),
            *(_ws('config/device_registry/update', device_id=d, area_id='$room') for d in devices),
            *(_ws('config/entity_registry/update', entity_id=e, area_id='$room') for e in things)]
    words = f'Room “{here["name"]}” removed' + (f'; its {count} thing{"s" if count != 1 else ""} are in no room' if count else '')
    return [change('room', here['id'], here['name'], words, [_ws('config/area_registry/delete', area_id=here['id'])], undo, asks='delete')]


def _floor_steps(w, do, name, new_name, level):
    floors = list(w['floors'].values())
    if do == 'add':
        if any(admin._plainly(f['name']) == admin._plainly(name) for f in floors):
            raise Already(f'There is already a floor called {admin._clip(name)}.')
        fields = {'name': admin._clip(name), **({'level': int(level)} if level is not None else {})}
        return [change('room', 'new', fields['name'], f'Floor “{fields["name"]}” added',
                       [_ws('config/floor_registry/create', keep=['floor', 'floor_id'], **fields)],
                       [_ws('config/floor_registry/delete', floor_id='$floor')])]
    here = _named(floors, name, 'floor')
    if do in ('rename', 'set'):
        new = {'name': admin._clip(new_name)} if do == 'rename' else {'level': int(level)} if level is not None else {}
        if not new or (do == 'rename' and not new['name']):
            raise Refused('Say the new name.' if do == 'rename' else 'Say the floor’s level: 0 for the ground floor, 1 above it, -1 below.')
        if all(here.get(k) == v for k, v in new.items()):
            raise Already(f'{here["name"]} is already like that.')
        words = f'Floor {here["name"]} renamed {new["name"]}' if do == 'rename' else f'Floor {here["name"]} set to level {new["level"]}'
        return [change('room', here['id'], new.get('name', here['name']), words,
                       [_ws('config/floor_registry/update', floor_id=here['id'], **new)],
                       [_ws('config/floor_registry/update', floor_id=here['id'], **{k: here[k] for k in new})])]
    on_it = [r['id'] for r in w['rooms'].values() if r['floor'] == here['id']]
    again = {k: v for k, v in (('name', here['name']), ('level', here['level']), ('icon', here['icon']), ('aliases', here['aliases']))
             if v not in (None, [], '')}
    undo = [_ws('config/floor_registry/create', keep=['floor', 'floor_id'], **again),
            *(_ws('config/area_registry/update', area_id=r, floor_id='$floor') for r in on_it)]
    return [change('room', here['id'], here['name'], f'Floor “{here["name"]}” removed; its rooms stay',
                   [_ws('config/floor_registry/delete', floor_id=here['id'])], undo, asks='delete')]


def _group_steps(w, do, name, new_name, rooms):
    groups = list(w['groups'].values())
    wanted = [admin.room(r, w) for r in rooms or ()]
    here = next((g for g in groups if admin._plainly(g['name']) == admin._plainly(name)), None)
    if here and wanted and do in ('add', 'remove'):     # rooms into a group that exists, or out of it
        changes = []
        for r in wanted:
            has = here['id'] in r['labels']
            if has == (do == 'add'):
                continue
            labels = [g for g in r['labels'] if g != here['id']] + ([here['id']] if do == 'add' else [])
            changes.append(change('room', r['id'], r['name'], f'{r["name"]} {"added to" if do == "add" else "taken out of"} {here["name"]}',
                                  [_ws('config/area_registry/update', area_id=r['id'], labels=labels)],
                                  [_ws('config/area_registry/update', area_id=r['id'], labels=r['labels'])],
                                  asks='security' if _guarded_room(r['id'], w) else None))
        if not changes:
            raise Already(f'{_listed([r["name"] for r in wanted])} {"is" if len(wanted) == 1 else "are"} already {"in" if do == "add" else "out of"} {here["name"]}.')
        return changes
    if do == 'add':
        if here:
            raise Already(f'There is already a group called {here["name"]}. Say which rooms to add to it.')
        title = admin._clip(name)
        steps = [_ws('config/label_registry/create', keep=['group', 'label_id'], name=title),
                 *(_ws('config/area_registry/update', area_id=r['id'], labels=[*r['labels'], '$group']) for r in wanted)]
        return [change('room', 'new', title, f'Group “{title}” made' + (f' of {_listed([r["name"] for r in wanted])}' if wanted else ''),
                       steps, [_ws('config/label_registry/delete', label_id='$group')],
                       asks='security' if any(_guarded_room(r['id'], w) for r in wanted) else None)]
    here = _named(groups, name, 'group of rooms')
    if do == 'rename':
        new = admin._clip(new_name)
        if not new or new == here['name']:
            raise Refused('Say the new name.') if not new else Already(f'It is already called {new}.')
        return [change('room', here['id'], new, f'Group {here["name"]} renamed {new}',
                       [_ws('config/label_registry/update', label_id=here['id'], name=new)],
                       [_ws('config/label_registry/update', label_id=here['id'], name=here['name'])])]
    if do == 'set':
        if rooms is None:
            raise Refused('Say which rooms are in the group.')
        ids, changes = {r['id'] for r in wanted}, []
        for r in sorted(w['rooms'].values(), key=lambda r: r['name'].lower()):
            has = here['id'] in r['labels']
            if has == (r['id'] in ids):
                continue
            labels = [g for g in r['labels'] if g != here['id']] + ([] if has else [here['id']])
            changes.append(change('room', r['id'], r['name'], f'{r["name"]} {"taken out of" if has else "added to"} {here["name"]}',
                                  [_ws('config/area_registry/update', area_id=r['id'], labels=labels)],
                                  [_ws('config/area_registry/update', area_id=r['id'], labels=r['labels'])],
                                  asks='security' if _guarded_room(r['id'], w) else None))
        if not changes:
            raise Already(f'{here["name"]} already has exactly those rooms.')
        return changes
    swap = lambda labels: [g if g != here['id'] else '$group' for g in labels]      # noqa: E731
    again = {k: v for k, v in (('name', here['name']), ('icon', here['icon']), ('color', here['color']),
                                ('description', here['description'])) if v}
    undo = [_ws('config/label_registry/create', keep=['group', 'label_id'], **again),
            *(_ws('config/area_registry/update', area_id=r['id'], labels=swap(r['labels'])) for r in w['rooms'].values() if here['id'] in r['labels']),
            *(_ws('config/device_registry/update', device_id=d['id'], labels=swap(d['labels'])) for d in w['devices'].values() if here['id'] in d['labels']),
            *(_ws('config/entity_registry/update', entity_id=t['id'], labels=swap(t['labels'])) for t in w['things'].values() if here['id'] in t['labels'])]
    return [change('room', here['id'], here['name'], f'Group “{here["name"]}” removed; its rooms stay',
                   [_ws('config/label_registry/delete', label_id=here['id'])], undo, asks='delete')]


def place(w, kind, do, name, new_name=None, floor=None, rooms=None, level=None):
    """Rooms, floors, and groups of rooms (Home Assistant's labels on areas): add, rename, set or remove one."""
    if do not in ('add', 'rename', 'set', 'remove'):
        raise Refused('Say add, rename, set or remove.')
    if not admin._clip(name):
        raise Refused('Say its name.')
    if kind == 'room':
        return _room_steps(w, do, name, new_name, floor)
    if kind == 'floor':
        return _floor_steps(w, do, name, new_name, level)
    if kind == 'group':
        return _group_steps(w, do, name, new_name, rooms)
    raise Refused('That is a room, a floor or a group of rooms.')


# ---- scenes ----

def _routine_named(said, kind, w):
    """The scene, automation or script the owner named, or None when there is none like it."""
    try:
        found = admin.match(said, w, kinds=(kind,))
    except Refused as error:
        if str(error).startswith('Which one?'):
            raise
        return None
    if len(found) > 1:
        raise admin.which(found, w)
    return found[0]


def _config(thing, kind):
    """(the id Home Assistant keeps it under, the config as kept). Raises Refused for one written by hand."""
    key = thing['id'].partition('.')[2] if kind == 'script' else thing['config']
    before = broker.setup_config(kind, key) if key else None
    if before is None:
        raise Refused(f'{thing["name"]} {HAND}.')
    return key, before


def _as_now(thing):
    """A light, plug or fan as it is right now, the way a scene keeps it."""
    attrs = thing['attrs']
    if thing['state'] != 'on':
        return {'state': 'off'}
    kept = {'state': 'on'}
    if thing['domain'] == 'light':
        if isinstance(attrs.get('brightness'), (int, float)):
            kept['brightness'] = int(attrs['brightness'])
        kept.update(read._colour_now(attrs) or {})
    elif thing['domain'] == 'fan' and isinstance(attrs.get('percentage'), (int, float)):
        kept['percentage'] = int(attrs['percentage'])
    return kept


def _as_asked(thing, item, old, w):
    """(how a scene keeps one thing as the owner asked, the words for it)."""
    name, level = admin.called(thing, w), item.get('level')
    if item.get('on') is False or level == 0:
        return {'state': 'off'}, f'{name} off'
    kept, said = {'state': 'on'}, f'{name} on'
    if level is not None:
        if not isinstance(level, (int, float)) or isinstance(level, bool) or not 1 <= level <= 100:
            raise Refused('A level is 1 to 100.')
        if thing['domain'] == 'light' and read._dims(thing['attrs']):
            kept['brightness'], said = round(int(level) * 255 / 100), f'{name} at {int(level)}%'
        elif thing['domain'] == 'fan':
            kept['percentage'], said = int(level), f'{name} at {int(level)}%'
    elif isinstance(old, dict) and old.get('state') == 'on':
        kept.update({k: old[k] for k in ('brightness', 'percentage') if k in old})
    if item.get('colour'):
        if thing['domain'] != 'light':
            raise Refused(f'{name} isn’t a light, so it has no colour.')
        colour, words = control._colour(item['colour'], {'name': name, 'colours': read._colours(thing['attrs'])})
        kept.update(colour)
        said += f', {words}'
    elif isinstance(old, dict):
        kept.update({k: old[k] for k in broker.COLOURS if k in old})
    return kept, said


def _for_scene(said, w):
    """What a scene means by a name: a room is its lights, as everywhere in Hub; a plug or a fan is reached by name."""
    try:
        return _things([said], w, kinds=('light',))
    except Refused as error:
        if str(error).startswith('Which one?'):
            raise
        found = _things([said], w, kinds=SCENE_KINDS)
        guard = next((t for t in found if t['guards']), None)
        if guard:
            raise Refused(f'{guard["name"]} belongs to the locks or the alarm, so it is worked by itself and not kept in a scene.') from None
        return found


def scene(w, do, name, set=None, capture=None, leave=None, new_name=None):     # noqa: A002 - "set" is the owner's word
    """Save, change, rename or remove a Home Assistant scene."""
    here = _routine_named(name, 'scene', w)
    if do in ('remove', 'rename'):
        if here is None:
            raise Refused(f'There is no scene called “{admin._clip(name, 60)}”.')
        key, before = _config(here, 'scene')
        if do == 'remove':
            used = admin.uses(here)
            return [change('scene', here['id'], here['name'], f'Scene “{here["name"]}” removed' + (f'; {_listed(used)} use it' if used else ''),
                           [{'config': 'scene', 'id': key, 'body': None}], [{'config': 'scene', 'id': key, 'body': before}], asks='delete')]
        new = admin._clip(new_name)
        if not new or new == here['name']:
            raise Refused('Say the new name.') if not new else Already(f'It is already called {new}.')
        body = {**before, 'name': new}
        # A scene that opens the house is partly known by its name, as a lock is: a new name for it waits.
        return [change('scene', here['id'], new, f'Scene {here["name"]} renamed {new}', [{'config': 'scene', 'id': key, 'body': body}],
                       [{'config': 'scene', 'id': key, 'body': before}], asks='security' if scene_opens(here['id'], w) else None,
                       after={'config': 'scene', 'id': key, 'body': body})]
    if do != 'save':
        raise Refused('Say save, rename or remove.')
    if here is not None:
        key, before = _config(here, 'scene')
        body, title = copy.deepcopy(before), here['name']
    else:
        title = admin._clip(name)
        if not title:
            raise Refused('Give the scene a name.')
        key, before = str(int(time.time() * 1000)), None
        body = {'id': key, 'name': title, 'entities': {}}
    kept, said = dict(body.get('entities') or {}), []
    for where in capture or ():
        found = _for_scene(where, w)
        for thing in found:
            kept[thing['id']] = _as_now(thing)
        said.append(f'{admin.called(found[0], w) if len(found) == 1 else admin._clip(where, 40)} as it is now')
    for item in set or ():
        if not isinstance(item, dict) or not item.get('thing'):
            raise Refused('Each thing in a scene needs its name, and on, off or a level.')
        for thing in _for_scene(item['thing'], w):
            kept[thing['id']], words = _as_asked(thing, item, kept.get(thing['id']), w)
            said.append(words)
    for gone in leave or ():
        for thing in _for_scene(gone, w):
            if kept.pop(thing['id'], None) is not None:
                said.append(f'{admin.called(thing, w)} left out')
    if not kept:
        raise Refused('A scene needs at least one light, plug or fan. Say which, or which rooms to keep as they are now.')
    if before is not None and kept == (before.get('entities') or {}):
        raise Already(f'{title} is already like that.')
    body['entities'] = kept
    words = (f'Scene “{title}” changed: ' if before else f'Scene “{title}” saved: ') + _listed(said)
    return [change('scene', here['id'] if here else 'new', title, words, [{'config': 'scene', 'id': key, 'body': body}],
                   [{'config': 'scene', 'id': key, 'body': before}], after={'config': 'scene', 'id': key, 'body': body})]


# ---- automations and scripts ----

PARTS = {'automation': (('triggers', 'trigger'), ('conditions', 'condition'), ('actions', 'action')), 'script': (('actions', 'sequence'),)}
CALLED = re.compile(r'"(?:action|service|service_template)":\s*"([^"]*)"')
SAID_TIME = re.compile(r'^(\d{1,2}):(\d{2})(?::\d{2})?$')


def _parts(kind, body):
    """A routine's triggers, conditions and actions, under the names Home Assistant checks them by."""
    out = {}
    for plural, old in PARTS[kind]:
        value = body.get(plural, body.get(old)) if kind == 'automation' else body.get('sequence')
        if value is not None:
            out[plural] = value if isinstance(value, list) else [value]
    return out


def _steps(value):
    """Every step of a routine's actions, however deeply it is nested (choose, if, repeat, parallel, sequence), as
    Home Assistant runs them: the steps that hold others among them, and anything in a step's place that isn't a
    step at all, for opens() to count as a step Hub can't read."""
    for step in value if isinstance(value, list) else [value]:
        yield step
        if not isinstance(step, dict):
            continue
        choose = step.get('choose')
        for option in choose if isinstance(choose, list) else [choose] if isinstance(choose, dict) else ():
            yield from _steps(option['sequence'] if isinstance(option, dict) and 'sequence' in option else [option])
        for key in ('default', 'then', 'else', 'sequence', 'parallel'):
            if key in step:
                yield from _steps(step[key])
        if isinstance(step.get('repeat'), dict) and 'sequence' in step['repeat']:
            yield from _steps(step['repeat']['sequence'])


def _service(step):
    """The service one step calls, as written ("lock.unlock", or a template), or None for a step that calls none."""
    return next((step[key] for key in CALLS if isinstance(step, dict) and isinstance(step.get(key), str)), None)


def _kind(step):
    """What one step of a routine is: 'call', 'device' (a device's own action), 'scene', 'event', one of QUIET or
    CONTAINERS, or None for one Hub doesn't know."""
    if not isinstance(step, dict):
        return None
    if _service(step) is not None:
        return 'call'
    if 'device_id' in step and 'type' in step and 'domain' in step:
        return 'device'
    return next((key for key in ('scene', 'event', *QUIET, *CONTAINERS) if key in step), None)


def _data(step):
    """What one call sends: its data, with the older data_template's."""
    return {**(step.get('data_template') if isinstance(step.get('data_template'), dict) else {}),
            **(step.get('data') if isinstance(step.get('data'), dict) else {})}


def _actions(kind, body):
    return (_parts(kind, body).get('actions') or []) if isinstance(body, dict) else []


def _calls(body):
    """Every service a routine names anywhere in it. Raises Refused for one worked out at run time."""
    found = set()
    for name in CALLED.findall(json.dumps(body)):
        if not PLAIN_CALL.fullmatch(name):
            raise Refused('A routine made here names each action plainly, such as light.turn_on. Nothing was saved.')
        found.add(name)
    return found


def _device_actions(kind, body):
    """The (domain, type) of each device action in a routine: Home Assistant's other way to write an action, with
    no service named ("device_id", "domain": "lock", "type": "unlock")."""
    return {(str(s.get('domain')), str(s.get('type'))) for s in _steps(_actions(kind, body)) if _kind(s) == 'device'}


def _reached(step, w):
    """(the things one call works, as far as Hub can tell: those it names, the members of any group among them, and
    what is in the rooms, floors, groups of rooms and devices it names; whether it may also work things Hub can't
    list, by a template or an id Hub doesn't know)."""
    domain = (_service(step) or '').partition('.')[0]
    named, rooms, devices, unknown = [], set(), set(), False
    for part in (step, step.get('target'), step.get('data'), step.get('data_template')):
        if not isinstance(part, dict):
            continue
        for key in ('entity_id', 'area_id', 'floor_id', 'label_id', 'device_id'):
            got = part.get(key)
            for one in [got] if isinstance(got, str) else got if isinstance(got, list) else []:
                if key == 'entity_id' and one == 'all':
                    named += [t['id'] for t in w['things'].values() if t['domain'] == domain]
                elif key == 'entity_id' and isinstance(one, str) and broker.ANY_ENTITY.fullmatch(one):
                    named.append(one)
                elif key == 'area_id' and one in w['rooms']:
                    rooms.add(one)
                elif key == 'floor_id' and one in w['floors']:
                    rooms |= {r['id'] for r in w['rooms'].values() if r['floor'] == one}
                elif key == 'label_id' and one in w['groups']:
                    rooms |= {r['id'] for r in w['rooms'].values() if one in r['labels']}
                    devices |= {d['id'] for d in w['devices'].values() if one in d['labels']}
                    named += [t['id'] for t in w['things'].values() if one in t['labels']]
                elif key == 'device_id' and one in w['devices']:
                    devices.add(one)
                else:
                    unknown = True
    named += [t['id'] for t in w['things'].values() if (t['area'] in rooms or t['device'] in devices)
              and (domain == 'homeassistant' or t['domain'] == domain) and not t['minor']]
    return [w['things'][e] for e in _reach(list(dict.fromkeys(named)), w) if e in w['things']], unknown


def _reach(named, w, depth=0):
    """The entities named and, for any that is a group of others (a group of lights or of covers, or one of Home
    Assistant's old groups), its members too, however deep. A scene is read as a scene, by scene_opens()."""
    out = []
    for entity in named:
        out.append(entity)
        thing = w['things'].get(entity)
        members = thing['attrs'].get('entity_id') if thing and thing['domain'] != 'scene' else None
        if isinstance(members, list) and depth < DEEP:
            out += _reach([m for m in members if isinstance(m, str)], w, depth + 1)
    return list(dict.fromkeys(out))


def _aimed(step):
    """(the entities one action names, whether it also reaches things Hub can't list: a whole room, a device,
    a label, a template, "all")."""
    named, loose = [], False
    for part in (step, step.get('target'), step.get('data'), step.get('data_template')):
        if not isinstance(part, dict):
            continue
        loose = loose or any(part.get(key) for key in ('area_id', 'device_id', 'label_id', 'floor_id') if part is not step or key != 'device_id')
        got = part.get('entity_id')
        for entity in [got] if isinstance(got, str) else got if isinstance(got, list) else []:
            if isinstance(entity, str) and broker.ANY_ENTITY.fullmatch(entity):
                named.append(entity)
            else:
                loose = True
    return named, loose


def _door(entity, w, depth=0):
    """Whether a cover may be a way into the house: any but a blind, a shade, a curtain, an awning or a shutter, and
    one that doesn't say what it is among them; a group of covers when one of its own may be. One Hub doesn't know
    counts."""
    thing = w['things'].get(entity)
    if thing is None:
        return True
    members = thing['attrs'].get('entity_id')
    if isinstance(members, list) and members and depth < DEEP:
        return any(_door(m, w, depth + 1) for m in members if isinstance(m, str))
    return thing['class'] not in admin.EVERYDAY_COVERS


def scene_opens(entity, w):
    """Whether a scene, when it is set, unlocks, opens a door or the garage, or disarms, itself or through an
    automation that what it sets would set off: Home Assistant's scenes can hold a lock or an alarm as well as
    lights. One Hub can't read counts as one that does."""
    thing = w['things'].get(entity)
    if thing is None:
        return True
    members = thing['attrs'].get('entity_id')
    members = [m for m in members if isinstance(m, str)] if isinstance(members, list) else None
    if members is None and thing['platform'] in LIGHT_SCENES:
        return False                # an integration's own scene, which only ever sets its own lights
    if members is not None and not any(m.partition('.')[0] in OPENS or m.partition('.')[0] in SET_OFF for m in members):
        return any(_sets_off(w['things'][m], w) for m in members if m in w['things'])
    kept = broker.setup_config('scene', thing['config']) if thing['config'] else None
    return True if kept is None else _kept_opens(kept.get('entities'), w)


def _kept_opens(entities, w):
    """Whether the states a scene keeps would open the house: a lock unlocked, the alarm disarmed, a door open, or
    a thing that guards them, that Hub doesn't know, or that sets off what does."""
    for member, wanted in (entities if isinstance(entities, dict) else {}).items():
        domain = str(member).partition('.')[0]
        state = wanted.get('state') if isinstance(wanted, dict) else wanted
        if state in WIDE_OPEN.get(domain, ()) and (domain != 'cover' or _door(member, w)):
            return True
        if member not in w['things'] or w['things'][member]['guards'] or _sets_off(w['things'][member], w):
            return True
    return False


def back_asks(steps, w):
    """Why putting something back would wait for a tap, by the same rule as the change itself: Undo is never a way
    round it. A lock unlocked again, a routine enabled or switched on again, a scene or routine brought back that
    opens, a way in moved back to its room. 'unlock', 'security', 'unread' or None."""
    why = []
    for step in steps:
        if 'call' in step:
            domain, _, service = str(step['call']).partition('.')
            data = step.get('data') if isinstance(step.get('data'), dict) else {}
            thing = w['things'].get(data.get('entity_id')) if isinstance(data.get('entity_id'), str) else None
            why.append(_asks(domain, service, thing, w, {k: v for k, v in data.items() if k != 'entity_id'}))
        elif 'config' in step and isinstance(step.get('body'), dict):
            body = step['body']
            why.append(('security' if _kept_opens(body.get('entities'), w) else None) if step['config'] == 'scene'
                       else opens(step['config'], body, w) if step['config'] in PARTS else None)
        elif isinstance(step.get('ws'), dict):
            command = step['ws']
            thing, device = w['things'].get(command.get('entity_id')), w['devices'].get(command.get('device_id'))
            near = [thing] if thing else [w['things'][e] for e in device['things'] if e in w['things']] if device else []
            if 'disabled_by' in command and command['disabled_by'] is None and any(t['domain'] in ('automation', 'script') for t in near):
                why.append('unread')
            if {'area_id', 'name', 'name_by_user'} & set(command) and any(_keeps_out(t, w) for t in near):
                why.append('security')
            if command.get('type') == 'config/area_registry/update' and {'labels', 'floor_id'} & set(command) \
                    and _guarded_room(command.get('area_id'), w):
                why.append('security')
    return next((kind for kind in ('unlock', 'security', 'unread') if kind in why), None)


def _heard(body):
    """What sets an automation going or lets it go on, as text: its triggers and conditions, the conditions and
    waits among its steps, and every template anywhere in it. A thing it only works isn't here."""
    parts = _parts('automation', body)
    found = [parts.get('triggers'), parts.get('conditions'), body.get('variables'), body.get('trigger_variables')]

    def look(value):
        if isinstance(value, dict):
            if 'condition' in value:            # a condition among the steps: all of it decides
                found.append(value)
                return
            for key, inner in value.items():
                if key in ('if', 'conditions', 'while', 'until', 'wait_template', 'wait_for_trigger', 'value_template'):
                    found.append(inner)
                else:
                    look(inner)
        elif isinstance(value, list):
            for inner in value:
                look(inner)
        elif isinstance(value, str) and ('{{' in value or '{%' in value):
            found.append(value)

    look(parts.get('actions'))
    return json.dumps(found)


def _named_by(text, thing):
    """Whether text names a thing by its id, or by its device's (a device trigger)."""
    return bool(re.search(rf'(?<![\w.]){re.escape(thing["id"])}(?![\w])', text)) or bool(thing['device'] and thing['device'] in text)


def _sets_off(thing, w, seen=frozenset(), depth=0):
    """Why working a thing waits for a tap, or None: "security" when an automation that triggers on it, waits on it
    or reads it would unlock, open or disarm; "unread" when such an automation, or one that may be, can't be read.
    So a helper, a switch, a button or a light is not a way round the tap. An automation that only works the
    thing isn't set off by it. Each answer is kept with the house as it was read."""
    if depth >= DEEP:
        return 'unread'
    memo = w.setdefault('sets_off', {})
    if (thing['id'], seen) not in memo:
        memo[(thing['id'], seen)] = _chain(thing, w, seen, depth)
    return memo[(thing['id'], seen)]


def _chain(thing, w, seen, depth):
    try:
        (found,) = broker.setup_read({'type': 'search/related', 'item_type': 'entity', 'item_id': thing['id']})
    except (broker.HomeDown, broker.Rejected, ValueError):
        return 'unread'
    why = []
    for entity in (found or {}).get('automation') or ():
        other = w['things'].get(entity) if isinstance(entity, str) else None
        if other is None:
            why.append('unread')
            continue
        if entity in seen or other['state'] == 'off':
            continue
        kind, kept = _kept(entity, w)
        if kept is None or 'use_blueprint' in kept:
            why.append('unread')        # kept in Home Assistant's files, or made from a blueprint: what sets it going is unknown
        elif _named_by(_heard(kept), thing):
            why.append(opens('automation', kept, w, seen | {entity}, depth + 1))
    return _worst(why)


ALERTS = ('notify', 'persistent_notification', 'tts', 'siren')


def watches(kind, body, w):
    """Whether an automation keeps watch on a way into the house: set going by a lock, a door, the garage or what
    guards them, or by a smoke, water or tamper sensor, it tells someone (a notice, words said aloud, a siren).
    Switching it off or writing over it means nobody is told, so that waits for a tap."""
    if kind != 'automation' or not isinstance(body, dict):
        return False
    heard = _heard(body)
    if not any(t['watches'] and _named_by(heard, t) for t in w['things'].values()):
        return False
    return any(_kind(s) == 'call' and _service(s).partition('.')[0] in ALERTS for s in _steps(_actions(kind, body)))


def touches(kind, body, w):
    """Whether a routine works a lock, a door, the garage, the alarm or anything that guards them, in any way at
    all: locking and arming too. Switching such a routine off, or writing over it, weakens the house as surely as
    unlocking does."""
    for step in _steps(_actions(kind, body)):
        what = _kind(step)
        if what == 'device':
            device = w['devices'].get(step.get('device_id'))
            if str(step.get('domain')) in admin.SECURE or device is None \
                    or any(_keeps_out(w['things'][e], w) for e in device['things'] if e in w['things']):
                return True
            continue
        if what != 'call':
            continue
        domain = _service(step).partition('.')[0]
        reached, unknown = _reached(step, w)
        if domain in admin.SECURE or domain == 'valve' or (domain == 'cover' and (unknown or not reached or any(_door(t['id'], w) for t in reached))):
            return True
        if any(t['watches'] and t['domain'] != 'binary_sensor' for t in reached):
            return True
    return bool(opens(kind, body, w))


def _kept(entity, w):
    """(kind, the config Home Assistant keeps for an automation or a script) or (kind, None) when it can't be read."""
    kind, thing = entity.partition('.')[0], w['things'].get(entity)
    key = entity.partition('.')[2] if kind == 'script' else thing['config'] if thing else None
    return kind, (broker.setup_config(kind, key) if thing and key else None)


def _worst(found):
    """Of several answers to "why would this wait?", the one that counts: opening the house, then not being able
    to read it, then nothing."""
    found = ['security' if why is True else why for why in found]
    return 'security' if 'security' in found else 'unread' if 'unread' in found else None


def _device_opens(step, w, seen, depth):
    """Why a device's own action ("device_id", "domain", "type") waits for a tap: one that opens; one on a device
    that is a way in or guards one, unless it shuts; one on a device Hub can't find or that runs what its owner
    wrote; else whatever working the device sets off."""
    action = str(step.get('type'))
    if str(step.get('domain')) in OPENS:            # a lock's, the alarm's, a cover's or a valve's own action
        return None if action in SHUTS else 'security'
    device = w['devices'].get(step.get('device_id'))
    if device is None:
        return 'unread'
    near = [w['things'][e] for e in device['things'] if e in w['things']]
    if any(_keeps_out(t, w) for t in near):
        return None if action in SHUTS else 'security'
    if any(t['platform'] == 'template' and t['domain'] in TEMPLATED for t in near):
        return 'unread'
    return _worst(_sets_off(t, w, seen, depth) for t in near if t['domain'] not in admin.ROUTINES)


def _step_opens(step, w, seen, depth):
    """Why one step of a routine makes the routine wait for a tap: 'security', 'unread' or None. The rule is one:
    what Hub can't read as safe waits. A step of a kind Hub doesn't know, an event another routine may act on, a
    call worked out at run time or one that sends what Hub can't read is 'unread'."""
    kind = _kind(step)
    if kind in QUIET or kind in CONTAINERS:
        return None                 # it only waits, decides or holds steps of its own, and those are read one by one
    if kind == 'scene':
        return 'security' if not isinstance(step['scene'], str) or scene_opens(step['scene'], w) else None
    if kind == 'device':
        return _device_opens(step, w, seen, depth)
    called = _service(step) if kind == 'call' else None
    if called is None or not PLAIN_CALL.fullmatch(called):
        return 'unread'
    domain, _, service = called.partition('.')
    if (domain, service) in OPAQUE:
        return 'unread'
    named, loose = _aimed(step)
    named = _reach(named, w)
    data = _data(step)
    reached, unknown = _reached(step, w)
    things = list({t['id']: t for t in [*(w['things'][e] for e in named if e in w['things']), *reached]}.values())
    if service in OPENS.get(domain, ()) and (domain != 'cover' or loose or not named or any(_door(e, w) for e in named)):
        return 'security'
    if any(t['guards'] for t in things):
        return 'security'           # a switch, button or helper that belongs to a lock or the alarm
    if any(t['platform'] == 'template' and t['domain'] in TEMPLATED for t in things):
        return 'unread'             # it runs whatever its owner wrote for it
    if domain == 'scene':
        if service == 'turn_on':
            return 'security' if loose or any(scene_opens(e, w) for e in named) else None
        if service in ('apply', 'create'):                      # a scene made on the spot: what it sets, or keeps to set again later
            sets = data.get('entities') if isinstance(data.get('entities'), dict) else {}
            keeps = data.get('snapshot_entities')
            keeps = [keeps] if isinstance(keeps, str) else keeps if isinstance(keeps, list) else []
            members = [str(e) for e in [*sets, *keeps]]
            if any(e.partition('.')[0] in OPENS or e not in w['things'] or w['things'][e]['guards'] for e in members):
                return 'security'
            return _worst(_sets_off(w['things'][e], w, seen, depth) for e in members)
        return None
    whole = domain == 'homeassistant' and service in ('turn_on', 'turn_off', 'toggle')
    if whole and service != 'turn_off' and (loose or any(e.partition('.')[0] in OPENS and (e.partition('.')[0] != 'cover' or _door(e, w)) for e in named)):
        return 'security'
    if whole and service != 'turn_off' and any(scene_opens(e, w) for e in named if e.startswith('scene.')):
        return 'security'
    found = ['unread'] if unknown else []
    # Switching an automation on sets going what it does; switching one off stops it guarding.
    if (domain == 'automation' or whole) and service in ('turn_on', 'turn_off', 'toggle'):
        if loose and domain == 'automation':
            return 'unread'
        for t in things:
            if t['domain'] != 'automation' or t['id'] in seen:
                continue
            kind, kept = _kept(t['id'], w)
            found.append('unread' if kept is None else None)
            if kept is not None and service != 'turn_off':
                found.append(opens(kind, kept, w, seen | {t['id']}, depth + 1))
            if kept is not None and service != 'turn_on' and touches(kind, kept, w):
                found.append('security')
    # Whatever it works, a helper, a button, a switch or a light: what does working it set off?
    if service not in ('reload', 'update_entity'):
        found += [_sets_off(t, w, seen, depth) for t in things if t['domain'] not in admin.ROUTINES]
    # Another routine run from this one.
    runs = named if (domain, service) in (('script', 'turn_on'), ('script', 'toggle'), ('automation', 'trigger')) else \
        [called] if domain == 'script' and service not in ('turn_off', 'reload') else \
        [e for e in named if e.startswith('script.')] if whole and service != 'turn_off' else None
    if runs is not None:
        if loose or depth >= DEEP:
            found.append('unread')
        found += [opens(*_kept(entity, w), w, seen | {entity}, depth + 1) for entity in runs if entity not in seen]
    return _worst(found)


def opens(kind, body, w, seen=frozenset(), depth=0):
    """Why a routine waits for a tap, or None: "security" when running it could unlock, open a door or the garage,
    or disarm (itself, through a scene it sets, or through another routine it runs); "unread" when Hub can't read
    what it does (a blueprint, a routine kept in Home Assistant's own files, one that aims at more than Hub can
    list)."""
    if not isinstance(body, dict) or 'use_blueprint' in body:
        return 'unread'
    return _worst(_step_opens(step, w, seen, depth) for step in _steps(_actions(kind, body)))


def _named_in(value, found):
    """Every entity a routine names under `entity_id` or `scene`, anywhere in it."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in ('entity_id', 'scene'):
                found.update(e for e in ([inner] if isinstance(inner, str) else inner if isinstance(inner, list) else ()) if isinstance(e, str))
            else:
                _named_in(inner, found)
    elif isinstance(value, list):
        for inner in value:
            _named_in(inner, found)
    return found


def _by_id(value, w, kind=None):
    """A routine with every device and room it names by name ("bedroom floor lamp", "Kitchen") given its Home
    Assistant id instead. A name must mean exactly one thing; ids and templates are left as they are. The
    service an action calls says what kind of thing its names are: light.turn_on names lights. Raises Refused."""
    def entity(said):
        if not isinstance(said, str) or said in w['things'] or '{' in said or said in ('all', 'none') or broker.ANY_ENTITY.fullmatch(said):
            return said
        found = admin.match(said, w, kinds=(kind,) if kind else None)
        if len(found) != 1:
            raise admin.which(found, w)
        return found[0]['id']

    def area(said):
        return said if not isinstance(said, str) or said in w['rooms'] or '{' in said else admin.room(said, w)['id']

    if isinstance(value, list):
        return [_by_id(v, w, kind) for v in value]
    if not isinstance(value, dict):
        return value
    called = value.get('action') or value.get('service')
    domain = called.partition('.')[0] if isinstance(called, str) and '.' in called else kind
    domain = domain if domain in admin.SAID_AS or domain in ('cover', 'input_boolean', 'input_number', 'input_select') else None
    out = {}
    for key, inner in value.items():
        if key in ('entity_id', 'scene'):
            out[key] = [entity(e) for e in inner] if isinstance(inner, list) else entity(inner)
        elif key == 'area_id':
            out[key] = [area(a) for a in inner] if isinstance(inner, list) else area(inner)
        else:
            out[key] = _by_id(inner, w, domain if key in ('target', 'data') else None)
    return out


def _checked(kind, body, before, w):
    """A routine Home Assistant can read and Hub may save: every action named plainly and behind the setup door,
    apart from what the owner had already put in it, and every device it names one that exists. Raises Refused
    with the reason, for the model to put right."""
    known = _named_in(before, set()) if before else set()
    missing = sorted(e for e in _named_in(body, set()) - known if '{' not in e and e not in w['things'] and e not in ('all', 'none'))
    if missing:
        raise Refused(f'There is no {missing[0]} in Home Assistant. Look it up with find and use the id it gives. Nothing was saved.')
    if 'use_blueprint' not in body:
        parts = _parts(kind, body)
        if not parts.get('actions') or (kind == 'automation' and not parts.get('triggers')):
            raise Refused('An automation needs triggers and actions.' if kind == 'automation' else 'A script needs a sequence of actions.')
        try:
            (verdicts,) = broker.setup_read({'type': 'validate_config', **parts})
        except broker.Rejected as error:
            raise Refused(f'Home Assistant can’t read that ({error.code}). Nothing was saved.') from None
        for part, verdict in (verdicts or {}).items():
            if isinstance(verdict, dict) and verdict.get('valid') is False:
                raise Refused(f'Home Assistant can’t read its {part}: {admin._clip(verdict.get("error"), 240)}. Nothing was saved.')
    for step in _steps(_actions(kind, body)):
        called, told = _service(step), _data(step) if isinstance(step, dict) else {}
        if isinstance(called, str) and called.partition('.')[0] in ('notify', 'persistent_notification', 'tts'):
            from ...engine import judge
            words = ' '.join(str(told.get(key) or '') for key in ('title', 'message'))
            new = before is None or json.dumps(step, sort_keys=True) not in json.dumps(before, sort_keys=True)
            if new and judge.scrub(words) != words:
                raise Refused('A notice can’t hold links or email addresses. Nothing was saved.')
            if new and called.partition('.')[0] != 'tts' and set(told) - {'title', 'message'}:
                raise Refused('A notice is its title and its message: no buttons, links or pictures. Nothing was saved.')
    shut = sorted(name for name in _calls(body) - (_calls(before) if before else set()) if not broker.service_open(*name.split('.', 1)))
    if shut:
        raise Refused(f'Hub can’t save a routine that calls {shut[0]}. That one is made in Home Assistant. Nothing was saved.')
    odd = sorted(domain for domain, _ in _device_actions(kind, body) - (_device_actions(kind, before) if before else set())
                 if domain not in broker.SERVICE_DOMAINS)
    if odd:
        raise Refused(f'Hub can’t save a routine that works a {odd[0].replace("_", " ")} device. That one is made in Home Assistant. '
                      'Nothing was saved.')


def _clock(at):
    found = SAID_TIME.match(str(at))
    if not found:
        return str(at)[:40]
    hour, minute = int(found.group(1)), found.group(2)
    return f'{hour % 12 or 12}:{minute} {"am" if hour < 12 else "pm"}'


def _called(entity, w):
    return admin.called(w['things'][entity], w) if entity in w['things'] else str(entity)[:60]


def _span(offset):
    """A sun trigger's offset as a length of time: "-00:30:00" is "30 minutes"."""
    found = re.fullmatch(r'[-+]?(\d{1,2}):(\d{2})(?::(\d{2}))?', str(offset).strip())
    if not found:
        return str(offset).lstrip('-+')[:20]
    hours, minutes = int(found.group(1)), int(found.group(2))
    return ' '.join(p for p in (f'{hours} hour{"s" if hours != 1 else ""}' if hours else '',
                                f'{minutes} minute{"s" if minutes != 1 else ""}' if minutes else '') if p) or 'just'


def _when(trigger, w):
    kind = trigger.get('trigger') or trigger.get('platform')
    entity = trigger.get('entity_id')
    entity = entity[0] if isinstance(entity, list) and entity else entity
    if kind == 'time':
        at = trigger.get('at')
        return 'at ' + _listed([_clock(a) for a in (at if isinstance(at, list) else [at])])
    if kind == 'sun':
        offset = str(trigger.get('offset') or '')
        return (f'{_span(offset)} {"before" if offset.startswith("-") else "after"} ' if offset.strip('-+0:') else 'at ') + str(trigger.get('event'))
    if kind == 'state' and entity:
        if str(entity).startswith('event.'):
            return f'when {_called(entity, w)} is pressed'
        to = trigger.get('to')
        return f'when {_called(entity, w)} ' + (f'turns {to}' if isinstance(to, str) else 'changes')
    if kind == 'numeric_state' and entity:
        return f'when {_called(entity, w)} goes ' + (f'above {trigger["above"]}' if 'above' in trigger else f'below {trigger.get("below")}')
    return {'time_pattern': 'on a timer', 'homeassistant': 'when Home Assistant starts', 'zone': 'when someone comes or goes',
            'event': 'on an event', 'device': 'when a device says so', 'template': 'when a condition comes true'}.get(kind, 'on a trigger')


def _does(step, w):
    name = _service(step)
    if isinstance(name, str):
        target = {k: v for part in (_data(step), step.get('target')) if isinstance(part, dict) for k, v in part.items()}
        named = target.get('entity_id', step.get('entity_id'))
        named = [named] if isinstance(named, str) else named if isinstance(named, list) else []
        areas = target.get('area_id')
        areas = [areas] if isinstance(areas, str) else areas if isinstance(areas, list) else []
        what = _listed([_called(e, w) for e in named] + [admin.room_name(a, w) or a for a in areas], 3)
        domain, _, service = name.partition('.')
        return _did(domain, service, what or domain.replace('_', ' '), _data(step))
    if 'delay' in step:
        return 'waits'
    if 'scene' in step:
        return f'scene {_called(step["scene"], w)}'
    if _kind(step) == 'device':
        device = w['devices'].get(step.get('device_id'))
        return f'{device["name"] if device else "a device"}: {str(step.get("type")).replace("_", " ")}'
    if 'event' in step:
        return 'an event fired'
    return 'chooses by conditions' if 'choose' in step or 'if' in step else 'repeats' if 'repeat' in step else ''


OPENING_WORDS = ('unlocked', 'opened', 'disarmed', 'set off')


def says(kind, body, w):
    """What a routine does, in a line: when it runs and what it then does, anything that opens the house first so a
    card never hides it among the rest. Read from the config, never from a model."""
    if 'use_blueprint' in body:
        return 'made from a blueprint'
    parts = _parts(kind, body)
    when = _listed([_when(t, w) for t in parts.get('triggers', []) if isinstance(t, dict)], 2)
    does = [d for d in (_does(s, w) for s in parts.get('actions', []) if isinstance(s, dict)) if d]
    does = _listed(sorted(does, key=lambda d: not any(word in d for word in OPENING_WORDS)), 3)
    return ', '.join(p for p in (when, does) if p)


def routine(w, kind, do, name, config=None, new_name=None):
    """Save, rename, remove, switch on or off, or run an automation or a script."""
    if kind not in ('automation', 'script'):
        raise Refused('That is an automation or a script.')
    here, what = _routine_named(name, kind, w), kind.capitalize()
    if here is None and do != 'save':
        raise Refused(f'There is no {kind} called “{admin._clip(name, 60)}”.')
    if do in ('on', 'off'):
        if kind != 'automation':
            raise Refused('A script has no on or off. It runs when asked.')
        if here['state'] == do:
            raise Already(f'{here["name"]} is already {do}.')
        back = [{'call': f'automation.turn_{here["state"]}', 'data': {'entity_id': here['id']}}] if here['state'] in ('on', 'off') else None
        # Switching on one that opens the house sets that going; switching off one that locks or arms, or that tells
        # the owner a door is open, stops it guarding.
        _, kept = _kept(here['id'], w)
        why = 'unread' if kept is None else opens(kind, kept, w, frozenset({here['id']})) if do == 'on' \
            else 'security' if touches(kind, kept, w) or watches(kind, kept, w) else None
        return [change(kind, here['id'], here['name'], f'Automation “{here["name"]}” switched {do}',
                       [{'call': f'automation.turn_{do}', 'data': {'entity_id': here['id']}}], back, asks=why)]
    if do == 'run':
        key = here['id'].partition('.')[2] if kind == 'script' else here['config']
        kept = broker.setup_config(kind, key) if key else None
        call = {'call': 'automation.trigger', 'data': {'entity_id': here['id'], 'skip_condition': True}} if kind == 'automation' \
            else {'call': 'script.turn_on', 'data': {'entity_id': here['id']}}
        return [change(kind, here['id'], here['name'], f'{what} “{here["name"]}” run', [call], asks=opens(kind, kept, w, frozenset({here['id']})))]
    if do == 'remove':
        key, before = _config(here, kind)
        used = admin.uses(here)
        return [change(kind, here['id'], here['name'], f'{what} “{here["name"]}” removed' + (f'; {_listed(used)} use it' if used else ''),
                       [{'config': kind, 'id': key, 'body': None}], [{'config': kind, 'id': key, 'body': before}], asks='delete')]
    if do == 'rename':
        key, before = _config(here, kind)
        new = admin._clip(new_name)
        if not new or new == here['name']:
            raise Refused('Say the new name.') if not new else Already(f'It is already called {new}.')
        body = {**before, 'alias': new}
        return [change(kind, here['id'], new, f'{what} {here["name"]} renamed {new}', [{'config': kind, 'id': key, 'body': body}],
                       [{'config': kind, 'id': key, 'body': before}], asks='security' if touches(kind, before, w) else None,
                       after={'config': kind, 'id': key, 'body': body})]
    if do != 'save':
        raise Refused('Say save, rename, remove, on, off or run.')
    if not isinstance(config, dict) or not config:
        raise Refused(f'Give the {kind} as Home Assistant writes it: ' + ('alias, description, triggers, conditions, actions and mode.'
                                                                           if kind == 'automation' else 'alias, description, sequence and mode.'))
    if here is not None:
        key, before = _config(here, kind)
        title = admin._clip(config.get('alias')) or here['name']
    else:
        title = admin._clip(config.get('alias')) or admin._clip(name)
        if not title:
            raise Refused(f'Give the {kind} a name.')
        key = str(int(time.time() * 1000)) if kind == 'automation' else re.sub(r'[^a-z0-9]+', '_', title.lower()).strip('_')[:60]
        if not key or f'script.{key}' in w['things']:
            raise Refused('Give the script another name: that one is taken.')
        before = None
    body = _by_id({k: v for k, v in config.items() if k != 'id'}, w)
    body['alias'] = title
    if kind == 'automation':
        body = {'id': key, **body}
    _checked(kind, body, before, w)
    if before is not None and admin._same(before, body):
        raise Already(f'{title} is already like that.')
    line = says(kind, body, w)
    words = f'{what} “{title}” {"changed" if before else "made"}' + (f': {line}' if line else '')
    why = opens(kind, body, w) or ('security' if before is not None and (touches(kind, before, w) or watches(kind, before, w)) else None)
    return [change(kind, here['id'] if here else 'new', title, words, [{'config': kind, 'id': key, 'body': body}],
                   [{'config': kind, 'id': key, 'body': before}], asks=why, after={'config': kind, 'id': key, 'body': body})]


# ---- devices beyond lights and speakers: any service the setup door lets through ----

ONOFF = ('light', 'switch', 'fan', 'input_boolean', 'siren', 'humidifier', 'automation', 'media_player', 'climate', 'water_heater', 'remote')
VALUE = {'input_number': 'set_value', 'number': 'set_value', 'input_text': 'set_value', 'text': 'set_value',
         'input_select': 'select_option', 'select': 'select_option'}
CLIMATE = {'set_hvac_mode': ('hvac_mode',), 'turn_on': ('hvac_mode',), 'turn_off': ('hvac_mode',), 'set_temperature': ('hvac_mode', 'temperature'),
           'set_preset_mode': ('preset_mode',), 'set_fan_mode': ('fan_mode',), 'set_humidity': ('humidity',), 'set_swing_mode': ('swing_mode',)}
SAYS = {'lock': 'locked', 'unlock': 'unlocked', 'open': 'opened', 'open_cover': 'opened', 'close_cover': 'closed', 'stop_cover': 'stopped',
        'open_valve': 'opened', 'close_valve': 'closed', 'turn_on': 'on', 'turn_off': 'off', 'toggle': 'switched', 'press': 'pressed',
        'alarm_disarm': 'disarmed', 'alarm_arm_home': 'armed (home)', 'alarm_arm_away': 'armed (away)', 'alarm_arm_night': 'armed (night)',
        'start': 'started', 'pause': 'paused', 'stop': 'stopped', 'return_to_base': 'sent back to its dock', 'trigger': 'run',
        'media_play': 'playing', 'media_pause': 'paused', 'volume_mute': 'muted', 'reload': 'reloaded', 'cancel': 'cancelled', 'finish': 'finished'}


def _did(domain, service, name, data):
    """What one service call did, in the owner's words."""
    if service in ('set_temperature',) and any(k in data for k in ('temperature', 'target_temp_low', 'target_temp_high')):
        heat = f'{data["temperature"]:g}°' if isinstance(data.get('temperature'), (int, float)) else \
            f'{data.get("target_temp_low", "")}–{data.get("target_temp_high", "")}°'
        return f'{name} set to {heat}'
    for key, words in (('hvac_mode', 'set to {}'), ('preset_mode', 'set to {}'), ('fan_mode', 'fan set to {}'), ('percentage', 'at {}%'),
                       ('position', 'at {}%'), ('option', 'set to {}'), ('value', 'set to {}'), ('source', 'on {}'), ('humidity', 'at {}% humidity')):
        if key in data and isinstance(data[key], (str, int, float)) and not isinstance(data[key], bool):
            return f'{name} ' + words.format(str(data[key]).replace('_', ' ')[:40])
    if domain == 'light' and service == 'turn_on' and isinstance(data.get('brightness_pct'), (int, float)):
        return f'{name} at {int(data["brightness_pct"])}%'
    if domain == 'media_player' and service == 'volume_set' and isinstance(data.get('volume_level'), (int, float)):
        return f'{name} volume {round(data["volume_level"] * 100)}%'
    if domain in ('notify', 'persistent_notification'):
        return 'A notice sent' + (f' to {service.removeprefix("mobile_app_").replace("_", " ")}' if service.startswith('mobile_app_') else '')
    if domain == 'tts':
        return f'Said aloud on {name}'
    return f'{name} {SAYS[service]}' if service in SAYS else f'{name}: {service.replace("_", " ")}'


def _back(domain, service, thing, data):
    """The calls that put one device back as it was before a service call, or None when there is no way back."""
    state, attrs, entity = thing['state'], thing['attrs'], {'entity_id': thing['id']}
    kind = thing['domain']
    number = lambda key: attrs.get(key) if isinstance(attrs.get(key), (int, float)) and not isinstance(attrs.get(key), bool) else None  # noqa: E731
    if state in ('unavailable', 'unknown'):
        return None
    if kind == 'climate':
        steps = []
        for field in CLIMATE.get(service, ()):
            if field == 'hvac_mode':
                steps.append({'call': 'climate.set_hvac_mode', 'data': {**entity, 'hvac_mode': state}})
            elif field == 'temperature' and state != 'off':
                heat = {'temperature': number('temperature')} if number('temperature') is not None else \
                    {'target_temp_low': number('target_temp_low'), 'target_temp_high': number('target_temp_high')}
                if all(v is not None for v in heat.values()):
                    steps.append({'call': 'climate.set_temperature', 'data': {**entity, **heat}})
            elif field != 'temperature' and attrs.get(field) is not None:
                steps.append({'call': f'climate.set_{field}', 'data': {**entity, field: attrs[field]}})
        return steps or None
    if kind == 'fan' and service in ('set_percentage', 'increase_speed', 'decrease_speed', 'set_preset_mode', 'oscillate', 'set_direction', 'turn_on', 'turn_off', 'toggle'):
        if state == 'off':
            return [{'call': 'fan.turn_off', 'data': entity}]
        steps = [{'call': 'fan.turn_on', 'data': {**entity, **({'percentage': int(number('percentage'))} if number('percentage') is not None else {})}}]
        if service == 'oscillate' and isinstance(attrs.get('oscillating'), bool):
            steps = [{'call': 'fan.oscillate', 'data': {**entity, 'oscillating': attrs['oscillating']}}]
        if service == 'set_direction' and attrs.get('direction'):
            steps = [{'call': 'fan.set_direction', 'data': {**entity, 'direction': attrs['direction']}}]
        if service == 'set_preset_mode' and attrs.get('preset_mode'):
            steps = [{'call': 'fan.set_preset_mode', 'data': {**entity, 'preset_mode': attrs['preset_mode']}}]
        return steps
    if kind == 'light' and service in ('turn_on', 'turn_off', 'toggle'):
        if state == 'off':
            return [{'call': 'light.turn_off', 'data': entity}]
        level = round(number('brightness') / 255 * 100) if number('brightness') else None
        return [{'call': 'light.turn_on', 'data': {**entity, **({'brightness_pct': max(1, level)} if level else {}), **(read._colour_now(attrs) or {})}}]
    if kind == 'media_player':
        if service == 'volume_set' and number('volume_level') is not None:
            return [{'call': 'media_player.volume_set', 'data': {**entity, 'volume_level': float(attrs['volume_level'])}}]
        if service == 'volume_mute' and isinstance(attrs.get('is_volume_muted'), bool):
            return [{'call': 'media_player.volume_mute', 'data': {**entity, 'is_volume_muted': attrs['is_volume_muted']}}]
        if service == 'select_source' and attrs.get('source'):
            return [{'call': 'media_player.select_source', 'data': {**entity, 'source': attrs['source']}}]
        if service in ('media_play', 'media_pause') and state in ('playing', 'paused'):
            return [{'call': 'media_player.media_play' if state == 'playing' else 'media_player.media_pause', 'data': entity}]
        return [{'call': f'media_player.turn_{state}', 'data': entity}] if service in ('turn_on', 'turn_off') and state in ('on', 'off') else None
    if kind in ONOFF and service in ('turn_on', 'turn_off', 'toggle') and state in ('on', 'off'):
        return [{'call': f'{kind}.turn_{state}', 'data': entity}]
    if kind == 'lock' and state in ('locked', 'unlocked'):
        return [{'call': 'lock.lock' if state == 'locked' else 'lock.unlock', 'data': entity}]
    if kind in ('cover', 'valve') and service != f'stop_{kind}':
        place = number('current_position')
        if place is not None and service == f'set_{kind}_position':
            return [{'call': f'{kind}.set_{kind}_position', 'data': {**entity, 'position': int(place)}}]
        return [{'call': f'{kind}.{"open" if state == "open" else "close"}_{kind}', 'data': entity}] if state in ('open', 'closed') else None
    if VALUE.get(kind) == service or (kind in ('input_select', 'select') and service.startswith('select_')):
        value = float(state) if kind in ('input_number', 'number') and re.fullmatch(r'-?\d+(\.\d+)?', state) else state
        return [{'call': f'{kind}.{VALUE[kind]}', 'data': {**entity, 'option' if 'select' in kind else 'value': value}}]
    return None


def _asks(domain, service, thing, w, data=None):
    """Why one service call waits for a tap, or None."""
    if (domain, service) == ('homeassistant', 'restart'):
        return 'restart'
    if service in OPENS.get(domain, ()) and (domain != 'cover' or thing is None or _door(thing['id'], w)):
        return 'unlock'
    if thing and thing['guards']:
        return 'security'
    if thing and thing['domain'] in ('script', 'automation') and service in ('turn_on', 'toggle', 'trigger') \
            and not (thing['domain'] == 'automation' and service != 'trigger'):
        return opens(*_kept(thing['id'], w), w, frozenset({thing['id']}))
    step = {'action': f'{domain}.{service}', 'target': {'entity_id': thing['id']} if thing else {}, 'data': data or {}}
    return _step_opens(step, w, frozenset(), 0)


def _is_already(service, thing):
    wanted = {'turn_on': ('on',), 'turn_off': ('off',), 'lock': ('locked',), 'unlock': ('unlocked',), 'open_cover': ('open',),
              'close_cover': ('closed',), 'open_valve': ('open',), 'close_valve': ('closed',)}.get(service)
    return bool(wanted) and thing['state'] in wanted


def act(w, action, things=None, data=None):
    """One service on the things named: a thermostat, a fan's speed, a lock, a blind, a helper's value."""
    domain, _, service = str(action or '').strip().partition('.')
    if not broker.service_open(domain, service):
        raise Refused('Hub can’t do that from Ask. It can be done in Home Assistant.')
    data = {k: v for k, v in (data or {}).items() if k not in broker.TARGETS} if isinstance(data, dict) else {}
    if not broker._plain(data, broker.DATA_BYTES):
        raise Refused('That is more than one action can carry.')
    if (domain, service) == ('homeassistant', 'restart'):
        return [change('house', 'homeassistant', 'Home Assistant', 'Home Assistant restarted', [{'call': 'homeassistant.restart', 'data': {}}], asks='restart')]
    if domain in ('notify', 'persistent_notification') or service in ('reload', 'reload_all', 'check_config'):
        if domain in ('notify', 'persistent_notification') and not admin._clip(data.get('message')):
            raise Refused('Say what the notice should say.')
        if domain in ('notify', 'persistent_notification') and set(data) - {'title', 'message'}:
            raise Refused('A notice is its title and its message, nothing more: no links, buttons or pictures.')
        return [change(domain, action, action, _did(domain, service, domain.replace('_', ' '), data), [{'call': f'{domain}.{service}', 'data': data}])]
    if domain == 'tts':
        speakers = _things(things, w, kinds=('media_player',))
        voice = next((t['id'] for t in w['things'].values() if t['domain'] == 'tts' and not t['disabled']), None)
        if service != 'speak' or voice is None or not admin._clip(data.get('message')):
            raise Refused('To say something aloud, Home Assistant needs a voice set up, and the words to say.')
        return [change('media_player', s['id'], s['name'], _did('tts', service, admin.called(s, w), data),
                       [{'call': 'tts.speak', 'data': {'entity_id': voice, 'media_player_entity_id': s['id'], 'message': admin._clip(data['message'], 400)}}])
                for s in speakers]
    if domain == 'scene' and service != 'turn_on':
        raise Refused('Scenes are made and changed with scene_setup, and run with scene.')
    if domain == 'script' and service not in ('turn_on', 'turn_off', 'toggle'):      # a script called by its own name
        return routine(w, 'script', 'run', f'script.{service}')
    if things or domain == 'homeassistant':
        found = _things(things, w, kinds=None if domain == 'homeassistant' else (domain,))
    else:                           # nothing named: the house's only one of that kind is meant, when there is only one
        found = [t for t in w['things'].values() if t['domain'] == domain and not (t['minor'] or t['hidden'] or t['disabled'])]
        if len(found) != 1:
            raise admin.which(found, w) if found else Refused(f'There is no {domain.replace("_", " ")} in Home Assistant.')
    if domain == 'homeassistant' and any(t['domain'] in OPENS for t in found):
        raise Refused('Use that device’s own action, such as lock.lock or cover.close_cover.')
    changes, already = [], []
    for thing in found:
        if thing['domain'] == 'automation' and service in ('turn_on', 'turn_off', 'toggle') and domain in ('automation', 'homeassistant'):
            do = 'on' if service == 'turn_on' or (service == 'toggle' and thing['state'] == 'off') else 'off'
            try:                    # the same as switching it by name: one that guards the house waits for a tap
                changes += routine(w, 'automation', do, thing['id'])
            except Already as note:
                already.append(str(note))
            continue
        if not data and _is_already(service, thing):
            already.append(f'{admin.called(thing, w)} is already {SAYS.get(service, thing["state"])}.')
            continue
        words = f'{thing["name"]} is on' if (domain, service) == ('scene', 'turn_on') else _did(domain, service, admin.called(thing, w), data)
        changes.append(change(thing['domain'], thing['id'], thing['name'], words,
                              [{'call': f'{domain}.{service}', 'data': {**data, 'entity_id': thing['id']}}],
                              _back(domain, service, thing, data), asks=_asks(domain, service, thing, w, data)))
    if not changes:
        raise Already(' '.join(already))
    return changes


# ---- helpers, and the house's own name and units ----

def helper(w, do, kind, name, fields=None, new_name=None):
    """Add, change or remove a helper: a switch, number, choice, text, time, button, timer or counter that
    automations can use."""
    if kind not in broker.HELPERS:
        raise Refused('A helper is one of: ' + ', '.join(broker.HELPERS) + '.')
    fields = dict(fields or {}) if isinstance(fields or {}, dict) else {}
    extra = sorted(set(fields) - broker.HELPERS[kind] - {'icon'})
    if extra:
        raise Refused(f'{kind} takes only: {", ".join(sorted(broker.HELPERS[kind] | {"icon"}))}. Not {", ".join(extra)}.')
    (rows,) = broker.setup_read({'type': f'{kind}/list'})
    rows = [r for r in rows or () if isinstance(r, dict) and isinstance(r.get('id'), str) and isinstance(r.get('name'), str)]
    here = next((r for r in rows if admin._plainly(r['name']) == admin._plainly(name) or r['id'] == str(name).strip()), None)
    title, key = admin._clip(name), f'{kind}_id'
    what = kind.replace('input_', '').replace('boolean', 'switch').replace('select', 'choice').replace('datetime', 'date and time')
    if do == 'add':
        if here:
            raise Already(f'There is already a {what} helper called {here["name"]}.')
        if not title:
            raise Refused('Give the helper a name.')
        return [change('helper', 'new', title, f'Helper “{title}” added ({what})',
                       [_ws(f'{kind}/create', keep=['helper', 'id'], name=title, **fields)], [_ws(f'{kind}/delete', **{key: '$helper'})])]
    if here is None:
        raise Refused(f'There is no {what} helper called “{title}”.')
    if do == 'remove':
        again = {k: here[k] for k in ('name', 'icon', *broker.HELPERS[kind]) if here.get(k) is not None}
        entity = next((t for t in w['things'].values() if t['domain'] == kind and admin._plainly(t['name']) == admin._plainly(here['name'])), None)
        used = admin.uses(entity) if entity else []
        return [change('helper', here['id'], here['name'], f'Helper “{here["name"]}” removed' + (f'; {_listed(used)} use it' if used else ''),
                       [_ws(f'{kind}/delete', **{key: here['id']})], [_ws(f'{kind}/create', **again)], asks='delete')]
    if do != 'change':
        raise Refused('Say add, change or remove.')
    new = {**fields, **({'name': admin._clip(new_name)} if admin._clip(new_name) else {})}
    new = {k: v for k, v in new.items() if here.get(k) != v}
    if not new:
        raise Already(f'{here["name"]} is already like that.')
    # A helper's settings can change what it holds (a choice that is gone, a number outside its new range), which
    # sets off whatever listens to it; and a new name for one that guards hides that it does.
    entity = next((t for t in w['things'].values() if t['domain'] == kind and admin._plainly(t['name']) == admin._plainly(here['name'])), None)
    why = 'security' if entity and entity['guards'] else (('unread' if entity is None else _sets_off(entity, w))
                                                          if set(new) - {'name', 'icon'} else None)
    return [change('helper', here['id'], new.get('name', here['name']), f'Helper “{here["name"]}” changed: {_listed([k.replace("_", " ") for k in new])}',
                   [_ws(f'{kind}/update', **{key: here['id']}, **new)],
                   [_ws(f'{kind}/update', **{key: here['id']}, **{k: here.get(k) for k in new})], asks=why)]


HOUSE = {'location_name': 'name', 'unit_system': 'units', 'time_zone': 'time zone', 'currency': 'currency', 'country': 'country',
         'language': 'language'}


def house(w, fields):
    """The whole house's own settings: its name, units, time zone, currency, country and language."""
    if not isinstance(fields, dict) or set(fields) - set(HOUSE) or not fields:
        raise Refused('The house’s settings are: ' + ', '.join(HOUSE) + '.')
    (now,) = broker.setup_read({'type': 'get_config'})
    now = now if isinstance(now, dict) else {}
    units = now.get('unit_system') if isinstance(now.get('unit_system'), dict) else {}
    before = {**{k: now.get(k) for k in HOUSE}, 'unit_system': 'metric' if units.get('temperature') == '°C' else 'us_customary'}
    new = {k: v for k, v in fields.items() if isinstance(v, str) and v and v != before.get(k)}
    if not new:
        raise Already('The house is already set like that.')
    undo = {k: before[k] for k in new if isinstance(before.get(k), str)}
    return [change('house', 'core', 'The house', 'The house’s ' + _listed([f'{HOUSE[k]} set to {v}' for k, v in new.items()]),
                   [_ws('config/core/update', **new)], [_ws('config/core/update', **undo)] if len(undo) == len(new) else None, asks='house')]


def integration(w, name):
    """Reload an integration: it reconnects to its devices, and nothing about how it is set up changes. Named by its
    own title ("Kitchen bridge"), or by what it is ("hue"), which means every one of that kind."""
    asked = admin.words_of(name) - admin.FILLER - {'integration', 'reload'}
    rows = admin.integrations()
    if not asked:
        raise Refused('Say which integration.')
    found = [r for r in rows if admin.words_of(r['name']) == asked] or [r for r in rows if admin.words_of(r['kind']) == asked] \
        or [r for r in rows if asked <= admin.words_of(r['name']) | admin.words_of(r['kind'])]
    kinds = {r['kind'] for r in found}
    if not found or len(kinds) > 1:
        names = ', '.join(sorted({r['kind'] for r in (found or rows)}))[:300]
        raise Refused((f'Which one? {names}' if found else f'There is no integration called “{admin._clip(name, 60)}”. There are: {names}.'))
    stuck = [r['name'] for r in found if not r['reloads']]
    if stuck:
        raise Refused(f'{stuck[0]} can only be reloaded by restarting Home Assistant.')
    return [change('integration', r['id'], r['name'], f'{r["name"]} reloaded' + ('' if r['name'].lower() == r['kind'] else f' ({r["kind"]})'),
                   [{'reload': r['id']}]) for r in found]


# ---- Home Assistant's own dashboards: made from the house's rooms and things, never from a model's markup ----

ON_A_BOARD = ('light', 'switch', 'fan', 'climate', 'cover', 'lock', 'media_player', 'vacuum', 'humidifier', 'scene', 'script', 'input_boolean')


def _board_view(title, things):
    """One view: a heading and a tile for each thing. Only Home Assistant's own card kinds, and only things Hub found."""
    cards = [{'type': 'heading', 'heading': title}, *({'type': 'tile', 'entity': t['id']} for t in things)]
    return {'title': title, 'path': re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or 'view', 'type': 'sections',
            'sections': [{'type': 'grid', 'cards': cards}]}


def _board_views(w, rooms, things, view):
    """The views asked for: one per room named, with what is in it; and one of the things named, under `view`."""
    out = []
    for said in rooms or ():
        here = admin.room(said, w)
        inside = sorted((t for t in w['things'].values() if t['area'] == here['id'] and t['domain'] in ON_A_BOARD
                         and not (t['minor'] or t['hidden'] or t['disabled'])), key=lambda t: (ON_A_BOARD.index(t['domain']), t['name'].lower()))
        if not inside:
            raise Refused(f'There is nothing in {here["name"]} to show.')
        out.append(_board_view(here['name'], inside[:40]))
    if things:
        found = [t for t in _things(things, w) if t['domain'] in ON_A_BOARD or not t['minor']]
        out.append(_board_view(admin._clip(view) or 'Home', found[:40]))
    if not out:
        raise Refused('Say what the dashboard shows: which rooms, or which things.')
    return out


def dashboard(w, do, name, rooms=None, things=None, view=None, new_name=None):
    """Make, change, rename or remove one of Home Assistant's own dashboards. What it shows is built here from
    the house's rooms and things."""
    boards, title = admin.dashboards(), admin._clip(name)
    if not title:
        raise Refused('Say the dashboard’s name.')
    here = next((b for b in boards if admin._plainly(b['name']) == admin._plainly(title)), None)
    save = lambda path, config: {'board': {'type': 'lovelace/config/save', 'url_path': path, 'config': config}}       # noqa: E731
    if do == 'add':
        if here:
            raise Already(f'There is already a dashboard called {here["name"]}. Add a view to it, or give this one another name.')
        views = _board_views(w, rooms, things, view)
        path = 'hub-' + (re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:40] or 'board')
        while any(b['path'] == path for b in boards):
            path += '-2'
        make = {'board': {'type': 'lovelace/dashboards/create', 'url_path': path, 'title': title, 'mode': 'storage', 'show_in_sidebar': True,
                          'require_admin': False}, 'keep': ['board', 'id']}
        return [change('dashboard', 'new', title, f'Dashboard “{title}” made, showing {_listed([v["title"] for v in views])}',
                       [make, save(path, {'views': views})], [{'board': {'type': 'lovelace/dashboards/delete', 'dashboard_id': '$board'}}])]
    if here is None:
        names = ', '.join(b['name'] for b in boards) or 'none yet'
        raise Refused(f'There is no dashboard called “{title}”. There are: {names}.')
    if not broker.DASHBOARD.fullmatch(here['path']):
        raise Refused(f'{here["name"]} is one of Home Assistant’s own dashboards, so it is changed there.')
    if do == 'rename':
        new = admin._clip(new_name)
        if not new or new == here['name']:
            raise Refused('Say the new name.') if not new else Already(f'It is already called {new}.')
        return [change('dashboard', here['id'], new, f'Dashboard {here["name"]} renamed {new}',
                       [{'board': {'type': 'lovelace/dashboards/update', 'dashboard_id': here['id'], 'title': new}}],
                       [{'board': {'type': 'lovelace/dashboards/update', 'dashboard_id': here['id'], 'title': here['name']}}])]
    if do == 'remove':
        again = [{'board': {'type': 'lovelace/dashboards/create', 'url_path': here['path'], 'title': here['name'], 'mode': 'storage',
                            'show_in_sidebar': here['sidebar'], 'require_admin': here['admin'], **({'icon': here['icon']} if here['icon'] else {})}}]
        again += [save(here['path'], here['config'])] if here['config'] else []
        return [change('dashboard', here['id'], here['name'], f'Dashboard “{here["name"]}” removed',
                       [{'board': {'type': 'lovelace/dashboards/delete', 'dashboard_id': here['id']}}], again, asks='delete')]
    if here['views'] is None:
        raise Refused(f'{here["name"]} is drawn by Home Assistant itself, so there are no views of its own to change. Make a new dashboard.')
    before = here['config']
    if do == 'add_view':
        new = [v for v in _board_views(w, rooms, things, view) if v['title'] not in here['views']]
        if not new:
            raise Already(f'{here["name"]} already has that view.')
        body = {**before, 'views': [*before['views'], *new]}
        words = f'View {_listed(["“" + v["title"] + "”" for v in new])} added to dashboard “{here["name"]}”'
        return [change('dashboard', here['id'], here['name'], words, [save(here['path'], body)], [save(here['path'], before)],
                       after={'config': 'dashboard', 'id': here['path'], 'body': body})]
    if do == 'remove_view':
        said = admin._plainly(view)
        gone = [n for n, v in enumerate(here['views']) if admin._plainly(v) == said]
        if len(gone) != 1:
            raise Refused(f'Which view? {here["name"]} has: {", ".join(here["views"]) or "none"}.')
        if len(here['views']) == 1:
            raise Refused(f'That is the only view of {here["name"]}. Remove the dashboard instead.')
        body = {**before, 'views': [v for n, v in enumerate(before['views']) if n != gone[0]]}
        return [change('dashboard', here['id'], here['name'], f'View “{here["views"][gone[0]]}” taken out of dashboard “{here["name"]}”',
                       [save(here['path'], body)], [save(here['path'], before)], asks='delete',
                       after={'config': 'dashboard', 'id': here['path'], 'body': body})]
    raise Refused('Say add, add_view, remove_view, rename or remove.')


PLANS = {'arrange': arrange, 'place': place, 'scene': scene, 'routine': routine, 'act': act, 'helper': helper, 'house': house,
         'integration': integration, 'dashboard': dashboard}
