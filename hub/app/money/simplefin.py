"""SimpleFIN Bridge: the one way Hub reaches a bank, and it can only read.

SimpleFIN (simplefin.org) is a read-only protocol by design: there is no request in it that pays, transfers or
trades. A member signs up at SimpleFIN Bridge, connects their banks there, and gives Hub a setup token. Hub claims
the token once (a POST to the address inside it) and gets back an access address with its own credentials, kept
in Hub's private folder (store.keep_access). From then on Hub only ever asks GET <access>/accounts.

The guard below looks at every request on its way out and refuses any that isn't one of those two, to SimpleFIN's
own hosts, so no setting, library update or mistake elsewhere can send anything else. The credentials never go in
an address that could be logged: the request carries them as Basic authentication.

Limits the Bridge sets, kept here: at most 24 requests a day for one access address (Hub makes a few), 90 days of
history a request, data refreshed about once a day.
"""
import base64
import binascii
import re
import time
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

HOSTS = re.compile(r'(^|\.)simplefin\.org$')
TIMEOUT = 60
DAYS = 90                   # the Bridge answers at most this many days of transactions a request
OVERLAP = 5                 # days read again each time, so a transaction that posted late isn't missed


class Refused(Exception):
    """SimpleFIN said no, or the token or address isn't one. The message is safe to show."""


class Down(Exception):
    """SimpleFIN couldn't be reached just now. The message is safe to show."""


class WrongRoute(Exception):
    """A request was about to leave for somewhere other than SimpleFIN's claim or accounts. It was not sent."""


def _ours(url):
    return url.scheme == 'https' and HOSTS.search(url.host or '') is not None


def guard(request: httpx.Request):
    """Only two requests may leave: claiming a setup token, and reading accounts."""
    path = request.url.path
    if not _ours(request.url):
        raise WrongRoute('Hub refused a request that was not to SimpleFIN.')
    if request.method == 'POST' and '/claim/' in path and not request.content:
        return
    if request.method == 'GET' and path.endswith('/accounts'):
        return
    raise WrongRoute('Hub refused a request SimpleFIN doesn’t need: it only claims a token and reads accounts.')


def client(transport=None):
    """The only HTTP client Money may use. `transport` is for tests; the guard stays on either way."""
    return httpx.Client(follow_redirects=False, timeout=TIMEOUT, event_hooks={'request': [guard]}, transport=transport)


def claim_address(token):
    """The address inside a setup token. Raises Refused."""
    plain = ''.join(str(token or '').split())
    try:
        address = base64.b64decode(plain + '=' * (-len(plain) % 4), validate=False).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError):
        raise Refused('That isn’t a SimpleFIN setup token. Copy the whole token from SimpleFIN Bridge.') from None
    parts = urlsplit(address)
    if parts.scheme != 'https' or not HOSTS.search(parts.hostname or '') or '/claim/' not in parts.path:
        raise Refused('That isn’t a SimpleFIN setup token. Copy the whole token from SimpleFIN Bridge.')
    return address


def claim(token, transport=None):
    """Claim a setup token: the access address to keep. Raises Refused or Down."""
    address = claim_address(token)
    try:
        with client(transport) as http:
            answer = http.post(address, headers={'Content-Length': '0'})
    except httpx.HTTPError:
        raise Down('SimpleFIN couldn’t be reached just now. Try again in a minute.') from None
    if answer.status_code == 403:
        raise Refused('SimpleFIN says that token was already used. If it wasn’t you, disable it at SimpleFIN Bridge, '
                      'then make a new one.')
    if answer.status_code != 200:
        raise Down(f'SimpleFIN didn’t take the token just now ({answer.status_code}). Try again in a minute.')
    access = answer.text.strip()
    parts = urlsplit(access)
    if parts.scheme != 'https' or not HOSTS.search(parts.hostname or '') or not parts.username or not parts.password:
        raise Refused('SimpleFIN answered with something Hub can’t use. Make a new token and try again.')
    return access


def _split(access):
    """(the address without its credentials, (user, password))."""
    parts = urlsplit(access)
    if parts.scheme != 'https' or not HOSTS.search(parts.hostname or '') or not parts.username:
        raise Refused('Hub’s SimpleFIN access isn’t usable. Connect again with a new setup token.')
    host = parts.hostname + (f':{parts.port}' if parts.port else '')
    bare = urlunsplit(('https', host, parts.path.rstrip('/'), '', ''))
    return bare, (unquote(parts.username), unquote(parts.password or ''))


def accounts(access, since=None, until=None, balances_only=False, transport=None):
    """One reading: {'accounts': [...], 'errors': [plain sentences]}, as SimpleFIN sent it. `since` and `until` are
    times (seconds); the Bridge allows at most DAYS between them. Raises Refused or Down."""
    bare, auth = _split(access)
    params = {'version': '2', 'pending': '1'}
    if balances_only:
        params['balances-only'] = '1'
    if since is not None:
        params['start-date'] = str(int(since))
    if until is not None:
        params['end-date'] = str(int(until))
    try:
        with client(transport) as http:
            answer = http.get(bare + '/accounts', params=params, auth=auth)
    except httpx.HTTPError:
        raise Down('SimpleFIN couldn’t be reached just now.') from None
    if answer.status_code == 402:
        raise Refused('SimpleFIN Bridge says the subscription needs paying. Hub reads nothing until it is.')
    if answer.status_code == 403:
        raise Refused('SimpleFIN refused Hub’s access: it was turned off or has expired. Connect again with a new setup token.')
    if answer.status_code != 200:
        raise Down(f'SimpleFIN didn’t answer just now ({answer.status_code}).')
    try:
        found = answer.json()
    except ValueError:
        raise Down('SimpleFIN answered with something Hub couldn’t read.') from None
    if not isinstance(found, dict) or not isinstance(found.get('accounts'), list):
        raise Down('SimpleFIN answered with something Hub couldn’t read.')
    errors = []
    for error in found.get('errlist') or []:
        if isinstance(error, dict) and error.get('msg'):
            errors.append(str(error['msg'])[:200])
    for error in found.get('errors') or []:
        if isinstance(error, str) and error not in errors:
            errors.append(error[:200])
    names = {c.get('conn_id'): c.get('name') or c.get('org_name') or '' for c in found.get('connections') or [] if isinstance(c, dict)}
    out = []
    for account in found['accounts']:
        if not isinstance(account, dict) or not account.get('id'):
            continue
        bank = names.get(account.get('conn_id')) or ((account.get('org') if isinstance(account.get('org'), dict) else None) or {}).get('name') or ''
        out.append({'id': str(account['id'])[:120], 'name': str(account.get('name') or 'Account')[:80], 'bank': str(bank)[:80],
                    'currency': str(account.get('currency') or 'USD')[:12], 'balance': _number(account.get('balance')),
                    'available': _number(account.get('available-balance')), 'at': _number(account.get('balance-date')) or time.time(),
                    'transactions': [t for t in (_txn(t) for t in account.get('transactions') or []) if t]})
    return {'accounts': out, 'errors': errors}


def _number(value):
    try:
        return float(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _txn(item):
    if not isinstance(item, dict) or not item.get('id'):
        return None
    amount = _number(item.get('amount'))
    if amount is None:
        return None
    posted = _number(item.get('posted')) or 0
    happened = _number(item.get('transacted_at'))
    pending = bool(item.get('pending')) or not posted
    words = ' '.join(str(item.get(k) or '') for k in ('description', 'payee', 'memo')).strip()
    return {'id': str(item['id'])[:120], 'amount': amount, 'at': happened or posted or time.time(), 'pending': pending,
            'line': str(item.get('description') or item.get('payee') or 'Unknown')[:200], 'words': words[:300]}
