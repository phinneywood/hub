"""The longer view: goals, net worth, and when retirement works.

Goals. A name, a target, maybe a day to reach it by, and what is saved so far: typed in, or an account's balance
when the goal is tied to one ("the trip fund is the savings account"). With a day, Hub says what it needs a month.

Net worth. Everything the member sees, owned less owed, with the others' private accounts as their totals.

Retirement. A rough range, never a promise. From what is invested for it now (retirement and investment accounts,
health savings; not college savings) and what is put away each month, Hub follows the money a year at a time at
three returns after inflation (2.5%, 4.5% and 6% a year), then takes out a year's spending, less Social Security
from the age each person starts it, until the older person is 95. The earliest age at which the money lasts is the
answer for each path. Taxes on money taken out of pre-tax accounts and a bad run of years early on are not in it,
which is why it is a range and the worse path is worth more attention than the better one.

Everything it rests on is shown and can be changed in Ask: each person's age and Social Security, the spending in
retirement (the last year's household spending, less childcare and loan payments, until said otherwise), what is
put away a month (the steady transfers into investment accounts Hub found, until said otherwise).
"""
from datetime import date

from . import budget, kinds, recurring, share, store

PATHS = (('If markets do worse', 0.025), ('Middle', 0.045), ('If markets do better', 0.06))
UNTIL = 95
LATEST = 80
SS_AGE = 67
NOT_RETIREMENT = frozenset({'childcare', 'loans', 'kids'})


def _months_until(today, until):
    return max(1.0, (until - today).days / 30.44)


def goals(today, login=None):
    """[{'id', 'name', 'target', 'saved', 'by', 'monthly', 'share', 'done', 'account'}], oldest first. A goal tied to an
    account another member keeps private shows its progress only."""
    out = []
    accounts = {a['id']: a for a in share.all_accounts()}
    for row in store.rows('select * from money_goals where done_at is null order by made_at'):
        account = accounts.get(row['account']) if row['account'] else None
        hidden = account is not None and account['share'] == 'private' and login is not None and not share.mine(login, account['owner'])
        balance = (round(share.month_start(account) / share.ROUND) * share.ROUND if hidden else account['balance'] or 0) if account else 0
        saved = balance if account else row['saved']
        if account and account['kind'] in kinds.OWED:
            saved = max(0.0, row['target'] - abs(balance))    # paying a card or loan down to nothing
        by = date.fromisoformat(row['by_day']) if row['by_day'] else None
        left = max(0.0, row['target'] - saved)
        monthly = round(left / _months_until(today, by), 2) if by and left else (row['monthly'] or None)
        out.append({'id': row['id'], 'name': row['name'], 'target': row['target'], 'saved': round(saved, 2), 'by': row['by_day'],
                    'monthly': monthly, 'share': min(100, round(100 * saved / row['target'])) if row['target'] else 0,
                    'done': saved >= row['target'] > 0, 'account': None if hidden else (account['name'] if account else None),
                    'late': bool(by and by < today and left), 'account_id': account['id'] if account else None})
    return out


def net_worth(login):
    """{'total', 'owned', 'owed', 'groups': [{'name', 'total', 'accounts': [...]}], 'private': [...]} as this member sees it."""
    full, hidden = share.accounts(login)
    groups = {}
    owned = owed = 0.0
    for account in full:
        value = kinds.worth(account['kind'], account['balance'])
        owned, owed = owned + max(value, 0), owed + min(value, 0)
        group = groups.setdefault(kinds.group(account['kind']), {'name': kinds.group(account['kind']), 'total': 0.0, 'accounts': []})
        group['total'] += value
        group['accounts'].append(account)
    for line in hidden:
        owned, owed = owned + max(line['worth'], 0), owed + min(line['worth'], 0)
    order = [name for name, _ in kinds.GROUPS]
    return {'total': round(owned + owed, 2), 'owned': round(owned, 2), 'owed': round(owed, 2),
            'groups': sorted(groups.values(), key=lambda g: order.index(g['name'])), 'private': hidden}


def history(login, months=6):
    """Net worth on the first of each of the last few months (the accounts this member sees in full, plus the
    others' private totals as they are now): [{'day', 'total'}]."""
    from ... import home
    full, hidden = share.accounts(login)
    fixed = sum(line['worth'] for line in hidden)
    today = home.today()
    out = []
    first = today.replace(day=1)
    days = []
    for _ in range(months):
        days.append(first)
        first = (first.replace(day=1) - date.resolution).replace(day=1)
    for day in reversed(days):
        total, seen = fixed, False
        for account in full:
            row = store.one('select balance from money_balances where account=? and day<=? order by day desc limit 1',
                            (account['id'], day.isoformat()))
            if row:
                total += kinds.worth(account['kind'], row['balance'])
                seen = True
        if seen:
            out.append({'day': day.isoformat(), 'total': round(total, 2)})
    out.append({'day': today.isoformat(), 'total': net_worth(login)['total']})
    return out


# ---- retirement ----

def assumptions(today, login):
    """What the retirement range rests on, each with where it came from: {'people', 'invested', 'saving', 'spend', ...}."""
    told = store.plan_all()
    people = [p for p in told.get('people') or [] if isinstance(p, dict) and isinstance(p.get('age'), (int, float))]
    full, hidden = share.accounts(login)
    invested = sum(max(0.0, a['balance'] or 0) for a in full if a['kind'] in kinds.INVESTED - {'college'})
    invested += sum(line['invested'] for line in hidden)
    saving = told.get('saving')
    saving_from = 'you'
    if not isinstance(saving, (int, float)):
        invested_ids = {a['id'] for a in share.all_accounts() if a['kind'] in kinds.INVESTED - {'college'}}
        found = [f for f in recurring.flows(today) if f['kind'] == 'save' and f['key'][5:] in invested_ids]
        saving, saving_from = round(-sum(recurring.monthly(f) for f in found), 2), 'found' if found else 'none'
    spend = told.get('spend')
    spend_from = 'you'
    if not isinstance(spend, (int, float)):
        year, days = budget.year_spending(today)
        spend = round(sum(v for k, v in year.items() if k not in NOT_RETIREMENT), -2) if year else None
        spend_from = 'found' if year else 'none'
    return {'people': people, 'invested': round(invested, 2), 'saving': saving, 'saving_from': saving_from, 'spend': spend,
            'spend_from': spend_from, 'retire_at': told.get('retire_at'), 'until': UNTIL}


def _lasts(start_age, retire_age, balance, saving, spend, people, rate):
    """Whether the money lasts to UNTIL for the oldest person, retiring when the first person is `retire_age`;
    and the balance each year: [(first person's age, balance)]."""
    oldest = max(p['age'] for p in people)
    gap = oldest - people[0]['age']
    path, age = [], start_age
    while age + gap <= UNTIL:
        if age < retire_age:
            balance = balance * (1 + rate) + saving * 12
        else:
            years = age - start_age
            income = sum((p.get('ss') or 0) * 12 for p in people if p['age'] + years >= (p.get('ss_age') or SS_AGE))
            balance = balance * (1 + rate) - max(0.0, spend - income)
        path.append((age, round(balance)))
        if balance < 0:
            return False, path
        age += 1
    return True, path


def retirement(today, login, changes=None):
    """{'ready', 'missing': [what Hub needs to be told], 'paths': [{'name', 'rate', 'age'}], 'chart': [(age, balance)],
    'assumptions'}. `changes` overrides assumptions for a what-if, without keeping them."""
    told = assumptions(today, login)
    told.update({k: v for k, v in (changes or {}).items() if v is not None})
    missing = [w for w, ok in (('ages', told['people']), ('spending', told['spend'])) if not ok]
    if missing:
        return {'ready': False, 'missing': missing, 'paths': [], 'chart': [], 'assumptions': told}
    people = told['people']
    start = int(people[0]['age'])
    paths = []
    for name, rate in PATHS:
        age = next((r for r in range(start, LATEST + 1)
                    if _lasts(start, r, told['invested'], told['saving'], told['spend'], people, rate)[0]), None)
        paths.append({'name': name, 'rate': rate, 'age': age})
    middle = paths[1]['age']
    shown = told['retire_at'] or middle or LATEST
    lasts, chart = _lasts(start, int(shown), told['invested'], told['saving'], told['spend'], people, PATHS[1][1])
    return {'ready': True, 'missing': [], 'paths': paths, 'chart': chart, 'shown_at': int(shown), 'lasts': lasts, 'assumptions': told}
