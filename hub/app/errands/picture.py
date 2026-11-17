"""What the app, Today and Ask see of errands, from Hub's own table: no Gmail call and no model.

Each errand is a card: what to do, one line of why, the facts that were found in the email, the sentence it rests on,
and one to three ways to finish it. A way is one or more steps, each opening one exact place with everything filled
in (links.py builds every address from what was checked when the errand was made), or, for the shopping list, the
one thing Hub does itself. A way with a single step is the button itself; one with more opens a short list of them.

Everything here is for one person, `login` (the Tailscale login of the device asking): only the errands they may see
(store.sees), their own setting for where replies open, and Gmail and Google Calendar opened in their own account.
"""
import re
import time
from datetime import date, datetime

from ... import home, luna, people, store as hub_store, today as hub_today
from ...engine import catalog, judge, state
from . import draft, links, read, store, taps, watch

TODAY_MOST = 3                   # errands on Today; the rest are a tap away
RECENT_DAYS = 7                  # how long a finished errand stays under "Done lately"
LOOKUP_MOST = 8
NAMES = {'reply': 'Reply', 'rsvp': 'Invitation', 'pay': 'Bill', 'book': 'Booking', 'renew': 'Renewal', 'sign': 'Form',
         'buy': 'To get', 'date': 'Date'}
ENDINGS = {'replied': 'You replied', 'calendar': 'On your calendar', 'confirmed': 'Confirmed by email',
           'handled': 'Handled in Mail', 'owner': 'Done', 'list': 'On the shopping list', 'autopay': 'Paid automatically'}
SEEN = ('replied', 'calendar', 'confirmed', 'handled')        # how an errand ends when Hub saw it done, not a tap
LOOKING_FOR = {'mail': 'your reply', 'calendar': 'it on your calendar', 'open': 'a confirmation email'}
STILL = {'calendar': 'Add it to your calendar too, or tap Done.', 'mail': 'Send the reply too, or tap Done.',
         'open': 'Finish it on their site too, or tap Done.', 'call': 'Call them too, or tap Done.',
         'list': 'Put it on the list too, or tap Done.'}
COMPOSE = ('mail', 'gmail')


def compose(login):
    """Where this person's drafted replies open: the device's own mail app (mailto:), or Gmail on the web. The setting
    belongs to their account; one with none has nothing to reply to."""
    mine = store.accounts_of(login)
    return store.compose(mine[0]) if mine else 'mail'


def set_compose(login, writes):
    """Where this person's replies open from now on: 'mail' or 'gmail', for each account of theirs. Returns what to
    tell them, or None when Hub reads no account of theirs."""
    mine = store.accounts_of(login)
    for account in mine:
        store.put_setting('compose:' + account, writes)
    if not mine:
        return None
    return 'Replies open in Gmail' if writes == 'gmail' else 'Replies open in your mail app'


def own(login):
    """Whether Hub reads a Google account of this person's, so their own mail can make errands for them."""
    return bool(store.accounts_of(login))


def _opens_in(errand, login):
    """The Google account Gmail and Calendar open in for this errand: the one its email came to, or for the
    household's, the asker's own (when Hub knows its address). '' lets Google pick."""
    if errand['account'] != store.HOUSEHOLD:
        return store.mailbox(errand['account'])
    return next((store.mailbox(a) for a in store.accounts_of(login) if store.mailbox(a)), '')


def _hour(value):
    """'14:30' as '2:30', and its half of the day."""
    moment = datetime.strptime(value, '%H:%M')
    return moment.strftime('%-I:%M').removesuffix(':00'), moment.strftime('%p').lower()


def times(start, end):
    """'2 pm', '2–4 pm', '11 am–1 pm' or ''."""
    if not start:
        return ''
    first, half = _hour(start)
    if not end:
        return f'{first} {half}'
    last, other = _hour(end)
    return f'{first}–{last} {half}' if half == other else f'{first} {half}–{last} {other}'


def _day(value, now):
    day = date.fromisoformat(value)
    gap = (day - now).days
    return 'today' if gap == 0 else 'tomorrow' if gap == 1 else day.strftime('%a, %b %-d')


def when(event, now):
    """'Sat, Nov 22, 2–4 pm', 'Tomorrow, 9 am' or 'Fri, Nov 21'."""
    words = _day(event['date'], now)
    hours = times(event.get('start'), event.get('end'))
    return words[:1].upper() + words[1:] + (f', {hours}' if hours else '')


def due(errand, now):
    """{'text', 'tone'}: when it is due, and how pressing that is; None when it has no date."""
    on = errand['due'] or ((errand['facts'].get('event') or {}).get('date'))
    if not on:
        return None
    day = date.fromisoformat(on)
    gap = (day - now).days
    tone = 'bad' if gap < 0 else 'warn' if gap <= 1 else ''
    if errand['kind'] == 'date':
        words = 'Was ' + day.strftime('%b %-d') if gap < 0 else home.day_label(day, now)
    elif gap < 0:
        words = 'Was due ' + ('yesterday' if gap == -1 else day.strftime('%b %-d'))
    else:
        words = 'Due ' + ('today' if gap == 0 else 'tomorrow' if gap == 1 else day.strftime('%A') if gap < 7 else day.strftime('%b %-d'))
    return {'text': words, 'tone': tone}


def money(amount):
    return f'${amount:,.2f}'.removesuffix('.00')


def facts(errand, now):
    """Short facts found in the email itself: the amount, when, where."""
    found, said = [], errand['facts']
    if said.get('amount'):
        found.append(money(said['amount']))
    if said.get('autopay'):
        found.append('Autopay')
    event = said.get('event')
    if event:
        found.append(when(event, now))
        if event.get('place'):
            found.append(event['place'])
    return found


def _who(errand):
    """Who it is from, as the owner would say it: "Sam Lee", not "Sam Lee (Google Calendar)"."""
    if errand['source'] == 'school':
        return 'Class guide'
    name = re.sub(r'\s*\((?:google calendar|via [^)]*)\)\s*$', '', errand['sender_name'] or '', flags=re.I).strip()
    return name or (errand['sender'] or '').rpartition('@')[2] or 'Email'


def _email(errand, account):
    """Where the email or the to-do it came from can be read."""
    if errand['source'] == 'school':
        return {'href': '/preschool/', 'label': 'Open the class guide'}
    if not errand['thread_id']:
        return None
    query = f'?authuser={account}' if account else ''
    return {'href': f'https://mail.google.com/mail/{query}#all/{errand["thread_id"]}', 'label': 'Open the email'}


def _step(errand, way, kind, account, writes, now):
    """One step of a way of finishing, with the exact address it opens. `label` says the step on its own (the button
    on a card); `go` is its button under the step in the errand's sheet, where the label is the heading."""
    if kind == 'mail':
        to, about = errand['reply_to'], links.subject(errand['subject'])
        href = links.gmail(to, about, way['reply'], account) if writes == 'gmail' else links.mailto(to, about, way['reply'])
        app = 'Gmail' if writes == 'gmail' else 'your mail app'
        return {'kind': kind, 'label': f'Reply to {_who(errand)}'[:40], 'go': f'Open in {app}', 'href': href,
                'detail': way['reply'], 'app': app}
    if kind == 'open':
        link = way['link']
        said = link.get('label') if link.get('label') and link.get('label') != link['site'] else ''
        return {'kind': kind, 'label': f'Open {link["host"]}', 'go': 'Open the page', 'href': link['url'],
                'detail': f'“{said}” in the email' if said else 'The sender’s own site', 'app': link['host']}
    if kind == 'call':
        return {'kind': kind, 'label': f'Call {links.phone_label(way["call"])}', 'go': 'Call', 'href': links.call(way['call']),
                'detail': 'The number in the email', 'app': 'the phone'}
    if kind == 'calendar':
        event = errand['facts']['event']
        details = errand['quote'] or errand['title']
        return {'kind': kind, 'label': 'Add to calendar', 'go': 'Open Google Calendar', 'href': links.calendar(event, details, account),
                'detail': ' · '.join(p for p in (event['title'], when(event, now), event.get('place')) if p),
                'app': 'Google Calendar'}
    if state.trust(catalog.action(taps.LIST_ACTION)) == 'suggest':
        return {'kind': 'open', 'label': 'Open Kitchen', 'go': 'Open Kitchen', 'href': '/app/#kitchen', 'detail': ', '.join(way['buy']),
                'app': 'Kitchen'}
    return {'kind': kind, 'label': 'Add to the shopping list', 'go': 'Add to the list', 'href': None, 'detail': ', '.join(way['buy']),
            'app': 'Kitchen'}


def _ways(errand, account, writes, now):
    found = []
    for n, way in enumerate(errand['choices']):
        taken = errand['steps'] if errand['state'] == 'opened' and errand['chose'] == n else []
        steps = [dict(_step(errand, way, kind, account, writes, now), taken=kind in taken) for kind in draft.steps(way)]
        found.append({'n': n, 'label': way['label'], 'steps': steps, 'chosen': errand['state'] == 'opened' and errand['chose'] == n})
    return found


def _ago(moment, now):
    minutes = int(max(0, now - moment) // 60)
    if minutes < 1:
        return 'just now'
    if minutes < 60:
        return f'{minutes} min ago'
    hours = minutes // 60
    if hours < 24:
        return f'{hours} hour{"s" * (hours != 1)} ago'
    return datetime.fromtimestamp(moment, home.PACIFIC).strftime('%a %-I:%M %p').replace(':00 ', ' ').replace('AM', 'am').replace('PM', 'pm')


def _opened(errand, ways, now):
    """What an errand the owner started says: when, and what Hub is looking for (or saw) to close it."""
    way = next((w for w in ways if w['chosen']), None)
    if not way:
        return None
    seen = {'replied': 'mail', 'calendar': 'calendar', 'confirmed': 'open'}.get(errand['how'] or '')
    left = [s for s in way['steps'] if not s['taken'] and s['kind'] != seen]
    if seen:
        told = ENDINGS[errand['how']] + '. ' + (STILL.get(left[0]['kind'], 'Tap Done once it’s all done.') if left else 'Tap Done.')
    else:                       # the household's errands close by a tap: no one's own mail is read for them
        proof = next((LOOKING_FOR[s['kind']] for s in way['steps'] if s['kind'] in LOOKING_FOR
                      and (s['kind'] != 'mail' or errand['reply_to'])), None) if errand['account'] == store.MAIN else None
        told = f'Hub closes this when it sees {proof}.' if proof else 'Tap Done once it’s done.'
    return {'label': f'Opened {_ago(errand["opened_at"] or now, now)}', 'watch': told, 'way': way['n'], 'left': len(left)}


def card(errand, now=None, login=None, writes=None):
    """One errand as the app draws it for this person. Its replies open where its own account's setting says."""
    now_s = store.now() if now is None else now
    day = datetime.fromtimestamp(now_s, home.PACIFIC).date()
    account = _opens_in(errand, login)
    writes = writes or (store.compose(errand['account']) if errand['account'] != store.HOUSEHOLD else compose(login))
    ways = _ways(errand, account, writes, day) if errand['state'] in store.OPEN else []
    ended = errand['state'] in store.ENDED
    return {'id': errand['id'], 'key': f'errand-{errand["id"]}', 'kind': errand['kind'], 'name': NAMES.get(errand['kind'], ''),
            'title': errand['title'], 'why': errand['why'], 'quote': errand['quote'], 'from': _who(errand),
            'state': errand['state'], 'due': None if ended else due(errand, day), 'facts': facts(errand, day),
            'ways': ways, 'opened': _opened(errand, ways, now_s) if errand['state'] == 'opened' else None,
            'email': _email(errand, account), 'family': errand['account'] == store.HOUSEHOLD,
            'ended': ({'text': ENDINGS.get(errand['how'] or '', 'Done') if errand['state'] == 'done' else
                       'Not needed' if errand['state'] == 'dismissed' else 'Missed',
                       'when': _ago(errand['ended_at'] or now_s, now_s),
                       'seen': errand['state'] == 'done' and errand['how'] in SEEN} if ended else None),
            'until': 'Back tomorrow morning' if errand['state'] == 'later' else None}


def _rank(errand, day):
    """Most pressing first: due within a day (or overdue), then what the owner started, then by date, newest."""
    on = errand['due'] or (errand['facts'].get('event') or {}).get('date') or '9999-12-31'
    soon = on <= date.fromordinal(day.toordinal() + 1).isoformat()
    return (not soon, errand['state'] != 'opened', on, -errand['created_at'])


def ready(now=None, most=None, login=None):
    """The errands waiting for this person, most pressing first, as cards."""
    now = store.now() if now is None else now
    day = datetime.fromtimestamp(now, home.PACIFIC).date()
    rows = sorted(store.rows_for(login, "state in ('ready', 'opened')"), key=lambda e: _rank(e, day))
    return [card(e, now, login) for e in rows[:most]]


def screen(now=None, login=None):
    """The Errands screen for this person: what is ready, what was set aside, what finished lately, how the reading
    went, and whether Hub reads a Google account of theirs (`own`; without one, only the household's are here)."""
    now = store.now() if now is None else now
    later = sorted(store.rows_for(login, "state='later'"), key=lambda e: e['until'] or 0)
    ended = store.rows_for(login, "state in ('done', 'dismissed', 'missed') and ended_at >= ? order by ended_at desc",
                           (now - RECENT_DAYS * 86400,))[:20]
    last = state.runs(read.KIND, limit=1)
    looked = last[0] if last else None
    return {'ok': True, 'ready': ready(now, login=login), 'later': [card(e, now, login) for e in later],
            'done': [card(e, now, login) for e in ended], 'reading': read.reading(), 'checking': watch.looking(),
            'looked': ({'text': 'Looked ' + _ago(looked['ts'], time.time()), 'failed': looked['status'] == 'error'} if looked else None),
            'set_up': luna.configured(), 'compose': compose(login), 'own': own(login), 'signed_in': bool(login)}


def today_part(now=None, login=None):
    """For Today: this person's few most pressing errands and how many more there are."""
    found = ready(now, login=login)
    return {'cards': found[:TODAY_MOST], 'more': max(0, len(found) - TODAY_MOST)}


def items(login=None):
    """Hub's "Needs you" list: one line for the errands this person may see, its title counts only, as mail's is. The
    list is built with no one signed in, so by default only the household's are counted."""
    rows = store.rows_for(login, "state in ('ready', 'opened')")
    if not rows:
        return []
    day = home.today()
    rows.sort(key=lambda e: _rank(e, day))
    pressing = any((due(e, day) or {}).get('tone') for e in rows)
    shown = [e['title'] for e in rows if not hub_store.ai_blocked(e['sender'] or '')][:3]
    more = len(rows) - len(shown)
    why = '; '.join(shown) + (f'; and {more} more.' if more > 0 and shown else '.' if shown else '')
    count = len(rows)
    return [hub_today.rec('errands', 'warn' if pressing else 'info',
                          f'{count} errand{"s" * (count != 1)} ready for your tap', why or 'Drafted from your mail.',
                          href='/app/#errands', cta='Open')]


def _what(errand):
    return luna.clip(judge.scrub(errand['title']), 80)


def titles(rows):
    """These errands' titles as Ask's model may be told them, as lookup gives them: scrubbed and clipped, and none from
    a sender with AI switched off."""
    return [_what(e) for e in rows if not hub_store.ai_blocked(e['sender'] or '')][:LOOKUP_MOST]


def lookup(show: str = 'ready', login: str | None = None) -> dict:
    """Errands Hub got ready for the person asking from their own mail and the class guide: replies drafted,
    invitations, bills with the page to pay them on, bookings, renewals, forms, things to buy and dates. show: ready
    (waiting for a tap, most pressing first) or done (finished in the last week, and how). Hub never sends, pays or
    books anything itself; each errand is finished by a tap on its card."""
    now, day = store.now(), home.today()
    if show not in ('ready', 'done'):
        return {'error': 'show must be ready or done'}
    if show == 'ready':
        rows = sorted(store.rows_for(login, "state in ('ready', 'opened')"), key=lambda e: _rank(e, day))
    else:
        rows = store.rows_for(login, "state in ('done', 'dismissed', 'missed') and ended_at >= ? order by ended_at desc",
                              (now - RECENT_DAYS * 86400,))
    rows = [e for e in rows if not hub_store.ai_blocked(e['sender'] or '')]
    found = []
    for errand in rows[:LOOKUP_MOST]:
        entry = {'ref': errand['id'], 'what': _what(errand),
                 'why': luna.clip(judge.scrub(errand['why']), 180), 'from': luna.clip(judge.scrub(_who(errand)), 60),
                 'kind': NAMES.get(errand['kind'], errand['kind'])}
        if show == 'ready':
            said = due(errand, day)
            entry.update({'due': said['text'] if said else None, 'ways': [w['label'] for w in errand['choices']],
                          'started': errand['state'] == 'opened'})
        else:
            entry['ended'] = ENDINGS.get(errand['how'] or '', 'Done') if errand['state'] == 'done' else errand['state']
        found.append(entry)
    return {'count': len(rows), 'errands': found, **({'not_shown': len(rows) - LOOKUP_MOST} if len(rows) > LOOKUP_MOST else {})}
