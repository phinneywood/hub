"""Ask Hub's per-turn trace: what each chat turn did, so a failed answer can be diagnosed without guessing.

One row per turn (thread, timings, model calls, tokens, cost, status) with its model calls and tool calls. A tool
call keeps its arguments (scrubbed: no links or addresses, clipped), how it answered (a kind such as read, done or
unavailable, plus Hub's own words for anything not done) and, on an error, the exception type, a safe message and the
traceback. What a lookup returned is never kept: no mail, subjects or other source text, and never the owner's
message or the reply. The house and music change rows a control made are found by time on the Ask activity page.

The trace is its own SQLite file in Hub's private data folder (ask-trace.db, owner-only), kept KEEP_DAYS.
Tracebacks live only there. hub.log gets one line per turn: ids, durations, counts and status, never text.

This module needs only the standard library, so the Manager can read a trace from the shell:

    tools/ask-trace --last 5
    tools/ask-trace TURN_ID --traceback
"""
import argparse
import contextvars
import json
import os
import sqlite3
import sys
import threading
import time
import traceback as tracebacks
import uuid
from contextlib import closing, contextmanager
from datetime import datetime

from . import config

FILE = 'ask-trace.db'
KEEP_DAYS = 14
ARG_LENGTH = 200
DETAIL_LENGTH = 300
MESSAGE_LENGTH = 200
TRACEBACK_LENGTH = 8000
CHANGE_SLACK = 2.0          # seconds after a control ends in which its change rows are still its own
# How a control or hand-off answered, in the order a result is read: the first key present names it.
OUTCOMES = ('refused', 'unavailable', 'nothing_matched', 'ask_first', 'needs_tap', 'stopped', 'not_confirmed',
            'not_done', 'songs_not_found', 'error', 'already', 'done', 'joined', 'ran', 'asked', 'picked')
NOT_DONE = ('refused', 'unavailable', 'nothing_matched', 'ask_first', 'stopped', 'not_confirmed', 'not_done',
            'songs_not_found', 'error')
# Exceptions whose message Hub writes itself to be shown: their words may go into an improvement brief.
SAFE_TYPES = ('Refused', 'HomeDown', 'MusicDown', 'KitchenDown', 'NotSetUp', 'WrongRoute', 'TimeoutError',
              'ConnectionRefusedError', 'ConnectionResetError', 'ConnectionError', 'UsageLimitExceeded')
SCHEMA = """
create table if not exists turns(
  id text primary key, thread text not null, started real not null, ended real, status text not null,
  model_calls integer not null default 0, tokens_in integer not null default 0, tokens_out integer not null default 0,
  cost real not null default 0, model_ms integer not null default 0, tools integer not null default 0,
  tool_errors integer not null default 0, error_type text, error_message text, traceback text);
create index if not exists turns_started on turns(started);
create table if not exists tool_calls(
  id integer primary key autoincrement, turn text not null, seq integer not null, call_id text, tool text not null,
  kind text not null, args text not null, started real not null, ms integer not null, outcome text not null,
  detail text, error_type text, error_message text, traceback text);
create index if not exists tool_calls_turn on tool_calls(turn);
create table if not exists model_calls(
  id integer primary key autoincrement, turn text not null, seq integer not null, started real not null,
  ms integer not null, tokens_in integer not null default 0, tokens_out integer not null default 0,
  cost real not null default 0, error_type text, error_message text, traceback text);
create index if not exists model_calls_turn on model_calls(turn);
"""
_current = contextvars.ContextVar('ask_trace_call', default=None)


def _path():
    return config.DATA_DIR / FILE


def _db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _path()
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    if path.stat().st_mode & 0o077:
        path.chmod(0o600)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _read():
    """A read-only connection, or None when there is no trace yet. Reading never creates the file."""
    if not _path().exists():
        return None
    conn = sqlite3.connect(f'file:{_path()}?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


# ---- what may be kept ----

def scrub(text, length):
    """Text as other records keep it: links and addresses taken out, one line, clipped."""
    from .engine import judge
    return ' '.join(judge.scrub(str(text)).split())[:length]


def _arg(value):
    if isinstance(value, str):
        return scrub(value, ARG_LENGTH)
    if isinstance(value, (list, tuple)):
        return [_arg(v) for v in value[:20]]
    if isinstance(value, dict):
        return {str(k)[:40]: _arg(v) for k, v in list(value.items())[:20]}
    return value if isinstance(value, (int, float, bool)) or value is None else scrub(value, ARG_LENGTH)


def describe(error):
    """(type, safe message, traceback) for an exception. The message is one scrubbed, clipped line."""
    kind = type(error).__name__
    message = scrub(str(error).strip().splitlines()[0] if str(error).strip() else '', MESSAGE_LENGTH)
    text = ''.join(tracebacks.format_exception(type(error), error, error.__traceback__))
    from .engine import judge
    return kind, message, judge.scrub(text)[-TRACEBACK_LENGTH:]


def outcome(kind, result):
    """(outcome, detail) for what a tool returned. Lookups say only that they read, unless they couldn't; controls
    and hand-offs name how they answered, in Hub's own words for anything not done. Never the data itself."""
    if not isinstance(result, dict):
        return 'answered', None
    found = next((k for k in OUTCOMES if result.get(k)), None)
    if kind == 'lookup' and found not in ('unavailable', 'error'):
        return 'read', None
    if found in NOT_DONE:
        detail = result[found]
        detail = '; '.join(map(str, detail)) if isinstance(detail, list) else detail
        return found, scrub(detail, DETAIL_LENGTH)
    return found or 'answered', None


# ---- recording one turn ----

class Call:
    """One tool call while it runs. `returned` and `failed` say how it went; `noted` keeps an error a source caught."""

    def __init__(self, trace, tool, kind, args, call_id):
        self.trace, self.tool, self.kind, self.call_id = trace, tool, kind, call_id
        self.args = json.dumps(_arg({k: v for k, v in dict(args).items() if v is not None}), ensure_ascii=False, default=str)
        self.started, self.outcome, self.detail, self.error, self.raised = time.time(), 'answered', None, None, False

    def returned(self, result):
        """What the tool returned. After an exception the outcome stays 'error', whatever stands in for it."""
        if not self.raised:
            self.outcome, self.detail = outcome(self.kind, result)

    def failed(self, error):
        self.error, self.raised, self.outcome = describe(error), True, 'error'


def noted(error):
    """A source caught `error` and answered “unavailable”: keep its type and traceback on the tool call that asked.
    Outside an Ask turn this does nothing. Never raises."""
    try:
        call = _current.get()
        if call is not None and call.error is None:
            call.error = describe(error)
    except Exception:  # noqa: BLE001 - tracing must never break what it watches
        pass


class Trace:
    """One Ask turn. Every method is safe to call from tool threads and never raises into the chat."""

    def __init__(self, thread):
        self.id, self.thread, self.started = uuid.uuid4().hex[:12], thread, time.time()
        self.status, self.error, self.calls = 'running', None, []
        self.model_calls = self.tokens_in = self.tokens_out = self.model_ms = 0
        self.cost, self._seq, self._lock = 0.0, 0, threading.Lock()
        self._write(lambda conn: (
            conn.execute('delete from tool_calls where turn in (select id from turns where started < ?)', (self._cutoff(),)),
            conn.execute('delete from model_calls where turn in (select id from turns where started < ?)', (self._cutoff(),)),
            conn.execute('delete from turns where started < ?', (self._cutoff(),)),
            conn.execute("insert into turns(id, thread, started, status) values (?,?,?,'running')",
                         (self.id, self.thread, self.started))))

    def _cutoff(self):
        return self.started - KEEP_DAYS * 86400

    def _write(self, work):
        try:
            with closing(_db()) as conn, conn:
                work(conn)
        except Exception:  # noqa: BLE001 - a lost trace row is better than a broken chat
            pass

    def _next(self):
        with self._lock:
            self._seq += 1
            return self._seq

    @contextmanager
    def call(self, tool, kind, args, call_id=None):
        """Record one tool call around its run."""
        call = Call(self, tool, kind, args, call_id)
        token = _current.set(call)
        try:
            yield call
        finally:
            _current.reset(token)
            self._end(call)

    def _end(self, call):
        ms = round((time.time() - call.started) * 1000)
        error = call.error or (None, None, None)
        with self._lock:
            self.calls.append(call)
        self._write(lambda conn: conn.execute(
            'insert into tool_calls(turn, seq, call_id, tool, kind, args, started, ms, outcome, detail, error_type, '
            'error_message, traceback) values (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (self.id, self._next(), call.call_id, call.tool, call.kind, call.args, call.started, ms, call.outcome,
             call.detail, *error)))

    def model(self, started, response=None, error=None):
        """One model call: its time, its tokens and cost when it answered, its error when it didn't."""
        ms = round((time.time() - started) * 1000)
        usage = getattr(response, 'usage', None)
        tokens_in, tokens_out = (getattr(usage, 'input_tokens', 0) or 0), (getattr(usage, 'output_tokens', 0) or 0)
        try:
            cost = float((getattr(response, 'provider_details', None) or {}).get('cost') or 0)
        except (TypeError, ValueError):
            cost = 0.0
        found = describe(error) if error is not None else (None, None, None)
        with self._lock:
            self.model_calls += 1
            self.tokens_in, self.tokens_out = self.tokens_in + tokens_in, self.tokens_out + tokens_out
            self.cost, self.model_ms = self.cost + cost, self.model_ms + ms
            seq = self.model_calls
        self._write(lambda conn: conn.execute(
            'insert into model_calls(turn, seq, started, ms, tokens_in, tokens_out, cost, error_type, error_message, '
            'traceback) values (?,?,?,?,?,?,?,?,?,?)', (self.id, seq, started, ms, tokens_in, tokens_out, cost, *found)))

    def fail(self, error):
        """The turn ended on this error. The first error wins: a later one is usually its echo."""
        if self.error is None:
            self.error = describe(error)

    def changed(self):
        """Whether a control or hand-off in this turn may have changed something or passed a message on."""
        return any(c.kind != 'lookup' and c.outcome not in (*NOT_DONE, 'needs_tap', 'already') for c in self.calls)

    def finish(self, status):
        """End the turn as 'ok', 'failed' or 'stopped', and write its one line to hub.log."""
        self.status = status
        ended = time.time()
        errors = sum(1 for c in self.calls if c.error or c.outcome in ('error', 'unavailable'))
        kind, message, text = self.error or (None, None, None)
        self._write(lambda conn: conn.execute(
            'update turns set ended=?, status=?, model_calls=?, tokens_in=?, tokens_out=?, cost=?, model_ms=?, tools=?, '
            'tool_errors=?, error_type=?, error_message=?, traceback=? where id=?',
            (ended, status, self.model_calls, self.tokens_in, self.tokens_out, self.cost, self.model_ms, len(self.calls),
             errors, kind, message, text, self.id)))
        log_line(self.id, self.thread, status, round((ended - self.started) * 1000), self.model_ms, self.model_calls,
                 len(self.calls), errors, kind)


def log_line(turn, thread, status, ms, model_ms, model_calls, tools, tool_errors, error_type):
    """hub.log is Hub's stderr under launchd. Ids, durations, counts and an exception type only, never text."""
    try:
        print(f'ask turn={turn} thread={thread} status={status} ms={ms} model_ms={model_ms} model_calls={model_calls} '
              f'tools={tools} tool_errors={tool_errors}' + (f' error={error_type}' if error_type else ''),
              file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def call(trace, tool, kind, args, call_id=None):
    """Trace.call when there is a trace (an Ask turn), else a call that is kept nowhere (golden tests, tools)."""
    if trace is None:
        found = Call(None, tool, kind, args, call_id)
        token = _current.set(found)
        try:
            yield found
        finally:
            _current.reset(token)
    else:
        with trace.call(tool, kind, args, call_id) as found:
            yield found


# ---- reading it back ----

def turns(limit=50):
    """The newest turns, each with its tool calls and model calls."""
    conn = _read()
    if conn is None:
        return []
    with closing(conn):
        rows = [dict(r) for r in conn.execute('select * from turns order by started desc limit ?', (int(limit),))]
        for row in rows:
            _fill(conn, row)
    return rows


def turn(turn_id):
    conn = _read()
    if conn is None:
        return None
    with closing(conn):
        row = conn.execute('select * from turns where id = ?', (str(turn_id)[:12],)).fetchone()
        return _fill(conn, dict(row)) if row else None


def _fill(conn, row):
    row['tool_calls'] = [dict(r) for r in conn.execute('select * from tool_calls where turn = ? order by seq', (row['id'],))]
    row['model_calls_list'] = [dict(r) for r in conn.execute('select * from model_calls where turn = ? order by seq',
                                                             (row['id'],))]
    for tool in row['tool_calls']:
        tool['changes'] = changes(tool) if tool['kind'] == 'control' else []
    return row


def changes(tool):
    """The house and music change rows a control made: those from Ask that started while it ran."""
    path = config.DATA_DIR / 'hub.db'
    if not path.exists():
        return []
    start, end = tool['started'] - 0.5, tool['started'] + tool['ms'] / 1000 + CHANGE_SLACK
    found = []
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        for table, page, what in (('house_changes', '/house/changes', 'name'), ('music_changes', '/house/playlists', 'name')):
            try:
                rows = conn.execute(f"select id, batch, ts, {what} as name, action, outcome, undone from {table} "
                                    "where via = 'ask' and ts between ? and ? order by id", (start, end)).fetchall()
            except sqlite3.OperationalError:
                continue             # that table isn't made until the first change
            found += [{**dict(r), 'table': table, 'href': f'{page}#batch-{r["batch"]}'} for r in rows]
    return found


def failure(trace_row):
    """For an improvement brief: how the turn failed and how each tool answered. Tool names, outcome kinds, Hub's
    own words and exception types; an exception's message only when Hub writes it to be shown. None if nothing."""
    if not trace_row:
        return None
    used = []
    for t in trace_row['tool_calls']:
        said = t['outcome'] + (f': “{t["detail"]}”' if t['detail'] else '')
        if t['error_type']:
            said += f'; {_error_words(t["error_type"], t["error_message"])}'
        used.append(f'{t["tool"]} ({said})')
    parts = []
    if trace_row['status'] in ('failed', 'stopped'):
        cause = _error_words(trace_row['error_type'], trace_row['error_message']) if trace_row['error_type'] else 'no error was caught'
        parts.append(f'This answer failed ({cause}).')
    if used:
        parts.append('Tool outcomes from Hub’s trace: ' + '; '.join(used) + '.')
    if not parts:
        return None
    return ' '.join(parts) + f' Trace {trace_row["id"]} is on the Ask activity page.'


def _error_words(kind, message):
    return f'{kind}: {message}' if kind in SAFE_TYPES and message else str(kind)


# ---- the shell ----

def _when(ts):
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else '-'


def _seconds(row):
    return f'{(row["ended"] - row["started"]):.1f}s' if row['ended'] else 'running'


def format_turn(row, show_traceback=False):
    lines = [f'turn {row["id"]}  thread {row["thread"]}  {_when(row["started"])}  {row["status"]}  {_seconds(row)}',
             f'  model: {row["model_calls"]} call(s), {row["model_ms"]} ms, {row["tokens_in"]} in / {row["tokens_out"]} out tokens, '
             f'${row["cost"]:.4f}']
    if row['error_type']:
        lines.append(f'  error: {row["error_type"]}: {row["error_message"] or ""}')
    for m in row['model_calls_list']:
        if m['error_type']:
            lines.append(f'  model call {m["seq"]} failed after {m["ms"]} ms: {m["error_type"]}: {m["error_message"] or ""}')
    for t in row['tool_calls']:
        lines.append(f'  {t["seq"]}. {t["tool"]} ({t["kind"]}) {t["args"]} -> {t["outcome"]}  {t["ms"]} ms')
        if t['detail']:
            lines.append(f'       {t["detail"]}')
        if t['error_type']:
            lines.append(f'       error: {t["error_type"]}: {t["error_message"] or ""}')
        for c in t['changes']:
            lines.append(f'       {c["table"]} #{c["id"]} batch {c["batch"]}: {c["action"]} {c["name"]} -> {c["outcome"]}'
                         + (' (undone)' if c['undone'] else ''))
        if show_traceback and t['traceback']:
            lines += ['       ' + line for line in t['traceback'].rstrip().splitlines()]
    if show_traceback:
        for m in row['model_calls_list']:
            if m['traceback']:
                lines += [f'  model call {m["seq"]} traceback:'] + ['    ' + line for line in m['traceback'].rstrip().splitlines()]
        if row['traceback']:
            lines += ['  turn traceback:'] + ['    ' + line for line in row['traceback'].rstrip().splitlines()]
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ask-trace', description='Read Ask Hub turn traces (read-only).')
    parser.add_argument('turn', nargs='?', help='a turn id from hub.log or the Ask activity page')
    parser.add_argument('--last', type=int, default=None, help='show the last N turns (default 10)')
    parser.add_argument('--failed', action='store_true', help='only turns that failed or had a tool error')
    parser.add_argument('--traceback', action='store_true', help='include stored tracebacks')
    parser.add_argument('--json', action='store_true', help='print JSON')
    parser.add_argument('--data-dir', help="Hub's data folder (default: $HUB_DATA_DIR)")
    args = parser.parse_args(argv)
    if args.data_dir:
        config.DATA_DIR = __import__('pathlib').Path(args.data_dir).expanduser()
    if not _path().exists():
        print(f'No Ask trace in {config.DATA_DIR}.', file=sys.stderr)
        return 1
    if args.turn:
        rows = [turn(args.turn)]
        if rows[0] is None:
            print('No such turn.', file=sys.stderr)
            return 1
    else:
        rows = turns(500 if args.failed else (args.last or 10))
        if args.failed:
            rows = [r for r in rows if r['status'] != 'ok' or r['tool_errors']][:args.last or 10]
    if not args.traceback:
        for r in rows:
            r['traceback'] = None
            for part in r['tool_calls'] + r['model_calls_list']:
                part['traceback'] = None
    if args.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        print('\n\n'.join(format_turn(r, args.traceback) for r in rows) or 'No turns yet.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
