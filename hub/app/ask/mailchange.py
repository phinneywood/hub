"""Ask changes mail: archive, back to the inbox, a label on or off, read or unread. Only after the person's tap.

Owner decision (2026-11-18, "Mail week 2" of the assistant plan): Ask may propose changes to the person's own mail,
and every one waits on a card for their tap. The model never changes Gmail: its tool (`tools.mail_change`) only
keeps a proposal here and returns the card. The tap (`POST /app/api/mail/change/{id}/confirm`) is the only thing that
sends anything, through the bridge's `/gmail/labels/apply` (and `/gmail/labels/create` for a new label), the same
fixed paths Hub's filing uses. The bridge itself has no send, draft, delete or trash path, so nothing here can
remove an email: deleting stays in Gmail, by the person's own hand.

Who: only a login that may see the bridge's main Google account (`mailbox.mine`), when the card is made and again
when it is tapped, and only the login that asked may tap. The emails are the ones the person was shown (this
request's search, or their last mail card), by number, so the model never handles a Gmail id. A card waits an hour.
Each change done can be undone through the bridge's `/gmail/undo` for a day.
"""
import json
import time

from ... import actions, bridge, luna, store
from . import mailbox

WAIT_SECONDS = 3600
UNDO_SECONDS = 86400
MOST = mailbox.ROWS              # emails one card may change: one search's rows
REFUSED = (400, 403, 404)        # the bridge said no before changing anything

# What each change does to Gmail's labels: (add, remove), with a label's id standing in for LABEL.
LABEL = object()
CHANGES = {'archive': ((), ('INBOX',)), 'inbox': (('INBOX',), ()), 'label': ((LABEL,), ()), 'unlabel': ((), (LABEL,)),
           'read': ((), ('UNREAD',)), 'unread': (('UNREAD',), ())}
VERB = {'archive': 'Archive', 'inbox': 'Move to the inbox', 'label': 'Label', 'unlabel': 'Take the label off',
        'read': 'Mark read', 'unread': 'Mark unread'}
BUTTON = {'archive': 'Archive', 'inbox': 'Move', 'label': 'Label', 'unlabel': 'Take it off', 'read': 'Mark read',
          'unread': 'Mark unread'}
DONE = {'archive': 'Archived', 'inbox': 'Back in the inbox', 'label': 'Labelled', 'unlabel': 'Label taken off',
        'read': 'Marked read', 'unread': 'Marked unread'}

SCHEMA = """
create table if not exists ask_mail_waiting(
  id integer primary key autoincrement, ts real not null, login text not null, do text not null, label text,
  ids text not null, title text not null, lines text not null, status text not null default 'waiting',
  change_id text, done_at real);
"""


class Refused(Exception):
    """Nothing was changed. The message is for the person (or the model), as it is."""


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _labels():
    try:
        return bridge.get('/gmail/labels', ttl=0).get('labels', [])
    except bridge.BridgeError:
        raise Refused('Hub couldn’t reach Gmail just now.') from None


def _label_name(do, label):
    """(the label as Gmail has it, or will once made; whether it is there yet) for label and unlabel, (None, True)
    for the rest. Raises Refused."""
    if do not in ('label', 'unlabel'):
        return None, True
    wanted = ' '.join(str(label or '').split())[:80]
    if not wanted:
        raise Refused('Say which label.')
    labels = _labels()
    found = actions.find_label(wanted, labels)
    if found:
        return found['name'], True
    if do == 'unlabel':
        raise Refused(f'There is no label called {wanted} in Gmail.')
    try:
        return actions.clean_label(wanted, labels)['name'], False
    except actions.ActionError as error:
        raise Refused(str(error)) from None


def _title(do, count, label):
    emails = 'this email' if count == 1 else f'these {count} emails'
    if do == 'label':
        return f'Label {emails} “{label}”?'
    if do == 'unlabel':
        return f'Take “{label}” off {emails}?'
    return f'{VERB[do]} {emails}?' if do != 'inbox' else f'Move {emails} back to the inbox?'


def propose(login, do, rows, which, label=None):
    """Keep a change for the person's tap and return its card. `rows`: the mail they were shown; `which`: numbers
    from it. Nothing is sent to Gmail. Raises Refused."""
    if not mailbox.mine(login):
        raise Refused('not_yours')
    if do not in CHANGES:
        raise Refused('Hub can archive, move back to the inbox, label, take a label off, or mark read or unread.')
    numbers = list(dict.fromkeys(int(n) for n in which or ()))
    if not numbers:
        raise Refused('Say which emails, by their numbers.')
    by_number = {r.get('n'): r for r in rows}
    picked = [by_number[n] for n in numbers if n in by_number][:MOST]
    if len(picked) != len(numbers):
        raise Refused('Some of those numbers aren’t on the emails Hub showed. Search first with mail_search.')
    name, there = _label_name(do, label)
    title = _title(do, len(picked), name)
    lines = [luna.clip(f'{r.get("who") or "Someone"}: {r.get("subject") or "(no subject)"}', 120) for r in picked]
    if not there:
        lines.append(f'Makes a new label, {name}.')
    with _db() as conn:
        conn.execute("update ask_mail_waiting set status = 'replaced' where status = 'waiting' and login = ?", (login,))
        number = conn.execute('insert into ask_mail_waiting(ts, login, do, label, ids, title, lines) values (?,?,?,?,?,?,?)',
                              (time.time(), login, do, name, json.dumps([r['id'] for r in picked]), title,
                               json.dumps(lines))).lastrowid
    return {'kind': 'confirm', 'id': number, 'path': 'mail/change', 'title': title, 'lines': lines,
            'why': 'Nothing in Gmail changes until you tap. Undo puts it back for a day.', 'do': BUTTON[do]}


def card(number):
    """One card's state for the app: waiting, done, dismissed, replaced, expired or undone; None when there is none."""
    with _db() as conn:
        conn.execute("update ask_mail_waiting set status = 'expired' where status = 'waiting' and ts < ?", (time.time() - WAIT_SECONDS,))
        row = conn.execute('select * from ask_mail_waiting where id = ?', (int(number),)).fetchone()
    return dict(row) if row else None


def _settle(number, status, was='waiting', **fields):
    sets = ', '.join(['status = ?'] + [f'{k} = ?' for k in fields])
    with _db() as conn:
        return conn.execute(f'update ask_mail_waiting set {sets} where id = ? and status = ?',
                            (status, *fields.values(), int(number), was)).rowcount == 1


def _label_id(name, create):
    labels = _labels()
    found = actions.find_label(name, labels)
    if found:
        return found['id']
    if not create:
        raise Refused(f'The label {name} is no longer in Gmail, so nothing was changed.')
    try:
        made = bridge.post('/gmail/labels/create', {'name': name})
    except bridge.BridgeError:
        raise Refused('Gmail didn’t make the label, so nothing was changed.') from None
    store.log('label created', name)
    return made['id']


def _mine(row, login):
    if row is None:
        raise Refused('That card isn’t there any more. Ask again.')
    if login is None or row['login'] != login or not mailbox.mine(login):
        raise Refused('That card is someone else’s.')


def confirm(number, login):
    """The person's tap: make the change the card shows. Returns what to say. Raises Refused."""
    row = card(number)
    _mine(row, login)
    if row['status'] != 'waiting':
        raise Refused({'expired': 'That card waited more than an hour. Ask again.', 'done': 'That is already done.'}.get(
            row['status'], 'That card isn’t waiting any more. Ask again.'))
    if not _settle(number, 'working'):
        raise Refused('That card isn’t waiting any more. Ask again.')
    try:
        label = _label_id(row['label'], row['do'] == 'label') if row['label'] else None
    except Refused:
        _settle(number, 'waiting', was='working')
        raise
    add, remove = ([label if x is LABEL else x for x in side] for side in CHANGES[row['do']])
    try:
        result = bridge.post('/gmail/labels/apply', {'message_ids': json.loads(row['ids']), 'add_label_ids': add,
                                                     'remove_label_ids': remove})
    except bridge.BridgeError as error:
        if error.status in REFUSED:
            _settle(number, 'waiting', was='working')
            raise Refused('paused' in str(error).lower() and 'Gmail changes are paused, so nothing was changed.'
                          or 'Gmail said no, so nothing was changed.') from None
        result = {}
    if not isinstance(result, dict) or result.get('status') != 'applied' or not result.get('change_id'):
        _settle(number, 'unsure', was='working', change_id=(result or {}).get('change_id') if isinstance(result, dict) else None)
        store.log('ask mail change needs review', row['title'])
        raise Refused('Hub sent that, but Gmail didn’t confirm it. Check Gmail.')
    _settle(number, 'done', was='working', change_id=str(result['change_id']), done_at=time.time())
    store.log('ask changed mail', f'{row["do"]}: {len(json.loads(row["ids"]))} emails')
    return DONE[row['do']]


def dismiss(number, login):
    row = card(number)
    _mine(row, login)
    _settle(number, 'dismissed')
    return 'Left as it was'


def undo(number, login):
    """Put a done card's change back, for a day. Returns what to say. Raises Refused."""
    row = card(number)
    _mine(row, login)
    if row['status'] != 'done' or not row['change_id']:
        raise Refused('There’s nothing to put back.')
    if time.time() - (row['done_at'] or 0) > UNDO_SECONDS:
        raise Refused('That was more than a day ago. Change it in Gmail.')
    if not _settle(number, 'undoing', was='done'):
        raise Refused('There’s nothing to put back.')
    try:
        result = bridge.post('/gmail/undo', {'change_id': row['change_id']})
    except bridge.BridgeError as error:
        _settle(number, 'done', was='undoing')
        raise Refused('Gmail said no, so it is as it was.' if error.status in REFUSED else 'Hub couldn’t reach Gmail just now.') from None
    if not isinstance(result, dict) or result.get('undone') is not True:
        _settle(number, 'done', was='undoing')
        raise Refused('Some of those emails changed since, so they were left as they are.')
    _settle(number, 'undone', was='undoing')
    store.log('ask mail change undone', row['title'])
    return 'Put back as it was'
