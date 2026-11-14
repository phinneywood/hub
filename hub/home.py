"""Live, read-only summaries of each tool, for the cards on Today.

Each tile reads what its tool already reads (cached bridge responses, local
SQLite, Home Assistant states). Tiles run in parallel under one short deadline
and fail soft: a slow or broken source shows a quiet tile, never an error page.
Nothing here writes, schedules or calls a model.
"""
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo('America/Los_Angeles')
DEADLINE_SECONDS = 2.5
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='hub-home')


def tile(slug, tone, headline, detail='', *, items=(), cta=None, href=None, as_of=None, acts=()):
    """tone: ok | warn | bad | info | idle. as_of: how old the numbers are, e.g. 'Updated 12 min ago'.
    acts: one-tap buttons, each {'label', 'post', 'fields'}; a signed-in tap only."""
    return {'slug': slug, 'tone': tone, 'headline': headline, 'detail': detail,
            'lines': list(items), 'cta': cta, 'href': href or '/' + slug, 'as_of': as_of, 'acts': list(acts)}


def today():
    return datetime.now(PACIFIC).date()


def day_label(value, now=None):
    now = now or today()
    day = date.fromisoformat(value) if isinstance(value, str) else value
    delta = (day - now).days
    if delta == 0:
        return 'Today'
    if delta == 1:
        return 'Tomorrow'
    if 1 < delta < 7:
        return day.strftime('%A')
    return day.strftime('%a, %b %-d')


def clock_label(value):
    if not value:
        return ''
    try:
        moment = datetime.strptime(value, '%H:%M')
    except ValueError:
        return ''
    return moment.strftime('%-I:%M %p').replace(':00 ', ' ')


def gmail():
    from . import store
    from .tools.gmail import common, missions, triage
    ov = common.overview()
    common.track_inbox(ov)
    inbox = common.inbox_counts(ov) or {}
    total, unread = inbox.get('messagesTotal'), inbox.get('messagesUnread')
    planned, paused, seen = store.plan_count(), missions.is_paused(), triage.picture()
    needs, bits, items = len(seen['needs']), [], []
    if seen['synced_at']:
        headline = f'{needs} email{"s need" if needs != 1 else " needs"} you' if needs else 'Nothing needs you'
        if seen['waiting']:
            bits.append(f'{len(seen["waiting"])} waiting {triage.WAITING_DAYS} days or more')
        if seen['sorted_today']:
            bits.append(f'{seen["sorted_today"]} sorted today')
        if isinstance(total, int):
            bits.append(f'{total:,} in inbox')
        if seen['odd']:
            items.append({'label': f'{len(seen["odd"])} record{"s" * (len(seen["odd"]) != 1)} worth a look',
                          'note': 'A security notice or a large charge'})
        if seen['review']:
            items.append({'label': 'A filing change needs checking', 'note': 'Hub files nothing more until you have looked'})
    else:  # never sorted: the plain counts
        headline = f'{total:,} in inbox' if isinstance(total, int) else 'Inbox'
        if isinstance(unread, int):
            bits.append(f'{unread:,} unread')
    if planned:
        items.append({'label': f'{planned} change{"s" * (planned != 1)} planned',
                      'note': 'Waiting — changes are paused' if paused else 'Ready to apply'})
    tone = 'warn' if (paused and planned) or seen['waiting'] or seen['odd'] or seen['review'] else 'info'
    when = common.overview_fetched_at()
    return tile('gmail', tone, headline, ' · '.join(bits), items=items, cta='Open', href='/gmail',
                as_of='Updated ' + common.age_label(when) if when else None)


def preschool():
    from preschool import publication
    current = publication.latest()
    if not current:
        return tile('preschool', 'idle', 'No class guide yet', 'Prepare the first edition in Manage.',
                    cta='Manage', href='/preschool/manage')
    now = today()
    upcoming = [row for row in current['payload'].get('events', []) if row.get('date', '') >= now.isoformat()]
    if not upcoming:
        return tile('preschool', 'idle', 'Nothing coming up', 'No upcoming dates in the reviewed guide.')
    first, rest = upcoming[0], upcoming[1:3]

    def when(row):
        parts = [day_label(row['date'], now)]
        if row.get('start_time'):
            parts.append(clock_label(row['start_time']))
        return ' · '.join(parts)

    def line(row):
        cancelled = row.get('status') == 'cancelled'
        return {'label': row.get('title', ''), 'note': when(row) + (' · cancelled' if cancelled else ''),
                'struck': cancelled}

    detail = when(first)
    if first.get('location'):
        detail += ' · ' + first['location']
    tone = 'ok' if first['date'] == now.isoformat() else 'info'
    return tile('preschool', tone, first.get('title', 'Next class date'), detail,
                items=[line(row) for row in rest], cta='Coming up', href='/preschool/')


def network():
    from .tools.network import analytics as a, ha, pages, store
    if not ha.configured():
        return tile('network', 'idle', 'Not connected', 'Home Assistant token not set up.')
    states = ha.states()
    devices, metrics = pages._device_rows(states)
    sources, _ = a.select_sources(states, metrics, store.get('sources', {}))
    live = {role: pages.current(role, sources) for role in sources}
    reach = pages._source_status(sources.get('reachability'), live.get('reachability', False))
    wan = pages._source_status(sources.get('wan'), live.get('wan', False))
    primary = reach if 'reachability' in sources else wan
    latency = a.value(sources['latency']) if live.get('latency') else None
    loss = a.value(sources['loss']) if live.get('loss') else None
    facts = []
    if latency is not None:
        facts.append(f'{latency:.0f} ms ping')
    if loss:
        facts.append(f'{loss:.0f}% loss')
    if wan == 'Reported up' and primary != wan:
        facts.append('router link up')
    attention = [d for d in devices if d.get('attention')]
    items = [{'label': d['name'], 'note': d['status']} for d in attention[:2]]
    if primary == 'Reported down':
        return tile('network', 'bad', 'Internet looks down', ' · '.join(facts) or 'Check the router and modem.',
                    items=items, cta='Details')
    if primary == 'Reported up':
        tone = 'warn' if (latency or 0) > 100 or (loss or 0) > 0 or attention else 'ok'
        return tile('network', tone, 'Internet is up', ' · '.join(facts), items=items)
    return tile('network', 'warn', 'Readings are stale', 'Home Assistant hasn’t reported recently.', items=items,
                cta='Check sources')


def trip():
    from .tools.trip import store
    data = store.load()
    now = today()
    start, end = date.fromisoformat(data['start']), date.fromisoformat(data['end'])
    title = data.get('title', 'Trip').split(' · ')[0]
    span = f'{start:%b %-d}–{end:%-d}' if start.month == end.month else f'{start:%b %-d} – {end:%b %-d}'
    if now > end:
        return tile('trip', 'idle', title, f'Finished {end:%b %-d, %Y}')
    if now >= start:
        return tile('trip', 'ok', title, f'Day {(now - start).days + 1} of {(end - start).days + 1}', cta='Itinerary')
    days = (start - now).days
    countdown = f'In {days} day{"s" * (days != 1)}' if days < 60 else f'In {round(days / 7)} weeks'
    items = []
    if not data.get('selected_flight'):
        items.append({'label': 'Choose flights', 'note': 'Nothing booked yet'})
    return tile('trip', 'info', title, f'{countdown} · {span}', items=items,
                cta='Compare flights' if items else 'Open plan')


def mobility():
    return tile('mobility', 'idle', 'Plan a ride', 'Bike or Lime plus the bus, anywhere in town.', cta='Start')


def calendar():
    from .tools.calendar import read
    now = datetime.now(PACIFIC)
    try:
        events = read.week(now)
    except read.SignInNeeded:
        return tile('calendar', 'bad', 'Calendar needs signing in again',
                    'Run ./phone-setup --calendar-auth in Termius on the Mac mini.', cta='How')
    todays = [e for e in events if e['day'] <= now.date() <= e['last_day']]
    upcoming = [e for e in todays if not e['all_day'] and e['end'] > now]
    items = [{'label': e['title'], 'note': read.label(e, now)} for e in (upcoming or todays)[:2]]
    if not todays:
        nxt = next((e for e in events if e['day'] > now.date()), None)
        return tile('calendar', 'idle', 'Nothing today', f'Next: {nxt["title"]}, {read.label(nxt, now)}' if nxt else
                    'Nothing in the next week.')
    clash = read.overlaps(events, {now.date()})
    return tile('calendar', 'warn' if clash else 'ok', f'{len(todays)} today' if len(todays) != 1 else '1 thing today',
                'Two events overlap' if clash else '', items=items)


def kitchen():
    from .tools.kitchen import client
    try:
        _, data = client.call('GET', '/messages?after=999999999', timeout=2)  # plans and busy only
    except client.KitchenDown:
        return tile('kitchen', 'idle', 'Kitchen is offline', 'The kitchen service isn’t answering.')
    waiting = [p for p in data.get('plans', {}).values() if p.get('status') == 'pending']
    if waiting:
        return tile('kitchen', 'warn', 'Cart plan to approve', 'Open Kitchen to check it and tap Approve.', cta='Review')
    if data.get('busy'):
        return tile('kitchen', 'info', 'Working on it…', 'The assistant is on a request.')
    return tile('kitchen', 'idle', 'Kitchen', 'Shopping list, recipes and the Fred Meyer cart.', cta='Open')


def house():
    from .tools.house import broker, read
    link = read.links().get('ha_url')
    where = {'href': link or '/house/changes', 'cta': 'Open Home Assistant' if link else 'Changes'}
    try:
        found = read.snapshot()
    except broker.HomeDown:
        return tile('house', 'idle', 'Home isn’t reachable', 'Hub couldn’t read Home Assistant through the broker.', **where)
    issues = read.attention(found)
    playing = [d for d in found['speakers'] if d['on']]
    facts = [f'{d["name"]}: {d["detail"]}' if d['detail'] else d['name'] + ' playing' for d in playing[:1]]
    facts += [f'Thermostat {d["detail"].removeprefix("Now ")}' for d in found['climate'][:1] if d['detail']]
    if found['security'] and not any(d['open'] or d['state'] == 'jammed' for d in found['security']):
        facts.append('All locked and closed')
    lights = found['lights_on']
    acts = []
    if lights:
        acts.append({'label': 'All lights off', 'post': '/house/do', 'fields': {'target': 'lights-on', 'action': 'off'}})
    acts += [{'label': f'Pause {d["name"]}', 'post': '/house/do', 'fields': {'target': d['id'], 'action': 'pause'}}
             for d in playing[:1]]
    headline = f'{lights} light{"s" * (lights != 1)} on' if lights else 'All lights off'
    if issues:
        return tile('house', 'warn', issues[0][2], ' · '.join(facts), acts=acts,
                    items=[{'label': words, 'note': ''} for _, _, words in issues[1:3]], **where)
    return tile('house', 'ok' if lights or playing else 'idle', headline, ' · '.join(facts), acts=acts, **where)


SUMMARIES = {'kitchen': kitchen, 'house': house, 'gmail': gmail, 'calendar': calendar, 'preschool': preschool, 'network': network, 'trip': trip, 'mobility': mobility}
UNAVAILABLE = 'Couldn’t load a summary'


def tiles(tools):
    """One tile per tool, in nav order. Never raises."""
    jobs = {t['slug']: _pool.submit(SUMMARIES[t['slug']]) for t in tools if t['slug'] in SUMMARIES}
    wait(jobs.values(), timeout=DEADLINE_SECONDS)
    result = []
    for t in tools:
        job = jobs.get(t['slug'])
        if job is None:
            item = tile(t['slug'], 'idle', t['name'], t.get('description', ''))
        elif not job.done():
            item = tile(t['slug'], 'idle', 'Still loading…', 'Open it, or refresh in a moment.')
        else:
            try:
                item = job.result()
            except Exception:  # noqa: BLE001 - a broken source must not break the page
                item = tile(t['slug'], 'idle', UNAVAILABLE, t.get('description', ''))
        item['name'] = t['name']
        result.append(item)
    return result


def paused():
    """Gmail organizer pause state for the shell. Cached by the bridge; fails closed to paused."""
    from .tools.gmail import missions
    return missions.is_paused()


def greeting(now=None):
    now = now or datetime.now(PACIFIC)
    part = 'morning' if now.hour < 12 else 'afternoon' if now.hour < 17 else 'evening'
    return f'Good {part}', now.strftime('%A, %B %-d')
