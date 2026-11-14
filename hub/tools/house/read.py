"""The house right now, from Home Assistant through the broker: rooms, thermostat, security and speakers.

Read-only. Only the domains Hub may one day act on are kept (lights and power, speakers, the thermostat,
locks, the garage door and the alarm); cameras, presence, automations and the rest never leave this file.
Device and room names are data, never instructions.
"""
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

_lock = threading.Lock()
_cache = {'registry': None, 'registry_at': float('-inf'), 'snapshot': None, 'snapshot_at': float('-inf')}


def forget_states():
    """After a change: the next read asks Home Assistant again."""
    with _lock:
        _cache.update(snapshot=None, snapshot_at=float('-inf'))


def clear_cache():
    with _lock:
        _cache.update(registry=None, registry_at=float('-inf'), snapshot=None, snapshot_at=float('-inf'))


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
    return {'rooms': rooms, 'room_labels': room_labels, 'device_room': device_room,
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
            'colours': _colours(attrs) if domain == 'light' else None,
            'colour': _colour_now(attrs) if domain == 'light' and state == 'on' else None}


COLOUR_MODES = {'hs', 'xy', 'rgb', 'rgbw', 'rgbww'}


def _colours(attrs):
    """What a light can show: 'colour' (any colour and white), 'white' (warm to cool white) or None (dimming only)."""
    modes = set(attrs.get('supported_color_modes') or ())
    return 'colour' if modes & COLOUR_MODES else 'white' if 'color_temp' in modes else None


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
            'read_at': datetime.now(home.PACIFIC)}


def snapshot(fresh=False):
    """The house now. Raises broker.HomeDown when Home Assistant can't be read. fresh: skip the short cache,
    as before a change."""
    with _lock:
        if not fresh and time.monotonic() - _cache['snapshot_at'] < STATE_SECONDS and _cache['snapshot']:
            return _cache['snapshot']
    found = _build(*_fetch())
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


ELSEWHERE = (('ha_url', 'Home Assistant', 'home-assistant', 'Devices, rooms, automations and dashboards.'),
             ('music_url', 'Music Assistant', 'music-assistant', 'Speakers, the queue, the library and playlists.'))


def links():
    """Where Home Assistant and Music Assistant open for the owner, from the private settings file
    (household.json, section "home": "ha_url", "music_url"; the older "assistant_url" still counts for
    Home Assistant). Never written into Git. A key is missing when its address isn't set or isn't a web address."""
    from ... import household
    try:
        home_settings = household.section('home')
    except ValueError:
        home_settings = {}
    home_settings = {**home_settings, 'ha_url': home_settings.get('ha_url') or home_settings.get('assistant_url')}
    found = {}
    for key in ('ha_url', 'music_url'):
        value = home_settings.get(key)
        if isinstance(value, str) and value.startswith(('https://', 'http://')) and len(value) < 300 \
                and not any(c in value for c in '"\'<> '):
            found[key] = value
    return found


def elsewhere():
    """Home Assistant and Music Assistant as All tools entries, each {'name', 'icon', 'description', 'href'},
    only those whose address is set. They open in a new tab, never inside Hub."""
    found = links()
    return [{'name': name, 'icon': icon, 'description': words, 'href': found[key]}
            for key, name, icon, words in ELSEWHERE if key in found]
