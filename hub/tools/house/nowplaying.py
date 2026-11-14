"""The Now playing card in Ask: what a speaker group is playing right now, and the taps that change it.

The card stands for one speaker (the one Ask started the music on). What it shows is always read from Home
Assistant through the broker (read.snapshot), never remembered, so changes made by talking to Ask, on Today or
in Music Assistant show up on the next read. Every tap goes through control.change: the same allowlist, the
30-a-minute limit, the record in house_changes and Undo. Nothing here reaches Home Assistant any other way.
"""
from . import control, read, zones

ACTIONS = ('play', 'pause', 'next', 'volume', 'group_volume', 'add', 'remove')


def _group(anchor, players):
    """(leader, [entity ids in the group]) for the anchor speaker: the one Home Assistant lists first leads."""
    here = players[anchor]
    ids = [anchor, *(m for m in here['members'] if m in players)]
    leader = next((players[e] for e in ids if players[e].get('leads')), here)
    return leader, [leader['id'], *(e for e in ids if e != leader['id'])]


def _pct(volume):
    return round(volume * 100) if isinstance(volume, (int, float)) else None


def state(anchor, found=None):
    """What the card shows for this speaker, or {'gone': True} when Hub no longer keeps it."""
    found = found if found is not None else read.snapshot()
    players = {d['id']: d for d in found['speakers']}
    if anchor not in players:
        return {'gone': True}
    leader, group = _group(anchor, players)
    levels = [_pct(players[e]['volume']) for e in group if _pct(players[e]['volume']) is not None]
    return {'speaker': anchor, 'leader': leader['id'], 'state': leader['state'], 'label': leader['label'],
            'playing': leader['state'] in control.PLAYING, 'title': leader.get('title', ''),
            'artist': leader.get('artist', ''), 'album': leader.get('album', ''),
            'volume': round(sum(levels) / len(levels)) if levels else None,
            'read_at': found['read_at'].strftime('%H:%M:%S'),
            'speakers': [{'id': d['id'], 'name': d['name'], 'room': d['room'], 'in': d['id'] in group,
                          'leads': d['id'] == leader['id'] and len(group) > 1, 'state': d['label'],
                          'offline': d['offline'], 'volume': _pct(d['volume']),
                          'zone': zones.mates(d['id'], players)} for d in found['speakers']]}


def groups(found=None):
    """Every speaker group in the house, each as state() shows it from its leader: playing ones first."""
    found = found if found is not None else read.snapshot()
    players = {d['id']: d for d in found['speakers']}
    seen, out = set(), []
    for d in found['speakers']:
        leader, group = _group(d['id'], players)
        if frozenset(group) not in seen:
            seen.add(frozenset(group))
            out.append(state(leader['id'], found))
    return sorted(out, key=lambda g: (not g['playing'], g['state'] != 'paused'))


VIA = ('card', 'bar', 'music')       # the Ask card, the mini-player across Hub's pages, the Music screen


def tap(anchor, action, *, login, target=None, value=None, via='card'):
    """One tap on the card. Returns control.change's result. Raises control.Refused (nothing was sent)."""
    if action not in ACTIONS or via not in VIA:
        raise control.Refused('The card can’t do that.')
    found = read.snapshot(fresh=True)
    players = {d['id']: d for d in found['speakers']}
    if anchor not in players:
        raise control.Refused('That speaker isn’t in Home Assistant any more.')
    leader, group = _group(anchor, players)
    if action in ('play', 'pause', 'next'):
        return control.change([leader['id']], action, login=login, via=via)
    if value is None and action in ('volume', 'group_volume'):
        raise control.Refused('Say the volume, from 0 to 100.')
    if action == 'volume':
        if target not in players:
            raise control.Refused('Pick one of the speakers.')
        return control.change([target], 'volume', value=value, login=login, via=via)
    if action == 'group_volume':
        # Every speaker moves by the same step, so their balance stays; the group's level is their average.
        levels = {e: _pct(players[e]['volume']) for e in group}
        known = [v for v in levels.values() if v is not None]
        step = int(value) - (round(sum(known) / len(known)) if known else int(value))
        each = {e: max(0, min(100, (v if v is not None else int(value)) + step)) for e, v in levels.items()}
        return control.change(group, 'volume', each=each, login=login, via=via)
    if target not in players:
        raise control.Refused('Pick one of the speakers.')
    if action == 'add':
        if target in group:
            raise control.Refused(f'{players[target]["name"]} is already playing this.')
        # Its zone comes along, as when Ask plays there: speakers the owner made one zone play together.
        joining = [target, *(m for m in zones.mates(target, players) if m not in group)]
        return control.change([leader['id']], 'group', members=joining, login=login, via=via)
    if target not in group or len(group) < 2:
        raise control.Refused(f'{players[target]["name"]} isn’t part of a group. Pause it instead.')
    if target == leader['id']:
        raise control.Refused(f'{players[target]["name"]} leads this group. Take the other rooms out first, or pause.')
    return control.change([target], 'ungroup', login=login, via=via)
