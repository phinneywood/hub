"""What the live checks check. Each one asks the real system one question and answers in one plain sentence.

A check is a function that returns a sentence when all is well, raises `Failed` with the reason when it isn't,
or `Skipped` when it has nothing to check here (a connection that isn't set up). Any other exception is a
failure too, recorded by its type only. Sentences hold counts and times, never names, mail or music titles:
they are kept for months.

`effect` says what a check may do, and the runner only runs the effects it was allowed:
- `read`: looks, changes nothing and costs nothing. All of these run on every run.
- `spend`: makes model calls on the pinned route (a fraction of a cent to a few cents), changes nothing.
- `undo`: changes one thing the owner named for the purpose and puts it straight back. Off until the owner
  names it in the private settings, never during quiet hours.

A new connection or capability adds its check here, with the real failure it guards against as `seed`.
"""
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .. import config


class Failed(Exception):
    """The check ran and the answer was wrong. The message is the reason, safe to keep and show."""


class Skipped(Exception):
    """Nothing to check here. The message says why."""


@dataclass(frozen=True)
class Check:
    id: str
    name: str
    about: str
    effect: str                 # 'read', 'spend' or 'undo'
    run: Callable[[], str]
    seconds: int = 30           # how long it may take before it counts as failed
    seed: str = ''              # the real failure it guards against, if it came from one


# ---- Hub's own pages, asked of the running Hub ----

SLOW_SECONDS = 8
MAIN_PAGES = ('/', '/today', '/insights', '/tools', '/settings', '/engine', '/checks', '/house/music', '/house/changes',
              '/house/assistant', '/house/player')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _get(path, timeout=SLOW_SECONDS + 4):
    """(status, seconds) for one page of the Hub running on this Mac. A redirect is an answer, not followed."""
    opener = urllib.request.build_opener(_NoRedirect)
    started = time.monotonic()
    try:
        with opener.open(f'http://{config.HOST}:{config.PORT}{path}', timeout=timeout) as reply:
            reply.read()
            status = reply.status
    except urllib.error.HTTPError as error:
        status = error.code
    return status, time.monotonic() - started


def pages():
    from ..tools import TOOLS
    paths = list(dict.fromkeys(MAIN_PAGES + tuple('/' + tool['slug'] for tool in TOOLS)))
    bad, slow, slowest = [], [], (0.0, '')
    for path in paths:
        try:
            status, took = _get(path)
        except OSError:
            raise Failed(f'Hub didn’t answer on this Mac ({path}).') from None
        if status >= 400:
            bad.append(f'{path} ({status})')
        elif took > SLOW_SECONDS:
            slow.append(f'{path} ({took:.0f} s)')
        slowest = max(slowest, (took, path))
    if bad:
        raise Failed('Pages that didn’t load: ' + ', '.join(bad) + '.')
    if slow:
        raise Failed(f'Pages slower than {SLOW_SECONDS} s: ' + ', '.join(slow) + '.')
    return f'{len(paths)} pages loaded; the slowest was {slowest[1]} at {slowest[0]:.1f} s.'


# ---- Home Assistant, through the broker ----

def house():
    from ..tools.house import broker
    try:
        states, areas = broker.read('get_states', 'config/area_registry/list')
    except broker.HomeDown as error:
        raise Failed(str(error)) from None
    if not states:
        raise Failed('Home Assistant answered with no devices at all.')
    return f'Home Assistant answered: {len(states)} entities in {len(areas or ())} rooms.'


def speakers():
    from ..tools.house import broker, read
    try:
        found = read.snapshot(fresh=True)
    except broker.HomeDown as error:
        raise Failed(str(error)) from None
    players = found['speakers']
    if not players:
        raise Failed('Hub found no speakers in Home Assistant.')
    there = [d for d in players if d.get('state') not in ('unavailable', 'unknown')]
    if not there:
        raise Failed(f'All {len(players)} speakers are unavailable.')
    return f'{len(there)} of {len(players)} speakers are reachable.'


# ---- The door: Home Assistant inside Hub ----

SAMPLE = {'triggers': [{'trigger': 'time', 'at': '18:45:00'}], 'conditions': [],
          'actions': [{'action': 'light.turn_on', 'target': {'entity_id': 'light.hub_check_not_a_lamp'}}]}


def setup():
    """What Ask needs to change how the house is set up, asked of the real Home Assistant without changing it: the
    whole house reads the way Hub expects, Home Assistant still checks a routine the way Hub writes one, a kept
    scene and automation can be read back, and a move is planned from it all and not sent."""
    from ..tools.house import admin, broker, plans
    try:
        w = admin.world(fresh=True)
        core, verdicts, entries = broker.setup_read({'type': 'get_config'}, {'type': 'validate_config', **SAMPLE}, {'type': 'config_entries/get'})
        helpers = broker.setup_read(*({'type': f'{kind}/list'} for kind in broker.HELPERS))
        boards = admin.dashboards()
    except broker.Rejected:
        raise Failed('Home Assistant refused a read that Ask’s setup tools depend on.') from None
    except broker.HomeDown:
        raise Failed('Home Assistant couldn’t be read through the broker.') from None
    things = [t for t in w['things'].values() if not t['disabled']]
    if not w['rooms'] or not things:
        raise Failed('Home Assistant listed no rooms or no devices.')
    if not isinstance(verdicts, dict) or any(not isinstance(verdicts.get(part), dict) or verdicts[part].get('valid') is not True for part in SAMPLE):
        raise Failed('Home Assistant no longer checks a routine the way Hub writes one, so Ask couldn’t save an automation.')
    if not isinstance(core, dict) or not core.get('time_zone') or not isinstance(entries, list) or not all(isinstance(h, list) for h in helpers) \
            or not isinstance(boards, list):
        raise Failed('Home Assistant’s own settings, integrations, helpers or dashboards didn’t read as lists Hub knows.')
    kept = 0
    for kind in ('scene', 'automation'):
        one = next((t for t in things if t['domain'] == kind and t['config']), None)
        if one is None:
            continue
        try:
            found = broker.setup_config(kind, one['config'])
        except broker.HomeDown:
            raise Failed(f'A kept {kind} couldn’t be read back.') from None
        if not isinstance(found, dict) or not ({'entities'} if kind == 'scene' else {'actions', 'action', 'use_blueprint'}) & set(found):
            raise Failed(f'A kept {kind} read back in a form Hub doesn’t know.')
        kept += 1
    lamp = next((t for t in sorted(things, key=lambda t: t['id']) if t['domain'] in ('light', 'switch', 'fan') and t['area'] and not t['minor']), None)
    other = next((r for r in sorted(w['rooms'].values(), key=lambda r: r['id']) if lamp and r['id'] != lamp['area']), None)
    planned = 0
    if lamp and other:
        try:
            moves = plans.arrange(w, [lamp['id']], room=other['id'])
        except admin.Refused:
            raise Failed('Hub couldn’t plan moving a thing to another room.') from None
        steps = [s['ws'] for change in moves for s in change['steps']]
        if not steps or any(s.get('type') not in ('config/entity_registry/update', 'config/device_registry/update') for s in steps):
            raise Failed('A planned move didn’t come out as registry changes.')
        try:
            for step in steps:
                broker._setup_checked(step)             # the fence's own look at each request; nothing is sent
        except ValueError:
            raise Failed('A planned move wouldn’t pass Hub’s own fence.') from None
        planned = len(steps)
    return (f'{len(w["rooms"])} rooms and {len(things)} things read. Home Assistant {str(core.get("version") or "")[:12]} checks a routine '
            f'as Hub writes one. {kept} kept routines read back; a move was planned in {planned} steps and not sent.')


def _door():
    from .. import door
    chosen = door.settings()
    if chosen is None:
        raise Skipped('The door to Home Assistant isn’t set up.')
    return door, chosen


def door_gate():
    """The door is up, and a request with no Tailscale login (this check itself) is turned away."""
    door, _ = _door()
    request = urllib.request.Request(f'http://{config.HOST}:{door.PORT}/', headers={'Host': 'door.invalid'})
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=8) as reply:
            raise Failed(f'The door let a request with no login through ({reply.status}).')
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise Failed(f'The door answered {error.code} to a request with no login; it should refuse with 403.') from None
    except OSError:
        raise Failed('The door isn’t listening on this Mac.') from None
    return 'The door is up and turns away a request with no login.'


def door_sign_in():
    """Home Assistant offers this Mac its no-password sign-in first, so the Home screen never shows a login."""
    import json
    _, chosen = _door()
    try:
        with urllib.request.urlopen(chosen['upstream'] + '/auth/providers', timeout=8) as reply:
            found = json.loads(reply.read(65536))
    except (OSError, ValueError):
        raise Failed('Home Assistant didn’t answer at the door’s address for it.') from None
    providers = found.get('providers') if isinstance(found, dict) else found
    kinds = [p.get('type') for p in providers or () if isinstance(p, dict)]
    if not kinds or kinds[0] != 'trusted_networks':
        raise Failed('Home Assistant would ask for a password: its trusted-address sign-in isn’t offered first to this Mac.')
    return 'Home Assistant signs this Mac in without a password.'


# ---- Music Assistant, through its own API ----

SEARCHES = (('love', 'playlist'), ('love', 'track'))


def _music():
    from ..tools.house import music
    if not music.ready():
        raise Skipped('Music Assistant’s address isn’t in Hub’s settings.')
    return music


def music_search():
    music = _music()
    from ..tools.house import broker
    found = 0
    for words, kind in SEARCHES:
        try:
            hits = music.search(words, (kind,), 3)[kind]
        except music.MusicDown as error:
            raise Failed(str(error)) from None
        if not hits:
            raise Failed(f'A plain {kind} search found nothing.')
        unplayable = [h for h in hits if not broker.playable(h.get('uri'), kind)]
        if unplayable:
            raise Failed(f'{len(unplayable)} of {len(hits)} {kind}s found have an address Hub would refuse to play.')
        found += len(hits)
    return f'Search answered, and Hub would play all {found} items it found.'


def music_key():
    music = _music()
    from datetime import date
    music._expiry.update(at=float('-inf'))     # ask now, not the answer remembered from this morning
    ends = music.key_ends()
    if ends is None:
        raise Failed('Hub couldn’t read when its Music Assistant key ends.')
    days = (ends - date.today()).days
    if days < music.WARN_DAYS:
        raise Failed(f'Hub’s Music Assistant key ends in {days} days.')
    return f'Hub’s Music Assistant key is accepted and lasts {days} more days.'


# ---- Mail, calendar and Kitchen ----

def mail():
    from .. import bridge
    if not bridge.health():
        raise Failed('The mail and calendar helper on this Mac isn’t answering.')
    try:
        profile = bridge.get('/gmail/profile', ttl=0)
    except bridge.BridgeError as error:
        raise Failed(f'Gmail couldn’t be read: {type(error).__name__}.') from None
    if profile.get('sending_available') is not False:
        raise Failed('The mail helper says it could send mail. It never should.')
    return 'Gmail answered, and the helper still can’t send.'


def calendar():
    from .. import bridge
    from ..tools.calendar import read
    bridge.clear_cache()
    try:
        read.week()
    except read.SignInNeeded:
        raise Failed('The calendar’s Google sign-in has lapsed.') from None
    except bridge.BridgeError:
        raise Failed('The calendar couldn’t be read.') from None
    return 'The calendar week was read.'


def caretaker_alive():
    """The caretaker's status file says it looked at the house in the last five minutes."""
    from .. import caretaker
    if not caretaker.set_up():
        raise Skipped('The caretaker isn’t set up on this Mac.')
    watching, words = caretaker.alive()
    if not watching:
        raise Failed(words)
    return words


def kitchen():
    from ..tools.kitchen import client
    if not config.KITCHEN_SOCKET.exists():
        raise Skipped('Kitchen isn’t set up on this Mac.')
    try:
        status, _ = client.call('GET', '/status', timeout=8)
    except client.KitchenDown:
        raise Failed('Kitchen isn’t answering.') from None
    if status != 200:
        raise Failed(f'Kitchen answered {status}.')
    return 'Kitchen answered.'


# ---- The model route ----

KIND = 'checks'             # these calls in the engine's record of runs
BACKOFF = (20.0, 60.0, 120.0)   # a model that is rate-limited or overloaded is asked again after each of these waits
BUSY = (429, 503, 529)      # what the route answers when its model is rate-limited or overloaded: not a failure of Hub
MODEL_SECONDS = 300


def model():
    """One call with invented data over the same pinned route everything else uses. A busy model is asked again,
    later each time; busy every time, the check says nothing either way (skipped), so it can't stop a deploy."""
    from .. import luna
    if not luna.configured():
        raise Failed('The model key isn’t set up.')
    until = time.monotonic() + MODEL_SECONDS - 10
    for wait in (*BACKOFF, None):
        try:
            return _model_once()
        except luna.LunaError as error:
            if error.status not in BUSY:
                raise Failed(str(error)) from None
            if wait is None or time.monotonic() + wait + luna.TIMEOUT > until:
                raise Skipped('Inconclusive: the model was busy (rate limit or overload) every time it was asked.') from None
        time.sleep(wait)


def _model_once():
    from .. import actions, home, luna
    from ..engine import state
    usage, ok = {}, False
    try:
        raw, usage = luna.call(luna.build_request(luna.build_messages(
            'shop@example.invalid', 12, ['Your order has shipped', 'Receipt for order 1234'], ['Receipts'],
            'Put these under Receipts')))
        actions.validate(raw, 'shop@example.invalid', [{'id': 'Label_1', 'name': 'Receipts', 'type': 'user'}])
        ok = True
    except actions.ActionError:
        raise Failed('The model answered, but not in the form Hub asked for.') from None
    finally:
        state.record_run(home.today().isoformat(), KIND, 'ok' if ok else 'error', luna_calls=1,
                         tokens_in=usage.get('tokens_in', 0), tokens_out=usage.get('tokens_out', 0), cost=usage.get('cost', 0.0))
    return 'The model answered on the private route, in the form Hub asked for.'


def ask():
    """Everyday requests through the Ask the owner uses (hub/app/ask) with the real model, on the real house, as a dry
    run: nothing is sent. Their playlists, playing one, a room's lights, the weather (`trial.core`). Until 2026-11-18
    this ran the older chat's golden scenarios, which said nothing about the Ask in the app."""
    from ..app.ask import trial
    try:
        result = trial.run('core', within=ASK_SECONDS - 20)
    except trial.Stopped as error:
        raise Skipped(str(error)) from None
    if result['failed']:
        raise Failed(f'{len(result["failed"])} of {result["ran"]} Ask requests went wrong: ' + ', '.join(result['failed'])
                     + f'. What Ask did is in Hub’s private folder (${result["cost"]:.3f}).')
    if not result['ran']:
        raise Skipped('Inconclusive: the model was busy or out of reach every time it was asked, so nothing was checked.'
                      if result['busy'] else 'No request ran.')
    busy = f' {len(result["busy"])} left out: the model was busy.' if result['busy'] else ''
    return f'{result["ran"]} everyday Ask requests went the right way as a dry run (${result["cost"]:.3f}).{busy}'


ASK_SECONDS = 360              # four requests, and waits for a busy model on top
ASK_SETUP_DAYS = 30
ASK_SETUP_SECONDS = 450


def ask_setup():
    """A few setup and list requests through Ask in the app with the real model, on the real house, as a dry run:
    every change is written down and none is sent. Once it has passed on a version it isn't asked again for a
    month, so it costs a few cents per release, not per day."""
    from ..app.ask import trial
    from . import runner
    last = (runner.history(limit=1).get('ask.setup') or [None])[-1]
    if last and last['status'] == 'pass' and last['version'] == runner.version() and time.time() - last['at'] < ASK_SETUP_DAYS * 86400:
        raise Skipped('It passed on this version already.')
    try:
        result = trial.run(within=ASK_SETUP_SECONDS - 20)
    except trial.Stopped as error:
        raise Skipped(str(error)) from None
    if result['failed']:
        raise Failed(f'{len(result["failed"])} of {result["ran"]} requests went wrong: ' + ', '.join(result['failed'])
                     + f'. What Ask did is in Hub’s private folder (${result["cost"]:.3f}).')
    if result.get('busy'):          # not a pass: it would not be asked again on this version for a month
        raise Skipped(f'Inconclusive: the model was busy for {len(result["busy"])} of the requests, so this version isn’t checked yet.')
    if not result['ran']:
        raise Skipped('No request ran.')
    left = f' {len(result["left_out"])} left out at the spending limit.' if result['left_out'] else ''
    return f'{result["ran"]} setup and list requests went the right way as a dry run (${result["cost"]:.3f}).{left}'


# ---- One real change, put straight back ----

LOGIN = 'live-check@hub.invalid'    # who "asks" for a check's change, so Home changes shows it as a test's
QUIET = (21, 8)             # no real change from 9 PM to 8 AM, whatever the settings say


def settings():
    from .. import household
    try:
        return household.section('checks')
    except ValueError:
        return {}


def quiet(hour=None):
    from datetime import datetime
    from .. import home
    hour = datetime.now(home.PACIFIC).hour if hour is None else hour
    start, end = QUIET
    return hour >= start or hour < end


SETTLE = 3.0                # seconds the light must hold as Hub left it; an automation that fights it acts sooner
NEAR = 2                    # brightness, in percent, that counts as the same when read back


def _held(entity):
    """The light as Home Assistant has it now, read fresh: {'state', 'level'}, or None when it can't be read."""
    from ..tools.house import broker, control
    try:
        found = control.devices(control.read.snapshot(fresh=True)).get(entity)
    except broker.HomeDown:
        return None
    return {'state': found['state'], 'level': found['level']} if found else None


def _same(now, before):
    if not now or now['state'] != before['state']:
        return False
    return before['state'] != 'on' or now['level'] is None or before['level'] is None or abs(now['level'] - before['level']) <= NEAR


def light():
    """Turn the one light the owner named for this on or off, and put it back, through the same door a typed request
    uses (allowlist, record, Undo). The record shows it as `via` = check. Each step is read back SETTLE seconds later,
    so a light that something else switches straight back (an automation fighting it) fails rather than passes, and
    a light that isn't back as it was is set back again before the check says so."""
    from ..tools.house import control
    entity = settings().get('undo_light')
    if not isinstance(entity, str) or not entity.startswith(('light.', 'switch.')):
        raise Skipped('No test light is named in Hub’s settings.')
    if quiet():
        raise Skipped('Quiet hours: nothing in the house is changed for a check.')
    try:
        device = control._known(control.devices(control.read.snapshot(fresh=True)), entity)
        before = {'state': device['state'], 'level': device['level']}
        if before['state'] not in ('on', 'off'):
            raise Failed(f'The test light isn’t answering in Home Assistant ({before["state"]}).')
        action = 'off' if before['state'] == 'on' else 'on'
        done = control.change([entity], action, login=LOGIN, via='check')
    except control.Refused as error:
        raise Failed(str(error)) from None
    if done['not_sent']:
        raise Failed('The change to the test light wasn’t sent.')
    time.sleep(SETTLE)
    held = _held(entity)
    try:
        back = control.undo(done['batch'], login=LOGIN, via='check')
    except control.Refused as error:
        raise Failed(f'The test light changed but wasn’t put back: {error} See Home changes.') from None
    time.sleep(SETTLE)
    if not _same(_held(entity), before):
        again = 'off' if before['state'] == 'off' else 'on'
        level = before['level'] if again == 'on' else None
        try:
            control.change([entity], again, value=level, login=LOGIN, via='check')
        except control.Refused:
            pass
        raise Failed(f'The test light wasn’t back as it was {SETTLE:.0f} s after it was put back, so Hub set it '
                     f'{again} again. See Home changes.')
    if not back['done']:
        raise Failed('The test light changed but putting it back wasn’t confirmed. See Home changes.')
    if not done['done']:
        raise Failed('The test light didn’t confirm the change. It was put back.')
    if not held or held['state'] != action:
        raise Failed(f'The test light didn’t stay {action}: something switched it back within {SETTLE:.0f} s, so an '
                     'automation or scene may fight it. Name a light nothing else switches. It was put back.')
    return f'The test light changed, held for {SETTLE:.0f} s and was put back as it was.'


CHECKS = (
    Check('hub.pages', 'Hub’s pages', 'Every main page and tool page of the running Hub loads, none slowly.', 'read', pages, 180),
    Check('house.read', 'Home Assistant', 'Hub can read the house through the broker.', 'read', house),
    Check('house.speakers', 'Speakers', 'Home Assistant lists speakers and at least one is reachable.', 'read', speakers),
    Check('house.setup', 'Ask can set up the house', 'Everything Ask’s setup tools read from Home Assistant reads as Hub expects, '
          'and a change is planned from it without being sent.', 'read', setup,
          seed='A newer Home Assistant stopped saying how a thing is named, and a rename would have been written twice.'),
    Check('door.gate', 'The door is shut to strangers', 'The door to Home Assistant is up and refuses a request with no login.', 'read', door_gate),
    Check('door.sign_in', 'No second login', 'Home Assistant signs this Mac in without a password, so Home and Music open straight away.',
          'read', door_sign_in),
    Check('music.search', 'Music search plays', 'Music Assistant finds music, and Hub would play what it finds.', 'read', music_search,
          seed='Hub refused every Apple Music item it had just found, so Ask could find a playlist but never play it.'),
    Check('music.key', 'Music Assistant key', 'Hub’s key is accepted and isn’t about to run out.', 'read', music_key),
    Check('mail.read', 'Gmail', 'The mail helper answers, Gmail is signed in, and sending is still impossible.', 'read', mail),
    Check('calendar.read', 'Calendar', 'The calendar week can be read.', 'read', calendar),
    Check('kitchen.answers', 'Kitchen', 'The Kitchen service answers.', 'read', kitchen),
    Check('caretaker.alive', 'Caretaker', 'The house’s caretaker looked at the house in the last five minutes.', 'read',
          caretaker_alive),
    # Before the checks that call the model: the Ask check stands a dry-run layer in front of the house while it runs.
    Check('house.light', 'A real change, undone', 'One named light is switched, holds, and is put back as it was.', 'undo', light, 60,
          seed='The test lamp had an automation that switched it straight back, and the check passed on what Home Assistant accepted.'),
    Check('model.route', 'Model route', 'One call with invented data over the pinned private route.', 'spend', model, MODEL_SECONDS),
    Check('ask.golden', 'Ask, end to end', 'Everyday requests through Ask in the app with the real model, as a dry run: your '
          'playlists, playing one, a room’s lights and the weather.', 'spend', ask, ASK_SECONDS,
          seed='Ask couldn’t say what playlists the owner has, and the old check tested the older chat instead.'),
    Check('ask.setup', 'Ask sets up the house, end to end', 'A move, an automation, a removal, an unlock and the shopping list asked of '
          'Ask with the real model on the real house, as a dry run: nothing is sent.', 'spend', ask_setup, ASK_SETUP_SECONDS),
)


def by_id(check_id):
    return next((c for c in CHECKS if c.id == check_id), None)
