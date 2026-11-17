"""Ask reads mail: a search of the person's own Gmail, and one message read in full. Read-only.

Owner decision (2026-11-17, "Mail week 1" of the assistant plan): Ask's model may search the person's mail with
Gmail's own search words and read a message it found, so a question like "did the plumber write back?" is answered
from the mail itself rather than from the last sort. Nothing here changes Gmail; the bridge's read paths only.

Who: only a login that may see the bridge's main Google account (`hub/people.py`); anyone else is told it isn't
theirs and nothing is read. Senders with AI switched off (`store.ai_blocked`) are never given to the model: their
rows reach the person's own card, and the model sees only that one is there. A message is checked again by the
From (and, for mail the person sent, the To) of the message itself before its text is given.

What the model sees of a row: who it is from (a name or the domain, never an address), the subject, when, and a
short snippet, with links and addresses scrubbed. It never sees Gmail's ids: rows are numbered, and `read` takes a
number from this request's search or the person's last mail card of the last half hour. The card keeps the rows
(who, subject, when, the link that opens it in Gmail), in the person's own conversation, so it draws without
reading Gmail again.
"""
import time
from datetime import datetime
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from ... import bridge, home, luna, people, store
from ...engine import judge

ROWS = 8                        # rows one search gives
QUERY = 200                     # characters of Gmail search words
SNIPPET = 160
SUBJECT = 120
BODY = 3000                     # characters of one message the model is given
SEARCHES_PER_TURN = 2
READS_PER_TURN = 3
FOLLOW_SECONDS = 30 * 60        # how long "open the second one" reaches back to an earlier card
NOT_THERE = '-in:spam -in:trash'


class NotNow(Exception):
    """Nothing was read. The message is for the model to pass on, as it is."""


def mine(login):
    """Whether this login may read the mail Hub reads: the bridge's main Google account's."""
    return people.sees(login, people.DEFAULT)


def _when(value, now=None):
    """'today 9:12 am', 'yesterday', 'Mon', 'Nov 3' or '2025-11-03', for a Date header; '' when unreadable."""
    try:
        sent = parsedate_to_datetime(value).astimezone(home.PACIFIC)
    except (TypeError, ValueError, IndexError):
        return ''
    today = (now or datetime.now(home.PACIFIC)).date()
    days = (today - sent.date()).days
    if days <= 0:
        return 'today ' + sent.strftime('%-I:%M %p').lower()
    if days == 1:
        return 'yesterday'
    if days < 7:
        return sent.strftime('%a')
    return sent.strftime('%b %-d') if sent.year == today.year else sent.strftime('%Y-%m-%d')


def _who(header):
    """(what to call a sender: a name, or failing that the domain; their address, lowercased)."""
    name, address = parseaddr(str(header or ''))
    address = address.lower()
    name = luna.clip(name, 60) if name and '@' not in name else ''
    return name or address.rpartition('@')[2] or 'Someone', address


def _link(row):
    """The address that opens this email in Gmail, in the right Google account."""
    from ..errands import store as errands_store
    account = errands_store.mailbox(errands_store.MAIN)
    return f'https://mail.google.com/mail/{"?authuser=" + account if account else ""}#all/{row.get("thread_id") or row["id"]}'


def _off(address):
    try:
        return bool(address) and store.ai_blocked(address)
    except Exception:  # noqa: BLE001 - when Hub can't tell, the sender is treated as off
        return True


def query_of(words):
    """The Gmail search as it is sent: the model's words on one line, cut to length, never spam or the bin."""
    said = ' '.join(str(words or '').split())[:QUERY]
    return f'{said} {NOT_THERE}'.strip()


def search(login, words):
    """(rows for the person's card, what the model is given). Raises NotNow."""
    if not mine(login):
        raise NotNow('not_yours')
    if not ' '.join(str(words or '').split()):
        raise NotNow('Say what to search for, in Gmail’s search words (from:, subject:, newer_than:7d, or plain words).')
    try:
        found = bridge.get('/gmail/search', ttl=0, query=query_of(words), limit=ROWS)
    except bridge.BridgeError:
        raise NotNow('Hub couldn’t reach Gmail just now.') from None
    rows, given = [], []
    for n, message in enumerate((found.get('messages') or [])[:ROWS], 1):
        if not isinstance(message, dict) or not message.get('id'):
            continue
        who, address = _who(message.get('from'))
        off = _off(address)
        row = {'n': n, 'id': str(message['id']), 'thread_id': str(message.get('thread_id') or ''), 'who': who,
               'subject': luna.clip(message.get('subject') or '', SUBJECT), 'when': _when(message.get('date')), 'off': off}
        rows.append(dict(row, when=row['when'].removeprefix('today '), href=_link(row)))      # the card: just the time today
        if off:
            given.append({'n': n, 'hidden': 'AI is switched off for this sender; the person can see it on the card.'})
        else:
            given.append({'n': n, 'from': judge.scrub(who), 'subject': judge.scrub(row['subject']), 'when': row['when'],
                          'snippet': luna.clip(judge.scrub(message.get('snippet') or ''), SNIPPET)})
    return rows, {'found': given, 'more': bool(found.get('next_page_token')) or len(found.get('messages') or []) > ROWS}


def last_card(login, recent=None, now=None):
    """The rows of this person's latest mail card from the last half hour, or []."""
    from . import turns
    now = now or time.time()
    for turn in reversed(recent if recent is not None else turns.recent(login, FOLLOW_SECONDS)):
        if now - turn['at'] > FOLLOW_SECONDS:
            break
        for card in reversed(turn['cards']):
            if card.get('kind') == 'mail' and card.get('rows'):
                return card['rows']
    return []


def read(login, row):
    """What the model is given of one message: who, to whom, when, the subject and its text. Raises NotNow."""
    if not mine(login):
        raise NotNow('not_yours')
    if row.get('off'):
        raise NotNow('AI is switched off for this sender, so Hub won’t read it. The person can open it from the card.')
    try:
        message = bridge.get('/gmail/message', ttl=0, message_id=row['id'])
    except bridge.BridgeError as error:
        raise NotNow('That email is no longer in Gmail.' if error.status == 404 else 'Hub couldn’t reach Gmail just now.') from None
    who, address = _who(message.get('from'))
    recipients = message.get('recipients') or {}
    to = [a.lower() for _, a in getaddresses([str(recipients.get(k) or '') for k in ('to', 'cc')]) if a]
    sent = 'SENT' in set(message.get('labels') or ())
    if _off(address) or (sent and any(_off(a) for a in to)):
        raise NotNow('AI is switched off for someone on this email, so Hub won’t read it. The person can open it from the card.')
    text = ' '.join(judge.scrub(str(message.get('text') or '')).split())
    return {'from': judge.scrub(who), 'to': len(to), 'when': _when(message.get('date')),
            'subject': judge.scrub(luna.clip(message.get('subject') or '', SUBJECT)),
            'text': text[:BODY], 'cut': len(text) > BODY or bool(message.get('truncated')),
            'attachments': len(message.get('attachments') or ())}
