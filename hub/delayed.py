"""Owner-enabled, bounded recurring archive rules.

The bridge creates a durable preview and journal for each batch. If any write
outcome is uncertain, suspend the rule rather than retrying automatically.
A failure before the archive request is sent changed nothing in Gmail: the rule
stays on and tries again later. A stopped rule records whether anything could
have changed, so the owner can resume it safely.
"""
import threading
import time
from contextlib import closing
from email.utils import parseaddr

from . import bridge, senders, store

INTERVAL = 24 * 60 * 60
CHECK_INTERVAL = 5 * 60
BATCH_SIZE = 10  # Bridge previews include metadata for at most ten messages.
MAX_BATCHES = 5
RETRY_SECONDS = 60 * 60
NOTHING_CHANGED = "Stopped before archiving anything; nothing in Gmail changed."
_lock = threading.Lock()
_started = False

SCHEMA = """
create table if not exists delayed_rules (
  id integer primary key autoincrement,
  sender text not null unique,
  enabled integer not null default 1,
  next_due real not null,
  last_checked real,
  error text
);
create table if not exists delayed_runs (
  id integer primary key autoincrement,
  rule_id integer not null,
  started real not null,
  status text not null,
  preview_id text,
  selected integer,
  processed integer,
  change_id text,
  error text
);
"""


def db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def query(sender):
    if not senders.valid_sender(sender):
        raise ValueError("Invalid sender")
    return f'from:{sender} in:inbox older_than:30d -is:starred'


def rules():
    with closing(db()) as conn:
        return [dict(r) for r in conn.execute("select * from delayed_rules order by id")]


def runs():
    with closing(db()) as conn:
        return [dict(r) for r in conn.execute(
            "select delayed_runs.*, delayed_rules.sender from delayed_runs "
            "join delayed_rules on delayed_runs.rule_id=delayed_rules.id order by delayed_runs.id desc limit 100")]


def enable(sender, now=None):
    query(sender)
    now = time.time() if now is None else now
    with _lock, closing(db()) as conn, conn:
        existing = conn.execute("select * from delayed_rules where sender=?", (sender,)).fetchone()
        if existing and existing["error"]:
            raise ValueError("This rule needs review before it can be enabled again.")
        if existing and existing["enabled"]:
            return
        conn.execute("insert into delayed_rules(sender, enabled, next_due) values (?, 1, ?) "
                     "on conflict(sender) do update set enabled=1, next_due=excluded.next_due", (sender, now + INTERVAL))
    store.log("delayed archive enabled", sender)


def disable(rule_id):
    # If a batch is already running this waits for it; no new batch starts after return.
    with _lock, closing(db()) as conn, conn:
        conn.execute("update delayed_rules set enabled=0 where id=?", (rule_id,))


def recover_interrupted():
    """A process interruption makes an in-flight write uncertain: never retry it."""
    with _lock, closing(db()) as conn, conn:
        conn.execute("update delayed_rules set enabled=0, error='Interrupted operation; inspect Activity before continuing.' "
                     "where id in (select rule_id from delayed_runs where status in ('applying','undoing'))")
        conn.execute("update delayed_runs set status='needs_review', error='Interrupted operation; inspect the bridge journal.' "
                     "where status in ('applying','undoing')")
        # Interrupted before the archive request: nothing was sent, so it is safe to try again later.
        conn.execute("update delayed_runs set status='failed', error=? where status='running'", (NOTHING_CHANGED,))


def run_due(now=None, budget=None):
    """Check every rule that is due. With `budget` seconds, rules not reached stay due for the next run."""
    now = time.time() if now is None else now
    stop = None if budget is None else time.monotonic() + budget
    with _lock:
        with closing(db()) as conn:
            due = [dict(r) for r in conn.execute(
                "select * from delayed_rules where enabled=1 and error is null and next_due<=? order by next_due, id", (now,))]
        for rule in due:
            if stop is not None and time.monotonic() >= stop:
                break
            _run_one(rule, now)


def archived_at(started):
    """(messages, senders) archived by the checks that began at `started`."""
    with closing(db()) as conn:
        row = conn.execute("select coalesce(sum(processed),0) n, count(distinct rule_id) s from delayed_runs "
                           "where started=? and status='done' and processed>0", (started,)).fetchone()
    return row["n"], row["s"]


def overdue(now=None):
    """{'rules', 'days'}: rules that are on but more than a day late, and how long the longest has waited."""
    now = time.time() if now is None else now
    late = [r for r in rules() if r['enabled'] and not r['error'] and r['next_due'] <= now - INTERVAL]
    if not late:
        return {'rules': 0, 'days': 0}
    waited = max(now - (r['last_checked'] or r['next_due'] - INTERVAL) for r in late)
    return {'rules': len(late), 'days': int(waited // INTERVAL)}


def _run_one(rule, now):
    for _ in range(MAX_BATCHES):
        more = _batch(rule, now)
        if not more:
            break


def _batch(rule, now):
    with closing(db()) as conn, conn:
        run_id = conn.execute("insert into delayed_runs(rule_id,started,status) values (?,?,'running')", (rule['id'], now)).lastrowid
    sent = False
    try:
        profile = bridge.get('/gmail/profile', ttl=0)
        if profile.get('organization_paused') is not False:
            with closing(db()) as conn, conn:
                conn.execute("update delayed_runs set status='paused' where id=?", (run_id,))
                conn.execute("update delayed_rules set next_due=? where id=?", (now + 3600, rule['id']))
            return False
        preview = bridge.post('/gmail/cleanup/preview', {'query': query(rule['sender']),
                              'remove_label_ids': ['INBOX'], 'max_messages': BATCH_SIZE})
        selected = preview['selected_messages']
        sample = preview.get('sample', [])
        if (not isinstance(selected, int) or not 0 <= selected <= BATCH_SIZE or len(sample) != selected
                or len({m.get('id') for m in sample}) != selected
                or any(not m.get('id') or parseaddr(m.get('from', ''))[1].lower() != rule['sender'].lower() for m in sample)):
            raise RuntimeError('Preview does not contain an exact sender match for every message.')
        with closing(db()) as conn, conn:
            conn.execute("update delayed_runs set preview_id=?,selected=?,status=? where id=?",
                         (preview['preview_id'], selected, 'applying' if selected else 'running', run_id))
        if selected:
            sent = True
            result = bridge.post('/gmail/cleanup/apply', {'preview_id': preview['preview_id']})
            changes = result.get('change_ids', [])
            if result.get('needs_review') or not result.get('complete') or result.get('processed') != selected or len(changes) != 1:
                raise RuntimeError('The bridge reported an uncertain batch.')
            change_id = changes[0]
        else:
            change_id = None
        with closing(db()) as conn, conn:
            conn.execute("update delayed_runs set status='done',processed=?,change_id=? where id=?", (selected, change_id, run_id))
            conn.execute("update delayed_rules set last_checked=?,next_due=? where id=?", (now, now + INTERVAL, rule['id']))
        if selected:
            store.log('delayed archive', f"{rule['sender']}: {selected} messages")
        return bool(selected and preview.get('more_matches_exist'))
    except bridge.BridgeError as err:
        if sent:
            return _suspend(rule, run_id, 'Check the bridge journal before continuing.')
        if err.status == 403 and 'paused' in str(err).lower():
            with closing(db()) as conn, conn:
                conn.execute("update delayed_runs set status='paused' where id=?", (run_id,))
                conn.execute("update delayed_rules set next_due=? where id=?", (now + 3600, rule['id']))
            return False
        # Nothing was sent for archiving, so nothing changed: keep the rule on and retry later.
        with closing(db()) as conn, conn:
            conn.execute("update delayed_runs set status='failed',error=? where id=?",
                         ('Gmail helper unavailable; nothing changed. Will try again.', run_id))
            conn.execute("update delayed_rules set next_due=? where id=?", (now + RETRY_SECONDS, rule['id']))
        store.log('delayed archive retry later', rule['sender'])
        return False
    except Exception:
        # The request may have reached the bridge even if its response was lost.
        return _suspend(rule, run_id, 'Check the bridge journal before continuing.' if sent else NOTHING_CHANGED)


def _suspend(rule, run_id, error):
    with closing(db()) as conn, conn:
        conn.execute("update delayed_runs set status='needs_review',error=? where id=?", (error, run_id))
        conn.execute("update delayed_rules set enabled=0,error='A check needs review; no automatic retry.' where id=?", (rule['id'],))
    store.log('delayed archive needs review', rule['sender'])
    return False


def needs_check(rule_id):
    """True when a stopped rule had a write whose outcome is unknown (not provably harmless)."""
    with closing(db()) as conn:
        rows = conn.execute("select preview_id, selected, change_id, status, error from delayed_runs "
                            "where rule_id=? and status='needs_review'", (rule_id,)).fetchall()
    return any(not (r['error'] == NOTHING_CHANGED or (r['preview_id'] is None and r['change_id'] is None)) for r in rows)


def resume(rule_id, checked=False, now=None):
    """Turn a stopped rule back on. Requires `checked` unless nothing could have changed."""
    now = time.time() if now is None else now
    if needs_check(rule_id) and not checked:
        return False
    with _lock, closing(db()) as conn, conn:
        rule = conn.execute("select * from delayed_rules where id=?", (rule_id,)).fetchone()
        if not rule:
            return False
        conn.execute("update delayed_runs set status='reviewed' where rule_id=? and status='needs_review'", (rule_id,))
        conn.execute("update delayed_rules set enabled=1, error=null, next_due=? where id=?", (now, rule_id))
    store.log('delayed archive resumed', rule['sender'])
    return True


def health():
    """{'on', 'stopped', 'safe_to_resume'} for summaries."""
    all_rules = rules()
    stopped = [r for r in all_rules if r['error']]
    return {'on': sum(1 for r in all_rules if r['enabled'] and not r['error']), 'stopped': len(stopped),
            'safe_to_resume': sum(1 for r in stopped if not needs_check(r['id']))}


def run_now(now=None):
    """Check every enabled rule immediately (owner request). Same bounds as the daily check."""
    now = time.time() if now is None else now
    with _lock:
        with closing(db()) as conn:
            due = [dict(r) for r in conn.execute("select * from delayed_rules where enabled=1 and error is null order by id")]
        for rule in due:
            _run_one(rule, now)
    return len(due)


def undo(run_id):
    """Disable the rule before undo so the next check cannot re-archive restored mail."""
    with _lock:
        with closing(db()) as conn, conn:
            run = conn.execute("select * from delayed_runs where id=? and status='done' and change_id is not null", (run_id,)).fetchone()
            if not run:
                return False
            conn.execute("update delayed_rules set enabled=0 where id=?", (run['rule_id'],))
            conn.execute("update delayed_runs set status='undoing' where id=?", (run_id,))
        try:
            result = bridge.post('/gmail/undo', {'change_id': run['change_id']})
            success = result.get('undone') is True
        except Exception:
            success = False
        with closing(db()) as conn, conn:
            conn.execute("update delayed_runs set status=?,error=? where id=?",
                         ('undone' if success else 'needs_review', None if success else 'Undo was incomplete or uncertain.', run_id))
            if not success:
                conn.execute("update delayed_rules set error='Undo needs review.' where id=?", (run['rule_id'],))
        return success


def start():
    global _started
    if _started:
        return
    recover_interrupted()
    _started = True
    def loop():
        while True:
            try:
                run_due()
            except Exception:
                store.log('delayed archive scheduler error', 'Checks stopped until the next interval.')
            threading.Event().wait(CHECK_INTERVAL)
    threading.Thread(target=loop, name='delayed-archive', daemon=True).start()
