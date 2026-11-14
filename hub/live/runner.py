"""Runs the live checks and keeps every result.

History lives in `live-checks.db` in Hub's private data folder (owner-only): one row per run and one per check
in it, with the Hub version that was running, how long each took and one sentence. Kept for KEEP_DAYS, so a
check that starts failing can be dated to the version and the day it broke.

One run at a time. Each check has its own time limit and runs on a thread of its own, so one that hangs is
recorded as failed and the rest still run. A check with the `undo` effect runs only when the caller allows
it and the owner has switched it on in the private settings; see `checks.py`.
"""
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime

from .. import config, home
from . import checks

FILE = 'live-checks.db'
KEEP_DAYS = 180
SHOWN = 30                      # runs of history the page shows per check
TRIGGERS = ('manual', 'morning', 'deploy', 'predeploy', 'shell')
SCHEMA = """
create table if not exists runs(
  id integer primary key autoincrement, at real not null, trigger text not null, version text not null default '',
  ms integer not null default 0, passed integer not null default 0, failed integer not null default 0,
  skipped integer not null default 0, done integer not null default 0);
create table if not exists results(
  run integer not null references runs(id), check_id text not null, status text not null, ms integer not null,
  words text not null, primary key (run, check_id));
create index if not exists results_check on results(check_id, run);
"""


class Busy(Exception):
    """A run is already going."""


def _db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / FILE
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def _lock():
    return config.DATA_DIR / 'live-checks.running'


def running():
    try:
        os.kill(int(_lock().read_text()), 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


def version():
    from ..engine import release
    return (release.running() or '')[:12]


def allowed(with_spend=True, with_undo=False):
    """The effects a run may have. `undo` also needs the owner's switch in the private settings."""
    effects = {'read'}
    if with_spend:
        effects.add('spend')
    if with_undo and checks.settings().get('undo') is True:
        effects.add('undo')
    return effects


def _one(check):
    """(status, ms, words) for one check, never raising and never waiting past its limit."""
    box = {}

    def work():
        try:
            box['out'] = ('pass', str(check.run())[:300])
        except checks.Skipped as error:
            box['out'] = ('skip', str(error)[:300])
        except checks.Failed as error:
            box['out'] = ('fail', str(error)[:300])
        except Exception as error:  # noqa: BLE001 - a broken check is a failed check, by type only
            box['out'] = ('fail', f'The check stopped with {type(error).__name__}.')

    started = time.monotonic()
    thread = threading.Thread(target=work, daemon=True, name=f'live-{check.id}')
    thread.start()
    thread.join(check.seconds)
    status, words = box.get('out') or ('fail', f'No answer within {check.seconds} s.')
    return status, round((time.monotonic() - started) * 1000), words


def run(trigger='manual', only=(), skip=(), effects=('read', 'spend')):
    """Run the checks whose effect is allowed. Returns the stored run: {'id', 'results': [...], counts}."""
    if trigger not in TRIGGERS:
        raise ValueError('unknown trigger')
    if running():
        raise Busy('A run of the live checks is already going.')
    lock = _lock()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))
    started, out = time.time(), []
    try:
        with closing(_db()) as conn, conn:
            run_id = conn.execute('insert into runs(at, trigger, version) values (?,?,?)', (started, trigger, version())).lastrowid
        for check in checks.CHECKS:
            if (only and check.id not in only) or check.id in skip:
                continue
            if check.effect not in effects:
                status, ms, words = 'skip', 0, {'undo': 'Not switched on.', 'spend': 'Left out of this run.'}.get(check.effect, 'Left out.')
            else:
                status, ms, words = _one(check)
            out.append({'id': check.id, 'name': check.name, 'status': status, 'ms': ms, 'words': words})
            with closing(_db()) as conn, conn:
                conn.execute('insert or replace into results(run, check_id, status, ms, words) values (?,?,?,?,?)',
                             (run_id, check.id, status, ms, words))
        counts = {k: sum(r['status'] == k for r in out) for k in ('pass', 'fail', 'skip')}
        with closing(_db()) as conn, conn:
            conn.execute('update runs set ms = ?, passed = ?, failed = ?, skipped = ?, done = 1 where id = ?',
                         (round((time.time() - started) * 1000), counts['pass'], counts['fail'], counts['skip'], run_id))
            old = time.time() - KEEP_DAYS * 86400
            conn.execute('delete from results where run in (select id from runs where at < ?)', (old,))
            conn.execute('delete from runs where at < ?', (old,))
    finally:
        lock.unlink(missing_ok=True)
    return {'id': run_id, 'at': started, 'trigger': trigger, 'results': out, 'passed': counts['pass'],
            'failed': counts['fail'], 'skipped': counts['skip']}


# ---- reading the history ----

def _when(ts):
    return datetime.fromtimestamp(ts, home.PACIFIC).strftime('%b %-d, %-I:%M %p')


def latest():
    """The newest finished run with its results, or None."""
    with closing(_db()) as conn:
        row = conn.execute('select * from runs where done = 1 order by id desc limit 1').fetchone()
        if not row:
            return None
        results = [dict(r) for r in conn.execute('select * from results where run = ?', (row['id'],))]
    order = {c.id: n for n, c in enumerate(checks.CHECKS)}
    results.sort(key=lambda r: order.get(r['check_id'], 99))
    return {**dict(row), 'when': _when(row['at']), 'results': results}


def history(limit=SHOWN):
    """Per check, oldest first: its last `limit` results that weren't skipped, each {'status', 'ms', 'when', 'version', 'words'}."""
    out = {}
    with closing(_db()) as conn:
        for check in checks.CHECKS:
            rows = conn.execute(
                "select r.status, r.ms, r.words, runs.at, runs.version, runs.trigger from results r join runs on runs.id = r.run "
                "where r.check_id = ? and r.status != 'skip' order by r.run desc limit ?", (check.id, limit)).fetchall()
            out[check.id] = [{'status': r['status'], 'ms': r['ms'], 'words': r['words'], 'when': _when(r['at']), 'at': r['at'],
                              'version': r['version'], 'trigger': r['trigger']} for r in reversed(rows)]
    return out


def failing():
    """Checks whose newest result (skips aside) is a failure, each with when it last passed and since when it
    fails: [{'id', 'name', 'words', 'since', 'since_version', 'last_pass', 'new'}]. `new`: it passed before."""
    out = []
    for check_id, rows in history(limit=200).items():
        if not rows or rows[-1]['status'] != 'fail':
            continue
        streak = 0
        for row in reversed(rows):
            if row['status'] != 'fail':
                break
            streak += 1
        first, before = rows[-streak], rows[-streak - 1] if streak < len(rows) else None
        check = checks.by_id(check_id)
        out.append({'id': check_id, 'name': check.name, 'words': rows[-1]['words'], 'since': first['when'],
                    'since_version': first['version'], 'last_pass': before['when'] if before else None,
                    'last_pass_version': before['version'] if before else None, 'new': before is not None, 'runs': streak})
    return out
