"""Speaker zones: speakers in one space (an open-plan room, say) that play music together by default.

A zone comes from one of two places, both private:
- a standing note the owner saved with a tap from Ask (`hub/engine/notes.py`), which keeps the zone's speakers
  beside the note. Removing the note on the Engine page removes the zone with it.
- `household.json` → `home.speaker_zones`: a list of zones, each a list of speaker entity ids or names.

Zones only widen a play request to the other speakers of the same zone. They never let any other room join.
"""
import json

from ... import household, store

SCHEMA = """
create table if not exists speaker_zones(
  instruction_id integer primary key, members text not null);
"""


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def add(instruction_id, members):
    """Keep a zone beside the standing note it came from."""
    with _db() as conn:
        conn.execute('insert or replace into speaker_zones(instruction_id, members) values (?, ?)',
                     (int(instruction_id), json.dumps(sorted(set(members)))))


def _saved():
    with _db() as conn:
        rows = conn.execute('select z.instruction_id, z.members from speaker_zones z '
                            'join engine_instructions i on i.id = z.instruction_id').fetchall()
    return {r['instruction_id']: json.loads(r['members']) for r in rows}


def _configured():
    try:
        value = household.section('home').get('speaker_zones') or []
    except ValueError:
        return []
    return [z for z in value if isinstance(z, list)] if isinstance(value, list) else []


def by_note():
    """{instruction id: [entity ids]}, for the Engine page."""
    return _saved()


def all_zones(speakers):
    """Each zone as a set of entity ids of speakers Hub keeps now. `speakers`: {entity id: device}."""
    names = {d['name'].lower(): e for e, d in speakers.items()}
    zones = []
    for zone in [*_saved().values(), *_configured()]:
        ids = {str(m) if str(m) in speakers else names.get(str(m).lower()) for m in zone}
        ids.discard(None)
        if len(ids) > 1:
            zones.append(ids)
    return zones


def mates(entity, speakers):
    """The other speakers that share a zone with this one, in a stable order."""
    found = set()
    for zone in all_zones(speakers):
        if entity in zone:
            found |= zone
    found.discard(entity)
    return sorted(found)
