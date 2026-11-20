"""The music light show, Hub's side of it.

Home Assistant runs the show with two scripts and a number, which Hub finds by their names (NAMES): "Start light
show" plays something Music Assistant found on one speaker and switches the show on, "End light show" stops it, and
"Light show timing" moves the lights earlier or later, in milliseconds. Which speaker plays the show and which switch
says it is on, Hub reads from the start script as Home Assistant keeps it. Hub may run those two scripts and set
that number, and nothing else of theirs: it never rewrites them. Each name must belong to exactly one entity, and
the start script must name one switch and one speaker, or Hub has no show.

Starting, ending and timing are recorded with the house's other changes, never with an Undo: ending the show is the
way back.

A show plays only songs that have a hand-made show: the music light's song maps on this Mac (SONGS), each with a cue
sheet beside it. With none named, all of them play in a shuffled order; anything else has no show yet.
"""
import json
import math
import os
import random
import threading
import time
from pathlib import Path

from . import broker, control, read

NAMES = {'start': ('script', 'Start light show'), 'end': ('script', 'End light show'),
         'timing': ('input_number', 'Light show timing')}
WIRING_SECONDS = 600         # how long what the start script says is trusted
MISS_SECONDS = 60            # a start script that couldn't be read, or says too little, is asked again after this
WIRING_TIMEOUT = 2.0         # reading it never holds up a reading of the house for long
CHECKS, CHECK_WAIT = 10, 0.5  # after starting: the show on and its speaker playing; a script answers before it runs
SONGS = Path(os.environ.get('HUB_SHOW_SONGS') or Path.home() / 'Library' / 'Application Support' / 'HomeOps' / 'music-light' / 'maps')
QUEUE = 12                   # the most songs one show plays

_lock = threading.Lock()
_wiring = {'script': None, 'until': float('-inf'), 'found': None}


def _one(items, domain, name):
    """The one entity of that domain with that name, or None when there is none or more than one."""
    hits = [s['entity_id'] for s in items if s['entity_id'].startswith(domain + '.')
            and str((s.get('attributes') or {}).get('friendly_name') or '').strip().lower() == name.lower()]
    return hits[0] if len(hits) == 1 else None


def _aimed(steps, action, out):
    """Every entity the steps aim `action` at, through ifs, chooses, sequences and parallels."""
    if isinstance(steps, list):
        for inner in steps:
            _aimed(inner, action, out)
    elif isinstance(steps, dict):
        if (steps.get('action') or steps.get('service')) == action:
            for place in (steps.get('target'), steps.get('data'), steps):
                named = place.get('entity_id') if isinstance(place, dict) else None
                out.update(e for e in ([named] if isinstance(named, str) else named if isinstance(named, list) else [])
                           if isinstance(e, str))
        for key, inner in steps.items():
            if key not in ('target', 'data') and isinstance(inner, (list, dict)):
                _aimed(inner, action, out)
    return out


def _wired(start):
    """{'flag', 'player'} as the start script is written: the one switch it turns on and the one speaker it plays on.
    None when it can't be read or doesn't name exactly one of each. Kept a while; a miss for a shorter while."""
    now = time.monotonic()
    with _lock:
        if _wiring['script'] == start and now < _wiring['until']:
            return _wiring['found']
    found = None
    try:
        config = broker.setup_config('script', start.partition('.')[2], timeout=WIRING_TIMEOUT)
    except (broker.HomeDown, ValueError):
        config = None
    if isinstance(config, dict):
        flags = {e for e in _aimed(config.get('sequence'), 'input_boolean.turn_on', set()) if e.startswith('input_boolean.')}
        players = {e for e in _aimed(config.get('sequence'), 'music_assistant.play_media', set()) if e.startswith('media_player.')}
        if len(flags) == 1 and len(players) == 1:
            found = {'flag': flags.pop(), 'player': players.pop()}
    with _lock:
        _wiring.update(script=start, found=found, until=now + (WIRING_SECONDS if found else MISS_SECONDS))
    return found


def forget():
    """Read the start script again next time (tests, and after Home Assistant's scripts change)."""
    with _lock:
        _wiring.update(script=None, until=float('-inf'), found=None)


def _number(item):
    """An input_number as {'id', 'value', 'min', 'max', 'step'}, or None when Home Assistant gives no usable one."""
    attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    try:
        low, high, step, value = (float(attrs.get('min')), float(attrs.get('max')), float(attrs.get('step') or 1),
                                  float(item.get('state')))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(n) for n in (low, high, step, value)) or not (low < high and step > 0):
        return None
    return {'id': item['entity_id'], 'value': value, 'min': low, 'max': high, 'step': step}


def seen(items):
    """The show as the house has it, from one reading of every state (read.py), or None when Home Assistant has none
    Hub can run: {'start', 'end', 'flag', 'player', 'on', 'timing'}, `timing` None without the number."""
    start, end = _one(items, *NAMES['start']), _one(items, *NAMES['end'])
    if not start or not end:
        return None
    wired = _wired(start)
    states = {s['entity_id']: s for s in items}
    if not wired or wired['flag'] not in states or wired['player'] not in states:
        return None
    timing = _one(items, *NAMES['timing'])
    return {'start': start, 'end': end, **wired, 'on': states[wired['flag']].get('state') == 'on',
            'timing': _number(states[timing]) if timing else None}


def _show():
    try:
        found = read.snapshot(fresh=True)
    except broker.HomeDown as error:
        raise control.Refused(str(error)) from None
    if not found.get('show'):
        raise control.Refused('Home Assistant has no light show Hub can run.')
    return found['show']


def _running(show):
    """Whether the show came on and its speaker plays, read back a few times while Music Assistant starts."""
    for n in range(CHECKS):
        if n:
            time.sleep(CHECK_WAIT)
        try:
            found = read.snapshot(fresh=True)
        except broker.HomeDown:
            return False
        now = found.get('show') or {}
        speaker = next((d for d in found['speakers'] if d['id'] == show['player']), None)
        if now.get('on') and (speaker is None or speaker['state'] in control.PLAYING):
            return True
    return False


def songs(folder=None):
    """The songs that have a hand-made show: each map in the music light's maps folder with a cue sheet beside it
    (sheets/ or cues/, same name), as [{'id', 'title', 'artist', 'uri'}] by title. The uri is the exact version that
    was mapped. Empty when there are none or the folder can't be read."""
    folder = Path(folder or SONGS)
    try:
        names = sorted(p.name for p in folder.glob('*.json') if p.name != 'index.json')
    except OSError:
        return []
    out = []
    for name in names:
        if not ((folder / 'sheets' / name).is_file() or (folder / 'cues' / name).is_file()):
            continue
        try:
            found = json.loads((folder / name).read_text())
        except (OSError, ValueError):
            continue
        ids = [i for i in found.get('ids') or [] if isinstance(i, str) and broker.playable(i, 'track')] \
            if isinstance(found, dict) else []
        title = str(found.get('title') or '').strip() if isinstance(found, dict) else ''
        if ids and title:
            out.append({'id': name[:-5], 'title': title[:120], 'artist': str(found.get('artist') or '').strip()[:120],
                        'uri': ids[0]})
    return sorted(out, key=lambda song: song['title'].lower())


def _words(text):
    return {w for w in ''.join(c.lower() if c.isalnum() else ' ' for c in str(text or '')).split() if w not in ('the', 'a')}


def matching(searches, every=None):
    """The songs with a show that the words name: a search whose words are all in a song's title and artist, or a
    song whose title's words are all in the search ("light show with levels"). Best first, no repeats."""
    every = songs() if every is None else every
    out = []
    for search in searches or ():
        said = _words(search)
        for song in every:
            title, both = _words(song['title']), _words(song['title'] + ' ' + song['artist'])
            if said and song not in out and (said <= both or (title and title <= said)):
                out.append(song)
    return out


def start_songs(chosen=None, *, login, via):
    """Start the show with songs that have a hand-made show: `chosen` (their ids, in that order) or, with none, all
    of them in a shuffled order. The first starts the show; the rest join its speaker's queue once it is on."""
    every = songs()
    if not every:
        raise control.Refused('No song has a light show yet.')
    by_id = {song['id']: song for song in every}
    picked = [by_id[i] for i in chosen if i in by_id] if chosen else random.sample(every, len(every))
    if not picked:
        raise control.Refused('That song has no light show.')
    picked = picked[:QUEUE]
    first, rest = picked[0], picked[1:]
    label = f'{first["title"]} and {len(rest)} more' if rest else first['title']
    result = start(first['uri'], 'track', label, login=login, via=via)
    if rest and result['done']:
        show = _show()
        control.once(show['player'], 'Light show', 'show_queue', f'Light show: {len(rest)} more songs queued',
                     [('music_assistant', 'play_media', {'entity_id': show['player'], 'media_id': song['uri'],
                                                         'media_type': 'track', 'enqueue': 'add'}) for song in rest],
                     login=login, via=via)
    result['songs'] = [song['title'] for song in picked]
    return result


def start(uri, kind, label, *, login, via):
    """Start the show with something Music Assistant found (`uri` of `kind`); `label` names it in the record."""
    if kind not in broker.SHOW_KINDS or not broker.playable(uri, kind):
        raise control.Refused('Hub starts a light show only with a playlist, album, artist or song it found.')
    show = _show()
    words = f'Light show with {label}' if label else 'Light show'
    return control.once(show['start'], 'Light show', 'show_start', words,
                        [('script', 'turn_on', {'entity_id': show['start'], 'variables': {'media_id': uri, 'media_type': kind}})],
                        login=login, via=via, check=lambda: _running(show))


def end(*, login, via):
    """End the show: Home Assistant stops the music and puts the lights back."""
    show = _show()
    if not show['on']:
        raise control.Refused('No light show is on.')
    return control.once(show['end'], 'Light show', 'show_end', 'Light show ended',
                        [('script', 'turn_on', {'entity_id': show['end']})], login=login, via=via)


def timing_words(ms):
    """How the lights sit against the music: "in time", "150 ms earlier", "100 ms later"."""
    ms = round(ms)
    return 'in time' if ms == 0 else f'{abs(ms)} ms {"earlier" if ms > 0 else "later"}'


def set_timing(wanted, *, login, via):
    """Set the lights `wanted` milliseconds earlier (above 0) or later (below 0) than the music, on one of the number's
    own steps and within its range. Higher is earlier: the show lights what the music reaches that much later. The
    card sends where it wants the timing, never a step from where it was, so quick taps add up. Answers with it."""
    if not isinstance(wanted, (int, float)) or isinstance(wanted, bool) or not math.isfinite(wanted):
        raise control.Refused('Say how much earlier or later.')
    show = _show()
    t = show['timing']
    if not t:
        raise control.Refused('Home Assistant has no light show timing.')
    value = min(t['max'], max(t['min'], t['min'] + round((wanted - t['min']) / t['step']) * t['step']))
    value = int(value) if float(value).is_integer() else value
    result = control.once(t['id'], 'Light show timing', 'show_timing', f'Light show lights {timing_words(value)}',
                          [('input_number', 'set_value', {'entity_id': t['id'], 'value': value})], login=login, via=via)
    result['timing'] = {'value': value, 'min': t['min'], 'max': t['max'], 'step': t['step']}
    return result
