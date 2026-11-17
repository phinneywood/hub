"""What a tap on an errand changes. Hub never sends, pays, books or orders anything.

- go: the owner opened one step of a way of finishing it: a reply in their mail app, a page, a call, the calendar.
  The app opens it itself, from the address picture.py built; this only notes it, so Hub knows what to look for.
- list: the one thing Hub does itself: it puts the errand's things on the household's shopping list
  (hub/app/kitchen.py), as the owner would by typing them, with Undo. It closes the errand once every step is taken.
- done, later (until tomorrow morning) and not ("Not this"), each with Undo (back).
An errand from an email that needs the owner closes that email with it on the Mail screen, and Undo opens it again.
Only someone who may see an errand may tap it (store.sees); to anyone else it isn't there.
"""
import time
from datetime import datetime, time as clock, timedelta

from ... import home
from ...engine import catalog, state
from . import draft, store, watch

LATER_HOUR = 7
LIST_ACTION = 'errands.list'


class Refused(Exception):
    """A tap that can't be done as asked; the words are for the owner."""


def morning(now=None):
    """Tomorrow at LATER_HOUR, Hub's time: when an errand set aside for later comes back."""
    moment = datetime.fromtimestamp(store.now() if now is None else now, home.PACIFIC)
    return datetime.combine(moment.date() + timedelta(days=1), clock(LATER_HOUR), home.PACIFIC).timestamp()


def _undo(errand, **more):
    """What Undo puts back: the errand as it was, and what else the tap changed."""
    return {'state': errand['state'], 'chose': errand['chose'], 'steps': errand['steps'], 'how': errand['how'],
            'until': errand['until'], 'opened_at': errand['opened_at'], 'ended_at': errand['ended_at'], **more}


def _close_email(errand):
    """Mark the email the errand came from as handled. True if this tap did it."""
    from ...tools.gmail import triage
    return bool(errand['message_id']) and errand['account'] == store.MAIN and triage.done(errand['message_id'])


def _choice(errand, body):
    try:
        n = int(body.get('choice'))
    except (TypeError, ValueError):
        raise Refused('Hub couldn’t tell which one you picked.') from None
    if not 0 <= n < len(errand['choices']):
        raise Refused('That choice isn’t there any more.')
    return n, errand['choices'][n]


def _taken(errand, n, kind):
    """The steps taken once this one is: a different way of finishing starts over."""
    taken = list(errand['steps']) if errand['chose'] == n else []
    return taken + ([kind] if kind not in taken else [])


def _go(errand, body, now):
    n, way = _choice(errand, body)
    kind = str(body.get('step') or '')
    if kind not in draft.steps(way) or kind == 'list':
        raise Refused('That step isn’t part of it.')
    store.update(errand['id'], state='opened', chose=n, steps=_taken(errand, n, kind), until=None,
                 opened_at=errand['opened_at'] if errand['state'] == 'opened' and errand['chose'] == n else now)
    return '', {}


def _list(errand, body, login, now):
    from .. import kitchen
    n, way = _choice(errand, body)
    if not way['buy']:
        raise Refused('There’s nothing to put on the list.')
    if state.trust(catalog.action(LIST_ACTION)) == 'suggest':
        raise Refused('Adding to the list from errands is off on the Engine page.')
    try:
        found = kitchen.add(way['buy'], login)
    except kitchen.Refused as error:
        raise Refused(str(error)) from None
    added = [i for i in found['added'] if isinstance(i.get('id'), int)]
    taken = _taken(errand, n, 'list')
    finished = all(kind in taken for kind in draft.steps(way))
    undo = _undo(errand, listed=[i['id'] for i in added], email=False)
    if finished:
        undo['email'] = _close_email(errand)
        store.update(errand['id'], state='done', how='list', chose=n, steps=taken, ended_at=now, until=None, undo=undo)
    else:
        store.update(errand['id'], state='opened', chose=n, steps=taken, opened_at=errand['opened_at'] or now, undo=undo)
    names = [i['name'] for i in added]
    said = ('Already on the list' if not names else f'{names[0][:1].upper()}{names[0][1:]} is on the list' if len(names) == 1
            else f'{len(names)} things are on the list')
    return said, {'undo': True}


def _done(errand, now):
    store.update(errand['id'], state='done', how='owner', ended_at=now, until=None,
                 undo=_undo(errand, email=_close_email(errand)))
    return 'Done', {'undo': True}


def _not(errand, now):
    """Not this: Hub's errand goes; the email stays as it was on the Mail screen."""
    store.update(errand['id'], state='dismissed', ended_at=now, until=None, undo=_undo(errand, email=False))
    return 'Gone from the list', {'undo': True}


def _later(errand, now):
    store.update(errand['id'], state='later', until=morning(now), undo=_undo(errand))
    return 'Back tomorrow morning', {'undo': True}


def _back(errand, login):
    from ...tools.gmail import triage
    from .. import kitchen
    was = errand['undo']
    if not was:
        raise Refused('There’s nothing to undo.')
    for item_id in was.get('listed') or ():
        try:
            kitchen.remove(item_id, login)
        except kitchen.Refused:
            continue                     # already off the list, or Kitchen isn't answering: the rest is put back
    if was.get('email') and errand['message_id'] and errand['account'] == store.MAIN:
        triage.reopen(errand['message_id'])
    store.update(errand['id'], undo=None, **{name: was.get(name) for name in
                                             ('state', 'chose', 'steps', 'how', 'until', 'opened_at', 'ended_at')})
    return 'Put back', {}


def tap(errand_id, do, body, login, now=None):
    """One tap. Returns (what to tell the owner, anything the app needs back). Raises Refused."""
    now = store.now() if now is None else now
    body = body if isinstance(body, dict) else {}
    try:
        errand = store.get(int(errand_id))
    except (TypeError, ValueError):
        errand = None
    if errand is None or errand['state'] == 'skipped' or not store.sees(login, errand):
        raise Refused('That errand isn’t there any more.')
    if do == 'back':
        return _back(errand, login)
    if errand['state'] not in store.OPEN:
        raise Refused('That errand is already finished.')
    if do == 'go':
        return _go(errand, body, now)
    if do == 'list':
        return _list(errand, body, login, now)
    if do == 'done':
        return _done(errand, now)
    if do == 'not':
        return _not(errand, now)
    if do == 'later':
        return _later(errand, now)
    raise Refused('Hub doesn’t know that tap.')


def look(now=None):
    """The owner came back to the screen: look for proof behind it, if anything waits for some."""
    if store.rows("state='opened' limit 1"):
        return watch.watch_soon(now)
    return False


