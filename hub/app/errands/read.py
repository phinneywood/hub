"""The daily read: what wants something of the household, drafted by Luna and checked in code before it is kept.

It runs with the morning job, right after the mail is sorted, and when the owner taps "Look again". It looks at:
- the emails that need the owner (hub/tools/gmail/triage.py), from the last LOOK_DAYS;
- from the same days, bills and renewals that were sorted as records, and invitations sorted as calendar notices:
  they can need the owner as much as a question does;
- the class guide's to-dos for the next SCHOOL_DAYS: something to bring, sign or return.

Each item is read once. An email's text comes through the read-only bridge for this run only and is never kept; what
Luna sees of it is in draft.py, and senders with AI switched off are never read. Luna is called at most CALLS_PER_RUN
times a run and LUNA_CALLS_PER_DAY times a day, within the monthly limit every kind of run shares. An item whose
answer couldn't be used is tried again on a later run, TRIES times in all; an item Luna says needs nothing is kept as
a bare "skipped" row, without its text, so it isn't read again.

The mail is the bridge's main account's, and so are its errands (store.MAIN); the class guide's are the household's.
One call to Luna never holds two accounts' items, and the calendar's free times go only with that account's own mail,
so nothing of one person's mail can shape an errand someone else sees. The morning reads each account someone at home
owns (store.mapped(): one nobody owns isn't read, as nobody could see its errands); "Look again" reads the asker's
own. The class guide is read either way. The limits are the household's, whoever asks.
"""
import hashlib
import re
import threading
import time
from datetime import date, datetime, time as clock, timedelta

from ... import bridge, home, luna, store as hub_store
from ...engine import judge, state
from . import draft, links, store

KIND = 'errands'                 # these runs in the engine's record
LUNA_CALLS_PER_DAY = 6
CALLS_PER_RUN = 2
READS_PER_RUN = draft.ITEMS_PER_CALL * CALLS_PER_RUN
LOOK_DAYS = 14
SCHOOL_DAYS = 14
RUN_SECONDS = 120                # no new read or model call starts after this; the morning job has more to do
TRIES = 2
FREE_TIMES = 4
MAIL, SCHOOL = 'mail:', 'school:'
# Records worth reading: a bill or something to renew, not a receipt or a payment that already went through.
BILL = re.compile(r'\bbills?\b|invoice|amount due|payment (?:is )?due|balance due|due (?:date|on|by)|past due|overdue'
                  r'|renew|expir|membership|registration|tuition|statement (?:is )?(?:ready|available)', re.I)
PAID = re.compile(r'receipt|payment (?:received|confirm|processed|posted|successful|scheduled|sent)|thanks? (?:you )?for your'
                  r' payment|\bpaid\b|auto-?pay|(?:has been|was|is) renewed', re.I)
INVITE = re.compile(r'^invitation\b', re.I)       # a calendar invitation that waits for an answer, not "Accepted:"

_lock = threading.Lock()
_thread = {'t': None}


class _Spend:
    """What one run may still spend, and what it spent."""

    def __init__(self, day):
        from ...engine import overnight
        used = state.used(day, KIND)
        capped = used['month_cost'] >= overnight.MONTH_COST_CAP
        self.left = 0 if capped else max(0, min(CALLS_PER_RUN, LUNA_CALLS_PER_DAY - used['luna_calls']))
        self.calls = 0
        self.usage = {'tokens_in': 0, 'tokens_out': 0, 'cost': 0.0}
        self.deadline = time.monotonic() + RUN_SECONDS
        self.failed = False

    def late(self):
        return time.monotonic() > self.deadline

    def luna(self, request, transport):
        """One Luna call if the day, the month and the clock allow it. None when it didn't happen or failed."""
        if not self.left or self.late():
            return None
        self.left -= 1
        self.calls += 1
        try:
            reply, usage = (transport or luna.call)(request)
        except Exception:  # noqa: BLE001 - recorded as a failed run; the reason is not kept
            self.failed = True
            return None
        for key in self.usage:
            self.usage[key] += usage.get(key, 0)
        return reply if isinstance(reply, dict) else None


def _named(name, address):
    """Who an email is from, for a model: the name, or failing that the domain. Never the address."""
    return name if name and '@' not in name else (address or name or '').rpartition('@')[2]


def _mail(now, known, tries):
    """The emails worth reading, each sort newest first: what needs the owner, then bills, then invitations."""
    from ...tools.gmail import triage
    rows = triage._rows("received >= ? and kind in ('needs', 'record', 'calendar') and state in ('open', 'filing', 'filed')"
                        " order by received desc", (now - LOOK_DAYS * 86400,))
    picked = {'needs': [], 'record': [], 'calendar': []}
    for row in rows:
        key, subject = (store.MAIN, MAIL + row['id']), row['subject'] or ''
        if key in known or tries.get(key, 0) >= TRIES or hub_store.ai_blocked(row['sender']):
            continue
        if row['kind'] == 'needs' and row['state'] == 'open' and row['checked'] == 1:
            picked['needs'].append(row)
        elif row['kind'] == 'record' and BILL.search(subject) and not PAID.search(subject):
            picked['record'].append(row)
        elif row['kind'] == 'calendar' and INVITE.search(subject):
            picked['calendar'].append(row)
    return picked['needs'] + picked['record'] + picked['calendar']


def _from_mail(row, message):
    """One email as the drafting needs it. Its text is scrubbed of links and addresses and lives only in this run."""
    sender, answer = row['sender'], links.answer_to(message, row['sender'])
    text = re.sub(r'\n\s*\n\s*(?:\n\s*)+', '\n\n', draft.scrubbed(message.get('text') or '')).strip()[:draft.BODY]
    subject = ' '.join(str(row['subject'] or message.get('subject') or '').split())[:200]
    shown = luna.clip(judge.scrub(subject), 200)
    return {'key': MAIL + row['id'], 'account': store.MAIN, 'source': 'mail', 'sorted': row['kind'], 'sender': sender,
            'from': luna.clip(judge.scrub(_named(row['name'], sender)), 80), 'reply_to': answer,
            'subject': shown, 'received': datetime.fromtimestamp(row['received'], home.PACIFIC).date(), 'text': text,
            'said': shown + '\n' + text, 'links': links.links(message, sender),
            'message': {'id': row['id'], 'thread_id': row['thread_id'], 'sender': sender, 'name': row['name'] or '',
                        'subject': subject, 'reply_to': answer, 'received': row['received']}}


def school_key(row):
    """The errand key of one of the class guide's rows: the same to-do keeps it from one version of the guide to the
    next."""
    identity = (row.get('_keys') or [f'{row.get("date")}:{row.get("title")}'])[0]
    return SCHOOL + hashlib.sha256(str(identity).encode()).hexdigest()[:16]


def _school(today, known, tries):
    """The class guide's to-dos from today for SCHOOL_DAYS, as the drafting needs them. Already reviewed for the family."""
    from preschool import publication
    current = publication.latest()
    if not current:
        return []
    found, last = [], today + timedelta(days=SCHOOL_DAYS)
    for row in current['payload'].get('events') or []:
        if row.get('kind') != 'action' or row.get('status') == 'cancelled':
            continue
        try:
            day = date.fromisoformat(str(row.get('date')))
        except ValueError:
            continue
        key = school_key(row)
        if not today <= day <= last or (store.HOUSEHOLD, key) in known or tries.get((store.HOUSEHOLD, key), 0) >= TRIES:
            continue
        title = luna.clip(judge.scrub(row.get('title') or ''), 160)
        details = luna.clip(judge.scrub(row.get('details') or ''), draft.BODY)
        start, end = row.get('start_time') or None, row.get('end_time') or None
        at = home.clock_label(start) if start else (row.get('time_note') or '')[:80] or None
        found.append({'key': key, 'account': store.HOUSEHOLD, 'source': 'school', 'sorted': 'school', 'sender': '',
                      'from': 'the class guide',
                      'reply_to': '', 'subject': title, 'received': None, 'text': details, 'said': title + '\n' + details,
                      'links': [], 'message': None,
                      'school': {'date': day.isoformat(), 'time': at, 'start': start, 'end': end,
                                 'place': luna.clip(row.get('location') or '', draft.PLACE) or None}})
    return sorted(found, key=lambda c: c['school']['date'])


def free_times(now=None):
    """A few free stretches of the coming week, for proposing a booking: weekday mornings and afternoons with nothing
    busy in them, from tomorrow, one a day first. [] when the calendar can't be read."""
    from ...tools.calendar import read as calendar
    now = now or datetime.fromtimestamp(store.now(), home.PACIFIC)
    try:
        events = calendar.week(now)
    except Exception:  # noqa: BLE001 - a booking can be drafted without them
        return []
    days = []
    for ahead in range(1, calendar.DAYS):
        day = now.date() + timedelta(days=ahead)
        if day.weekday() >= 5 or any(e['busy'] and e['all_day'] and e['day'] <= day <= e['last_day'] for e in events):
            continue
        free = []
        for begins, ends, words in ((9, 12, '9 am to noon'), (13, 17, '1 to 5 pm')):
            start, end = (datetime.combine(day, clock(hour), home.PACIFIC) for hour in (begins, ends))
            if not any(e['busy'] and not e['all_day'] and e['start'] < end and e['end'] > start for e in events):
                free.append(f'{day:%A} {day:%B} {day.day}, {words}')
        if free:
            days.append(free)
    spread = [stretches[0] for stretches in days] + [s for stretches in days for s in stretches[1:]]
    return spread[:FREE_TIMES]


def _mailbox():
    """The main account's own address, so the Gmail and Calendar pages a tap opens are in the right account."""
    try:
        mailbox = str(bridge.get('/gmail/profile', ttl=300).get('mailbox') or '').strip().lower()
    except bridge.BridgeError:
        return
    if links.address(mailbox) == mailbox and mailbox != store.mailbox(store.MAIN):
        store.put_setting('mailbox:' + store.MAIN, mailbox)


def _keep(candidate, answer, today, now):
    """Check Luna's answer about one candidate and keep what passed. Returns 'made', 'skipped' or 'unusable'."""
    key, source, account = candidate['key'], candidate['source'], candidate['account']
    try:
        made = draft.check(answer, candidate, today)
    except draft.Unusable:
        store.tried(key, account, now)
        return 'unusable'
    if made is None:
        store.add(key, source, {'kind': draft.NOTHING}, account=account, state='skipped', now=now)
        return 'skipped'
    if made['facts'].get('autopay'):                      # nothing to do but know it: kept as done, said so
        store.add(key, source, made, account=account, state='done', how='autopay', message=candidate['message'], now=now)
        return 'made'
    store.add(key, source, made, account=account, message=candidate['message'], now=now)
    return 'made'


def _batches(candidates):
    """The candidates in calls to Luna of at most ITEMS_PER_CALL, each holding one account's items only."""
    by_account = {}
    for candidate in candidates:
        by_account.setdefault(candidate['account'], []).append(candidate)
    return [items[start:start + draft.ITEMS_PER_CALL] for items in by_account.values()
            for start in range(0, len(items), draft.ITEMS_PER_CALL)]


def run(transport=None, now=None, accounts=None):
    """Read what is new and draft its errands. `accounts`: whose mail to read (None: each one someone at home owns);
    the class guide's to-dos are read either way. Never raises; returns counts."""
    if not _lock.acquire(blocking=False):
        return {'status': 'busy'}
    try:
        return _run(transport, now, accounts)
    finally:
        _lock.release()


def _run(transport, now, accounts=None):
    now = store.now() if now is None else now
    today, day = home.today(), home.today().isoformat()
    accounts = store.mapped() if accounts is None else set(accounts)
    store.prune(now)
    if not luna.configured():
        return {'status': 'not_set_up'}
    spend = _Spend(day)
    found = {'made': 0, 'skipped': 0, 'unusable': 0}
    if not spend.left:
        state.record_run(day, KIND, 'limit')
        return {'status': 'limit', **found}
    known, tries = store.keys(), store.tries()
    candidates, reads = [], 0
    try:
        school = _school(today, known, tries)
    except Exception:  # noqa: BLE001 - the guide that can't be read leaves the mail to read
        school = []
    try:
        rows = _mail(now, known, tries) if store.MAIN in accounts else []     # the bridge reads the main account's only
    except Exception:  # noqa: BLE001
        rows, spend.failed = [], True
    if rows:
        _mailbox()
    for row in rows:
        if reads >= READS_PER_RUN or spend.late():
            break
        reads += 1
        try:
            message = bridge.get('/gmail/message', ttl=0, message_id=row['id'])
        except bridge.BridgeError as error:
            if error.status == 404:                    # gone from Gmail: nothing to do
                store.add(MAIL + row['id'], 'mail', {'kind': draft.NOTHING}, account=store.MAIN, state='skipped', now=now)
                continue
            spend.failed = True
            break
        candidates.append(_from_mail(row, message))
    candidates += school[:max(0, READS_PER_RUN - len(candidates))]
    free = free_times() if any(c['source'] == 'mail' for c in candidates) else []
    for batch in _batches(candidates):
        for n, candidate in enumerate(batch, 1):
            candidate['ref'] = f'i{n}'
        mine = free if batch[0]['account'] == store.MAIN else []      # the main calendar's free times: its own mail only
        reply = spend.luna(draft.request(batch, mine, today), transport)
        if reply is None:
            break
        answers = {}
        for item in reply.get('items') or []:
            if isinstance(item, dict) and isinstance(item.get('ref'), str):
                answers.setdefault(item['ref'], item)
        for candidate in batch:
            found[_keep(candidate, answers.get(candidate['ref']), today, now)] += 1
    status = 'error' if spend.failed else 'ok'
    state.record_run(day, KIND, status, luna_calls=spend.calls, **spend.usage)
    hub_store.log('errands read', f'{found["made"]} new')
    return {'status': status, 'read': len(candidates), **found}


def reading():
    thread = _thread['t']
    return bool(thread and thread.is_alive())


def read_soon(transport=None, accounts=None):
    """Read behind the page, so the phone doesn't wait. One at a time. Proof is looked for first. `accounts`: whose
    mail, as for run()."""
    def work():
        from . import watch
        try:
            watch.run()
            run(transport, accounts=accounts)
        except Exception:  # noqa: BLE001 - the screen says the read didn't finish; the reason is not kept
            hub_store.log('errands read failed')
    if not reading():
        thread = threading.Thread(target=work, name='errands-read', daemon=True)
        _thread['t'] = thread
        thread.start()


def wait(timeout=30):
    thread = _thread['t']
    if thread:
        thread.join(timeout)


def morning():
    """With the morning job, after the mail is sorted: close what is done, then read what is new."""
    from . import watch
    watch.run()
    run()
