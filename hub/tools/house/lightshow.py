"""The music light show, Hub's side of it.

Home Assistant runs the show with two scripts and a number, which Hub finds by their names (NAMES): "Start light
show" plays something Music Assistant found on one speaker and switches the show on, "End light show" stops it, and
"Light show timing" moves the lights earlier or later, in milliseconds. Which speaker plays the show and which switch
says it is on, Hub reads from the start script as Home Assistant keeps it. Hub may run those two scripts and set
that number, and nothing else of theirs: it never rewrites them. Each name must belong to exactly one entity, and
the start script must name one switch and one speaker, or Hub has no show.

Starting, ending and timing are recorded with the house's other changes, never with an Undo: ending the show is the
way back.
"""
import math
import threading
import time

from . import broker, control, read

NAMES = {'start': ('script', 'Start light show'), 'end': ('script', 'End light show'),
         'timing': ('input_number', 'Light show timing')}
WIRING_SECONDS = 600         # how long what the start script says is trusted
MISS_SECONDS = 60            # a start script that couldn't be read, or says too little, is asked again after this
WIRING_TIMEOUT = 2.0         # reading it never holds up a reading of the house for long
CHECKS, CHECK_WAIT = 10, 0.5  # after starting: the show on and its speaker playing; a script answers before it runs

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
