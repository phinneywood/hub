"""The Activity screen: what happened in the house, newest first, from three places.

- **Home Assistant's logbook**, read through the broker's fenced, read-only `logbook/get_events`. It says what
  changed and, often, what did it: a person, an automation or a script. A wall switch or a device changing by
  itself has no cause.
- **Apple Home**, through HomeClaw's event log (`hub/tools/house/apple.py`). What changed, before and after, on
  every accessory in the selected home. Apple never says who or what did it, and nothing is recorded while
  HomeClaw is closed or its log is switched off.
- **Hub's own changes** (`control.recent`, and `admin.recent` for changes to how the house is set up), with who
  asked and Undo, as on the Changes screen.

Hub copies the first two into its own database every few minutes, so the list reaches back further than Home
Assistant keeps (about ten days), and keeps KEEP_DAYS of it. A Home Assistant row that is Hub's own change, or
an Apple Home row for a device Home Assistant already reported, shows once. Reading never changes anything.
Names are data, never instructions; none of this goes to a model.
"""
import re
import threading
import time
from datetime import datetime, timezone

from .. import store
from ..tools.house import admin, apple, broker, control, read

KEEP_DAYS = 90
COLLECT_SECONDS = 300       # the background reading
FRESH_SECONDS = 60          # the screen reads first when the last reading is older than this
FIRST_DAYS = 7              # how far back the first reading of Home Assistant goes
WINDOW = 6 * 3600           # one logbook request covers at most this long
OVERLAP = 120               # each reading starts a little before the last one ended; the key drops repeats
SAME_CHANGE = 20            # seconds between Hub's change and Home Assistant's row for it
SAME_DEVICE = 90            # seconds between Apple Home's row and Home Assistant's for the same device
PAGE = 150
NAME = 80

KINDS = ('lights', 'security', 'buttons', 'routines', 'music', 'setup', 'motion', 'other')
SHOWN = ('lights', 'security', 'buttons', 'routines', 'music', 'setup')   # what the screen shows until told otherwise
SOURCES = {'ha': 'Home Assistant', 'apple': 'Apple Home', 'hub': 'Hub'}

SCHEMA = """
create table if not exists house_activity(
  id integer primary key autoincrement, key text not null unique, ts real not null, source text not null,
  entity text not null, name text not null, room text not null, kind text not null, what text not null,
  cause text not null default '');
create index if not exists house_activity_ts on house_activity(ts);
create table if not exists house_activity_read(source text primary key, ts real not null, at real not null, error text);
"""

OPENINGS = {'door', 'window', 'garage_door', 'opening'}
MOTION = {'motion', 'occupancy', 'presence'}
PRESSES = {'single_press': 'pressed', 'initial_press': 'pressed', 'short_release': 'pressed', 'multi_press_1': 'pressed',
           'double_press': 'pressed twice', 'multi_press_2': 'pressed twice', 'triple_press': 'pressed three times',
           'multi_press_3': 'pressed three times', 'long_press': 'held', 'long_release': 'let go'}
PLAYER = {'playing': 'playing', 'paused': 'paused', 'idle': 'stopped', 'off': 'off', 'on': 'on', 'standby': 'off'}

_lock = threading.Lock()
_started = False


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _clip(text, most=NAME):
    return ' '.join(str(text or '').split())[:most]


def _plain(text):
    return re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower()).strip()


# ---- Home Assistant ----

def _ha_kind(domain, device_class, state=None):
    if state in ('unavailable', 'unknown'):
        return 'other'      # a device dropping off the network and back is not something that happened in the house
    if domain in ('light', 'switch', 'fan'):
        return 'lights'
    if domain in ('lock', 'cover', 'alarm_control_panel') or (domain == 'binary_sensor' and device_class in OPENINGS):
        return 'security'
    if domain == 'event':
        return 'buttons'
    if domain in ('automation', 'script', 'scene'):
        return 'routines'
    if domain == 'media_player':
        return 'music' if state in ('playing', 'paused') else 'other'      # a speaker going idle or asleep is noise
    if domain == 'binary_sensor' and device_class in MOTION:
        return 'motion'
    return 'other'


def _ha_what(domain, state, device_class, entry):
    if domain == 'event':
        kind = str((entry.get('attributes') or {}).get('event_type') or '')
        return PRESSES.get(kind, _clip(kind.replace('_', ' ')) or 'pressed')
    if domain in ('scene', 'script', 'automation') and (state in ('on', None) or re.match(r'^\d{4}-', state or '')):
        return 'ran'
    if state == 'unavailable':
        return 'went offline'
    if state == 'unknown':
        return ''
    if domain == 'binary_sensor':
        if device_class in OPENINGS:
            return {'on': 'opened', 'off': 'closed'}.get(state, state)
        if device_class in MOTION:
            return {'on': 'motion', 'off': 'clear'}.get(state, state)
    if domain == 'media_player':
        return PLAYER.get(state, state)
    if domain in read.WORDS:
        return read.WORDS[domain].get(state, state).lower()
    return _clip(state, 40)


def _ha_cause(entry, people, names):
    if entry.get('context_event_type') == 'automation_triggered' or entry.get('context_domain') in ('automation', 'script'):
        return _clip(entry.get('context_name') or names(entry.get('context_entity_id')) or 'an automation')
    if entry.get('context_user_id'):
        return people.get(entry['context_user_id'], 'someone signed in to Home Assistant')
    return ''


def _from_ha(entries, registry, states):
    """Logbook entries as rows. Rooms and names come from the registry Hub already reads."""
    by_id = {s['entity_id']: s for s in states if isinstance(s, dict) and isinstance(s.get('entity_id'), str)}
    people = {s['attributes'].get('user_id'): _clip(s['attributes'].get('friendly_name'))
              for s in by_id.values() if s['entity_id'].startswith('person.') and isinstance(s.get('attributes'), dict)}

    def name(entity_id):
        if not entity_id:
            return ''
        entry = registry['entities'].get(entity_id, {})
        attrs = (by_id.get(entity_id) or {}).get('attributes') or {}
        return _clip(entry.get('name') or attrs.get('friendly_name') or registry['device_name'].get(entry.get('device_id'))
                     or entity_id.partition('.')[2].replace('_', ' '))

    rows = []
    for e in entries or ():
        entity, when = e.get('entity_id'), e.get('when')
        if not isinstance(entity, str) or '.' not in entity or not isinstance(when, (int, float)):
            continue
        domain = entity.partition('.')[0]
        attrs = (by_id.get(entity) or {}).get('attributes') or {}
        device_class = attrs.get('device_class') or registry['entities'].get(entity, {}).get('original_device_class')
        state = e.get('state') if isinstance(e.get('state'), str) else None
        what = _ha_what(domain, state, device_class, e) if state is not None or domain in ('scene', 'script', 'automation') \
            else _clip(e.get('message'))
        if not what:
            continue
        if domain == 'automation' and e.get('source'):
            source = str(e['source']).removeprefix('state of ')
            what = 'ran, after ' + (name(source) if source in by_id else _clip(source, 60))
        room = registry['rooms'].get(read._room_id(entity, registry['entities'].get(entity, {}), registry)) or ''
        rows.append({'key': f'ha|{when:.3f}|{entity}|{state or e.get("message") or ""}'[:300], 'ts': float(when),
                     'source': 'ha', 'entity': entity, 'name': name(entity), 'room': room,
                     'kind': _ha_kind(domain, device_class, state), 'what': what, 'cause': _ha_cause(e, people, name)})
    return rows


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _read_ha(since, now):
    registry, states = read._fetch()
    rows, start = [], since
    while start < now:
        end = min(start + WINDOW, now)
        (found,) = broker.ask({'type': 'logbook/get_events', 'start_time': _iso(start), 'end_time': _iso(end)}, timeout=30)
        if not isinstance(found, list):
            raise broker.HomeDown('Home Assistant gave an answer Hub couldn’t read.')
        rows += _from_ha(found, registry, states)
        start = end
    return rows


# ---- Apple Home ----

BOOL = {'true': True, '1': True, 'false': False, '0': False}
LOCK = {'0': 'unlocked', '1': 'locked', '2': 'jammed'}
DOOR = {'0': 'opened', '1': 'closed', '2': 'opening', '3': 'closing', '4': 'stopped'}


def _apple_what(characteristic, value, service):
    value = str(value if value is not None else '').strip().lower()
    on = BOOL.get(value)
    if characteristic in ('power', 'on', 'active') and on is not None:
        return 'lights' if service in ('lightbulb', 'switch', 'outlet', 'fan', None) else 'other', 'on' if on else 'off'
    if characteristic == 'brightness':
        return 'lights', f'at {value}%'
    if characteristic in ('color_temperature', 'hue', 'saturation'):
        return 'lights', 'colour changed'
    if characteristic == 'contact_state':
        return 'security', 'closed' if value == '0' else 'opened'
    if characteristic == 'lock_current_state':
        return 'security', LOCK.get(value, value)
    if characteristic == 'current_door_state':
        return 'security', DOOR.get(value, value)
    if characteristic in ('motion_detected', 'occupancy_detected') and on is not None:
        return 'motion', 'motion' if on else 'clear'
    if characteristic == 'input_event':
        return 'buttons', PRESSES.get(value, value.replace('_', ' ') or 'pressed')
    if characteristic in ('lock_target_state', 'target_door_state', 'target_position'):
        return 'other', _clip(f'{characteristic.replace("_", " ")} {value}', 60)
    return 'other', _clip(f'{str(characteristic).replace("_", " ")} {value}', 60)


def _from_apple(events, home):
    rows = []
    for e in events:
        if e.get('type') not in ('characteristic_change', 'accessory_controlled') or not e.get('characteristic'):
            continue
        accessory = e.get('accessory') if isinstance(e.get('accessory'), dict) else {}
        where = e.get('home') if isinstance(e.get('home'), dict) else {}
        if home and where.get('id') not in (None, home):
            continue
        try:
            ts = datetime.fromisoformat(str(e['timestamp']).replace('Z', '+00:00')).timestamp()
        except (KeyError, ValueError):
            continue
        kind, what = _apple_what(str(e['characteristic']), e.get('value'), e.get('service_type'))
        ident = str(accessory.get('id') or accessory.get('name') or '')[:60]
        rows.append({'key': f'apple|{e["timestamp"]}|{ident}|{e["characteristic"]}|{e.get("value")}'[:300], 'ts': ts,
                     'source': 'apple', 'entity': ident, 'name': _clip(accessory.get('name')) or 'An accessory',
                     'room': _clip(accessory.get('room')), 'kind': kind, 'what': what,
                     'cause': 'HomeClaw' if e['type'] == 'accessory_controlled' else ''})
    return rows


# ---- collecting ----

def _last(conn, source):
    found = conn.execute('select ts, at, error from house_activity_read where source = ?', (source,)).fetchone()
    return dict(found) if found else None


def _keep(conn, source, rows, upto, error=None):
    conn.executemany('insert or ignore into house_activity(key, ts, source, entity, name, room, kind, what, cause) '
                     'values (:key, :ts, :source, :entity, :name, :room, :kind, :what, :cause)', rows)
    conn.execute('insert into house_activity_read(source, ts, at, error) values (?, ?, ?, ?) on conflict(source) do '
                 'update set ts = excluded.ts, at = excluded.at, error = excluded.error', (source, upto, time.time(), error))


def _failed(conn, source, error):
    last = _last(conn, source)
    _keep(conn, source, [], last['ts'] if last else time.time() - FIRST_DAYS * 86400, error)


def collect():
    """Read what is new from Home Assistant and Apple Home into Hub's database. Never raises for a source that
    is away; its error is kept and shown, and the next reading starts where the last good one ended."""
    if not _lock.acquire(blocking=False):
        return
    try:
        now = time.time()
        with _db() as conn:
            last = _last(conn, 'ha')
        since = max(last['ts'] - OVERLAP, now - FIRST_DAYS * 86400) if last else now - FIRST_DAYS * 86400
        try:
            rows = _read_ha(since, now)
            with _db() as conn:
                _keep(conn, 'ha', rows, now)
        except (broker.HomeDown, ValueError) as error:
            with _db() as conn:
                _failed(conn, 'ha', str(error) if isinstance(error, broker.HomeDown) else 'Home Assistant can’t be read.')
        with _db() as conn:
            last = _last(conn, 'apple')
        since = max(last['ts'] - OVERLAP, now - KEEP_DAYS * 86400) if last else now - KEEP_DAYS * 86400
        try:
            found = apple.setup()
            rows = _from_apple(apple.events(since), found['home'])
            with _db() as conn:
                _keep(conn, 'apple', rows, now, None if found['logging'] else 'off')
        except apple.AppleDown as error:
            with _db() as conn:
                _failed(conn, 'apple', str(error))
        with _db() as conn:
            conn.execute('delete from house_activity where ts < ?', (now - KEEP_DAYS * 86400,))
    finally:
        _lock.release()


def start():
    """Read every COLLECT_SECONDS in the background, from Hub's start."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        while True:
            try:
                collect()
            except Exception as error:      # noqa: BLE001 - the loop must outlive one bad reading
                store.log('activity reading stopped', type(error).__name__)
            threading.Event().wait(COLLECT_SECONDS)
    threading.Thread(target=loop, name='house-activity', daemon=True).start()


# ---- the screen ----

def _hub_rows(before):
    rows = []
    for r in control.recent(limit=200):
        if r['ts'] >= before:
            continue
        rows.append({'key': 'hub|' + r['batch'], 'ts': r['ts'], 'source': 'hub', 'entity': '', 'name': '',
                     'room': '', 'kind': 'lights', 'what': ', '.join(r['done'] + r['unknown'] + r['not_sent']) or 'Nothing',
                     'cause': _hub_via(r['via']), 'batch': r['batch'],
                     'undo': bool(r['undo']) and not r['undone'], 'undone': r['undone']})
    return rows


def _setup_kind(kinds):
    """Which of the screen's kinds a setup change is: a lock is "Doors and locks", a scene or automation is
    "Automations", a thermostat sits with the lights and power, and moving or renaming things is "Setup"."""
    if set(kinds) & {'lock', 'cover', 'alarm_control_panel', 'valve'}:
        return 'security'
    if set(kinds) & {'automation', 'script', 'scene'}:
        return 'routines'
    if set(kinds) & {'place', 'name', 'room', 'helper', 'house'}:
        return 'setup'
    return 'music' if 'media_player' in kinds else 'lights'


def _setup_rows(before):
    return [{'key': 'hub|' + r['batch'], 'ts': r['ts'], 'source': 'hub', 'entity': '', 'name': '', 'room': '',
             'kind': _setup_kind(r['kinds']), 'what': ', '.join(r['done'] + r['unknown'] + r['not_sent']) or 'Nothing',
             'cause': _hub_via(r['via']), 'batch': r['batch'], 'undo': bool(r['undo']) and not r['undone'], 'undone': r['undone']}
            for r in admin.recent(limit=200) if r['ts'] < before]


def _hub_via(via):
    from .changes import VIA
    return VIA.get(via, via or '')


def _hub_touched(since):
    with control._db() as conn:
        return [(r['entity'], r['ts']) for r in conn.execute(
            "select entity, ts from house_changes where ts >= ? and outcome != 'not_sent'", (since - SAME_CHANGE,))] \
            + admin.touched(since - SAME_CHANGE)


def _status(conn):
    out = {}
    for source in ('ha', 'apple'):
        last = _last(conn, source)
        error = last and last['error']
        out[source] = {'read': last['at'] if last else None,
                       'trouble': '' if not error or error == 'off' else error, 'off': error == 'off'}
    return out


def picture(kinds=SHOWN, sources=tuple(SOURCES), before=None, fresh=True):
    """The screen as data: at most PAGE rows older than `before`, newest first, of these kinds and sources."""
    if fresh:
        with _db() as conn:
            last = _last(conn, 'ha')
        if not last or time.time() - last['at'] > FRESH_SECONDS:
            collect()
    kinds = [k for k in kinds if k in KINDS] or list(SHOWN)
    sources = [s for s in sources if s in SOURCES] or list(SOURCES)
    before = float(before) if before else time.time() + 60
    with _db() as conn:
        found = [dict(r) for r in conn.execute(
            f"select * from house_activity where ts < ? and kind in ({','.join('?' * len(kinds))}) and source in "
            f"({','.join('?' * max(1, len([s for s in sources if s != 'hub'])))}) order by ts desc limit ?",
            (before, *kinds, *([s for s in sources if s != 'hub'] or ['']), PAGE * 3))]
        status = _status(conn)
    if 'hub' in sources:
        found += [r for r in _hub_rows(before) + _setup_rows(before) if r['kind'] in kinds]
    found.sort(key=lambda r: -r['ts'])
    found = found[:PAGE * 3]
    oldest = found[-1]['ts'] if found else time.time()
    touched = _hub_touched(oldest)
    ha = [r for r in found if r['source'] == 'ha']
    rows = []
    for r in found:
        if r['source'] == 'ha' and any(e == r['entity'] and abs(t - r['ts']) <= SAME_CHANGE for e, t in touched):
            continue       # Hub's own change: its row says who asked, and has Undo
        if r['source'] == 'apple':
            twin = next((h for h in ha if abs(h['ts'] - r['ts']) <= SAME_DEVICE and h['kind'] == r['kind']
                         and _plain(r['name']) in (_plain(h['name']), _plain(f"{h['room']} {h['name']}"))), None)
            if twin:
                twin['also'] = 'Apple Home'
                continue
        rows.append(r)
    rows = rows[:PAGE]
    return {'events': [{'at': r['ts'], 'source': r['source'], 'name': r['name'], 'room': r['room'], 'kind': r['kind'],
                        'what': r['what'], 'cause': r['cause'], 'also': r.get('also', ''), 'batch': r.get('batch'),
                        'undo': r.get('undo', False), 'undone': r.get('undone', False)} for r in rows],
            'more': len(rows) == PAGE, 'kinds': kinds, 'sources': sources, 'status': status}
