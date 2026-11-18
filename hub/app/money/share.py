"""Who sees what of Money: the one place that decides it, keyed on the person asking.

Owner decision (2026-11-16, "Shared, with private accounts"):
- Every member (a login `hub/people.py` knows) sees every account and the family's budget and plans, whoever
  connected it. No login, or one Hub doesn't know, sees nothing of Money at all.
- An account belongs to whoever connected it or gave Hub its statement. Only they can mark it private. The other
  member then sees it only inside the totals, as one line per person ("Sam's private accounts"), never its name,
  its bank or a transaction. So that the line can't be watched day by day to work out what was spent, it holds
  the balance as it was on the first of the month, rounded to $100.
- A private account stays out of the household's budget, bills and cash ahead. Money moved between a shared
  account and a private one is shown on the shared side as "To a private account".

Every reading of accounts and transactions in Money goes through `accounts()` and `txns()` here.
"""
from ... import people
from . import store

ROUND = 100


def person(login):
    """The member this login is, {'name', 'logins', 'accounts'}, or None: no Money for them."""
    return people.who(login)


def key(login):
    """One name for a member however many logins they have, for their own switches and their Undo."""
    found = person(login)
    return found['logins'][0] if found else None


def mine(login, owner):
    """Whether `owner` (the login that connected an account) is this member."""
    found = person(login)
    return bool(found and owner and str(owner).strip().lower() in found['logins'])


def first_name(owner):
    found = person(owner)
    return (found['name'].split() or [''])[0] if found and found['name'] else ''


def _open(account):
    return not account['closed']


def all_accounts():
    return store.rows('select * from money_accounts order by created_at')


def accounts(login, everything=False):
    """The accounts this member sees in full, and [{'owner', 'name', 'count', 'worth', 'kinds'}] for the private
    accounts of others they see only as a total. `everything` keeps closed ones too."""
    from . import kinds
    if person(login) is None:
        return [], []
    full, hidden = [], {}
    for account in all_accounts():
        if not everything and not _open(account):
            continue
        if account['share'] == 'private' and not mine(login, account['owner']):
            if not _open(account):
                continue
            line = hidden.setdefault(account['owner'], {'owner': account['owner'], 'name': first_name(account['owner']), 'count': 0,
                                                        'worth': 0.0, 'invested': 0.0})
            line['count'] += 1
            value = kinds.worth(account['kind'], month_start(account))
            line['worth'] += value
            if account['kind'] in kinds.INVESTED and account['kind'] != 'college':
                line['invested'] += value
            continue
        full.append(account)
    for line in hidden.values():
        line['worth'] = round(line['worth'] / ROUND) * ROUND
        line['invested'] = round(line['invested'] / ROUND) * ROUND
    return full, list(hidden.values())


def month_start(account):
    """A private account's balance as others may see it: on the first of this month (or its first known after)."""
    from ... import home
    first = home.today().replace(day=1).isoformat()
    row = store.one('select balance from money_balances where account=? and day>=? order by day limit 1', (account['id'], first)) \
        or store.one('select balance from money_balances where account=? order by day desc limit 1', (account['id'],))
    return row['balance'] if row else (account['balance'] or 0.0)


def household():
    """The accounts the household's budget, bills and cash ahead are made of: shared, open, counted in the budget."""
    return [a for a in all_accounts() if a['share'] == 'shared' and _open(a) and a['in_budget']]


def txns(login, where='1', args=(), household_only=False):
    """Transactions this member may see (a list of dicts with the account's name and kind beside each), newest
    first. `household_only`: only those that count for the household's budget."""
    shown = {a['id']: a for a in (household() if household_only else accounts(login)[0])}
    if person(login) is None or not shown:
        return []
    marks = ','.join('?' * len(shown))
    found = store.rows(f'select * from money_txns where account in ({marks}) and ({where}) order by day desc, id desc',
                       (*shown, *args))
    for row in found:
        account = shown[row['account']]
        row['account_name'], row['account_kind'] = account['name'], account['kind']
    return found


def private_side(txn):
    """Whether a transaction is one side of money moved to or from an account someone keeps private."""
    if not txn.get('pair'):
        return False
    other = store.one('select a.share from money_txns t join money_accounts a on a.id=t.account where t.id=?', (txn['pair'],))
    return bool(other and other['share'] == 'private')


# ---- "Money in Ask": each member's own switch (owner decision: on unless they turn it off) ----

def model_on(login):
    """Whether Ask's model may be given money for this member's own requests."""
    who = key(login)
    return bool(who) and store.setting('ask:' + who, True) is not False


def model_may_see(account):
    """Whether an account may reach Ask's model at all: not a private one whose owner switched Money in Ask off."""
    return account['share'] != 'private' or model_on(account['owner'])
