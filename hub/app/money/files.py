"""Statement files: for a bank SimpleFIN can't reach, or history older than it keeps.

Almost every bank and card lets a member download their transactions from its website, as OFX (also called QFX,
Quicken or Money format) or CSV. A member gives Hub the file on the Money screen; Hub reads it here, in memory,
and keeps only what it keeps for any account: the balance, and each transaction's date, amount and line. The
file itself is not kept.

OFX names the account (its type and number); only the last four digits are kept, to tell two accounts apart on
the screen. A CSV says nothing about its account, so the member picks which one it is.
"""
import csv
import hashlib
import io
import re
from datetime import date, datetime, timezone

LIMIT = 5 * 1024 * 1024         # bytes: a few years of a busy account is far less
TYPES = {'CHECKING': 'checking', 'SAVINGS': 'savings', 'MONEYMRKT': 'savings', 'CREDITLINE': 'card', 'CD': 'savings'}


class Unreadable(Exception):
    """The file isn't a statement Hub can read. The message is safe to show."""


def read(name, data):
    """[{'key', 'name', 'kind', 'ends', 'bank', 'balance', 'at', 'transactions': [...]}] from one file. CSV accounts
    have no key: the member says which account they are. Raises Unreadable."""
    if len(data) > LIMIT:
        raise Unreadable('That file is bigger than a statement should be (over 5 MB).')
    text = _text(data)
    if re.search(r'<\s*OFX\b|OFXHEADER', text[:4000], re.I):
        return ofx(text)
    if str(name or '').lower().endswith(('.ofx', '.qfx', '.qbo')):
        raise Unreadable('That file says it is OFX, but Hub couldn’t find a statement in it.')
    return [csv_statement(text)]


def _text(data):
    for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise Unreadable('Hub couldn’t read that file’s text.')


# ---- OFX and QFX: the old SGML kind and the newer XML kind alike ----

def _leaf(block, tag):
    found = re.search(rf'<{tag}>\s*([^<\r\n]*)', block, re.I)
    return found.group(1).strip() if found else ''


def _blocks(text, tag):
    return [m.group(1) for m in re.finditer(rf'<{tag}>(.*?)</{tag}>', text, re.S | re.I)]


def _ofx_time(value):
    digits = re.match(r'(\d{8})(\d{6})?', value or '')
    if not digits:
        return None
    moment = datetime.strptime(digits.group(1) + (digits.group(2) or '120000'), '%Y%m%d%H%M%S')
    return moment.replace(tzinfo=timezone.utc).timestamp()


def _amount(value):
    plain = str(value or '').strip().replace('$', '').replace(',', '')
    if re.fullmatch(r'\(\s*[\d.]+\s*\)', plain):          # (12.50) is money out
        plain = '-' + plain.strip('() ')
    try:
        return float(plain)
    except ValueError:
        return None


def ofx(text):
    bank = _leaf(text, 'ORG')
    found = []
    for tag, kind_tag in (('STMTRS', 'BANKACCTFROM'), ('CCSTMTRS', 'CCACCTFROM'), ('INVSTMTRS', 'INVACCTFROM')):
        for block in _blocks(text, tag):
            source = (_blocks(block, kind_tag) or [block])[0]
            number = _leaf(source, 'ACCTID')
            if tag == 'STMTRS':
                kind = TYPES.get(_leaf(source, 'ACCTTYPE').upper(), 'checking')
            else:
                kind = 'card' if tag == 'CCSTMTRS' else 'brokerage'
            txns = []
            for item in _blocks(block, 'STMTTRN'):
                amount, at = _amount(_leaf(item, 'TRNAMT')), _ofx_time(_leaf(item, 'DTPOSTED'))
                if amount is None or at is None:
                    continue
                line = _leaf(item, 'NAME') or _leaf(item, 'PAYEE') or _leaf(item, 'MEMO') or 'Unknown'
                ident = _leaf(item, 'FITID') or hashlib.sha256(f'{at}|{amount}|{line}'.encode()).hexdigest()[:20]
                txns.append({'id': ident[:120], 'amount': amount, 'at': at, 'pending': False, 'line': line[:200],
                             'words': f'{line} {_leaf(item, "MEMO")}'.strip()[:300]})
            if tag == 'INVSTMTRS':
                worth = sum(_amount(_leaf(p, 'MKTVAL')) or 0 for p in re.findall(r'<MKTVAL>[^<\r\n]*', block, re.I))
                balance = worth + (_amount(_leaf(block, 'AVAILCASH')) or 0)
                at = _ofx_time(_leaf(block, 'DTASOF'))
            else:
                ledger = (_blocks(block, 'LEDGERBAL') or [''])[0]
                balance, at = _amount(_leaf(ledger, 'BALAMT')), _ofx_time(_leaf(ledger, 'DTASOF'))
            name = {'checking': 'Checking', 'savings': 'Savings', 'card': 'Credit card', 'brokerage': 'Investments'}[kind]
            found.append({'key': 'file:' + hashlib.sha256(f'{_leaf(source, "BANKID")}|{number}|{kind}'.encode()).hexdigest()[:16],
                          'name': name, 'kind': kind, 'ends': re.sub(r'\D', '', number)[-4:], 'bank': bank[:80],
                          'balance': balance, 'at': at, 'transactions': txns})
    if not found:
        raise Unreadable('Hub couldn’t find a statement in that OFX file.')
    return found


# ---- CSV: whatever columns the bank chose ----

DATES = ('date', 'posted date', 'posting date', 'transaction date', 'trans date', 'post date', 'settlement date')
LINES = ('description', 'payee', 'name', 'merchant', 'details', 'transaction description', 'memo', 'narrative')
AMOUNTS = ('amount', 'transaction amount', 'amount (usd)', 'value')
OUT = ('debit', 'withdrawal', 'withdrawals', 'money out', 'paid out', 'charge', 'charges')
IN = ('credit', 'deposit', 'deposits', 'money in', 'paid in', 'payment', 'payments')
BALANCES = ('balance', 'running balance', 'running bal.', 'ledger balance')


def _column(header, names):
    plain = [' '.join(h.lower().replace('_', ' ').split()) for h in header]
    for name in names:
        if name in plain:
            return plain.index(name)
    return None


def _date(value):
    plain = str(value or '').strip()
    for form in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%Y/%m/%d', '%m-%d-%Y', '%d %b %Y', '%b %d, %Y', '%Y%m%d'):
        try:
            return datetime.strptime(plain[:len(datetime.now().strftime(form)) + 2].strip(), form).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(plain[:10])
    except ValueError:
        return None


def csv_statement(text):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(cell.strip() for cell in r)]
    start = next((n for n, r in enumerate(rows[:15]) if _column(r, DATES) is not None and _column(r, LINES) is not None), None)
    if start is None:
        raise Unreadable('Hub couldn’t find the columns it needs (a date, a description and an amount) in that file.')
    header = rows[start]
    when, line, amount = _column(header, DATES), _column(header, LINES), _column(header, AMOUNTS)
    out, into, balance_at = _column(header, OUT), _column(header, IN), _column(header, BALANCES)
    if amount is None and out is None and into is None:
        raise Unreadable('Hub couldn’t find an amount column in that file.')
    txns, last_balance, seen = [], None, {}
    for row in rows[start + 1:]:
        cell = lambda n: row[n] if n is not None and n < len(row) else ''  # noqa: E731
        on = _date(cell(when))
        if on is None:
            continue
        if amount is not None and _amount(cell(amount)) is not None:
            value = _amount(cell(amount))
        else:
            value = (_amount(cell(into)) or 0) - abs(_amount(cell(out)) or 0)
        if value is None or value == 0:
            continue
        words = cell(line).strip() or 'Unknown'
        at = datetime(on.year, on.month, on.day, 12, tzinfo=timezone.utc).timestamp()
        base = f'{on.isoformat()}|{value:.2f}|{words}'
        seen[base] = seen.get(base, 0) + 1          # two coffees the same day at the same price are two coffees
        txns.append({'id': 'csv' + hashlib.sha256(f'{base}|{seen[base]}'.encode()).hexdigest()[:20], 'amount': value, 'at': at,
                     'pending': False, 'line': words[:200], 'words': words[:300]})
        if balance_at is not None and _amount(cell(balance_at)) is not None and last_balance is None:
            last_balance = (_amount(cell(balance_at)), at)
    if not txns:
        raise Unreadable('Hub found no transactions in that file.')
    newest = max(txns, key=lambda t: t['at'])
    if last_balance and last_balance[1] < newest['at']:      # the file runs oldest first: its last balance is the newest
        last_balance = next(((_amount(r[balance_at]), t['at']) for r, t in zip(reversed(rows[start + 1:]), reversed(txns))
                             if balance_at < len(r) and _amount(r[balance_at]) is not None), last_balance)
    return {'key': None, 'name': '', 'kind': None, 'ends': '', 'bank': '', 'balance': last_balance[0] if last_balance else None,
            'at': last_balance[1] if last_balance else None, 'transactions': txns}


def charges_positive(txns):
    """Whether a card's file writes what was spent as positive amounts (some do), so Hub turns the sign round."""
    plain = [t for t in txns if not re.search(r'payment|thank you|autopay', t['line'], re.I)]
    return bool(plain) and sum(t['amount'] > 0 for t in plain) / len(plain) > 0.6
