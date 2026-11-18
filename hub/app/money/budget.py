"""The month against the budget: what was spent in each category, what is left, and whether it is on pace.

The household's budget counts the shared accounts that are in it (share.household): everyday spending and bills,
never money moved between the household's own accounts, and never a pending charge (its amount can still
change). A refund from a shop comes off what was spent there. An everyday category is on pace when what was spent
is no more than its budget times the share of the month gone, with a little room; bills come when they come, so
they are only measured against the whole month.

With no budget set yet, Hub suggests one: the middle of the last three whole months for each category, rounded
up a little. One tap (or "use Hub's budget" in Ask) keeps it.
"""
import calendar
import math
import statistics
from datetime import date, timedelta

from . import categories, share, store

ROOM = 1.10             # on pace with up to 10% (and $25) more than the month's share
ROOM_DOLLARS = 25


def _spent(start, end):
    """{category: dollars spent} between two days (end exclusive), for the household, posted only."""
    accounts = [a['id'] for a in share.household()]
    if not accounts:
        return {}, 0.0, 0.0
    marks = ','.join('?' * len(accounts))
    rows = store.rows(f'select category, amount, pair from money_txns where account in ({marks}) and pending=0 and day>=? and day<?',
                      (*accounts, start.isoformat(), end.isoformat()))
    spent, income, moved_out = {}, 0.0, 0.0
    shared = set(accounts)
    for row in rows:
        category = row['category'] or 'other'
        if category in categories.SPENT:
            spent[category] = spent.get(category, 0.0) - row['amount']
        elif category in ('income', 'interest', 'refunds'):
            income += row['amount']
        elif category in categories.MOVES and row['pair'] and row['amount'] < 0:
            other = store.one('select account from money_txns where id=?', (row['pair'],))
            if other and other['account'] not in shared:
                moved_out -= row['amount']          # to a private account, or one left out of the budget
    return {k: round(v, 2) for k, v in spent.items()}, round(income, 2), round(moved_out, 2)


def budgets():
    return {r['category']: r['monthly'] for r in store.rows('select category, monthly from money_budgets')}


def month_bounds(today):
    start = today.replace(day=1)
    days = calendar.monthrange(today.year, today.month)[1]
    return start, start + timedelta(days=days), days


def month(today):
    """This month: {'month', 'days', 'day', 'spent', 'budget', 'income', 'moved_out', 'categories': [...]} with
    each category's spent, budget, left, expected-by-now and state (over, ahead, on, under, none)."""
    start, end, days = month_bounds(today)
    spent, income, moved_out = _spent(start, end)
    set_ = budgets()
    share_gone = today.day / days
    rows = []
    for key in sorted(set(spent) | set(set_), key=lambda k: -max(spent.get(k, 0), set_.get(k, 0))):
        used, budget = spent.get(key, 0.0), set_.get(key)
        expected = budget * share_gone if budget and key in categories.EVERYDAY else budget
        if not budget:
            state = 'none'
        elif used > budget:
            state = 'over'
        elif key in categories.EVERYDAY and used > expected * ROOM and used > expected + ROOM_DOLLARS:
            state = 'ahead'
        elif used >= budget * 0.9:
            state = 'on'
        else:
            state = 'under'
        if used or budget:
            rows.append({'key': key, 'name': categories.NAMES.get(key, key.title()), 'spent': round(used, 2), 'budget': budget,
                         'left': round(budget - used, 2) if budget else None, 'expected': round(expected, 2) if budget else None,
                         'state': state, 'group': categories.GROUP.get(key, 'everyday')})
    total_budget = sum(set_.values()) or None
    total = round(sum(v for v in spent.values()), 2)
    return {'month': today.strftime('%B'), 'start': start.isoformat(), 'days': days, 'day': today.day, 'spent': total,
            'budget': total_budget, 'income': income, 'moved_out': moved_out, 'categories': rows,
            'pace': round(total_budget * share_gone, 2) if total_budget else None}


def whole_months(today, count=3):
    """The last `count` whole months, newest first, as (start, end)."""
    out, end = [], today.replace(day=1)
    for _ in range(count):
        start = (end - timedelta(days=1)).replace(day=1)
        out.append((start, end))
        end = start
    return out


def history(today, count=3):
    """[{month, spent: {category: dollars}, income}] for the last whole months, newest first."""
    out = []
    for start, end in whole_months(today, count):
        spent, income, _ = _spent(start, end)
        out.append({'month': start.strftime('%B'), 'start': start.isoformat(), 'spent': spent, 'income': income})
    return out


def _round_up(amount):
    step = 50 if amount >= 500 else 10
    return float(math.ceil(amount / step) * step)


def suggest(today):
    """{category: monthly dollars}: the middle of the last three whole months, for categories spent on in at least
    two of them. Empty without two whole months read."""
    months = history(today)
    with_data = [m for m in months if m['spent']]
    if len(with_data) < 2:
        return {}
    found = {}
    for key in {k for m in with_data for k in m['spent']}:
        seen = [m['spent'].get(key, 0.0) for m in with_data]
        if sum(v > 0 for v in seen) >= 2 and statistics.median(seen) >= 5:
            found[key] = _round_up(statistics.median(seen))
    return found


def year_spending(today):
    """What the household spent over the last year, by category, from what Hub has read (scaled up when it has read
    less than a year, but at least two months), and how many days that rests on. ({}, 0) when too little."""
    accounts = [a['id'] for a in share.household()]
    if not accounts:
        return {}, 0
    marks = ','.join('?' * len(accounts))
    first = store.one(f'select min(day) as d from money_txns where account in ({marks})', tuple(accounts))
    if not first or not first['d']:
        return {}, 0
    start = max(date.fromisoformat(first['d']), today - timedelta(days=365))
    days = (today - start).days
    if days < 60:
        return {}, days
    spent, _, _ = _spent(start, today)
    scale = 365 / days
    return {k: round(v * scale, 2) for k, v in spent.items()}, days
