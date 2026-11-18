"""The mail dashboard: what needs you, how the inbox is trending and what Hub's cleanup has done.

Drawn from Hub's own tables only, as the Mail screen is (`hub/app/mail.py`): the sorting tables, the daily inbox
size Hub records (`store.inbox_history`) and the plan of inbox changes. No Gmail call and no model, so it opens at
once. It is the mailbox owner's alone (`people.sees(login, DEFAULT)`), like Mail.
"""
from ... import people, store

ID = 'mail'
NAME = 'Mail'
WHAT = 'What needs you, the inbox over a month and Hub’s cleanup'
DAYS = 30


def sees(login):
    return people.sees(login, people.DEFAULT)


def _n(count):
    return f'{count:,}'


def _trend(history):
    known = [h for h in history if h['total'] is not None]
    if len(known) < 2:
        return None, ''
    change = known[-1]['total'] - known[0]['total']
    days = len(known) - 1
    word = 'down' if change < 0 else 'up'
    return known, f'{word} {_n(abs(change))} in {days} day{"s" * (days != 1)}' if change else f'no change in {days} days'


def _day(iso):
    from datetime import date
    d = date.fromisoformat(iso)
    return f'{d:%b} {d.day}'


def _inbox_line(known):
    """The inbox size over the days Hub recorded, as points in the same 720 by 180 box the network charts use."""
    if not known:
        return []
    values = [h['total'] for h in known]
    low, high = min(values), max(values)
    span = (high - low) or 1
    step = 716 / max(len(values) - 1, 1)
    return [' '.join(f'{2 + n * step:.1f},{170 - 160 * (v - low) / span:.1f}' for n, v in enumerate(values))]


def _cleanup():
    from ...tools.gmail import missions
    from ... import apply
    items = store.items()
    done = [i for i in items if i['status'] == 'done']
    problems = [i for i in items if i['status'] in ('failed', 'needs_review', 'undo_partial')]
    return {'archived': sum(i['archived'] or 0 for i in done if missions.leaves_inbox(i)),
            'filters': sum(1 for i in done if apply.spec_for(i)['future']),
            'planned': sum(1 for i in items if i['status'] == 'planned' and i['decision'] in store.ACTIONS),
            'problems': [{'title': missions.item_title(i), 'sub': 'Needs a check before Hub continues', 'tone': 'warn'} for i in problems][:4]}


def _seen():
    """The Mail screen's picture, leaving its one-time notice of a filing for the Mail screen to tell."""
    from .. import mail
    from ...tools.gmail import inbox
    held = inbox._filing['result']
    try:
        return mail.picture()
    finally:
        inbox._filing['result'] = held


def picture(login, today):
    seen = _seen()
    known, trend = _trend(store.inbox_history(DAYS + 1))
    total = known[-1]['total'] if known else None
    cleanup = _cleanup()
    needs = len(seen['needs']) + seen['needs_more']
    say = 'Nothing in the mail needs you' if not needs else f'{needs} email{"s" * (needs != 1)} need{"s" * (needs == 1)} you'
    sub = (f'Sorted {seen["synced"]}. ' if seen['synced'] else '') + (f'Today: {seen["sorted_today"]}.' if seen['sorted_today'] else '')
    shelves = sorted(seen['shelves'], key=lambda s: -s['count'])
    blocks = [
        {'id': 'counts', 'title': 'Right now', 'kind': 'tiles', 'tiles': [
            {'value': str(needs), 'label': 'need you', 'tone': 'warn' if needs else ''},
            {'value': str(len(seen['asked'])), 'label': 'asked, no answer'},
            {'value': str(len(seen['odd'])), 'label': 'worth a look'},
            {'value': _n(total) if total is not None else '—', 'label': 'in the inbox' + (f' · {trend}' if trend else '')}]},
        {'id': 'needs', 'title': 'Needs you', 'kind': 'rows', 'view': 'mail',
         'rows': [{'title': e['who'], 'sub': (e['subject'] + (f'. {e["step"]}' if e['step'] else '')).strip(),
                   'right': 'today' if e['days'] == 0 else f'{e["days"]}d', 'href': e['find'], 'tone': 'warn' if e['days'] >= seen['waiting_days'] else ''}
                  for e in seen['needs'][:5]],
         'empty': 'Nothing needs you.'},
        {'id': 'inbox', 'title': f'The inbox, last {DAYS} days', 'kind': 'line', 'segments': _inbox_line(known), 'view': [720, 180],
         'start': _day(known[0]['day']) if known else '', 'end': _day(known[-1]['day']) if known else '', 'now': _n(total) if total is not None else '',
         'top': trend, 'empty': 'Hub starts recording the inbox size once a day; the line shows after two days.'},
        {'id': 'kinds', 'title': 'In the inbox, by kind', 'kind': 'bars',
         'bars': [{'label': s['name'], 'value': s['count'], 'text': _n(s['count'])} for s in shelves if s['count']][:8]
         + ([{'label': 'Not sorted', 'value': seen['unsorted'], 'text': _n(seen['unsorted'])}] if seen['unsorted'] else []),
         'empty': 'Nothing sorted is waiting in the inbox.'},
        {'id': 'cleanup', 'title': 'Hub’s cleanup', 'kind': 'tiles', 'tiles': [
            {'value': _n(cleanup['archived']), 'label': 'archived by Hub so far'},
            {'value': str(cleanup['filters']), 'label': 'filters keeping new mail out'},
            {'value': str(cleanup['planned']), 'label': 'changes planned'}]},
        {'id': 'problems', 'title': 'Needs a check', 'kind': 'rows', 'view': 'mailsettings', 'rows': cleanup['problems'],
         'empty': 'Every change Hub made is confirmed.'},
        {'id': 'asked', 'title': 'You asked, no answer yet', 'kind': 'rows',
         'rows': [{'title': a['subject'] or '(no subject)', 'sub': f'To {a["to"]}' if a['to'] else 'Sent', 'right': f'{a["days"]}d', 'href': a['find']}
                  for a in seen['asked'][:5]],
         'empty': 'Every question you sent has an answer.'},
        {'id': 'more', 'title': 'More', 'kind': 'rows', 'rows': [
            {'title': 'Mail', 'sub': 'Every email that needs you, and filing with Undo', 'view': 'mail'},
            {'title': 'Mail settings', 'sub': 'How Hub sorts, the plan and the rules', 'view': 'mailsettings'}]},
    ]
    return {'say': say, 'sub': sub.strip(), 'blocks': blocks}


def line(login, today):
    known, trend = _trend(store.inbox_history(8))
    if not known:
        return None
    return {'text': f'{_n(known[-1]["total"])} in the inbox', 'next': trend, 'tone': ''}


def facts(login, today):
    seen = _seen()
    known, trend = _trend(store.inbox_history(DAYS + 1))
    cleanup = _cleanup()
    return {'need_the_owner': len(seen['needs']) + seen['needs_more'], 'questions_unanswered': len(seen['asked']),
            'in_the_inbox': known[-1]['total'] if known else None, 'inbox_trend': trend, 'archived_by_hub': cleanup['archived'],
            'filters': cleanup['filters'], 'changes_needing_a_check': len(cleanup['problems'])}
