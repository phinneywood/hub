"""What a member can change in Money, from the screen or from Ask, each kept with its Undo (changes.py).

None of it reaches a bank: a budget, a category and the rule it teaches, a goal, a bill Hub can't see, an account's
name, kind or place in the budget, the cushion and the retirement plan's assumptions are Hub's own record.

Two switches are the screen's alone, never Ask's: marking an account private (only whoever connected it), and
"Money in Ask" (each member's own). A model can't flip what decides what a model may see.
"""
import json
import math
from datetime import date

from . import categories, changes, kinds, recurring, say, share, store, sync

EVERY = tuple(recurring.EVERY)


class Refused(Exception):
    """It can't be done as asked. The message is safe to show."""


def _member(login):
    if share.person(login) is None:
        raise Refused('Change money from your own phone or laptop.')


def _amount(value, what='amount'):
    try:
        number = round(float(str(value).replace('$', '').replace(',', '')), 2)
    except (TypeError, ValueError):
        raise Refused(f'Hub couldn’t read that {what}.') from None
    if not math.isfinite(number) or number < 0 or number > 10_000_000:
        raise Refused(f'Hub couldn’t read that {what}.')
    return number


def _category(words):
    found = categories.named(words)
    if not found:
        raise Refused(f'Hub doesn’t have a category called “{str(words)[:40]}”. Try one like groceries, eating out or utilities.')
    return found


def _account(login, words, own=False):
    """The account a member means, by its name (or its last four digits), among those they see in full."""
    plain = ' '.join(str(words or '').lower().split()).removeprefix('the ').removesuffix(' account')
    seen = share.accounts(login)[0]
    found = [a for a in seen if a['id'] == str(words or '')] or [a for a in seen if a['name'].lower() == plain] or [a for a in seen if plain and plain in a['name'].lower()] \
        or [a for a in seen if plain and plain == (a['ends'] or '')] \
        or [a for a in seen if plain and plain in f'{a["bank"]} {a["name"]}'.lower()]
    if not found:
        raise Refused(f'Hub didn’t find an account called “{str(words)[:40]}”.')
    if len(found) > 1:
        raise Refused('Which one: ' + ', '.join(a['name'] for a in found[:4]) + '?')
    if own and not share.mine(login, found[0]['owner']):
        raise Refused('Only whoever connected that account can change who sees it.')
    return found[0]


# ---- the budget ----

def set_budget(login, category, amount, via='ask'):
    """Set (or with amount None, clear) one category's monthly budget. Returns (said, batch)."""
    _member(login)
    key = _category(category)
    if categories.GROUP.get(key) not in ('everyday', 'bills'):
        raise Refused(f'{categories.NAMES[key]} isn’t spending, so it has no budget.')
    snap = changes.snap('money_budgets', key)
    name = categories.NAMES[key]
    if amount is None:
        if snap['row'] is None:
            raise Refused(f'{name} has no budget to take off.')
        store.run('delete from money_budgets where category=?', (key,))
        said = f'{name} has no budget now.'
    else:
        value = _amount(amount)
        store.run('insert into money_budgets(category, monthly, login, set_at) values(?,?,?,?) on conflict(category) do update set '
                  'monthly=excluded.monthly, login=excluded.login, set_at=excluded.set_at', (key, value, share.key(login), store.now()))
        said = f'{name} is {say.dollars(value)} a month.'
    return said, changes.record(login, via, said, [snap])


def use_suggested(login, suggested, via='ask'):
    """Keep Hub's suggested budget for every category it suggests one for."""
    _member(login)
    if not suggested:
        raise Refused('Hub needs two whole months of spending before it can suggest a budget.')
    snaps = [changes.snap('money_budgets', key) for key in suggested]
    for key, value in suggested.items():
        store.run('insert into money_budgets(category, monthly, login, set_at) values(?,?,?,?) on conflict(category) do update set '
                  'monthly=excluded.monthly, login=excluded.login, set_at=excluded.set_at', (key, value, share.key(login), store.now()))
    total = sum(suggested.values())
    said = f'The budget is {say.dollars(total)} a month across {len(suggested)} categories.'
    return said, changes.record(login, via, said, snaps)


# ---- sorting ----

def sort(login, payee, category, via='ask', only=None):
    """Teach Hub a payee's category from now on (and for what it already read), or with `only` (a transaction id),
    change just that one. Returns (said, batch)."""
    _member(login)
    key = _category(category)
    if only is not None:
        try:
            rows = share.txns(login, 'id=?', (int(only),))
        except (TypeError, ValueError):
            rows = []
        if not rows:
            raise Refused('Hub doesn’t see that transaction.')
        row = rows[0]
        snap = changes.snap('money_txns', row['id'], cols=('category', 'how'))
        store.run("update money_txns set category=?, how='mine' where id=?", (key, row['id']))
        said = f'{categories.name(row["payee"])} on {say.day(row["day"])} is {categories.NAMES[key].lower()}.'
        return said, changes.record(login, via, said, [snap])
    words = categories.rule_words(payee)
    if len(words) < 2:
        raise Refused('Say which payee, as it shows on the screen.')
    touched = [r for r in share.txns(login, "coalesce(how, '') not in ('mine', 'pair')")
               if categories.by_rule(r['payee'], [(words, key)])]
    snaps = [changes.snap('money_txns', r['id'], cols=('category', 'how')) for r in touched]
    rule = store.one('select * from money_rules where words=?', (words,))
    snaps.append(changes.snap('money_rules', rule['id']) if rule else {'t': 'money_rules', 'v': None, 'row': None, 'cols': []})
    store.run('insert into money_rules(words, category, login, made_at) values(?,?,?,?) on conflict(words) do update set '
              'category=excluded.category, login=excluded.login, made_at=excluded.made_at', (words, key, share.key(login), store.now()))
    if rule is None:
        snaps[-1]['v'] = store.one('select id from money_rules where words=?', (words,))['id']
    sync.settle()
    called = categories.name(touched[0]['payee']) if touched else words.title()
    said = (f'{called} is {categories.NAMES[key].lower()} from now on'
            + (f', and {len(touched)} earlier charge{"s" * (len(touched) != 1)} moved.' if touched else '.'))
    return said, changes.record(login, via, said, snaps)


# ---- goals ----

def _goal(name):
    plain = ' '.join(str(name or '').lower().split()).removeprefix('the ').removesuffix(' goal').removesuffix(' fund')
    rows = store.rows('select * from money_goals where done_at is null')
    found = [g for g in rows if g['name'].lower() == plain] or [g for g in rows if plain and plain in g['name'].lower()]
    if len(found) != 1:
        raise Refused('Hub didn’t find that goal.' if not found else 'Which goal: ' + ', '.join(g['name'] for g in found) + '?')
    return found[0]


def goal(login, do, name, target=None, by=None, saved=None, account=None, via='ask'):
    """add, change or remove (also: done) a goal. `by`: a day (YYYY-MM-DD). Returns (said, batch)."""
    _member(login)
    linked = _account(login, account)['id'] if account else None
    by_day = None
    if by:
        try:
            by_day = date.fromisoformat(str(by)[:10]).isoformat()
        except ValueError:
            raise Refused('Hub couldn’t read that day.') from None
    if do == 'add':
        if not str(name or '').strip() or target is None:
            raise Refused('A goal needs a name and an amount.')
        number = store.run('insert into money_goals(name, target, by_day, saved, account, owner, made_at) values(?,?,?,?,?,?,?)',
                           (str(name).strip()[:60], _amount(target), by_day, _amount(saved or 0), linked, share.key(login), store.now()))
        said = f'{str(name).strip()[:60]}: {say.dollars(_amount(target))}' + (f' by {say.day(by_day)}' if by_day else '') + '.'
        return said, changes.record(login, via, said, [{'t': 'money_goals', 'v': number, 'row': None, 'cols': []}])
    row = _goal(name)
    snap = changes.snap('money_goals', row['id'])
    if do in ('remove', 'done'):
        store.run('update money_goals set done_at=? where id=?', (store.now(), row['id']))
        said = f'{row["name"]} is {"done" if do == "done" else "gone"}.'
    else:
        store.run('update money_goals set target=coalesce(?, target), by_day=coalesce(?, by_day), saved=coalesce(?, saved), '
                  'account=coalesce(?, account) where id=?',
                  (_amount(target) if target is not None else None, by_day, _amount(saved) if saved is not None else None, linked, row['id']))
        fresh = store.one('select * from money_goals where id=?', (row['id'],))
        said = f'{fresh["name"]}: {say.dollars(fresh["target"])}' + (f' by {say.day(fresh["by_day"])}' if fresh['by_day'] else '') + '.'
    return said, changes.record(login, via, said, [snap])


# ---- bills Hub can't see, and ones that aren't bills ----

def bill(login, do, name, amount=None, every='month', next_day=None, category=None, via='ask'):
    """add a bill Hub can't see in the accounts; remove one a member added; or `not` (one Hub found isn't a bill)."""
    _member(login)
    if do == 'add':
        if every not in EVERY:
            raise Refused('Say how often: every week, 2 weeks, month, quarter or year.')
        try:
            on = date.fromisoformat(str(next_day)[:10])
        except (TypeError, ValueError):
            raise Refused('Say when it is next due.') from None
        key = _category(category) if category else 'other'
        number = store.run('insert into money_bills(name, amount, every, next_day, category, login, made_at) values(?,?,?,?,?,?,?)',
                           (str(name).strip()[:60], _amount(amount), every, on.isoformat(), key, share.key(login), store.now()))
        said = f'{str(name).strip()[:60]}, {say.dollars(_amount(amount))} every {every}, next on {say.day(on)}.'
        return said, changes.record(login, via, said, [{'t': 'money_bills', 'v': number, 'row': None, 'cols': []}])
    from ... import home
    flows = recurring.flows(home.today())
    plain = ' '.join(str(name or '').lower().split())
    found = [f for f in flows if plain and plain in f['name'].lower()]
    if len(found) != 1:
        raise Refused('Hub didn’t find that bill.' if not found else 'Which one: ' + ', '.join(f['name'] for f in found[:4]) + '?')
    flow = found[0]
    if flow['source'] == 'you':
        snap = changes.snap('money_bills', flow['id'])
        store.run('delete from money_bills where id=?', (flow['id'],))
        said = f'{flow["name"]} is off the bills.'
        return said, changes.record(login, via, said, [snap])
    snap = changes.snap('money_plan', 'not_bills')
    kept = list(store.plan('not_bills', []) or []) + [flow['key']]
    store.run('insert or replace into money_plan(key, value) values(?,?)', ('not_bills', json.dumps(kept)))
    said = f'{flow["name"]} won’t count as a bill.'
    return said, changes.record(login, via, said, [snap])


# ---- accounts ----

def account(login, words, kind=None, name=None, in_budget=None, via='ask'):
    """Rename an account, say what kind it is, or put it in or out of the household's budget."""
    _member(login)
    found = _account(login, words)
    snap = changes.snap('money_accounts', found['id'], cols=('name', 'kind', 'kind_set', 'in_budget'))
    told = []
    if kind is not None:
        if kind not in kinds.NAMES:
            raise Refused('Hub doesn’t know that kind of account.')
        store.run('update money_accounts set kind=?, kind_set=1 where id=?', (kind, found['id']))
        told.append(f'is {kinds.NAMES[kind].lower()}')
    if name:
        store.run('update money_accounts set name=? where id=?', (str(name).strip()[:60], found['id']))
        told.append(f'is called {str(name).strip()[:60]}')
    if in_budget is not None:
        store.run('update money_accounts set in_budget=? where id=?', (int(bool(in_budget)), found['id']))
        told.append('counts in the budget' if in_budget else 'is out of the budget')
    if not told:
        raise Refused('Say what to change about it.')
    said = f'{found["name"]} ' + ' and '.join(told) + '.'
    return said, changes.record(login, via, said, [snap])


def private(login, account_id, on):
    """The screen's switch: only whoever connected an account can make it private or shared again."""
    _member(login)
    found = store.one('select * from money_accounts where id=?', (account_id,))
    if found is None or not share.mine(login, found['owner']):
        raise Refused('Only whoever connected that account can change who sees it.')
    snap = changes.snap('money_accounts', found['id'], cols=('share',))
    store.run('update money_accounts set share=? where id=?', ('private' if on else 'shared', found['id']))
    said = f'{found["name"]} is {"private: others see it only in the totals" if on else "shared"}.'
    return said, changes.record(login, 'app', said, [snap])


def model(login, on):
    """The screen's switch: whether Ask's model may be given money for this member's requests and private accounts."""
    _member(login)
    key = 'ask:' + share.key(login)
    snap = changes.snap('money_settings', key)
    store.put(key, bool(on))
    said = 'Ask can use your money to answer.' if on else 'Ask’s model gets nothing of your money now; Hub answers with its own cards.'
    return said, changes.record(login, 'app', said, [snap])


# ---- the plan ----

PLAN_KEYS = ('people', 'spend', 'saving', 'retire_at', 'cushion')


def set_plan(login, via='ask', **told):
    """Keep what a member said the plan should assume. `people`: [{'name', 'age', 'ss', 'ss_age'}]."""
    _member(login)
    keep = {}
    for key in PLAN_KEYS:
        value = told.get(key)
        if value is None:
            continue
        if key == 'people':
            people = []
            for p in value if isinstance(value, list) else []:
                if not isinstance(p, dict):
                    continue
                age = _amount(p.get('age'), 'age') if p.get('age') is not None else None
                if age is None or not 16 <= age <= 100:
                    raise Refused('Say each person’s age.')
                people.append({'name': str(p.get('name') or '').strip()[:30], 'age': int(age),
                               'ss': _amount(p.get('ss') or 0), 'ss_age': int(_amount(p.get('ss_age') or 67, 'age'))})
            if not people:
                raise Refused('Say each person’s age.')
            keep[key] = people
        elif key == 'retire_at':
            keep[key] = int(_amount(value, 'age'))
        else:
            keep[key] = _amount(value)
    if not keep:
        raise Refused('Say what to change in the plan.')
    snaps = [changes.snap('money_plan', key) for key in keep]
    for key, value in keep.items():
        store.run('insert or replace into money_plan(key, value) values(?,?)', (key, json.dumps(value)))
    words = {'people': 'ages and Social Security', 'spend': f'{say.dollars(keep.get("spend"))} a year in retirement',
             'saving': f'{say.dollars(keep.get("saving"))} a month put away', 'retire_at': f'retiring at {keep.get("retire_at")}',
             'cushion': f'a {say.dollars(keep.get("cushion"))} cushion in checking'}
    said = 'The plan now assumes ' + ', '.join(words[k] for k in keep) + '.'
    return said, changes.record(login, via, said, snaps)
