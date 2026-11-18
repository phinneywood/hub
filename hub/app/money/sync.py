"""Bringing a reading in: from SimpleFIN Bridge, or from a statement file a member gives Hub.

Reading. A member connects once with a setup token from SimpleFIN Bridge (one connection each; both members may
connect their own banks). Hub then reads each connection twice a day on its own, early morning and mid
afternoon, and when someone taps "Read now" (at most once an hour). That is at most a few of the Bridge's 24
requests a day. The first reading asks for 90 days, the Bridge's most; after that, from five days before the last
reading, so a transaction that posted late is still caught.

Each reading replaces an account's pending transactions (a pending charge changes its amount and its id when it
posts: a tip, a fuel hold), keeps one balance a day, gives each new transaction its category (sorting) and pairs
money moved between the household's own accounts, so it is never counted as spending.

Statement files go to an account the member picks, or to the account the file names (OFX). The file is read in
memory and not kept. A transaction already read from SimpleFIN for that account is not added again.
"""
import json
import logging
import threading
from contextlib import closing
from datetime import datetime, timedelta

from ... import config, home
from . import categories, files, kinds, share, simplefin, store

log = logging.getLogger('hub.money')
READS_A_DAY = 8                 # Hub's own cap a connection, well under the Bridge's 24
TAP_SECONDS = 3600              # "Read now" at most this often
FIRST_DAYS = simplefin.DAYS
PAIR_DAYS = 4                   # the two sides of a transfer post this far apart at most
SORT_DAYS = 400                 # transactions older than this keep the category they have
LOOP_SECONDS = 600
READ_HOURS = (6, 15)            # Hub's own readings: one after each of these hours, local time
NOT_OURS = frozenset({'loan', 'mortgage'})      # a payment to a loan is spending (housing, loans), never a transfer


class Refused(Exception):
    """The member's request can't be done. The message is safe to show."""


def _day(at):
    return datetime.fromtimestamp(float(at), home.PACIFIC).date().isoformat()


# ---- connecting ----

def connect(token, login):
    """Claim a setup token for this member and read their banks for the first time. Returns what to say."""
    if share.person(login) is None:
        raise Refused('Connect banks from your own phone or laptop.')
    try:
        access = simplefin.claim(token)
    except (simplefin.Refused, simplefin.Down) as problem:
        raise Refused(str(problem)) from None
    link = store.run('insert into money_links(login, kind, name, created_at) values(?,?,?,?)',
                     (share.key(login), 'simplefin', 'SimpleFIN Bridge', store.now()))
    store.keep_access(link, access)
    said = read(link, why='first')
    return f'Connected. {said}'


def disconnect(link, login):
    """Forget a connection's access, so Hub can't read those banks any more. Their accounts and history stay, marked
    as no longer read, until the member removes them."""
    row = store.one('select * from money_links where id=? and gone=0', (int(link),))
    if row is None:
        raise Refused('That connection is already gone.')
    if not share.mine(login, row['login']):
        raise Refused('Only whoever connected it can disconnect it.')
    store.keep_access(row['id'], None)
    store.run('update money_links set gone=1, said=? where id=?', ('Disconnected.', row['id']))
    return 'Disconnected. Hub won’t read those banks again; what it read stays until you remove it.'


def links(login=None):
    found = store.rows('select * from money_links where gone=0 order by id')
    return [r for r in found if login is None or share.mine(login, r['login'])]


# ---- reading ----

def _reads(row):
    try:
        return [t for t in json.loads(row['reads'] or '[]') if store.now() - t < 86400]
    except ValueError:
        return []


def read(link, why='tap'):
    """Read one connection now. `why`: first, tap, or schedule. Returns what to say. Raises Refused for a tap that
    comes too soon."""
    row = store.one('select * from money_links where id=? and gone=0', (int(link),))
    if row is None:
        raise Refused('That connection is gone.')
    done = _reads(row)
    if len(done) >= READS_A_DAY:
        raise Refused('Hub has read those banks enough for today. They update about once a day.')
    if why == 'tap' and done and store.now() - max(done) < TAP_SECONDS:
        raise Refused('Hub read them less than an hour ago. Banks update about once a day.')
    access = store.access(row['id'])
    if not access:
        store.run('update money_links set ok=0, said=? where id=?', ('Hub lost its access. Connect again.', row['id']))
        raise Refused('Hub lost its access to those banks. Connect again with a new setup token.')
    until = store.now()
    earliest = until + 86400 - FIRST_DAYS * 86400        # the Bridge gives at most 90 days a request, `until` included
    since = earliest if not row['read_at'] else max(row['read_at'] - simplefin.OVERLAP * 86400, earliest)
    store.run('update money_links set tried_at=?, reads=? where id=?', (until, json.dumps(done + [until]), row['id']))
    try:
        found = simplefin.accounts(access, since=since, until=until + 86400)
    except simplefin.Refused as problem:
        store.run('update money_links set ok=0, said=? where id=?', (str(problem), row['id']))
        return str(problem)
    except simplefin.Down as problem:
        store.run('update money_links set ok=0, said=? where id=?', (str(problem), row['id']))
        return str(problem)
    count = 0
    for account in found['accounts']:
        count += _take(f'sf{row["id"]}:{account["id"]}', account, row['login'], 'simplefin', row['id'], since)
    said = ' '.join(found['errors'])[:400] or None
    store.run('update money_links set read_at=?, ok=?, said=? where id=?', (until, int(not found['errors']), said, row['id']))
    settle()
    accounts = len(found['accounts'])
    return (f'Read {accounts} account{"s" * (accounts != 1)}' + (f' and {count} new transaction{"s" * (count != 1)}.' if count else '.')
            + (f' The bank said: {said}' if said else ''))


def _take(account_id, account, owner, source, link, since=None):
    """Put one account's reading in: the account, today's balance, and its transactions. Returns how many were new."""
    now = store.now()
    known = store.one('select * from money_accounts where id=?', (account_id,))
    if known is None:
        kind = account.get('kind') or kinds.guess(account['name'], account.get('bank', ''))
        store.run('insert into money_accounts(id, link, source, owner, name, bank, kind, currency, balance, available, balance_at, ends, '
                  'in_budget, created_at, updated_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (account_id, link, source, owner, account['name'] or kinds.NAMES.get(kind, 'Account'), account.get('bank', ''), kind,
                   account.get('currency', 'USD'), account.get('balance'), account.get('available'), account.get('at'),
                   account.get('ends', ''), int(kind in kinds.SPENDING | {'savings'}), now, now))
        known = store.one('select * from money_accounts where id=?', (account_id,))
    elif account.get('balance') is not None and (account.get('at') or 0) >= (known['balance_at'] or 0):
        store.run('update money_accounts set balance=?, available=?, balance_at=?, updated_at=? where id=?',
                  (account['balance'], account.get('available'), account.get('at'), now, account_id))
    if account.get('balance') is not None:
        store.run('insert into money_balances(account, day, balance) values(?,?,?) on conflict(account, day) do update set '
                  'balance=excluded.balance', (account_id, _day(account.get('at') or now), account['balance']))
    txns = account.get('transactions') or []
    with closing(store.db()) as conn, conn:
        if since is not None:       # pending ones are read again in full each time; the posted one replaces them
            conn.execute('delete from money_txns where account=? and pending=1 and day>=?', (account_id, _day(since)))
        had = {r['ext'] for r in conn.execute('select ext from money_txns where account=?', (account_id,))}
        new = sum(1 for t in txns if t['id'] not in had and not t['pending'])
        for t in txns:
            conn.execute('insert into money_txns(account, ext, day, at, amount, payee, words, pending, seen_at) values(?,?,?,?,?,?,?,?,?) '
                         'on conflict(account, ext) do update set pending=excluded.pending, amount=excluded.amount, day=excluded.day',
                         (account_id, t['id'], _day(t['at']), t['at'], round(t['amount'], 2), t['line'], t.get('words', ''),
                          int(t['pending']), now))
    return new


# ---- statement files ----

def add_file(name, data, login, account_id=None, flip=None, new_name=None, new_kind=None):
    """Read a statement file into the account it names, or `account_id`. Returns what to say. Raises Refused."""
    if share.person(login) is None:
        raise Refused('Add statements from your own phone or laptop.')
    try:
        statements = files.read(name, data)
    except files.Unreadable as problem:
        raise Refused(str(problem)) from None
    owner, said = share.key(login), []
    for found in statements:
        target = None
        if account_id == 'new':
            if not (new_name or '').strip():
                raise Refused('Name the new account.')
            made = _new_file_account(new_name, new_kind, owner)
            target, account_id = made, made['id']
        elif account_id:
            target = store.one('select * from money_accounts where id=?', (account_id,))
            if target is None or (target['share'] == 'private' and not share.mine(login, target['owner'])):
                raise Refused('Hub doesn’t know that account.')
        elif found['key'] is None:
            raise Refused('Say which account this file is for.')
        txns = found['transactions']
        kind = target['kind'] if target else found['kind']
        if flip if flip is not None else (kind == 'card' and files.charges_positive(txns)):
            txns = [dict(t, amount=-t['amount']) for t in txns]
        if target is not None and target['source'] == 'simplefin':
            txns = [t for t in txns if not _already(target['id'], t)]
        reading = dict(found, transactions=txns, name=found['name'] or 'Account')
        if target is not None:
            reading['balance'] = found['balance'] if target['source'] != 'simplefin' else None
        new = _take(target['id'] if target else found['key'], reading, owner, 'file', None)
        called = target['name'] if target else store.one('select name from money_accounts where id=?', (found['key'],))['name']
        said.append(f'{called}: {new} new transaction{"s" * (new != 1)}')
    settle()
    return '. '.join(said) + '.'


def _new_file_account(name, kind, owner):
    import secrets
    kind = kind if kind in kinds.NAMES else kinds.guess(name)
    account_id = 'file:' + secrets.token_hex(8)
    now = store.now()
    store.run('insert into money_accounts(id, source, owner, name, kind, in_budget, created_at, updated_at) values(?,?,?,?,?,?,?,?)',
              (account_id, 'file', owner, str(name).strip()[:60], kind, int(kind in kinds.SPENDING | {'savings'}), now, now))
    return store.one('select * from money_accounts where id=?', (account_id,))


def _already(account_id, txn):
    """Whether a file's transaction was already read from SimpleFIN: the same amount, within a day, the same payee."""
    day = datetime.fromisoformat(_day(txn['at'])).date()
    near = store.rows('select payee from money_txns where account=? and amount=? and day between ? and ?',
                      (account_id, round(txn['amount'], 2), (day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat()))
    return any(categories.key(r['payee']) == categories.key(txn['line']) for r in near)


# ---- sorting and pairing ----

def rules():
    return [(r['words'], r['category']) for r in store.rows('select words, category from money_rules')]


def settle():
    """Give every transaction Hub may sort its category (a member's own pick for one transaction is kept), then
    pair money moved between the household's own accounts."""
    kinds_of = {a['id']: a['kind'] for a in store.rows('select id, kind from money_accounts')}
    known = rules()
    since = (home.today() - timedelta(days=SORT_DAYS)).isoformat()
    with closing(store.db()) as conn, conn:
        for t in conn.execute("select id, account, payee, amount, category, how from money_txns where day>=? "
                              "and coalesce(how, '') not in ('mine', 'pair')", (since,)).fetchall():
            category, how = categories.guess(t['payee'], t['amount'], kinds_of.get(t['account'], 'other'), known)
            if (category, how) != (t['category'], t['how']):
                conn.execute('update money_txns set category=?, how=? where id=?', (category, how, t['id']))
    pair(kinds_of)


def _moves(line):
    said = categories.clean(line)
    return bool(categories.MOVED.search(said) or categories.CARD_PAID.search(said) or categories.TO_A_CARD.search(said)
                or 'deposit' in said or 'withdrawal' in said)


def pair(kinds_of=None):
    """Match each amount that left one of the household's accounts with the same amount arriving in another within
    a few days, one to one, where either side's words say it was moved or paid. Both become "Between your
    accounts" (or "Saving and investing" when one side is an investment), and are never spending."""
    kinds_of = kinds_of or {a['id']: a['kind'] for a in store.rows('select id, kind from money_accounts')}
    since = (home.today() - timedelta(days=120)).isoformat()
    open_ = store.rows("select id, account, day, amount, payee, how from money_txns where pair is null and pending=0 and day>=? "
                       "and coalesce(how, '') != 'mine' order by day", (since,))
    ins = [t for t in open_ if t['amount'] > 0 and kinds_of.get(t['account']) not in NOT_OURS]
    used = set()
    with closing(store.db()) as conn, conn:
        for out in (t for t in open_ if t['amount'] < 0 and kinds_of.get(t['account']) not in NOT_OURS):
            day = datetime.fromisoformat(out['day']).date()
            fits = [t for t in ins if t['id'] not in used and t['account'] != out['account'] and abs(t['amount'] + out['amount']) < 0.005
                    and abs((datetime.fromisoformat(t['day']).date() - day).days) <= PAIR_DAYS
                    and (_moves(t['payee']) or _moves(out['payee']))]
            if not fits:
                continue
            best = min(fits, key=lambda t: abs((datetime.fromisoformat(t['day']).date() - day).days))
            used.add(best['id'])
            invested = kinds.INVESTED & {kinds_of.get(best['account']), kinds_of.get(out['account'])}
            category = 'saving' if invested else 'transfer'
            conn.execute("update money_txns set pair=?, category=?, how='pair' where id=?", (best['id'], category, out['id']))
            conn.execute("update money_txns set pair=?, category=?, how='pair' where id=?", (out['id'], category, best['id']))


# ---- on its own, twice a day ----

def due():
    """The connections Hub should read now: after 6 am and after 3 pm, once each, local time."""
    now = datetime.now(home.PACIFIC)
    out = []
    for row in links():
        last = datetime.fromtimestamp(row['tried_at'], home.PACIFIC) if row['tried_at'] else None
        for hour in READ_HOURS:
            mark = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if now >= mark and (last is None or last < mark):
                out.append(row['id'])
                break
    return out


def run_due():
    for link in due():
        try:
            read(link, why='schedule')
        except Refused:
            continue
        except Exception as error:  # noqa: BLE001 - one connection's failure leaves the others to be read
            log.warning('money: a reading failed: %s', type(error).__name__)


_started = False


def start():
    """Read what is due every ten minutes, from Hub's start."""
    global _started
    if _started or not config.BACKGROUND_JOBS_ENABLED:
        return
    _started = True

    def loop():
        while True:
            try:
                run_due()
            except Exception as error:  # noqa: BLE001 - the loop must outlive one bad reading
                log.warning('money: readings stopped once: %s', type(error).__name__)
            threading.Event().wait(LOOP_SECONDS)
    threading.Thread(target=loop, name='money-reading', daemon=True).start()
