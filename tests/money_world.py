"""A made-up household's money, and a stand-in for SimpleFIN Bridge that serves it.

Alex and Sam (fictional) each connect their own banks. Alex's connection: a joint checking account, a card, a
savings account and a Roth IRA. Sam's: Sam's own checking (Sam marks it private in the tests) and a 401(k).
Four months of an ordinary family's money, the same every run: two paydays, rent, daycare, power that varies, the
internet, insurance, a monthly move to savings and to the IRA, the card paid off each month, groceries and eating
out on the card, a streaming price that went up, a refund, and one charge still pending today.

`install(monkeypatch)` fixes the day, the household's people and the network: the only thing that answers is
this Bridge, at https://beta-bridge.simplefin.org, and it counts every request it is sent.
"""
import base64
import json
import random
from datetime import date, datetime, timedelta

import httpx

from hub import config, home
from hub.app.money import simplefin, store

ALEX, SAM, GUEST = 'alex@example.com', 'sam@example.com', 'guest@example.com'
TODAY = date(2026, 11, 17)
NOW = datetime(2026, 11, 17, 9, 30, tzinfo=home.PACIFIC).timestamp()
START = TODAY - timedelta(days=130)
HOST = 'beta-bridge.simplefin.org'


def token(who):
    return base64.b64encode(f'https://{HOST}/simplefin/claim/{who}-token'.encode()).decode()


def _at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=home.PACIFIC).timestamp()


def _days():
    day = START
    while day <= TODAY:
        yield day
        day += timedelta(days=1)


def build():
    """{user: [accounts as SimpleFIN v2 sends them]}, with every transaction since START."""
    luck = random.Random(7)
    txns = {k: [] for k in ('chk', 'visa', 'sav', 'ira', 'sam-chk', 'sam-401k')}
    count = [0]

    def add(account, day, amount, line, pending=False):
        count[0] += 1
        txns[account].append({'id': f'TRN-{count[0]:05d}', 'posted': 0 if pending else int(_at(day)), 'amount': f'{amount:.2f}',
                              'description': line, 'transacted_at': int(_at(day, 10)), 'pending': pending})

    statement = 0.0
    for day in _days():
        if day.weekday() == 4 and (day - date(2026, 7, 10)).days % 14 == 0:
            add('chk', day, 3150.00, 'ACME CORP PAYROLL DIR DEP PPD ID: 9182736450')
        if day.day == 15:
            add('sam-chk', day, 2600.00, 'NORTHWIND TRADERS PAYROLL')
        if day.day == 16:
            add('sam-chk', day, -1200.00, 'ONLINE TRANSFER TO CHK ...4471')
            add('chk', day, 1200.00, 'ONLINE TRANSFER FROM CHK ...9930')
        if day.day == 1:
            add('chk', day, -2400.00, 'OAKWOOD PROPERTY MGMT RENT WEB PMTS')
        if day.day == 2:
            add('chk', day, -500.00, 'ONLINE TRANSFER TO SAVINGS 4421')
            add('sav', day, 500.00, 'ONLINE TRANSFER FROM CHECKING 4471')
        if day.day == 3:
            add('chk', day, -500.00, 'EVERGREEN INVEST ROTH CONTRIB TRANSFER')
            add('ira', day, 500.00, 'CONTRIBUTION TRANSFER DEPOSIT')
        if day.day == 5:
            add('chk', day, -1850.00, 'BRIGHT HORIZONS TUITION')
        if day.day == 8:
            add('chk', day, -80.00, 'COMCAST XFINITY 8155 ONLINE PMT')
        if day.day == 12:
            add('chk', day, -round(luck.uniform(95, 125), 2), 'SPRINGFIELD CITY LIGHT UTIL PMT')
        if day.day == 22:
            add('chk', day, -142.50, 'GEICO AUTO INSURANCE')
        if day.day == 25 and statement:
            add('chk', day, -round(statement, 2), 'CASCADE CARD AUTOPAY PMT')
            add('visa', day, round(statement, 2), 'AUTOMATIC PAYMENT - THANK YOU')
            statement = 0.0
        if day.weekday() == 5:
            add('chk', day, -35.00, 'SQ *FARMERS MARKET SPRINGFIELD WA')
        charges = []
        if day.weekday() == 6:
            charges.append((-round(luck.uniform(110, 160), 2), 'TRADER JOE S #552 SPRINGFIELD WA'))
        if day.weekday() == 2:
            charges.append((-round(luck.uniform(40, 80), 2), 'SAFEWAY #1234 SPRINGFIELD WA'))
            charges.append((-round(luck.uniform(40, 60), 2), 'CHEVRON 0091234 SPRINGFIELD WA'))
        if day.day == 20:
            charges.append((-round(luck.uniform(180, 260), 2), 'COSTCO WHSE #0001 SPRINGFIELD WA'))
        if day.weekday() in (0, 2, 4):
            charges.append((-6.50, 'STARBUCKS STORE 01234 SPRINGFIELD WA'))
        if day.weekday() in (3, 5):
            charges.append((-round(luck.uniform(35, 70), 2), 'TST* LITTLE THAI KITCHEN SPRINGFIELD WA'))
        if day.day == 14:
            charges.append((-15.49, 'NETFLIX.COM'))
        if day.day == 9:
            charges.append((-(12.99 if day.month >= 11 else 11.99), 'SPOTIFY USA'))
        if day.day in (6, 19):
            charges.append((-round(luck.uniform(15, 90), 2), 'AMZN Mktp US*2K4L19XY0'))
        if day == date(2026, 10, 28):
            charges.append((29.99, 'AMZN Mktp US*RETURN 2K4L19'))
        if day.day == 11 and day.month in (9, 10):
            add('sam-chk', day, -64.00, 'SEPHORA 0412 SPRINGFIELD')
        for amount, line in charges:
            if day == TODAY:
                continue
            add('visa', day, amount, line)
            statement -= amount
    add('visa', TODAY, -87.12, 'TRADER JOE S #552 SPRINGFIELD WA', pending=True)
    opening = {'chk': 6000.0, 'visa': 0.0, 'sav': 12000.0, 'ira': 84000.0, 'sam-chk': 3000.0, 'sam-401k': 61000.0}
    balance = {k: round(opening[k] + sum(float(t['amount']) for t in txns[k] if not t['pending']), 2) for k in txns}
    names = {'chk': ('Everyday Checking', 'c-alex-1'), 'visa': ('Rewards Visa', 'c-alex-1'), 'sav': ('High Yield Savings', 'c-alex-1'),
             'ira': ('Roth IRA', 'c-alex-2'), 'sam-chk': ('Sam Checking', 'c-sam-1'), 'sam-401k': ('Sam 401(k)', 'c-sam-2')}
    out = {'alex': [], 'sam': []}
    for key, (name, conn) in names.items():
        out['sam' if key.startswith('sam') else 'alex'].append(
            {'id': f'ACT-{key}', 'name': name, 'conn_id': conn, 'currency': 'USD', 'balance': f'{balance[key]:.2f}',
             'available-balance': f'{balance[key]:.2f}', 'balance-date': int(NOW - 3600), 'transactions': txns[key]})
    return out


CONNECTIONS = {'alex': [{'conn_id': 'c-alex-1', 'name': 'Cascade Bank', 'org_id': 'o1', 'sfin_url': f'https://{HOST}/simplefin'},
                        {'conn_id': 'c-alex-2', 'name': 'Evergreen Invest', 'org_id': 'o2', 'sfin_url': f'https://{HOST}/simplefin'}],
               'sam': [{'conn_id': 'c-sam-1', 'name': 'Harbor Credit Union', 'org_id': 'o3', 'sfin_url': f'https://{HOST}/simplefin'},
                       {'conn_id': 'c-sam-2', 'name': 'Northwind Retirement', 'org_id': 'o4', 'sfin_url': f'https://{HOST}/simplefin'}]}


class Bridge:
    """Stands in for SimpleFIN Bridge: claims tokens once, and answers GET /accounts for the window asked."""

    def __init__(self):
        self.data = build()
        self.claimed = set()
        self.requests = []
        self.errors = []            # what the next reading says went wrong at a bank
        self.status = 200

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        path = request.url.path
        if request.method == 'POST' and '/claim/' in path:
            who = path.rsplit('/', 1)[-1].removesuffix('-token')
            if who in self.claimed or who not in self.data:
                return httpx.Response(403)
            self.claimed.add(who)
            return httpx.Response(200, text=f'https://{who}:secret-{who}@{HOST}/simplefin')
        if request.method == 'GET' and path.endswith('/accounts'):
            if self.status != 200:
                return httpx.Response(self.status)
            user = base64.b64decode(request.headers['authorization'].split()[1]).decode().split(':')[0]
            since = float(request.url.params.get('start-date', 0))
            until = float(request.url.params.get('end-date', 10 ** 12))
            accounts = []
            for account in self.data.get(user, []):
                shown = [t for t in account['transactions'] if t['pending'] or since <= t['posted'] < until]
                accounts.append(dict(account, transactions=shown))
            return httpx.Response(200, json={'errlist': [{'code': 'con.auth', 'msg': m} for m in self.errors],
                                             'connections': CONNECTIONS[user], 'accounts': accounts})
        return httpx.Response(404)


def people():
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'people': [
        {'name': 'Alex Rivera', 'logins': [ALEX], 'accounts': ['default']},
        {'name': 'Sam Rivera', 'logins': [SAM], 'accounts': ['sam']}]}))


def install(monkeypatch, connect=True):
    """The fixed day, Alex and Sam, and the Bridge. With `connect`, both have connected their banks."""
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ALEX, SAM, GUEST})
    people()
    monkeypatch.setattr(home, 'today', lambda: TODAY)
    clock = {'now': NOW}
    monkeypatch.setattr(store, 'now', lambda: clock['now'])
    bridge = Bridge()
    real = simplefin.client
    monkeypatch.setattr(simplefin, 'client', lambda transport=None: real(httpx.MockTransport(bridge)))
    bridge.clock = clock
    if connect:
        from hub.app.money import sync
        sync.connect(token('alex'), ALEX)
        sync.connect(token('sam'), SAM)
    return bridge
