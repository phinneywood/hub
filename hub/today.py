"""“Needs you” list for the Hub home page and the morning brief.

Each source in `engine.catalog` looks at what its tool already keeps locally and returns
zero or more recommendations. Everything here is read-only and calls no model; suggestions
from the overnight review are read from where it saved them. A recommendation links to the
tool's own preview page. It becomes one tap only when it names a catalog action whose step is
reversible and the owner's trust level for that action allows it. Sources fail soft.
"""
from concurrent.futures import wait
from datetime import date, timedelta

from . import home

MAX_ITEMS = 5
DEADLINE_SECONDS = 1.5
TRIP_HORIZON_DAYS = 120
RANK = {'bad': 0, 'warn': 1, 'ok': 2, 'info': 3}


def rec(key, tone, title, why, *, href, cta, action=None, suggested=False):
    """action: slug of a catalog action that performs this step. suggested: came from the overnight review."""
    return {'key': key, 'tone': tone, 'title': title, 'why': why, 'href': href, 'cta': cta, 'action': action,
            'post': None, 'suggested': suggested}


def _plural(count, word):
    return f'{count} {word}{"s" * (count != 1)}'


def gmail_rules():
    from . import delayed
    state = delayed.health()
    stopped, safe = state['stopped'], state['safe_to_resume']
    if not stopped:
        return []
    title = f'{_plural(stopped, "archive-after-30-days rule")} stopped'
    if safe == stopped:
        return [rec('gmail-rules', 'warn', title, 'They stopped before changing anything, so turning them back on is safe.',
                    href='/gmail/delayed', cta='Turn back on', action='gmail.resume_rules')]
    return [rec('gmail-rules', 'warn', title, 'At least one stopped part-way and needs a look before it runs again.',
                href='/gmail/delayed', cta='Review rules')]


def gmail_plan():
    from . import store
    from .tools.gmail import missions
    planned = store.plan_count()
    if not planned:
        return []
    if missions.is_paused():
        return [rec('gmail-plan', 'warn', f'{_plural(planned, "Gmail change")} waiting',
                    'Changes are paused or the Gmail helper isn’t answering.', href='/status', cta='Check status')]
    return [rec('gmail-plan', 'info', f'{_plural(planned, "Gmail change")} ready to apply',
                'You already decided these. Each one can be undone.', href='/gmail/plan', cta='Review and apply')]


def _names(people, limit=3):
    """“Dana Lee, Acme Dental and 2 more” from names, most recent first, each once."""
    people = list(dict.fromkeys(p for p in people if p))
    shown, more = people[:limit], len(people) - limit
    if more > 0:
        return ', '.join(shown) + f' and {more} more'
    return ' and '.join([', '.join(shown[:-1]), shown[-1]] if len(shown) > 1 else shown)


def gmail_triage():
    """What the last sort found. Titles hold counts and kinds only; names appear in the line under them."""
    from .tools.gmail import triage
    seen = triage.picture()
    if not seen['synced_at']:
        return []
    found, needs, odd, asked = [], seen['needs'], seen['odd'], [a for a in seen['asked'] if a['who']]
    if needs:
        first = needs[0]
        why = (f'{first["who"]}: {first["step"].rstrip(".")}.' if len(needs) == 1 and first['step']
               else f'From {_names(r["who"] for r in needs)}.')
        if seen['waiting']:
            count = len(seen['waiting'])
            why += (f' {"It has" if len(needs) == 1 else f"{count} have" if count > 1 else "One has"}'
                    f' waited {triage.WAITING_DAYS} days or more.')
        if seen['sorted_today']:
            why += f' Hub sorted {_plural(seen["sorted_today"], "other")} today.'
        found.append(rec('gmail-needs', 'warn' if seen['waiting'] else 'info',
                         f'{len(needs)} email{"s need" if len(needs) != 1 else " needs"} you', why, href='/gmail', cta='Open'))
    if odd:
        what = {'security': 'A security notice needs', 'charge': 'A large charge needs'}.get(odd[0]['odd'], 'A record needs')
        found.append(rec('gmail-odd', 'warn', f'{what} a look' if len(odd) == 1 else f'{len(odd)} records need a look',
                         f'From {_names(r["who"] for r in odd)}. Hub would normally file these quietly.', href='/gmail', cta='Look'))
    if asked:
        oldest = max(a['days'] for a in asked)
        found.append(rec('gmail-asked', 'info',
                         'No answer yet to an email you sent' if len(asked) == 1 else f'No answer yet to {len(asked)} emails you sent',
                         f'To {_names(a["who"] for a in asked)}, {"" if len(asked) == 1 else "the oldest "}{_plural(oldest, "day")} ago.',
                         href='/gmail', cta='See'))
    if seen['review']:
        found.append(rec('gmail-review', 'warn', 'Hub couldn’t confirm a filing change',
                         f'{_plural(seen["review"], "email")} may or may not have left the inbox. Hub files nothing more until you have looked.',
                         href='/gmail', cta='Check'))
    if seen['filed_today']:
        kinds = triage.counts_text({kind: entry['filed_today'] for kind, entry in seen['kinds'].items()})
        found.append(rec(GMAIL_FILED, 'info', f'Hub filed {_plural(seen["filed_today"], "email")}',
                         f'{kinds[:1].upper()}{kinds[1:]}. They are under their Hub labels, and any batch can be put back for 14 days.',
                         href='/gmail', cta='See'))
    return found


OLD_MAIL_WORTH = 500       # a smaller backlog than this isn't worth a bulk step
FILING_WORTH = 10          # sorted mail sitting in the inbox before Hub offers to file it
GMAIL_FILED = 'gmail-filed'
FYI = (GMAIL_FILED,)       # told, not asked: shown on the home page, left out of the morning message
GMAIL_NEXT = 'gmail-next'  # the one slot for Hub's own Gmail offers; each offer's key starts with it
WEEK_KEYS = (GMAIL_NEXT + '-',)  # “not now” hides these for a week: the same offer every morning is nagging


def gmail_rules_due():
    """Rules the owner turned on that nobody has run. Their standing decision, so it gets its own line."""
    from . import delayed
    from .tools.gmail import missions
    late = delayed.overdue()
    if not late['rules'] or missions.is_paused():
        return []
    verb = 'hasn’t' if late['rules'] == 1 else 'haven’t'
    return [rec('gmail-rules-due', 'info', f'{_plural(late["rules"], "archive rule")} {verb} run for {_plural(late["days"], "day")}',
                'They run only when asked. On the Engine page you can let Hub run them each morning.',
                href='/gmail/delayed', cta='Check now', action='gmail.run_rules')]


def gmail_next():
    """One offer for the inbox, the nearest to hand first. Reads only what Hub already keeps."""
    from . import store
    from .tools.gmail import filing, missions, nightly, triage
    hidden = store.snoozed(home.today().isoformat())
    if GMAIL_NEXT in hidden:
        return []
    offers, seen = [], triage.picture()
    if seen['synced_at'] and not seen['review']:
        levels = {kind: filing.trusted(kind) for kind in filing.LABELS}
        ready = sum(len(filing.ready(kind)) for kind, level in levels.items() if level == 'undo')
        waiting = sum(len(seen['kinds'][kind]['open']) for kind in filing.LABELS)
        if ready and not missions.is_paused():
            offers.append(rec(GMAIL_NEXT + '-file', 'info',
                              f'{_plural(ready, "sorted email")} {"is" if ready == 1 else "are"} ready to file',
                              'Mail that asks nothing of you, of the kinds you let Hub file. Each batch can be put back.',
                              href='/gmail', cta='File them'))
        elif waiting >= FILING_WORTH and set(levels.values()) == {'suggest'}:
            offers.append(rec(GMAIL_NEXT + '-filing', 'info', f'Hub can file {waiting} sorted emails for you',
                              'Receipts, codes, newsletters and promotions that ask nothing of you. Choose which kinds Hub may '
                              'take out of the inbox; each batch can be put back.', href='/gmail', cta='Choose'))
    backlog = nightly.saved_backlog()
    sweeping = any(i['decision'] == 'sweep' and i['status'] in ('planned', 'applying') for i in store.items())
    if backlog and backlog['count'] >= OLD_MAIL_WORTH and not sweeping:
        offers.append(rec(GMAIL_NEXT + '-backlog', 'info',
                          f'{nightly.backlog_text(backlog)} inbox messages are over {nightly.BACKLOG_DAYS} days old',
                          'One step archives them all, never starred mail. You see what it covers first, and every batch can be undone.',
                          href=nightly.BACKLOG_PREVIEW, cta='Preview'))
    return next(([offer] for offer in offers if offer['key'] not in hidden), [])


def hide(key):
    """“Not today”, or “not this week” for an offer that would only come back unchanged."""
    from . import store
    day = home.today()
    week = key.startswith(WEEK_KEYS)
    store.snooze(key, day.isoformat(), (day + timedelta(days=6)).isoformat() if week else None)
    if week:
        store.snooze(GMAIL_NEXT, day.isoformat())  # and no other Gmail offer takes its place today


def preschool():
    from preschool import publication
    current = publication.latest()
    if not current:
        return []
    now = home.today()
    soon = {now.isoformat(), (now + timedelta(days=1)).isoformat()}
    found = []
    for row in current['payload'].get('events', []):
        if row.get('date') not in soon:
            continue
        cancelled = row.get('status') == 'cancelled'
        parts = [home.day_label(row['date'], now), home.clock_label(row.get('start_time')), row.get('location') or '']
        found.append(rec(f'preschool-{row["date"]}-{row.get("title", "")}', 'warn' if cancelled else 'ok',
                         ('Cancelled: ' if cancelled else '') + row.get('title', 'Class date'),
                         ' · '.join(p for p in parts if p), href='/preschool/', cta='Class guide'))
    return found


def calendar():
    from .tools.calendar import read
    now = home.today()
    try:
        events = read.week()
    except read.SignInNeeded:
        return []  # the Calendar tile already reports this as a problem
    found = []
    for first, second in read.overlaps(events, {now, now + timedelta(days=1)}):
        found.append(rec(f'calendar-clash-{first["day"]}-{first["title"]}-{second["title"]}', 'warn',
                         f'Two things at once: {read.label(second)}',
                         f'{first["title"]} and {second["title"]} overlap.', href='/calendar', cta='See the day'))
    return found


def trip():
    from .tools.trip import store
    data = store.load()
    days = (date.fromisoformat(data['start']) - home.today()).days
    if data.get('selected_flight') or not 0 <= days <= TRIP_HORIZON_DAYS:
        return []
    return [rec('trip-flights', 'info', 'Choose flights', f'The trip is in {_plural(days, "day")} and nothing is selected.',
                href='/trip', cta='Compare flights')]


def from_tiles(tiles):
    """Problems the home tiles already found, so nothing is fetched twice."""
    return [rec('tile-' + t['slug'], 'bad', t['headline'], t['detail'], href=t['href'], cta=t.get('cta') or 'Details')
            for t in tiles if t['tone'] == 'bad']


def _checks():
    from .engine import catalog
    return [check for source in catalog.SOURCES for check in source.items or ()]


def start():
    """Begin every source's checks in the home pool; pair with collect()."""
    return [home._pool.submit(check) for check in _checks()]


def rule_items():
    """Every rule-based item, gathered in turn. For the overnight review; never raises."""
    found = []
    for check in _checks():
        try:
            found += check()
        except Exception:  # noqa: BLE001
            continue
    return found


def _settle(item):
    """One tap only for a reversible catalog action the owner trusts that far; otherwise a link to its page."""
    from .engine import catalog, state
    action = catalog.action(item['action']) if item['action'] else None
    if action and action.post and state.trust(action) != 'suggest':
        item['post'] = action.post
    elif action:
        item['cta'] = 'Review'
    item['later'] = 'Not this week' if item['key'].startswith(WEEK_KEYS) else 'Not today'
    return item


def collect(jobs, tiles=()):
    """Up to MAX_ITEMS recommendations, most urgent first, without anything snoozed. Never raises."""
    from . import store
    from .engine import state
    wait(jobs, timeout=DEADLINE_SECONDS)
    found = list(from_tiles(tiles))
    for job in jobs:
        if not job.done():
            continue
        try:
            found += job.result()
        except Exception:  # noqa: BLE001 - one broken source must not hide the others
            continue
    try:
        day = home.today().isoformat()
        found += [rec(s['key'], s.get('tone', 'info'), s['title'], s['why'], href=s['href'], cta=s['cta'],
                      suggested=s['key'].startswith('ai-')) for s in state.suggestions(day)]
        hidden = store.snoozed(day)
        found = [_settle(r) for r in found if r['key'] not in hidden]
    except Exception:  # noqa: BLE001 - without the store, items stay as plain links
        pass
    found.sort(key=lambda r: RANK.get(r['tone'], 9))
    return found[:MAX_ITEMS]


def needs_you(tiles=()):
    return collect(start(), tiles)


def brief(items):
    """Plain text for the morning message: titles only, never mail content. What Hub merely reports is left out."""
    items = [r for r in items if r['key'] not in FYI]
    if not items:
        return 'Nothing needs you today.'
    return '\n'.join([f'{_plural(len(items), "thing")} for today:'] + [f'• {r["title"]}' for r in items])
