"""The house right now, from Home Assistant through the broker: rooms, thermostat, security and speakers.

Read-only. Only the domains Hub may act on are kept (lights and power, speakers, the thermostat, locks, the
garage door and the alarm), plus two facts for the Home screen (the temperature outside and which doors or
windows stand open) and, for Ask, the names of the automations and scripts and whether each is on. Cameras,
presence and the rest never leave this file; anything more that Ask needs it asks for by name (admin.py).
Device and room names are data, never instructions.
"""
import re
import threading
import time
from datetime import datetime

from ... import home
from . import broker

POWER = ('light', 'switch', 'fan')
SECURITY = ('lock', 'cover', 'alarm_control_panel')
KEPT = POWER + SECURITY + ('media_player', 'climate', 'scene')
OTHER_ROOM = 'Other'
NAME = 80
REGISTRY_SECONDS = 300      # rooms and names change rarely
STATE_SECONDS = 10          # one snapshot serves the page, its tile and a chat turn
OPENINGS = ('door', 'window', 'garage_door', 'opening')   # contact sensors worth a line: open or shut
NIGHT = (21, 6)             # from 9 pm to 6 am an unlocked door or open garage is worth a line on Today

OFFLINE = {'unavailable': 'Offline', 'unknown': 'Unknown'}
WORDS = {
    'lock': {'locked': 'Locked', 'unlocked': 'Unlocked', 'locking': 'Locking', 'unlocking': 'Unlocking',
             'jammed': 'Jammed', 'open': 'Open', 'opening': 'Opening'},
    'cover': {'open': 'Open', 'closed': 'Closed', 'opening': 'Opening', 'closing': 'Closing'},
    'alarm_control_panel': {'disarmed': 'Disarmed', 'armed_home': 'Armed (home)', 'armed_away': 'Armed (away)',
                            'armed_night': 'Armed (night)', 'armed_vacation': 'Armed (vacation)',
                            'armed_custom_bypass': 'Armed', 'arming': 'Arming', 'disarming': 'Disarming',
                            'pending': 'About to go off', 'triggered': 'Going off'},
    'media_player': {'playing': 'Playing', 'paused': 'Paused', 'idle': 'Idle', 'off': 'Off', 'on': 'On',
                     'buffering': 'Loading', 'standby': 'Standby'},
    'climate': {'off': 'Off', 'heat': 'Heating', 'cool': 'Cooling', 'heat_cool': 'Heat and cool', 'auto': 'Auto',
                'dry': 'Dry', 'fan_only': 'Fan only'},
}
OPEN = {'lock': ('unlocked', 'open', 'opening', 'unlocking'), 'cover': ('open', 'opening', 'closing')}
# Integrations whose own scenes only ever set their own lights, though Home Assistant doesn't list what they hold.
LIGHT_SCENES = ('hue', 'lifx', 'deconz', 'tradfri')
# A wall or remote button (an event entity of class button): what its presses are called.
PRESSES = {'multi_press_1': 'Single press', 'multi_press_2': 'Double press', 'multi_press_3': 'Triple press',
           'long_press': 'Long press', 'long_release': 'Long press', 'initial_press': 'Press', 'short_release': 'Press'}
SAYS_PRESS = re.compile(r'\b(Single|Double|Triple|Long) press:\s*(.+?)(?=\s+(?:Single|Double|Triple|Long) press:|$)', re.S)

_lock = threading.Lock()
_cache = {'registry': None, 'registry_at': float('-inf'), 'snapshot': None, 'snapshot_at': float('-inf'),
          'floors': None, 'floors_at': float('-inf'), 'presses': {}, 'presses_at': float('-inf'),
          'changed_at': float('-inf')}


def forget_states():
    """After a change: the next read asks Home Assistant again."""
    with _lock:
        _cache.update(snapshot=None, snapshot_at=float('-inf'), changed_at=time.monotonic())


def changed_at():
    """When Hub last changed the house or forgot what it read (time.monotonic()), for readings kept elsewhere."""
    with _lock:
        return _cache['changed_at']


def clear_cache():
    """Forget everything read from the house, the forecast and Apple Home's list included."""
    from . import apple, weather
    with _lock:
        _cache.update(registry=None, registry_at=float('-inf'), snapshot=None, snapshot_at=float('-inf'),
                      floors=None, floors_at=float('-inf'), presses={}, presses_at=float('-inf'),
                      changed_at=time.monotonic())
    weather.forget()
    apple.forget()


def _clip(text):
    return ' '.join(str(text or '').split())[:NAME]


def _percent(value, whole=1.0):
    try:
        return round(float(value) / whole * 100)
    except (TypeError, ValueError):
        return None


def _degrees(value):
    try:
        return f'{float(value):.0f}°'
    except (TypeError, ValueError):
        return None


def _registry(areas, devices, entities, labels=()):
    """Rooms are Home Assistant's own areas. An area's labels (e.g. one floor's open-plan spaces) let a room word
    reach every area that carries it."""
    areas = [a for a in areas or () if isinstance(a, dict) and isinstance(a.get('area_id'), str)]
    label_names = {lab['label_id']: _clip(lab.get('name')) for lab in labels or ()
                   if isinstance(lab, dict) and lab.get('label_id')}
    rooms = {a['area_id']: _clip(a.get('name')) for a in areas}
    room_labels = {rooms[a['area_id']]: [label_names[x] for x in a.get('labels') or () if label_names.get(x)]
                   for a in areas if rooms[a['area_id']]}
    device_room = {d['id']: d.get('area_id') for d in devices or () if isinstance(d, dict) and d.get('id')}
    device_name = {d['id']: _clip(d.get('name_by_user') or d.get('name')) for d in devices or ()
                   if isinstance(d, dict) and d.get('id') and (d.get('name_by_user') or d.get('name'))}
    room_floor = {rooms[a['area_id']]: a['floor_id'] for a in areas if rooms[a['area_id']] and isinstance(a.get('floor_id'), str)}
    return {'rooms': rooms, 'room_labels': room_labels, 'room_floor': room_floor, 'device_room': device_room,
            'device_name': device_name,
            'entities': {e['entity_id']: e for e in entities or () if isinstance(e, dict) and e.get('entity_id')}}


def _room_id(entity_id, entry, registry):
    """The entity's own area, else its device's, else the area whose id starts its object id
    (light.kitchen_counter is in kitchen), the longest such id winning."""
    found = entry.get('area_id') or registry['device_room'].get(entry.get('device_id'))
    if found:
        return found
    object_id = entity_id.partition('.')[2]
    return max((a for a in registry['rooms'] if object_id.startswith(a + '_')), key=len, default=None)


def _fetch():
    """(registry, states), reading the registries only when they are old."""
    with _lock:
        registry = _cache['registry'] if time.monotonic() - _cache['registry_at'] < REGISTRY_SECONDS else None
    if registry is None:
        areas, devices, entities, labels, states = broker.read(
            'config/area_registry/list', 'config/device_registry/list', 'config/entity_registry/list',
            'config/label_registry/list', 'get_states')
        registry = _registry(areas, devices, entities, labels)
        with _lock:
            _cache.update(registry=registry, registry_at=time.monotonic())
    else:
        (states,) = broker.read('get_states')
    if not isinstance(states, list):
        raise broker.HomeDown('Home Assistant gave an answer Hub couldn’t read.')
    return registry, states


def _detail(domain, state, attrs):
    if state in OFFLINE:
        return ''
    if domain == 'light' and state == 'on':
        level = _percent(attrs.get('brightness'), 255)
        return f'{level}%' if level is not None else ''
    if domain == 'media_player':
        parts = [' – '.join(_clip(attrs[k]) for k in ('media_title', 'media_artist') if attrs.get(k))]
        volume = _percent(attrs.get('volume_level'))
        if volume is not None and state != 'off':
            parts.append(f'volume {volume}%')
        return ' · '.join(p for p in parts if p)
    if domain == 'climate':
        now, low, high = attrs.get('current_temperature'), attrs.get('target_temp_low'), attrs.get('target_temp_high')
        target = _degrees(attrs.get('temperature'))
        if not target and _degrees(low) and _degrees(high):
            target = f'{_degrees(low)}–{_degrees(high)}'
        parts = [f'Now {_degrees(now)}' if _degrees(now) else '', f'set to {target}' if target and state != 'off' else '']
        return ' · '.join(p for p in parts if p)
    return ''


def _device(entity_id, item, registry, players_only):
    """One kept device, or None for anything Hub leaves alone."""
    domain = entity_id.partition('.')[0]
    if domain not in KEPT:
        return None
    entry = registry['entities'].get(entity_id, {})
    if entry.get('hidden_by') or entry.get('disabled_by') or entry.get('entity_category'):
        return None
    attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    if domain == 'cover' and (attrs.get('device_class') or entry.get('original_device_class')) != 'garage':
        return None
    if domain == 'media_player' and players_only and entry.get('platform') != 'music_assistant':
        return None
    state = str(item.get('state') or 'unknown')
    room_id = _room_id(entity_id, entry, registry)
    label = OFFLINE.get(state) or WORDS.get(domain, {}).get(state) or ('On' if state == 'on' else 'Off' if state == 'off'
                                                                       else state.replace('_', ' ').capitalize())
    return {'id': entity_id, 'kind': domain, 'name': _clip(entry.get('name') or attrs.get('friendly_name')
                                                          or entry.get('original_name') or entity_id),
            'room': registry['rooms'].get(room_id) or OTHER_ROOM, 'state': state, 'label': label,
            'on': state == 'on' if domain in POWER else state in ('playing', 'buffering') if domain == 'media_player'
            else state not in ('off', *OFFLINE) if domain == 'climate' else False,
            'offline': state in OFFLINE, 'open': state in OPEN.get(domain, ()), 'detail': _detail(domain, state, attrs),
            'level': _percent(attrs.get('brightness'), 255) if domain == 'light' and state == 'on' else None,
            'volume': attrs.get('volume_level') if isinstance(attrs.get('volume_level'), (int, float)) else None,
            'members': [m for m in attrs.get('group_members') or () if isinstance(m, str) and m != entity_id],
            # The kinds of thing a scene sets, so Ask can tell one that only sets lights from one that holds a lock.
            'sets': _scene_sets(attrs, entry) if domain == 'scene' else None,
            'colours': _colours(attrs) if domain == 'light' else None,
            'dims': _dims(attrs) if domain == 'light' else False,
            'colour': _colour_now(attrs) if domain == 'light' and state == 'on' else None,
            # Home Assistant lists a group's leader first; joining goes through the leader.
            'leads': domain == 'media_player' and len(attrs.get('group_members') or ()) > 1
            and (attrs.get('group_members') or [None])[0] == entity_id,
            **({'title': _clip(attrs.get('media_title')), 'artist': _clip(attrs.get('media_artist')),
                'album': _clip(attrs.get('media_album_name'))} if domain == 'media_player' else {})}


def _scene_sets(attrs, entry):
    """The kinds of thing a scene sets, or None when it doesn't say: an integration's own scene lists nothing, so
    it could hold anything, unless that integration's scenes only ever set its lights."""
    members = attrs.get('entity_id')
    if isinstance(members, list):
        return sorted({m.partition('.')[0] for m in members if isinstance(m, str)})
    return ['light'] if entry.get('platform') in LIGHT_SCENES else None


COLOUR_MODES = {'hs', 'xy', 'rgb', 'rgbw', 'rgbww'}


def _colours(attrs):
    """What a light can show: 'colour' (any colour and white), 'white' (warm to cool white) or None (dimming only)."""
    modes = set(attrs.get('supported_color_modes') or ())
    return 'colour' if modes & COLOUR_MODES else 'white' if 'color_temp' in modes else None


def _dims(attrs):
    """Whether a light has a brightness to set. One that only reports on/off doesn't; an unknown one is tried."""
    modes = set(attrs.get('supported_color_modes') or ())
    return not modes or bool(modes - {'onoff'})


def _colour_now(attrs):
    """The light's colour now as one Home Assistant argument, so Undo can put it back, or None."""
    mode = attrs.get('color_mode')
    if mode == 'color_temp' and isinstance(attrs.get('color_temp_kelvin'), (int, float)):
        low, high = broker.KELVIN
        return {'color_temp_kelvin': max(low, min(high, int(attrs['color_temp_kelvin'])))}
    hs = attrs.get('hs_color')
    if mode in COLOUR_MODES and isinstance(hs, (list, tuple)) and len(hs) == 2 \
            and all(isinstance(v, (int, float)) for v in hs):
        return {'hs_color': [round(max(0, min(360, float(hs[0]))), 1), round(max(0, min(100, float(hs[1]))), 1)]}
    return None


def _build(registry, states):
    from . import lightshow       # it acts through control, which reads the house through here
    items = [s for s in states if isinstance(s, dict) and isinstance(s.get('entity_id'), str)]
    players_only = any(registry['entities'].get(s['entity_id'], {}).get('platform') == 'music_assistant'
                       for s in items if s['entity_id'].startswith('media_player.'))
    devices = [d for d in (_device(s['entity_id'], s, registry, players_only) for s in items) if d]
    devices.sort(key=lambda d: d['name'].lower())
    names = {d['id']: d['name'] for d in devices}
    for d in devices:
        d['group'] = [names[m] for m in d['members'] if m in names]
    rooms = {}
    for d in devices:
        if d['kind'] in POWER:
            rooms.setdefault(d['room'], []).append(d)
    labels = registry.get('room_labels', {})
    return {'rooms': [{'name': name, 'labels': labels.get(name, []), 'devices': rows, 'on': sum(r['on'] for r in rows)}
                      for name, rows in sorted(rooms.items(), key=lambda kv: (kv[0] == OTHER_ROOM, kv[0].lower()))],
            'room_labels': labels,
            'climate': [d for d in devices if d['kind'] == 'climate'],
            'security': [d for d in devices if d['kind'] in SECURITY],
            'speakers': [d for d in devices if d['kind'] == 'media_player'],
            'scenes': [d for d in devices if d['kind'] == 'scene'],
            'lights_on': sum(d['on'] for d in devices if d['kind'] == 'light'),
            'room_floor': registry.get('room_floor', {}),
            'outside': _outside(items), 'openings': _openings(items, registry), 'buttons': _buttons(items, registry),
            'routines': _routines(items, registry), 'weather': _weathers(items), 'sun': _sun(items),
            'batteries': _batteries(items, registry), 'show': lightshow.seen(items), 'read_at': datetime.now(home.PACIFIC)}


def _number(value):
    try:
        found = float(value)
    except (TypeError, ValueError):
        return None
    return found if found == found and abs(found) < 1e6 else None        # not NaN, not absurd


def _weathers(items):
    """Home Assistant's weather entities as they stand, for the brief (weather.py): each one's id, condition,
    temperature, wind, humidity, UV and units. Its forecasts are asked for separately."""
    found = []
    for s in items:
        attrs = s.get('attributes') if isinstance(s.get('attributes'), dict) else {}
        if not s['entity_id'].startswith('weather.') or s.get('state') in OFFLINE:
            continue
        found.append({'id': s['entity_id'], 'condition': str(s.get('state') or '')[:40], 'temperature': _number(attrs.get('temperature')),
                      'unit': attrs.get('temperature_unit') if attrs.get('temperature_unit') in ('°F', '°C') else None,
                      'wind': _number(attrs.get('wind_speed')), 'wind_unit': _clip(attrs.get('wind_speed_unit'))[:10],
                      'humidity': _number(attrs.get('humidity')), 'uv': _number(attrs.get('uv_index')),
                      'rain_unit': attrs.get('precipitation_unit') if attrs.get('precipitation_unit') in ('in', 'mm') else None})
    return found


def _sun(items):
    """When the sun next rises and sets, as Home Assistant's sun says (UTC times), and whether it is up; or None."""
    for s in items:
        if s['entity_id'] == 'sun.sun' and isinstance(s.get('attributes'), dict):
            attrs = s['attributes']
            times = {key: attrs.get(f'next_{key}') for key in ('rising', 'setting')}
            if all(isinstance(t, str) and len(t) < 40 for t in times.values()):
                return {**times, 'up': s.get('state') == 'above_horizon'}
    return None


BATTERY_WORDS = re.compile(r'\s+battery(\s+level)?$', re.I)


def _batteries(items, registry):
    """Every battery Home Assistant reports, one row a device: {'id', 'name', 'room', 'level' (percent or None),
    'low' (whether it says low)}. From battery sensors, low-battery binary sensors, and the battery_low or
    battery_level an alarm system's sensors carry. Whether one needs a look is batteries.py's call."""
    found = {}
    for s in items:
        entry = registry['entities'].get(s['entity_id'], {})
        attrs = s.get('attributes') if isinstance(s.get('attributes'), dict) else {}
        kind = attrs.get('device_class') or entry.get('original_device_class')
        domain = s['entity_id'].partition('.')[0]
        if entry.get('hidden_by') or entry.get('disabled_by') or s.get('state') in OFFLINE \
                or entry.get('platform') == 'mobile_app' or domain in ('device_tracker', 'person'):
            continue            # phones and watches are charged every night; their batteries aren't the house's
        level = low = None
        if domain == 'sensor' and kind == 'battery':
            level = _number(s.get('state'))
        elif domain == 'binary_sensor' and kind == 'battery':
            low = s.get('state') == 'on'
        elif isinstance(attrs.get('battery_low'), bool) or _number(attrs.get('battery_level')) is not None:
            low, level = attrs.get('battery_low') if isinstance(attrs.get('battery_low'), bool) else None, _number(attrs.get('battery_level'))
        else:
            continue
        device = entry.get('device_id')
        name = registry.get('device_name', {}).get(device) or BATTERY_WORDS.sub('', _clip(
            entry.get('name') or attrs.get('friendly_name') or entry.get('original_name') or s['entity_id']))
        row = found.setdefault(device or s['entity_id'], {
            'id': s['entity_id'], 'name': name, 'level': None, 'low': False,
            'room': registry['rooms'].get(_room_id(s['entity_id'], entry, registry)) or OTHER_ROOM})
        if level is not None and 0 <= level <= 100:
            row['level'] = round(level) if row['level'] is None else min(row['level'], round(level))
        row['low'] = row['low'] or bool(low)
    return sorted(found.values(), key=lambda b: b['name'].lower())


ROUTINES = 60       # automations and scripts named to Ask


def _routines(items, registry):
    """Automations and scripts by name, and whether each automation is on. Not what they do: Ask reads that when
    it needs it."""
    found = []
    for s in items:
        kind = s['entity_id'].partition('.')[0]
        entry = registry['entities'].get(s['entity_id'], {})
        if kind not in ('automation', 'script') or entry.get('hidden_by') or entry.get('disabled_by') or s.get('state') in OFFLINE:
            continue
        attrs = s.get('attributes') if isinstance(s.get('attributes'), dict) else {}
        found.append({'id': s['entity_id'], 'kind': kind, 'on': s.get('state') != 'off' if kind == 'automation' else True,
                      'name': _clip(entry.get('name') or attrs.get('friendly_name') or s['entity_id'].partition('.')[2].replace('_', ' '))})
    return sorted(found, key=lambda r: (r['kind'], r['name'].lower()))[:ROUTINES]


def _outside(items):
    """The temperature outside, from Home Assistant's weather, as "52°", or None."""
    for s in items:
        if s['entity_id'].startswith('weather.') and isinstance(s.get('attributes'), dict):
            found = _degrees(s['attributes'].get('temperature'))
            if found:
                return found
    return None


def _openings(items, registry):
    """Door and window contact sensors: {'count': how many Home Assistant has, 'open': names of those open now}."""
    count, standing = 0, []
    for s in items:
        attrs = s.get('attributes') if isinstance(s.get('attributes'), dict) else {}
        entry = registry['entities'].get(s['entity_id'], {})
        if not s['entity_id'].startswith('binary_sensor.') or entry.get('hidden_by') or entry.get('disabled_by') \
                or (attrs.get('device_class') or entry.get('original_device_class')) not in OPENINGS \
                or s.get('state') in OFFLINE:
            continue
        count += 1
        if s.get('state') == 'on':
            standing.append(_clip(entry.get('name') or attrs.get('friendly_name') or entry.get('original_name') or 'A door'))
    return {'count': count, 'open': sorted(standing)}


def _buttons(items, registry):
    """Buttons on walls and remotes: their room, battery and last press. What each press does is added by
    presses(), from the Home Assistant automations that listen to them. Hub only reads buttons, never presses them."""
    battery = {}
    for s in items:
        entry = registry['entities'].get(s['entity_id'], {})
        attrs = s.get('attributes') if isinstance(s.get('attributes'), dict) else {}
        if s['entity_id'].startswith('sensor.') and entry.get('device_id') \
                and (attrs.get('device_class') or entry.get('original_device_class')) == 'battery':
            level = _percent(s.get('state'), 100)
            if level is not None:
                battery[entry['device_id']] = level
    found = []
    for s in items:
        entry = registry['entities'].get(s['entity_id'], {})
        attrs = s.get('attributes') if isinstance(s.get('attributes'), dict) else {}
        if not s['entity_id'].startswith('event.') or entry.get('hidden_by') or entry.get('disabled_by') \
                or (attrs.get('device_class') or entry.get('original_device_class')) != 'button':
            continue
        state = str(s.get('state') or 'unknown')
        found.append({'id': s['entity_id'],
                      'name': registry.get('device_name', {}).get(entry.get('device_id')) or _clip(
                          entry.get('name') or attrs.get('friendly_name') or s['entity_id']),
                      'room': registry['rooms'].get(_room_id(s['entity_id'], entry, registry)) or OTHER_ROOM,
                      'battery': battery.get(entry.get('device_id')), 'offline': state == 'unavailable',
                      'last': None if state in OFFLINE else PRESSES.get(attrs.get('event_type'), 'Press'),
                      'at': None if state in OFFLINE else state[:40], 'does': []})
    return sorted(found, key=lambda b: b['name'].lower())


def _says(description):
    """An automation's description as rows: "Single press: Chill. Long press: Off." gives one row a press;
    any other description is one row of its own."""
    text = ' '.join(description.split())[:400] if isinstance(description, str) else ''
    rows = [{'press': f'{kind} press', 'does': does.strip().rstrip('.')} for kind, does in SAYS_PRESS.findall(text)]
    return rows or ([{'press': '', 'does': text}] if text else [])


def presses(buttons):
    """Fill each button's 'does' from the descriptions of the automations that listen to it. Read rarely, and
    fails soft: a button whose automations can't be read just says nothing about its presses."""
    ids = tuple(sorted(b['id'] for b in buttons))
    with _lock:
        cached = _cache['presses'] if time.monotonic() - _cache['presses_at'] < REGISTRY_SECONDS else None
    if cached is None or set(cached) != set(ids):
        cached = {}
        try:
            related = broker.ask(*({'type': 'search/related', 'item_type': 'entity', 'item_id': i} for i in ids)) if ids else []
            for button, found in zip(ids, related):
                autos = [a for a in (found or {}).get('automation') or () if isinstance(a, str)][:4]
                configs = broker.ask(*({'type': 'automation/config', 'entity_id': a} for a in autos)) if autos else []
                cached[button] = [row for c in configs for row in _says((c or {}).get('config', {}).get('description'))]
        except (broker.HomeDown, AttributeError, TypeError):
            cached = {i: [] for i in ids}
        with _lock:
            _cache.update(presses=cached, presses_at=time.monotonic())
    for b in buttons:
        b['does'] = cached.get(b['id'], [])[:6]
    return buttons


def floors():
    """Home Assistant's floors, {floor id: {'name', 'level'}}, for laying the rooms out. Empty when it has none or
    can't be read: the rooms then show as one list."""
    with _lock:
        if time.monotonic() - _cache['floors_at'] < REGISTRY_SECONDS and _cache['floors'] is not None:
            return _cache['floors']
    try:
        (rows,) = broker.read('config/floor_registry/list')
    except broker.HomeDown:
        return {}
    found = {f['floor_id']: {'name': _clip(f.get('name')), 'level': f.get('level') if isinstance(f.get('level'), int) else None}
             for f in rows or () if isinstance(f, dict) and isinstance(f.get('floor_id'), str)}
    with _lock:
        _cache.update(floors=found, floors_at=time.monotonic())
    return found


def snapshot(fresh=False):
    """The house now. Raises broker.HomeDown when Home Assistant can't be read. fresh: skip the short cache,
    as before a change."""
    with _lock:
        if not fresh and time.monotonic() - _cache['snapshot_at'] < STATE_SECONDS and _cache['snapshot']:
            return _cache['snapshot']
    found = _build(*_fetch())
    presses(found['buttons'])
    with _lock:
        _cache.update(snapshot=found, snapshot_at=time.monotonic())
    return found


def night(now=None):
    hour = (now or datetime.now(home.PACIFIC)).hour
    return hour >= NIGHT[0] or hour < NIGHT[1]


def attention(found, now=None):
    """(tone, device, words) for security devices worth a look: the alarm going off or a jammed lock at any
    hour, an unlocked door or an open garage at night. Most urgent first."""
    late, out = night(now), []
    for d in found['security']:
        if d['kind'] == 'alarm_control_panel' and d['state'] in ('triggered', 'pending'):
            out.append(('bad', d, f'{d["name"]}: {d["label"].lower()}'))
        elif d['state'] == 'jammed':
            out.append(('warn', d, f'{d["name"]} is jammed'))
        elif d['open'] and late:
            out.append(('warn', d, f'{d["name"]} is {"unlocked" if d["kind"] == "lock" and d["state"] != "open" else "open"}'))
    return sorted(out, key=lambda x: x[0] != 'bad')


def links():
    """Where Home Assistant and Music Assistant open for the owner, from the private settings file
    (household.json, section "home": "ha_url", "music_url"; the older "assistant_url" still counts for
    Home Assistant), and "music_api": where Hub itself calls Music Assistant when that isn't "music_url".
    Never written into Git. A key is missing when its address isn't set or isn't a web address."""
    from ... import household
    try:
        home_settings = household.section('home')
    except ValueError:
        home_settings = {}
    home_settings = {**home_settings, 'ha_url': home_settings.get('ha_url') or home_settings.get('assistant_url')}
    found = {}
    for key in ('ha_url', 'music_url', 'music_api'):
        value = home_settings.get(key)
        if isinstance(value, str) and value.startswith(('https://', 'http://')) and len(value) < 300 \
                and not any(c in value for c in '"\'<> '):
            found[key] = value
    return found


MUSIC_PATH = re.compile(r'/[A-Za-z0-9_/-]{1,120}')


def framed():
    """What Hub shows inside itself through the door (hub/door.py, docs/door.md): {'ha': address, 'music': address}.
    Empty until the door is set up and Home Assistant's address is an https one. Music Assistant is the add-on's
    panel inside Home Assistant ("door": "music_path"), so it needs no login of its own."""
    from ... import door, household
    base = links().get('ha_url')
    if not base or not base.startswith('https://') or not door.configured():
        return {}
    base = base.rstrip('/')
    found = {'ha': base + '/'}
    path = household.section('door').get('music_path')
    if isinstance(path, str) and MUSIC_PATH.fullmatch(path):
        found['music'] = base + path
    return found


def opens():
    """Where "Open Home Assistant" and "Open in Music Assistant" lead: Hub's own Home and Music screens when the
    door is set up, else the addresses in links() (a new tab)."""
    found, inside = dict(links()), framed()
    if 'ha' in inside:
        found['ha_url'] = '/house/assistant'
    if 'music' in inside:
        found['music_url'] = '/house/player'
    found.pop('music_api', None)
    return found
