"""What the app shows of Money, for one member: the screen, the line on Today, and what needs them.

All of it is worked out here from Money's own tables, in code, with no model and no bank read: opening the screen
never costs one of the Bridge's requests. A member who isn't one (no login, or a login Hub doesn't know) gets
nothing; routes.py turns them away before this is asked.
"""
from datetime import date, timedelta

from ... import home
from . import ahead, budget, categories, changes, kinds, plan, recurring, say, share, store, sync

RECENT = 40
SOON_DAYS = 14
STALE_DAYS = 3
UNSORTED_MIN = 20       # dollars: a charge smaller than this isn't worth a member's time to sort


def _account(account, login):
    return {'id': account['id'], 'name': account['name'], 'bank': account['bank'], 'kind': account['kind'],
            'kind_name': kinds.NAMES.get(account['kind'], 'Other'), 'balance': account['balance'], 'available': account['available'],
            'worth': kinds.worth(account['kind'], account['balance']), 'owed': account['kind'] in kinds.OWED,
            'private': account['share'] == 'private', 'mine': share.mine(login, account['owner']),
            'owner': share.first_name(account['owner']), 'ends': account['ends'] or '', 'source': account['source'],
            'at': account['balance_at'], 'in_budget': bool(account['in_budget']), 'closed': bool(account['closed'])}


def _txn(row):
    private_side = share.private_side(row) if row.get('pair') else False
    return {'id': row['id'], 'day': row['day'], 'payee': 'To a private account' if private_side and row['amount'] < 0
            else 'From a private account' if private_side else categories.name(row['payee']), 'amount': row['amount'],
            'category': row['category'] or 'other', 'category_name': categories.NAMES.get(row['category'] or 'other', 'Other'),
            'account': row['account_name'], 'pending': bool(row['pending']), 'how': row['how'] or ''}


def unsorted(login, today):
    """Charges this month Hub could only call Other, the biggest first."""
    start = today.replace(day=1).isoformat()
    rows = share.txns(login, "category='other' and amount<? and day>=? and pending=0", (-UNSORTED_MIN, start), household_only=True)
    return sorted((_txn(r) for r in rows), key=lambda t: t['amount'])


def needs(login, today, month=None, cash=None, flows=None):
    """What needs this member, most pressing first: [{'key', 'title', 'why', 'do'}]. `do` names the tap: connect,
    read, sort, budget, open (a part of the screen) or ask (words to put in Ask)."""
    out = []
    accounts = share.accounts(login)[0]
    if not accounts:
        return [{'key': 'connect', 'title': 'Connect your banks', 'why': 'Hub reads them once or twice a day and never moves money.',
                 'do': 'connect'}]
    for link in sync.links():
        when = link['read_at'] or link['created_at']
        if link['ok'] == 0 and link['said']:
            out.append({'key': f'link-{link["id"]}', 'title': 'A bank connection needs a look', 'why': link['said'][:200],
                        'do': 'read' if share.mine(login, link['login']) else 'open'})
        elif store.now() - when > STALE_DAYS * 86400:
            out.append({'key': f'stale-{link["id"]}', 'title': 'Hub hasn’t read the banks for a few days',
                        'why': f'Last read {say.day(date.fromtimestamp(when), today)}.', 'do': 'read'})
    cash = cash if cash is not None else ahead.forecast(today)
    if cash['ok'] and cash['low'] and cash['low']['balance'] < cash['cushion']:
        low = cash['low']
        out.append({'key': 'cash-low', 'title': f'Checking dips to {say.dollars(low["balance"])} {say.day(low["day"], today)}',
                    'why': f'Under your {say.dollars(cash["cushion"])} cushion. Move money from savings, or wait on something.',
                    'do': 'open', 'part': 'ahead'})
    month = month or budget.month(today)
    for row in month['categories']:
        if row['state'] == 'over':
            out.append({'key': f'over-{row["key"]}', 'title': f'{row["name"]} is {say.dollars(-row["left"])} over',
                        'why': f'{say.dollars(row["spent"])} of {say.dollars(row["budget"])} this month.', 'do': 'open', 'part': 'month'})
    for flow in flows if flows is not None else recurring.flows(today):
        if flow['up'] and flow['kind'] == 'bill' and (today - date.fromisoformat(flow['last'])).days <= 10:
            out.append({'key': f'up-{flow["key"]}', 'title': f'{flow["name"]} went up to {say.dollars(-flow["amount"], cents=True)}',
                        'why': f'It comes every {flow["every"]}.', 'do': 'open', 'part': 'bills'})
    loose = unsorted(login, today)
    if len(loose) >= 3:
        out.append({'key': 'unsorted', 'title': f'{len(loose)} charges Hub couldn’t place',
                    'why': 'Pick a category once; Hub sorts that payee from then on.', 'do': 'open', 'part': 'unsorted'})
    for goal in plan.goals(today, login):
        if goal['late']:
            out.append({'key': f'goal-{goal["id"]}', 'title': f'{goal["name"]} missed its day', 'why': 'Give it a new day in Ask.',
                        'do': 'ask', 'words': f'move {goal["name"]} to '})
    return out


def sentence(month, cash, accounts):
    """The screen's first line, and the one under it."""
    if not accounts:
        return 'Connect your banks to start.', 'Hub reads them, works out the month, the weeks ahead and the long run. It never moves money.'
    left = month['days'] - month['day']
    if month['budget']:
        state = ('over budget' if month['spent'] > month['budget'] else
                 'ahead of pace' if month['spent'] > month['pace'] * budget.ROOM + budget.ROOM_DOLLARS else 'on pace')
        first = (f'{month["month"]} is {state}: {say.dollars(month["spent"])} of {say.dollars(month["budget"])} spent, '
                 f'{left} day{"s" * (left != 1)} to go.')
    else:
        first = f'{say.dollars(month["spent"])} spent so far in {month["month"]}.'
    second = ''
    if cash['ok'] and cash['low']:
        second = f'Checking has {say.dollars(cash["start"])}; its low point in the next six weeks is about {say.dollars(cash["low"]["balance"])}.'
    return first, second


def screen(login, today=None):
    """The whole Money screen for this member."""
    today = today or home.today()
    accounts = share.accounts(login)[0]
    month = budget.month(today)
    cash = ahead.forecast(today)
    flows = recurring.flows(today) if accounts else []
    first, second = sentence(month, cash, accounts)
    worth = plan.net_worth(login)
    soon = today + timedelta(days=SOON_DAYS)
    coming = [{'day': on.isoformat(), 'name': f['name'], 'amount': f['amount'], 'about': f['about'], 'kind': f['kind'], 'key': f['key'],
               'source': f['source'], 'id': f.get('id')}
              for on, f in recurring.between(flows, today, soon)]
    rows = share.txns(login)[:RECENT]
    return {
        'ok': True, 'today': today.isoformat(), 'sentence': first, 'under': second,
        'needs': needs(login, today, month, cash, flows),
        'month': dict(month, suggested=budget.suggest(today) if not month['budget'] else {}),
        'ahead': cash,
        'coming': coming,
        'flows': [dict(f, monthly=round(recurring.monthly(f), 2)) for f in flows],
        'goals': plan.goals(today, login),
        'worth': dict(worth, groups=[dict(g, total=round(g['total'], 2), accounts=[_account(a, login) for a in g['accounts']])
                                     for g in worth['groups']]),
        'history': plan.history(login),
        'retire': plan.retirement(today, login),
        'recent': [_txn(r) for r in rows],
        'unsorted': unsorted(login, today),
        'categories': [{'key': k, 'name': n, 'group': g} for k, n, g in categories.CATEGORIES],
        'kinds': [{'key': k, 'name': n} for k, n in kinds.NAMES.items()],
        'links': [{'id': link['id'], 'mine': share.mine(login, link['login']), 'owner': share.first_name(link['login']),
                   'read_at': link['read_at'], 'ok': link['ok'] != 0, 'said': link['said'] or ''} for link in sync.links()],
        'changes': changes.recent(login),
        'model': share.model_on(login),
        'cushion': ahead.cushion(),
    }


def today_line(login, today=None):
    """One line for Today: the most pressing thing about money, or how the month is going. None for no member,
    and for a member with no accounts read yet."""
    today = today or home.today()
    if share.person(login) is None or not share.accounts(login)[0]:
        return None
    month = budget.month(today)
    cash = ahead.forecast(today)
    found = needs(login, today, month, cash)
    first, _ = sentence(month, cash, True)
    if found:
        top = found[0]
        return {'title': top['title'], 'why': top['why'], 'more': len(found) - 1, 'part': top.get('part'), 'key': top['key']}
    return {'title': first, 'why': '', 'more': 0, 'part': 'month'}
