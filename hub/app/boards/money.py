"""The money dashboard: how the month is going, the weeks ahead, bills, goals, accounts and the long run.

Money is a category like any other (hub/app/boards/__init__.py): Ask knows it and answers about it (hub/app/money/ask.py),
what needs a member shows on Today through `line`, and this is its one page. Everything here is worked out from Money's
own tables by hub/app/money/picture.py, in code: opening the board never reads a bank and never asks a model.

Who sees it: the household's members, on their own phone or laptop (hub/people.py). The wall and programs on the Mac
have no login and see nothing. Each member sees every shared account and the household's budget and plans; another
member's private account is only its rounded total (hub/app/money/share.py).

The board's settings are each member's own, not the household's, so they don't use the boards' shared settings
table: connecting a bank with a SimpleFIN setup token, which of their own accounts are private, and whether Ask's
model may use their money at all. Those are changed here only, never from Ask.
"""
from ... import people
from ..money import ask, changes, say, share, sync, taps
from ..money import picture as screen

ID = 'money'
NAME = 'Money'
WHAT = 'The month against the budget, the weeks ahead, bills, goals and accounts'
ROWS = 8
URGENT = ('cash-low', 'over-', 'link-')        # what on Today is drawn as needing a look soon


class Refused(Exception):
    """A setting that can't be kept as asked; the words are for the member."""


def sees(login):
    return login is not None and people.who(login) is not None and share.person(login) is not None


def _money(amount, cents=False):
    return say.dollars(amount, cents=cents) if amount is not None else '—'


def _connect_blocks():
    return [{'id': 'connect', 'title': 'Connect your banks', 'kind': 'note',
             'text': 'Sign up at SimpleFIN Bridge (bridge.simplefin.org), connect your banks there, then make a setup token and '
                     'paste it under Arrange > Settings below. Hub reads the accounts once or twice a day and can never move money. '
                     'You can also ask Hub things like “how is the month going?” once it has read them.'}]


def picture(login, today):
    found = screen.screen(login, today)
    month, cash = found['month'], found['ahead']
    blocks = []
    if found['needs']:
        blocks.append({'id': 'needs', 'title': 'Needs you', 'kind': 'rows',
                       'rows': [{'title': n['title'], 'sub': n['why'], 'tone': 'warn' if n['key'].startswith(URGENT) else ''}
                                for n in found['needs'][:ROWS]]})
    if not found['worth']['groups'] and not found['links']:
        blocks += _connect_blocks()
    else:
        low = cash.get('low') if cash.get('ok') else None
        blocks.append({'id': 'numbers', 'title': 'Right now', 'kind': 'tiles', 'tiles': [
            {'value': _money(month['spent']), 'label': 'spent in ' + month['month']},
            {'value': _money(month['budget']) if month['budget'] else '—', 'label': 'budget' if month['budget'] else 'no budget yet'},
            {'value': _money(cash.get('start')) if cash.get('ok') else '—', 'label': 'in checking'},
            {'value': _money(low['balance']) if low else '—', 'label': 'low point, 6 weeks',
             'tone': 'warn' if low and low['balance'] < cash['cushion'] else ''},
        ]})
        rows = [c for c in month['categories'] if c['spent'] or c['budget']]
        blocks.append({'id': 'month', 'title': month['month'] + ' by category', 'kind': 'bars', 'empty': 'Nothing spent yet this month.',
                       'bars': [{'label': c['name'], 'value': max(c['spent'], 0),
                                 'text': _money(c['spent']) + (f' of {_money(c["budget"])}' if c['budget'] else '')}
                                for c in sorted(rows, key=lambda c: -c['spent'])[:10]]})
        blocks.append({'id': 'coming', 'title': 'Next two weeks', 'kind': 'rows', 'empty': 'No bills or paydays in the next two weeks.',
                       'rows': [{'title': c['name'], 'sub': say.day(c['day'], today), 'right': ('about ' if c['about'] else '') + _money(c['amount'], cents=True),
                                 'tone': '' if c['amount'] < 0 else 'ok'} for c in found['coming'][:ROWS]]})
        if found['unsorted']:
            blocks.append({'id': 'unsorted', 'title': 'Charges Hub couldn’t place', 'kind': 'rows',
                           'sub': 'Tell Ask, such as “the farmers market is groceries”, and Hub sorts that payee from then on.',
                           'rows': [{'title': t['payee'], 'sub': say.day(t['day'], today), 'right': _money(t['amount'], cents=True)}
                                    for t in found['unsorted'][:ROWS]]})
        if found['goals']:
            blocks.append({'id': 'goals', 'title': 'Goals', 'kind': 'rows',
                           'rows': [{'title': g['name'], 'sub': f'{_money(g["saved"])} of {_money(g["target"])}'
                                     + (f', by {say.day(g["by"], today)}' if g.get('by') else ''),
                                     'right': f'{_money(g["monthly"])}/mo' if g.get('monthly') else '', 'tone': 'warn' if g.get('late') else ''}
                                    for g in found['goals']]})
        worth = found['worth']
        accounts = [a for g in worth['groups'] for a in g['accounts'] if not a['closed']]
        blocks.append({'id': 'accounts', 'title': 'Accounts', 'kind': 'rows', 'empty': 'No accounts read yet.',
                       'rows': [{'title': a['name'], 'sub': ' · '.join(x for x in (a['kind_name'], a['owner'] if not a['mine'] else '',
                                                                                    'private' if a['private'] else '') if x),
                                 'right': _money(a['balance'])} for a in accounts]
                       + [{'title': 'Net worth', 'right': _money(worth['total']), 'tone': 'ok'}]})
        retire = found['retire']
        if retire.get('ready'):
            ages = {p['name']: p['age'] for p in retire['paths']}
            middle = retire['paths'][1]['age']
            blocks.append({'id': 'retire', 'title': 'The long run', 'kind': 'hero',
                           'big': f'The money lasts from about {middle}' if middle else 'Not by 80 on the middle path',
                           'small': ' · '.join(f'{name}: {age or "past 80"}' for name, age in ages.items())})
        else:
            blocks.append({'id': 'retire', 'title': 'The long run', 'kind': 'note',
                           'text': 'Tell Ask your ages and what you expect to spend, such as “I’m 41 and Sam is 39”, and Hub works out '
                                   'the earliest age the money lasts.'})
        recent = found['changes'][:ROWS]
        if recent:
            blocks.append({'id': 'changes', 'title': 'What changed', 'kind': 'rows',
                           'rows': [{'title': c['said'], 'sub': 'Undone' if c['undone'] else ''} for c in recent]})
    blocks.append({'id': 'about', 'title': 'About Money', 'kind': 'note',
                   'text': 'Hub reads the accounts and can never pay, transfer or trade. Account numbers never leave Hub. '
                           'Hub is not a financial adviser: it says what each answer rests on.'})
    return {'say': found['sentence'], 'sub': found['under'], 'blocks': blocks, 'settings': settings(login)}


def settings(login):
    """This member's own settings, shown when they arrange the board."""
    out = [{'key': 'token', 'label': 'Bank setup token (SimpleFIN)', 'type': 'text', 'value': ''}]
    for account in share.accounts(login)[0]:
        if share.mine(login, account['owner']):
            out.append({'key': f'private:{account["id"]}', 'label': f'Keep {account["name"]} private',
                        'type': 'switch', 'value': 'on' if account['share'] == 'private' else ''})
    out.append({'key': 'ask', 'label': 'Ask may use my money to answer', 'type': 'switch', 'value': 'on' if share.model_on(login) else ''})
    return out


def set_for(login, key, value):
    """Keep one of this member's settings; the words say what happened."""
    key, value = str(key or ''), str(value or '').strip()
    on = value.lower() in ('on', 'true', '1', 'yes')
    try:
        if key == 'token':
            return sync.connect(value, login)
        if key == 'ask':
            return taps.model(login, on)[0]
        if key.startswith('private:'):
            return taps.private(login, key.split(':', 1)[1], on)[0]
    except (sync.Refused, taps.Refused, changes.Refused) as problem:
        raise Refused(str(problem)) from problem
    raise Refused('Hub couldn’t read that request.')


def line(login, today):
    found = screen.today_line(login, today)
    if not found:
        return None
    return {'text': found['title'], 'next': found['why'] + (f' And {found["more"]} more.' if found['more'] else ''),
            'tone': 'warn' if str(found.get('key', '')).startswith(URGENT) else ''}


def facts(login, today):
    """What Ask's model may be given: Money's own allow-list, or a line saying this member turned it off."""
    found = ask.overview(login, today)
    return found if found is not None else {'note': 'This member keeps their money out of Ask’s model.'}
