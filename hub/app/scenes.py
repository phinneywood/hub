"""Scenes Hub keeps itself: the lights of some rooms as they were when the owner said "keep this".

Home Assistant has scenes of its own and the app runs those too. These exist because a scene kept here can
be made with one tap from the house as it stands, runs through the same change layer as every other tap (so it
has Undo), and can say whether the house is in it right now. Kept in Hub's own database, never in Git.
"""
import json
import re
import time

from .. import store
from ..tools.house import control, read

PREFIX = 'hub:'
MOST = 12               # scenes kept
NAME = 30
NEAR = 3                # a brightness this close to the kept one counts as the same (Home Assistant rounds)
SCHEMA = """
create table if not exists app_scenes(
  id integer primary key autoincrement, name text not null unique, lights text not null,
  rest_off integer not null default 0, ts real not null, login text);
"""


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _clean(name):
    name = ' '.join(re.sub(r'[^\w \-’\'&+]', ' ', str(name or '')).split())[:NAME].strip()
    if not name:
        raise control.Refused('Give the scene a name.')
    return name


def kept():
    """Every kept scene, oldest first: {'id', 'name', 'lights': {entity: level, 0 for off}, 'rest_off'}."""
    with _db() as conn:
        rows = conn.execute('select id, name, lights, rest_off from app_scenes order by id').fetchall()
    return [{'id': f'{PREFIX}{r["id"]}', 'name': r['name'], 'lights': json.loads(r['lights']), 'rest_off': bool(r['rest_off'])}
            for r in rows]


def _lights(found):
    return {d['id']: d for r in found['rooms'] for d in r['devices'] if d['kind'] == 'light'}


def _is(d, level):
    """Whether a light is as the scene keeps it."""
    if not level:
        return not d['on']
    return d['on'] and (d['level'] is None or not d.get('dims') or abs(d['level'] - level) <= NEAR)


def _rest(scene, found, outside):
    """The lamps a scene's "every other light off" means: lit, indoors, and not part of the scene."""
    return [d['id'] for r in found['rooms'] if r['name'] not in outside for d in r['devices']
            if d['kind'] == 'light' and d['on'] and not d['offline'] and d['id'] not in scene['lights']]


def on(scene, found, outside=()):
    """Whether the house is in this scene right now: every kept light as kept, and the rest off when it says so."""
    lights = _lights(found)
    here = [(lights[e], level) for e, level in scene['lights'].items() if e in lights and not lights[e]['offline']]
    if not here or not all(_is(d, level) for d, level in here):
        return False
    return not scene['rest_off'] or not _rest(scene, found, outside)


def keep(name, rooms, rest_off, *, login):
    """Remember the lights of these rooms (by name) as they are now, under `name`. A scene of the same name is
    replaced. Raises control.Refused."""
    name = _clean(name)
    found = read.snapshot(fresh=True)
    wanted = {d['id']: (d['level'] if d['level'] is not None else 100) if d['on'] else 0
              for r in found['rooms'] if r['name'] in set(rooms) for d in r['devices'] if d['kind'] == 'light' and not d['offline']}
    if not wanted:
        raise control.Refused('Pick at least one room.')
    with _db() as conn:
        if conn.execute('select count(*) from app_scenes where name != ?', (name,)).fetchone()[0] >= MOST:
            raise control.Refused(f'Hub keeps {MOST} scenes. Forget one first.')
        conn.execute('insert into app_scenes(name, lights, rest_off, ts, login) values (?,?,?,?,?) '
                     'on conflict(name) do update set lights = excluded.lights, rest_off = excluded.rest_off, '
                     'ts = excluded.ts, login = excluded.login', (name, json.dumps(wanted), int(bool(rest_off)), time.time(), login))
    return name


def forget(scene_id):
    with _db() as conn:
        conn.execute('delete from app_scenes where id = ?', (_number(scene_id),))


def _number(scene_id):
    try:
        return int(str(scene_id).removeprefix(PREFIX))
    except ValueError:
        raise control.Refused('That scene isn’t kept any more.') from None


def run(scene_id, *, login, via='app'):
    """Put the house into a kept scene: only the lights that differ are asked, as one batch with one Undo."""
    scene = next((s for s in kept() if s['id'] == f'{PREFIX}{_number(scene_id)}'), None)
    if scene is None:
        raise control.Refused('That scene isn’t kept any more.')
    from . import model
    found = read.snapshot(fresh=True)
    lights = _lights(found)
    off, on_plain, levels = [], [], {}
    for entity, level in scene['lights'].items():
        d = lights.get(entity)
        if d is None or d['offline'] or _is(d, level):
            continue
        if not level:
            off.append(entity)
        elif d.get('dims'):
            levels.setdefault(int(level), []).append(entity)
        else:
            on_plain.append(entity)
    if scene['rest_off']:
        off += _rest(scene, found, model.outdoors(found, read.floors()))
    changes = [(off, 'off', None), (on_plain, 'on', None), *((ids, 'brightness', level) for level, ids in sorted(levels.items()))]
    changes = [c for c in changes if c[0]]
    if not changes:
        raise control.Refused('The house is already like that.')
    return control.several(changes, login=login, via=via, taps=via == 'app')
