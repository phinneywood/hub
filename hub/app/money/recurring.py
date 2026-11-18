"""What comes back: bills, subscriptions, card payments, money put away, and paydays, with when each comes next.

Found in code from the household's own transactions, no model: the same payee, the same way, at least three
times (twice for something yearly or quarterly) at a steady rhythm. Monthly ones come back on their usual day of
the month; weekly and fortnightly ones on their weekday. A bill's amount is the middle of its last three; one
whose amounts move more than 15% is "about" that much (power, water). A card's payment is the card's balance
now, when Hub reads the card. One that missed its day by half a cycle has stopped and is left out.

A member can add one Hub can't see (rent paid by check, a yearly premium) and say one isn't a bill; both are kept.
"""
import calendar
import statistics
from datetime import date, timedelta

from . import categories, kinds, share, store

EVERY = {'week': 7, '2 weeks': 14, 'month': 30, 'quarter': 91, 'year': 365}
FITS = {'week': (6, 8), '2 weeks': (13, 15), 'month': (26, 35), 'quarter': (84, 98), 'year': (350, 380)}
LOOK_DAYS = 400


def _key(txn, accounts):
    """What groups one flow's transactions: a payee and its direction, or for money moved, where it went."""
    if txn['category'] in categories.MOVES:
        other = store.one('select t.account, a.kind, a.share from money_txns t join money_accounts a on a.id=t.account where t.id=?',
                          (txn['pair'],)) if txn.get('pair') else None
        if other is None:
            return None
        if other['share'] == 'private' or other['account'] not in accounts:
            return ('priv:in' if txn['amount'] > 0 else 'priv:out') if other['share'] == 'private' else \
                ('save:' + other['account'] if txn['amount'] < 0 else None)
        if txn['amount'] > 0:
            return None                     # money arriving from another household account: its leaving side is the flow
        if other['kind'] == 'card':
            return 'card:' + other['account']
        if txn['category'] == 'saving' or other['kind'] == 'savings':
            return 'save:' + other['account']
        return None
    return ('in:' if txn['amount'] > 0 else 'out:') + categories.key(txn['payee'])


def _cadence(days):
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    if not gaps:
        return None
    middle = statistics.median(gaps)
    for every, (low, high) in FITS.items():
        if low <= middle <= high:
            steady = sum(low <= g <= high for g in gaps) / len(gaps)
            enough = len(days) >= (2 if every in ('quarter', 'year') else 3)
            return every if enough and steady >= 0.66 else None
    return None


def step(on, every, day_of_month=None):
    """The next time after `on` for this rhythm."""
    if every != 'month':
        return on + timedelta(days=EVERY[every])
    year, month = (on.year + (on.month == 12), on.month % 12 + 1)
    return date(year, month, min(day_of_month or on.day, calendar.monthrange(year, month)[1]))


def _next(last, every, day_of_month, today):
    on = step(last, every, day_of_month)
    while on < today - timedelta(days=4):      # a few days late is still due; more than that, look at the next one
        on = step(on, every, day_of_month)
    return on


def flows(today, look_days=LOOK_DAYS):
    """Every steady flow in or out of the household's accounts, soonest first:
    [{'key', 'name', 'category', 'amount' (negative = out), 'about', 'every', 'next', 'last', 'times', 'account', 'kind', 'up', 'source'}].
    `kind`: bill, income, card (a card's payment), save (money put away)."""
    accounts = {a['id']: a for a in share.household()}
    if not accounts:
        return _theirs(today)
    marks = ','.join('?' * len(accounts))
    txns = store.rows(f'select * from money_txns where account in ({marks}) and pending=0 and day>=? order by day',
                      (*accounts, (today - timedelta(days=look_days)).isoformat()))
    groups = {}
    for txn in txns:
        key = _key(txn, accounts)
        if key:
            groups.setdefault(key, []).append(txn)
    skipped = set(store.plan('not_bills', []) or [])
    out = []
    for key, items in groups.items():
        if key in skipped:
            continue
        days = sorted({date.fromisoformat(t['day']) for t in items})
        every = _cadence(days)
        if every is None:
            continue
        last = days[-1]
        if (today - last).days > EVERY[every] * 1.5 + 4:
            continue                        # it stopped
        amounts = [t['amount'] for t in items]
        recent = amounts[-3:]
        middle = statistics.median(recent)
        about = items[-1]['category'] in categories.VARIES or (len(recent) > 1 and (max(recent) - min(recent)) > 0.15 * abs(middle))
        before = amounts[-4:-1]
        up = bool(len(before) >= 2 and not about and abs(amounts[-1]) > abs(statistics.median(before)) * 1.05
                  and abs(amounts[-1]) - abs(statistics.median(before)) >= 1)
        dom = round(statistics.median([d.day for d in days[-3:]])) if every == 'month' else None
        account = items[-1]['account']
        kind = key.split(':')[0]
        kind = {'in': 'income', 'out': 'bill', 'priv': 'income' if key == 'priv:in' else 'save'}.get(kind, kind)
        if kind == 'bill' and items[-1]['category'] not in categories.BILLS and (about or every in ('week', '2 weeks')):
            continue                        # the weekly shop or a habit: everyday spending, not a bill
        if up:
            middle = amounts[-1]            # a new price: the next one will be that too
        name = categories.name(items[-1]['payee'])
        if kind == 'card':
            card = store.one('select * from money_accounts where id=?', (key[5:],))
            name = f'{card["name"]} payment' if card else 'Card payment'
            if card and card['balance'] is not None:
                middle, about = -abs(card['balance']), True
        elif key.startswith('priv:'):
            name = 'From a private account' if key == 'priv:in' else 'To a private account'
        elif kind == 'save':
            target = store.one('select name from money_accounts where id=?', (key[5:],))
            name = f'To {target["name"]}' if target else 'Saving'
        out.append({'key': key, 'name': name, 'category': items[-1]['category'], 'amount': round(middle, 2), 'about': about, 'every': every,
                    'next': _next(last, every, dom, today).isoformat(), 'last': last.isoformat(), 'times': len(days),
                    'account': account, 'kind': kind, 'up': up, 'source': 'found'})
    out += _theirs(today)
    return sorted(out, key=lambda f: f['next'])


def _theirs(today):
    """Bills a member told Hub about."""
    out = []
    for row in store.rows('select * from money_bills'):
        on = date.fromisoformat(row['next_day'])
        while on < today:
            on = step(on, row['every'])
        out.append({'key': f'bill:{row["id"]}', 'id': row['id'], 'name': row['name'], 'category': row['category'],
                    'amount': -abs(row['amount']), 'about': False, 'every': row['every'], 'next': on.isoformat(), 'last': None,
                    'times': 0, 'account': None, 'kind': 'bill', 'up': False, 'source': 'you'})
    return out


def between(flows_, start, end):
    """Each time a flow comes between two days: [(day, flow)]."""
    out = []
    for flow in flows_:
        on = date.fromisoformat(flow['next'])
        dom = on.day if flow['every'] == 'month' else None
        while on <= end:
            if on >= start:
                out.append((on, flow))
            on = step(on, flow['every'], dom)
    return sorted(out, key=lambda pair: pair[0])


def monthly(flow):
    """What a flow comes to in an average month."""
    return flow['amount'] * {'week': 52 / 12, '2 weeks': 26 / 12, 'month': 1, 'quarter': 1 / 3, 'year': 1 / 12}[flow['every']]


def cash_kinds():
    return kinds.CASH - {'savings'}
