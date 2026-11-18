"""The trip dashboard: when the trip is, the plan's numbers, what is still to decide, book and get ready.

It reads the one saved trip (`hub/tools/trip/store.load`), read-only. Changing the trip stays in its workspace
(`/trip`), which this board links to: the workspace edits the whole revisioned document, and the board only shows it.
The household's, as the trip page is.
"""
from datetime import date

from ... import home, people

ID = 'trip'
NAME = 'Trip'
WHAT = 'When the trip is, what is decided, booked and still to do'
SHOWN = 8


def sees(login):
    return people.who(login) is not None


def _trip():
    from ...tools.trip import store
    try:
        return store.load()
    except (ValueError, OSError):
        return None


def _money(value):
    return f'${value:,.0f}' if value is not None else '—'


def _open(rows):
    return [r for r in rows if not r.get('done') and r.get('status') not in ('done', 'booked', 'confirmed', 'selected preference')]


def _row(record, today=None):
    when = ''
    if record.get('date'):
        when = home.day_label(record['date'], today) if today else record['date']
        if record.get('time'):
            when += ' ' + home.clock_label(record['time'])
    sub = ' · '.join(part for part in [when, record.get('status') or '', record.get('notes') or ''] if part)
    row = {'title': record['title'], 'sub': sub[:240]}
    if record.get('url'):
        row['href'] = record['url']
    return row


def picture(login, today):
    found = _trip()
    if found is None:
        return {'say': 'Can’t read the trip', 'sub': 'Hub couldn’t open the saved trip just now.',
                'blocks': [{'id': 'edit', 'title': 'Trip', 'kind': 'rows', 'rows': [{'title': 'The trip workspace', 'href': '/trip'}]}]}
    from ...tools.trip import domain, pages, store
    start, end = date.fromisoformat(found['start']), date.fromisoformat(found['end'])
    heading = found['title'].split(' · ')[0]
    away = (start - today).days
    say = (f'{heading} in {away} days' if away > 1 else f'{heading} tomorrow' if away == 1 else
           f'{heading}: on now' if today <= end else f'{heading} is behind you')
    records = {name: found.get('records', {}).get(name) or [] for name in ('itinerary', 'decisions', 'reservations', 'preparation', 'documents')}
    tiles = []
    plan = None
    if found.get('selected_scenario'):
        try:
            plan = domain.summarize(found, found['selected_scenario'])
        except (KeyError, StopIteration, ValueError):
            plan = None
    if plan:
        names = store.stay_names(found)
        tiles = [{'value': str(plan['first_nights']), 'label': f"nights, {names['first']}"},
                 {'value': str(plan['second_nights']), 'label': f"nights, {names['second']}"},
                 {'value': _money(plan['known_total']) if plan['known_total'] else '—',
                  'label': 'known stay cost' if plan['complete'] else 'stay cost, some unknown', 'tone': '' if plan['complete'] else 'warn'}]
    chosen = next((f for f in found.get('flights', []) if f['id'] == found.get('selected_flight')), None)
    tiles.append({'value': chosen['label'].split(' · ')[0] if chosen else 'None yet', 'label': 'flights chosen' if chosen else 'flights'})
    blocks = [
        {'id': 'when', 'title': 'When', 'kind': 'hero', 'big': pages.dates(found['start'], found['end']).title(), 'small': (found.get('notes') or '')[:240]},
        {'id': 'plan', 'title': 'The plan', 'kind': 'tiles', 'tiles': tiles},
        {'id': 'itinerary', 'title': 'Itinerary', 'kind': 'rows', 'rows': [_row(r, today) for r in sorted(records['itinerary'], key=lambda r: r.get('date') or '')][:SHOWN],
         'empty': 'Nothing on the itinerary yet.'},
        {'id': 'decide', 'title': 'Still to decide', 'kind': 'rows', 'rows': [_row(r) for r in _open(records['decisions'])][:SHOWN],
         'empty': 'Nothing left to decide.'},
        {'id': 'book', 'title': 'Bookings', 'kind': 'rows', 'rows': [_row(r) for r in records['reservations']][:SHOWN], 'empty': 'No bookings listed.'},
        {'id': 'ready', 'title': 'Getting ready', 'kind': 'rows', 'rows': [_row(r) for r in _open(records['preparation'])][:SHOWN],
         'empty': 'Everything is ready.'},
        {'id': 'documents', 'title': 'Links and documents', 'kind': 'rows', 'rows': [_row(r) for r in records['documents']][:SHOWN], 'empty': 'None saved.'},
        {'id': 'edit', 'title': 'Change the trip', 'kind': 'rows', 'rows': [
            {'title': 'The trip workspace', 'sub': 'Edit dates, flights, budget and every list. Changes are kept with their history.', 'href': '/trip'}]},
    ]
    left = len(_open(records['decisions']))
    return {'say': say, 'sub': f"{left} {'thing' if left == 1 else 'things'} still to decide. Planning, not booked." if left else 'Planning, not booked.',
            'blocks': blocks}


def line(login, today):
    """None: Today already shows the trip when it is near."""
    return None


def facts(login, today):
    found = _trip()
    if found is None:
        return {'note': 'Hub can’t read the trip right now.'}
    return {'start': found['start'], 'end': found['end'], 'flights_chosen': bool(found.get('selected_flight')),
            'still_to_decide': [r['title'] for r in _open(found.get('records', {}).get('decisions') or [])][:8]}
