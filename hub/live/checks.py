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
MAIN_PAGES = ('/', '/today', '/insights', '/tools', '/settings', '/engine', '/checks', '/house/music', '/house/changes')


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


def model():
    """One call with invented data over the same pinned route everything else uses."""
    from .. import actions, home, luna
    from ..engine import state
    if not luna.configured():
        raise Failed('The model key isn’t set up.')
    usage, ok = {}, False
    try:
        raw, usage = luna.call(luna.build_request(luna.build_messages(
            'shop@example.invalid', 12, ['Your order has shipped', 'Receipt for order 1234'], ['Receipts'],
            'Put these under Receipts')))
        actions.validate(raw, 'shop@example.invalid', [{'id': 'Label_1', 'name': 'Receipts', 'type': 'user'}])
        ok = True
    except luna.LunaError as error:
        raise Failed(str(error)) from None
    except actions.ActionError:
        raise Failed('The model answered, but not in the form Hub asked for.') from None
    finally:
        state.record_run(home.today().isoformat(), KIND, 'ok' if ok else 'error', luna_calls=1,
                         tokens_in=usage.get('tokens_in', 0), tokens_out=usage.get('tokens_out', 0), cost=usage.get('cost', 0.0))
    return 'The model answered on the private route, in the form Hub asked for.'


GOLDEN = ('what-needs-me-today', 'music-softly-in-one-room', 'lights-room-off', 'music-era-found-plays')
GOLDEN_CAP = 0.10


def ask():
    """A few golden scenarios through the real Ask with the real model, as a dry run: nothing is sent to the house."""
    from ..golden import runner
    try:
        result = runner.live(only=GOLDEN, cap=GOLDEN_CAP)
    except runner.Stopped as error:
        raise Skipped(str(error)) from None
    rows = result['scenarios']
    failed = [r['id'] for r in rows if r['status'] in ('fail', 'error')]
    ran = [r for r in rows if r['status'] != 'skipped']
    if failed:
        raise Failed(f'{len(failed)} of {len(ran)} Ask scenarios failed: ' + ', '.join(failed) + '.')
    if not ran:
        raise Skipped(result.get('stopped') or 'No scenario ran.')
    return f'{len(ran)} Ask scenarios passed as a dry run (${result["cost"]:.3f}).'


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


def light():
    """Turn the one light the owner named for this on or off, read it back, and put it back, through the same
    door a typed request uses (allowlist, record, Undo). The record shows it as `via` = check."""
    from ..tools.house import control
    entity = settings().get('undo_light')
    if not isinstance(entity, str) or not entity.startswith(('light.', 'switch.')):
        raise Skipped('No test light is named in Hub’s settings.')
    if quiet():
        raise Skipped('Quiet hours: nothing in the house is changed for a check.')
    try:
        device = control._known(control.devices(control.read.snapshot(fresh=True)), entity)
        action = 'off' if device['state'] == 'on' else 'on'
        done = control.change([entity], action, login=LOGIN, via='check')
    except control.Refused as error:
        raise Failed(str(error)) from None
    if done['not_sent']:
        raise Failed('The change to the test light wasn’t sent.')
    try:
        back = control.undo(done['batch'], login=LOGIN, via='check')
    except control.Refused as error:
        raise Failed(f'The test light changed but wasn’t put back: {error} See Home changes.') from None
    if not back['done']:
        raise Failed('The test light changed but putting it back wasn’t confirmed. See Home changes.')
    if not done['done']:
        raise Failed('The test light didn’t confirm the change. It was put back.')
    return 'The test light changed and was put back.'


CHECKS = (
    Check('hub.pages', 'Hub’s pages', 'Every main page and tool page of the running Hub loads, none slowly.', 'read', pages, 180),
    Check('house.read', 'Home Assistant', 'Hub can read the house through the broker.', 'read', house),
    Check('house.speakers', 'Speakers', 'Home Assistant lists speakers and at least one is reachable.', 'read', speakers),
    Check('music.search', 'Music search plays', 'Music Assistant finds music, and Hub would play what it finds.', 'read', music_search,
          seed='Hub refused every Apple Music item it had just found, so Ask could find a playlist but never play it.'),
    Check('music.key', 'Music Assistant key', 'Hub’s key is accepted and isn’t about to run out.', 'read', music_key),
    Check('mail.read', 'Gmail', 'The mail helper answers, Gmail is signed in, and sending is still impossible.', 'read', mail),
    Check('calendar.read', 'Calendar', 'The calendar week can be read.', 'read', calendar),
    Check('kitchen.answers', 'Kitchen', 'The Kitchen service answers.', 'read', kitchen),
    # Before the checks that call the model: the Ask check stands a dry-run layer in front of the house while it runs.
    Check('house.light', 'A real change, undone', 'One named light is switched and put straight back.', 'undo', light, 60),
    Check('model.route', 'Model route', 'One call with invented data over the pinned private route.', 'spend', model, 60),
    Check('ask.golden', 'Ask, end to end', 'A few real requests through Ask with the real model, as a dry run.', 'spend', ask, 240,
          seed='Ask passed a whole sentence to the speaker as a search; “as well” started a second stream.'),
)


def by_id(check_id):
    return next((c for c in CHECKS if c.id == check_id), None)
