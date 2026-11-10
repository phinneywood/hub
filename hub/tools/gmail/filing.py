"""Filing sorted mail: out of the inbox and under a Hub label, one kind at a time.

Only kinds the owner raised above “Suggest only” are ever filed, and never mail that needs them,
starred mail, a record that looks odd, or anything Hub did not just see in the inbox. Filing adds
a label and removes the Inbox label through the helper, at most 50 messages per call; nothing is
deleted or marked read, and each call can be undone.

The rows are marked before anything is sent. If the helper refuses, nothing changed. If the
result is unknown, the rows wait for the owner and filing stops: it is never retried by itself.
"""
import time
from contextlib import closing

from ... import actions, bridge, store
from . import triage

LABELS = {'record': 'Hub/Records', 'reading': 'Hub/Reading', 'noise': 'Hub/Noise', 'expires': 'Hub/Expired',
          'calendar': 'Hub/Calendar'}
ACTIONS = {'record': 'gmail.file_records', 'reading': 'gmail.file_reading', 'noise': 'gmail.file_noise',
           'expires': 'gmail.file_expired', 'calendar': 'gmail.file_calendar'}
WAIT_DAYS = {'expires': 2, 'calendar': 2}   # these stay in the inbox while they may still be used
SYNC_FRESH = 600                            # file only against a look at the inbox this recent
BATCH = 50                                  # the helper's limit per call
PER_RUN = 200
UNDO_DAYS = 14
REFUSED = (400, 403, 404)                   # the helper said no before changing anything


def trusted(kind):
    """The owner's level for filing this kind: suggest, undo or auto."""
    from ...engine import catalog, state
    return state.trust(catalog.action(ACTIONS[kind]))


def ready(kind, now=None):
    """Messages of a kind that would be filed now, newest first."""
    now = time.time() if now is None else now
    return triage._rows(
        "state='open' and kind=? and starred=0 and not (kind='record' and odd is not null and ok=0)"
        ' and seen_at>=? and received<=? order by received desc',
        (kind, triage.synced_at(), now - WAIT_DAYS.get(kind, 0) * 86400))


def waiting_review():
    """Messages whose filing Hub could not confirm, or that an interrupted run left marked."""
    return triage._rows("state in ('review', 'filing')")


def _claim(ids, kind):
    """Mark rows ‘filing’ if they may still be filed. Returns those marked: the owner may have moved one meanwhile."""
    with closing(triage.db()) as conn, conn:
        conn.executemany("update triage_messages set state='filing' where id=? and state='open' and kind=? and starred=0"
                         " and not (kind='record' and odd is not null and ok=0)", [(message_id, kind) for message_id in ids])
        marks = ','.join('?' * len(ids))
        return [r['id'] for r in conn.execute(f"select id from triage_messages where state='filing' and id in ({marks})", ids)]


def _mark(ids, **fields):
    with closing(triage.db()) as conn, conn:
        conn.executemany(f'update triage_messages set {", ".join(k + "=?" for k in fields)} where id=?',
                         [(*fields.values(), message_id) for message_id in ids])


def _label(name):
    labels = bridge.get('/gmail/labels', ttl=0).get('labels', [])
    found = actions.find_label(name, labels)
    if found:
        return found['id']
    created = bridge.post('/gmail/labels/create', {'name': name})
    store.log('label created', name)
    return created['id']


def _change(ids, body):
    """Send one label change for rows already marked ‘filing’. Returns its change id, or None after settling the rows."""
    try:
        result = bridge.post('/gmail/labels/apply', {'message_ids': ids, **body})
    except bridge.BridgeError as err:
        if err.status in REFUSED:
            _mark(ids, state='open')
            return None
        result = {}
    if result.get('status') == 'applied' and result.get('change_id'):
        return result['change_id']
    _mark(ids, state='review', change_id=result.get('change_id'))
    store.log('mail filing needs review', f'{len(ids)} messages')
    return None


def file(kind, now=None):
    """File what is ready of one kind. Returns {'status', 'count'}; never raises."""
    from . import missions
    if kind not in LABELS or trusted(kind) == 'suggest':  # checked here too, whoever calls
        return {'status': 'refused', 'count': 0}
    if not triage._lock.acquire(blocking=False):
        return {'status': 'busy', 'count': 0}
    count = 0
    try:
        if waiting_review():
            return {'status': 'review', 'count': 0}
        if missions.is_paused():
            return {'status': 'paused', 'count': 0}
        now = time.time() if now is None else now
        if now - triage.synced_at() > SYNC_FRESH:
            triage.sync(now)
        rows = ready(kind, now)[:PER_RUN]
        if not rows:
            return {'status': 'nothing', 'count': 0}
        label = _label(LABELS[kind])
        for start in range(0, len(rows), BATCH):
            ids = _claim([r['id'] for r in rows[start:start + BATCH]], kind)
            if not ids:
                continue
            change = _change(ids, {'add_label_ids': [label], 'remove_label_ids': ['INBOX']})
            if not change:
                return {'status': 'review' if waiting_review() else 'part' if count else 'refused', 'count': count}
            _mark(ids, state='filed', change_id=change, filed_at=now)
            count += len(ids)
        return {'status': 'filed' if count else 'nothing', 'count': count}
    except bridge.BridgeError:  # reading the inbox or the labels failed; nothing was sent
        return {'status': 'error', 'count': count}
    finally:
        try:
            if count:
                store.log('mail filed', f'{count} × {triage.NAMES[kind]}')
        finally:
            triage._lock.release()


def undo(change_id):
    """Put one filed batch back in the inbox. Those messages are then left alone. Returns a status word."""
    rows = triage._rows("change_id=? and state in ('filed', 'review')", (change_id,))
    if not rows:
        return 'nothing'
    ids = [r['id'] for r in rows]
    try:
        result = bridge.post('/gmail/undo', {'change_id': change_id})
    except bridge.BridgeError as err:
        if err.status in REFUSED:
            return 'refused'
        result = {}
    if result.get('undone') is True:
        _mark(ids, state='kept')
        store.log('mail filing undone', f'{len(ids)} messages')
        return 'undone'
    _mark(ids, state='review')
    store.log('mail filing undo needs review', f'{len(ids)} messages')
    return 'review'


def restore(message_id):
    """Put one filed message back in the inbox and leave it alone from now on."""
    row = triage.get(message_id)
    if not row or row['state'] != 'filed' or row['kind'] not in LABELS:
        return 'nothing'
    if not triage._lock.acquire(blocking=False):
        return 'busy'
    try:
        found = actions.find_label(LABELS[row['kind']], bridge.get('/gmail/labels', ttl=0).get('labels', []))
        _mark([message_id], state='filing')
        change = _change([message_id], {'add_label_ids': ['INBOX'], 'remove_label_ids': [found['id']] if found else []})
        if not change:
            if triage.get(message_id)['state'] == 'open':  # refused: it is still filed
                _mark([message_id], state='filed')
                return 'refused'
            return 'review'
        _mark([message_id], state='kept')
        store.log('mail put back', '1 message')
        return 'restored'
    except bridge.BridgeError:
        return 'error'
    finally:
        triage._lock.release()


def checked():
    """The owner looked at Gmail after an unconfirmed change. The next sort sees what is really in the inbox.

    Mail Hub had filed before (an unconfirmed put-back) counts as filed again, so it is never filed a second
    time: found back in the inbox it is left alone, and found under its label it can be put back once more.
    """
    waiting = waiting_review()
    ids = [r['id'] for r in waiting]
    _mark([r['id'] for r in waiting if r['filed_at']], state='filed')
    _mark([r['id'] for r in waiting if not r['filed_at']], state='open', seen_at=0, change_id=None)
    if ids:
        store.log('mail filing checked by owner', f'{len(ids)} messages')
    return len(ids)


def batches(now=None):
    """Filed batches that can still be undone, newest first: [{'change_id', 'kind', 'count', 'at'}]."""
    now = time.time() if now is None else now
    found = {}
    for row in triage._rows("state='filed' and change_id is not null and filed_at>=?", (now - UNDO_DAYS * 86400,)):
        batch = found.setdefault(row['change_id'], {'change_id': row['change_id'], 'kind': row['kind'], 'count': 0, 'at': row['filed_at']})
        batch['count'] += 1
    return sorted(found.values(), key=lambda b: -b['at'])
