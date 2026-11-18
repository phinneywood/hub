"""The school dashboard: the class guide's coming dates, what changed or was cancelled, and its saved links.

It reads the class guide as it was last published for the family (`preschool/publication.latest`), read-only:
dates, titles, times and places, the guide's topic pages by name and its links. Reviewing what goes into the guide
stays on the class guide's own pages (`/preschool/`), which this board links to. No child's name or detail is
added here; the guide's own reviewed text is shown as it is published. The household's, as the guide is.
"""
from datetime import date, timedelta

from ... import home, people

ID = 'school'
NAME = 'School'
WHAT = 'The class guide’s coming dates, changes and links'
AHEAD = 30
SHOWN = 10


def sees(login):
    return people.who(login) is not None


def _guide():
    from preschool import publication
    try:
        current = publication.latest()
    except Exception:  # noqa: BLE001 - the board still opens and says so
        return None
    return current['payload'] if current else None


def _row(event, today):
    cancelled = event.get('status') == 'cancelled'
    parts = [home.day_label(event['date'], today), home.clock_label(event.get('start_time')) if event.get('start_time') else '',
             event.get('location') or '']
    return {'title': ('Cancelled: ' if cancelled else '') + (event.get('title') or 'Class date'),
            'sub': ' · '.join(p for p in parts if p), 'tone': 'warn' if cancelled else ''}


def _coming(guide, today):
    last = (today + timedelta(days=AHEAD)).isoformat()
    return [e for e in guide.get('events', []) if today.isoformat() <= (e.get('date') or '') <= last]


def picture(login, today):
    guide = _guide()
    if guide is None:
        return {'say': 'No class guide yet', 'sub': 'The guide appears here once it has been reviewed and published.',
                'blocks': [{'id': 'more', 'title': 'Class guide', 'kind': 'rows', 'rows': [{'title': 'The class guide', 'href': '/preschool/'}]}]}
    coming = _coming(guide, today)
    week = [e for e in coming if date.fromisoformat(e['date']) <= today + timedelta(days=6)]
    cancelled = [e for e in coming if e.get('status') == 'cancelled']
    nxt = next((e for e in coming if e.get('status') != 'cancelled'), None)
    say = f"Next: {nxt['title']}, {home.day_label(nxt['date'], today)}" if nxt else 'Nothing coming up at school'
    blocks = [
        {'id': 'numbers', 'title': 'Coming up', 'kind': 'tiles', 'tiles': [
            {'value': str(len(week)), 'label': 'this week'}, {'value': str(len(coming)), 'label': f'in {AHEAD} days'},
            {'value': str(len(cancelled)), 'label': 'cancelled', 'tone': 'warn' if cancelled else ''}]},
        {'id': 'dates', 'title': f'The next {AHEAD} days', 'kind': 'rows', 'rows': [_row(e, today) for e in coming][:SHOWN],
         'empty': 'No class dates in the next month.'},
        {'id': 'topics', 'title': 'In the guide', 'kind': 'rows',
         'rows': [{'title': page['title'], 'href': f"/preschool/guide/{page['slug']}"} for page in guide.get('pages', [])][:SHOWN],
         'empty': 'No topic pages yet.'},
        {'id': 'links', 'title': 'Links', 'kind': 'rows',
         'rows': [{'title': r['title'], 'sub': (r.get('details') or '')[:200], 'href': r['url']} for r in guide.get('resources', [])][:SHOWN],
         'empty': 'No links saved.'},
        {'id': 'more', 'title': 'More', 'kind': 'rows', 'rows': [
            {'title': 'The class guide', 'sub': 'Every date, the topic pages, search and history, and what waits for review', 'href': '/preschool/'}]},
    ]
    return {'say': say, 'sub': 'From the class guide, as last published.', 'blocks': blocks}


def line(login, today):
    """None: Today already has “This week at school”."""
    return None


def facts(login, today):
    guide = _guide()
    if guide is None:
        return {'note': 'There is no published class guide yet.'}
    return {'class_dates_next_30_days': len(_coming(guide, today)),
            'cancelled': sum(1 for e in _coming(guide, today) if e.get('status') == 'cancelled')}
