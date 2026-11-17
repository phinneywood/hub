"""Closing errands by themselves: Hub looks for proof that what a tap opened was finished.

Proof is read, never asked for, and only as much as it takes to see it:
- a reply: a message the owner sent to that address since the email came (Gmail's sent mail, through the read-only
  bridge: dates only, never text);
- an event: an event that day on the owner's calendar at the same time, or with a word of its title in common
  (titles and times only);
- a payment, renewal, booking or form: an email from the same site since, already sorted by Hub, whose subject says
  it went through ("Payment received", "Your appointment is confirmed");
- the email marked as handled on the Mail screen.
A way of finishing with more than one step (a reply and the calendar) closes once one step is seen to be done and
the owner has opened the others. A call can't be seen, so it waits for the owner's Done. An errand set aside comes
back when its time comes; one whose event or class day has passed, or that waited STALE_DAYS, is let go as missed (a
bill keeps waiting past its due date, as it still needs paying). A run makes at most WATCH_READS bridge reads; an
errand nobody has tapped is looked at every CHECK_EVERY.

Proof is looked for only in the account the errand came from: the main account's sent mail, calendar and sorted mail
for its own errands (store.MAIN). A household errand (the class guide's) is never closed by what one person's mail or
calendar shows; a tap closes it, or its day passing. An account no one at home owns (store.mapped()) isn't read for
proof: nobody could see what it closed.
"""
import re
import threading
import time
from datetime import date, datetime, time as clock, timedelta

from ... import bridge, home
from . import draft, links, store

WATCH_READS = 10
CHECK_EVERY = 6 * 3600
STALE_DAYS = 21
SOON_SECONDS = 90                # the screen asks for a look at most this often
CONFIRMS = {
    'pay': r'payment|paid|receipt|thank you|confirm',
    'renew': r'renew|receipt|confirm|thank you|payment|welcome back',
    'book': r'confirm|booked|scheduled|appointment|reservation|see you',
    'sign': r'signed|complet|received|submitted|thank you',
    'rsvp': r'rsvp|confirm|thank you|going|registered|see you|you.re in',
    'buy': r'order|receipt|confirm',
}
COMMON = {'with', 'from', 'your', 'this', 'that', 'event', 'meeting', 'appointment', 'call', 'about', 'party', 'class',
          'reminder', 'invitation', 'visit'}
PROOF = {'mail': 'replied', 'calendar': 'calendar', 'open': 'confirmed'}

_lock = threading.Lock()
_soon = {'at': 0.0, 't': None}


class _Reads:
    """The bridge reads a run may still make; none once Gmail or the calendar didn't answer."""

    def __init__(self, most):
        self.left, self.down = most, False

    def read(self, what, *args):
        if self.down or self.left <= 0:
            return False
        self.left -= 1
        try:
            return what(*args)
        except bridge.BridgeError:
            self.down = True             # nothing is assumed; the next run looks again
            return False


def _when(value):
    from ...tools.gmail import triage
    return triage._when(value)


def _words(text):
    return {w for w in re.findall(r'[a-z]{4,}', str(text or '').lower()) if w not in COMMON}


def sent_to(to, since, now):
    """Whether the owner has sent anything to this address since `since`. Dates only."""
    days = max(1, int((now - since) // 86400) + 2)
    found = bridge.get('/gmail/search', ttl=0, query=f'in:sent to:{to} newer_than:{days}d', limit=10)
    return any(_when(m.get('date')) > since for m in found.get('messages') or [])


def on_calendar(event):
    """Whether the owner's calendar holds this event on its day: the same start, or a word of its title in common."""
    day = date.fromisoformat(event['date'])
    start = datetime.combine(day, clock(), home.PACIFIC)
    reply = bridge.get('/calendar/events', ttl=0, timeout=15, start=start.isoformat(), end=(start + timedelta(days=1)).isoformat(),
                       time_zone=home.PACIFIC.key, limit=50)
    words = _words(event['title'])
    for raw in reply.get('events') or []:
        if raw.get('status') == 'cancelled':
            continue
        begins = (raw.get('start') or {}).get('dateTime')
        if event.get('start') and begins:
            at = datetime.fromisoformat(begins.replace('Z', '+00:00')).astimezone(home.PACIFIC)
            if at.date() == day and at.strftime('%H:%M') == event['start']:
                return True
        if words & _words(raw.get('summary')):
            return True
    return False


def confirmed(errand, since):
    """An email from the errand's own site since `since`, sorted by Hub, whose subject says it went through."""
    from ...tools.gmail import triage
    words, mine = CONFIRMS.get(errand['kind']), links.site(str(errand['sender'] or '').rpartition('@')[2])
    if not words or not mine or mine in links.PERSONAL:
        return False
    return any(links.site(row['sender'].rpartition('@')[2]) == mine and re.search(words, row['subject'] or '', re.I)
               for row in triage._rows('received > ? and id != ?', (since, errand['message_id'] or '')))


def chosen(errand):
    """The way of finishing the owner opened, or None."""
    choices, n = errand['choices'], errand['chose']
    return choices[n] if errand['state'] == 'opened' and n is not None and 0 <= n < len(choices) else None


def _since(errand):
    return errand['received'] or errand['created_at']


def finish(errand, how, now=None):
    """Close an errand as done, and the email it came from with it."""
    from ...tools.gmail import triage
    now = store.now() if now is None else now
    store.update(errand['id'], state='done', how=how, ended_at=now, until=None)
    if errand['message_id'] and errand['account'] == store.MAIN:        # Hub's sorted mail is the main account's
        row = triage.get(errand['message_id'])
        if row and row['state'] == 'open':
            triage.done(errand['message_id'])


def _quiet(errand, today, now):
    """What changes without a look outside: back from later, missed, handled on the Mail screen. True if it ended."""
    from ...tools.gmail import triage
    if errand['state'] == 'later':
        if (errand['until'] or 0) > now:
            return False
        store.update(errand['id'], state='ready', until=None)
        errand['state'] = 'ready'
    event = errand['facts'].get('event') or {}
    on = event.get('date') or errand['due']
    past = bool(on) and on < today.isoformat() and (errand['kind'] in ('rsvp', 'date') or errand['source'] == 'school')
    if past or now - errand['created_at'] > STALE_DAYS * 86400:
        if errand['state'] == 'opened' and errand['how'] in PROOF.values():
            finish(errand, errand['how'], now)       # part of it was seen done, and that was the part that mattered
        else:
            store.update(errand['id'], state='missed', ended_at=now)
        return True
    row = triage.get(errand['message_id']) if errand['message_id'] and errand['account'] == store.MAIN else None
    if row and row['state'] == 'done':
        store.update(errand['id'], state='done', how='handled', ended_at=now)
        return True
    return False


def _proof(errand, now, reads):
    """(how the outside shows the errand finished, or None; the first step seen done, or None).
    How: 'replied', 'calendar' or 'confirmed'."""
    event, since = errand['facts'].get('event'), _since(errand)
    look = errand['state'] == 'opened' or now - (errand['checked_at'] or 0) >= CHECK_EVERY

    def seen(kind):
        if kind == 'mail':
            return bool(errand['reply_to']) and look and reads.read(sent_to, errand['reply_to'], since, now)
        if kind == 'calendar':
            return bool(event) and look and reads.read(on_calendar, event)
        if kind == 'open':
            return confirmed(errand, errand['opened_at'] or since)
        return False

    way = chosen(errand)
    if way is None:                       # nothing tapped yet: done in the usual way for its kind, outside Hub
        usual = {'reply': ('mail',), 'rsvp': ('mail', 'open'), 'date': ('calendar',)}.get(errand['kind'], ('open',))
        found = next((PROOF[kind] for kind in usual if seen(kind)), None)
        return found, found
    steps = draft.steps(way)
    already = {kind for kind, how in PROOF.items() if how == errand['how']}
    proven = [kind for kind in steps if kind in already or seen(kind)]
    first = PROOF[proven[0]] if proven else None
    return (first if proven and all(kind in proven or kind in errand['steps'] for kind in steps) else None), first


def run(now=None):
    """Look for proof on every open errand, within WATCH_READS. Never raises; returns how many ended."""
    if not _lock.acquire(blocking=False):
        return 0
    try:
        return _run(store.now() if now is None else now)
    except Exception:  # noqa: BLE001 - what closed stays closed; the rest is looked at next time
        return 0
    finally:
        _lock.release()


def _run(now):
    today, ended, reads, owned = home.today(), 0, _Reads(WATCH_READS), store.mapped()
    errands = store.rows("state in ('ready', 'opened', 'later')")
    errands.sort(key=lambda e: (e['state'] != 'opened', e['checked_at'] or 0))      # what was just tapped first
    for errand in errands:
        if _quiet(errand, today, now):
            ended += 1
            continue
        if errand['state'] == 'later' or errand['account'] != store.MAIN or store.MAIN not in owned:   # its own account's mail only
            continue
        left = reads.left
        proof, part = _proof(errand, now, reads)
        if reads.left < left:
            store.update(errand['id'], checked_at=now)
        if proof:
            finish(errand, proof, now)
            ended += 1
        elif part and part != errand['how']:      # one step is seen done; the card says so, and it counts if time runs out
            store.update(errand['id'], how=part)
    return ended


def looking():
    thread = _soon['t']
    return bool(thread and thread.is_alive())


def watch_soon(now=None):
    """A look behind the screen, at most every SOON_SECONDS. True if one started."""
    now = store.now() if now is None else now
    if looking() or now - _soon['at'] < SOON_SECONDS:
        return False
    _soon['at'] = now
    thread = threading.Thread(target=run, name='errands-watch', daemon=True)
    _soon['t'] = thread
    thread.start()
    return True


def wait(timeout=30):
    thread = _soon['t']
    if thread:
        thread.join(timeout)
