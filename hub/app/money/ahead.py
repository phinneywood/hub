"""Cash ahead: what the household's everyday accounts will hold over the next weeks, and what a purchase does to it.

The forecast starts from today's balance in the shared checking accounts in the budget, then adds each payday
and takes off each bill, card payment and transfer to savings when it is due (recurring.py), and a day's ordinary
spending every day: the average of what left those accounts over the last 90 days outside those steady flows,
leaving out the two biggest one-off payments. Card purchases are not in it twice: they leave checking as the
card's payment. Savings are not spent from, but are said beside it.

The low point is given as a range: at the usual pace of spending, and at a pace a third higher. The cushion is the
least the household wants to keep in checking ($1,000 until a member says otherwise).

"Can we afford it?" adds the purchase on its day. Within two months it reads the forecast's low point after it;
further off, it adds up what the household keeps each month (income less spending, the last three whole months)
until that day, less what the goals need. Either way it says what the answer rests on.
"""
from datetime import date, timedelta

from . import budget, plan, recurring, say, share, store

DAYS = 45
CUSHION = 1000.0
BASE_DAYS = 90
STEEPER = 4 / 3
NEAR_DAYS = 60
ROOM = 1.15             # "yes" leaves this much more than the price; "tight" only just covers it


def cushion():
    value = store.plan('cushion')
    return float(value) if isinstance(value, (int, float)) and value >= 0 else CUSHION


def cash_accounts():
    return [a for a in share.household() if a['kind'] in recurring.cash_kinds()]


def savings_accounts():
    return [a for a in share.household() if a['kind'] == 'savings']


def _balance(accounts):
    return round(sum(a['balance'] or 0 for a in accounts), 2)


def baseline(today, flows):
    """A day's ordinary spending from the cash accounts: what left them over the last 90 days that isn't a steady
    flow or a move between the household's accounts, without the two biggest one-off payments."""
    accounts = [a['id'] for a in cash_accounts()]
    if not accounts:
        return 0.0
    marks = ','.join('?' * len(accounts))
    first = store.one(f'select min(day) as d from money_txns where account in ({marks})', tuple(accounts))
    if not first or not first['d']:
        return 0.0
    start = max(date.fromisoformat(first['d']), today - timedelta(days=BASE_DAYS))
    days = max(14, (today - start).days)
    rows = store.rows(f'select payee, amount, category, pair from money_txns where account in ({marks}) and pending=0 and amount<0 '
                      f'and day>=? and day<?', (*accounts, start.isoformat(), today.isoformat()))
    steady = {f['key'] for f in flows}
    from . import categories
    plain = [-r['amount'] for r in rows if r['category'] not in categories.MOVES
             and ('out:' + categories.key(r['payee'])) not in steady and r['category'] != 'cards']
    plain = sorted(plain)[:-2] if len(plain) > 10 else plain
    return round(sum(plain) / days, 2)


def forecast(today=None, days=DAYS, extra=()):
    """{'start', 'savings', 'cushion', 'daily', 'points': [{'day', 'balance', 'steep'}], 'events': [{'day', 'name', 'amount', 'kind'}],
    'low': {'day', 'balance', 'steep'}, 'accounts': [names], 'ok': bool}. `extra`: [(day, name, amount)] to add."""
    from ... import home
    today = today or home.today()
    accounts = cash_accounts()
    if not accounts:
        return {'ok': False, 'accounts': [], 'start': 0.0, 'savings': _balance(savings_accounts()), 'cushion': cushion(), 'points': [],
                'events': [], 'low': None, 'daily': 0.0}
    ids = {a['id'] for a in accounts}
    flows = [f for f in recurring.flows(today) if f['account'] in ids or f['account'] is None]
    daily = baseline(today, flows)
    end = today + timedelta(days=days)
    events = [{'day': on.isoformat(), 'name': f['name'], 'amount': f['amount'], 'kind': f['kind'], 'about': f['about']}
              for on, f in recurring.between(flows, today + timedelta(days=1), end)]
    events += [{'day': on.isoformat(), 'name': name, 'amount': -abs(amount), 'kind': 'what-if', 'about': False}
               for on, name, amount in extra if today < on <= end]
    events.sort(key=lambda e: e['day'])
    start = _balance(accounts)
    level, steep, points, at = start, start, [{'day': today.isoformat(), 'balance': start, 'steep': start}], 0
    for n in range(1, days + 1):
        on = (today + timedelta(days=n)).isoformat()
        level -= daily
        steep -= daily * STEEPER
        while at < len(events) and events[at]['day'] == on:
            level += events[at]['amount']
            steep += events[at]['amount']
            at += 1
        points.append({'day': on, 'balance': round(level, 2), 'steep': round(steep, 2)})
    low = min(points, key=lambda p: p['balance'])
    low_steep = min(p['steep'] for p in points)
    return {'ok': True, 'accounts': [a['name'] for a in accounts], 'start': start, 'savings': _balance(savings_accounts()),
            'cushion': cushion(), 'daily': daily, 'points': points, 'events': events,
            'low': {'day': low['day'], 'balance': low['balance'], 'steep': round(low_steep, 2)}}


def kept_monthly(today):
    """What the household kept each month, on average over the last three whole months: income less spending."""
    months = [m for m in budget.history(today) if m['spent'] or m['income']]
    if not months:
        return None
    return round(sum(m['income'] - sum(m['spent'].values()) for m in months) / len(months), 2)


def afford(amount, on=None, what='', today=None):
    """Whether the household can spend `amount` on day `on` (a date; today when None). {'verdict': yes, tight, no or
    unknown, 'said', 'rests_on', and the numbers}."""
    from ... import home
    today = today or home.today()
    on = on or today
    amount = abs(float(amount))
    called = what or 'it'
    room = cushion()
    savings = _balance(savings_accounts())
    if (on - today).days <= NEAR_DAYS:
        before = forecast(today, days=max(DAYS, (on - today).days + 14))
        if not before['ok']:
            return {'verdict': 'unknown', 'said': 'Hub can’t tell yet: it hasn’t read a checking account.', 'rests_on': ''}
        after = forecast(today, days=max(DAYS, (on - today).days + 14), extra=[(max(on, today + timedelta(days=1)), called, amount)])
        low = after['low']['balance']
        verdict = 'yes' if low >= room else 'tight' if low >= 0 or low + savings >= room else 'no'
        lows = f'Checking’s low point would be {say.dollars(low)} on {say.day(after["low"]["day"], today)}'
        said = {'yes': f'Yes. {lows}, above your {say.dollars(room)} cushion.',
                'tight': f'Only just. {lows}' + (', under your cushion' if low >= 0 else '')
                + (f'; savings ({say.dollars(savings)}) would cover the gap.' if low < room and savings else '.'),
                'no': f'Not without help. {lows}' + (f', and savings hold {say.dollars(savings)}.' if savings else '.')}[verdict]
        rests = (f'The next {len(after["points"]) - 1} days of bills and paydays Hub found, and '
                 f'about {say.dollars(before["daily"])} a day of everyday spending.')
        return {'verdict': verdict, 'said': said, 'rests_on': rests, 'low_before': before['low']['balance'], 'low_after': low,
                'low_day': after['low']['day'], 'cushion': room, 'savings': savings, 'amount': amount, 'on': on.isoformat(), 'near': True}
    kept = kept_monthly(today)
    if kept is None:
        return {'verdict': 'unknown', 'said': 'Hub can’t tell yet: it needs a whole month of spending first.', 'rests_on': ''}
    months = (on - today).days / 30.44
    cash = _balance(cash_accounts())
    goals_need = sum(g['monthly'] or 0 for g in plan.goals(today) if not g['done'])
    free = cash + savings - room + (kept - goals_need) * months
    verdict = 'yes' if free >= amount * ROOM else 'tight' if free >= amount else 'no'
    month_name = on.strftime('%B') if on.year == today.year else on.strftime('%B %Y')
    said = {'yes': f'Yes. By {month_name} you should have about {say.dollars(free)} to spare, after the cushion and your goals.',
            'tight': f'Only just. By {month_name} you should have about {say.dollars(free)} to spare, after the cushion and your goals.',
            'no': f'Not on the current pace. By {month_name} you’d have about {say.dollars(max(free, 0))} to spare after the cushion and '
                  f'your goals; it would take about {say.dollars(max(0, (amount - free) / max(months, 1)))} a month more.'}[verdict]
    rests = (f'You kept about {say.dollars(kept)} a month over the last three months; your goals need '
             f'{say.dollars(goals_need)} a month.')
    return {'verdict': verdict, 'said': said, 'rests_on': rests, 'free': round(free, 2), 'kept': kept, 'goals_need': round(goals_need, 2),
            'cushion': room, 'savings': savings, 'amount': amount, 'on': on.isoformat(), 'near': False}
