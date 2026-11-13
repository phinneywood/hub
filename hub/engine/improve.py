"""Improvement requests: short briefs for the next building session, filed by the owner's tap, read by the Manager.

"Improve this" under an Ask answer files one right away. The brief is one paragraph about what Hub could and
couldn't do on that turn: which tools it used, how each control or hand-off answered, in Hub's own words, and
whether the answer said it couldn't. When the turn has a trace (hub/ask_trace.py), it adds each tool's outcome and
exception type and, for a failed answer, how it failed; an exception's own words only when Hub wrote them to be shown. It never holds the owner's message, the reply, mail, or anything a lookup
returned, so it is safe to show on Today and to hand to a coding session. The owner adds what they wanted when
the Manager asks.

The list is a file in Hub's private data folder (improvement-requests.json, owner-only), so the Manager can read
it without Hub running. Hub only ever adds to it and marks entries; it never edits or deploys its own code.
"""
import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime

from .. import config, home

FILE = 'improvement-requests.json'
KEEP = 200
SHOW_SECONDS = 24 * 3600         # a new request shows on Today for a day
SENTENCE = 160
OUTCOMES = ('refused', 'unavailable', 'nothing_matched', 'ask_first', 'needs_tap', 'stopped', 'not_confirmed',
            'not_done', 'songs_not_found', 'already', 'done', 'joined', 'ran', 'asked', 'picked')
SAYS_CANT = re.compile(r"\b(can'?t|can’t|cannot|couldn'?t|couldn’t|not able|isn'?t able|unable|not yet)\b", re.I)
_lock = threading.Lock()


def _path():
    return config.DATA_DIR / FILE


def requests():
    """Every request, newest first."""
    try:
        found = json.loads(_path().read_text())
    except (FileNotFoundError, ValueError):
        return []
    return sorted((r for r in found if isinstance(r, dict)), key=lambda r: r.get('at', 0), reverse=True)


def _write(rows):
    folder = _path().parent
    folder.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=folder, prefix='.improve-')
    with os.fdopen(handle, 'w') as out:
        json.dump(rows[:KEEP], out, indent=1, ensure_ascii=False)
    os.chmod(temporary, 0o600)
    os.replace(temporary, _path())


def file(brief, *, source, login=None):
    """Add one request. The same brief from the same place within a day is kept once. Returns the entry."""
    brief = ' '.join(str(brief).split())[:1200]
    with _lock:
        rows = requests()
        same = next((r for r in rows if r['brief'] == brief and r['source'] == source
                     and time.time() - r['at'] < SHOW_SECONDS), None)
        if same:
            return same
        entry = {'id': uuid.uuid4().hex[:12], 'at': time.time(), 'source': source, 'status': 'new', 'brief': brief,
                 'by': 'owner' if login else None}
        _write([entry, *rows])
    return entry


def remove(request_id):
    with _lock:
        _write([r for r in requests() if r['id'] != str(request_id)[:12]])


def today_items():
    """A quiet line on Today for each request filed in the last day."""
    from .. import today
    return [today.rec(f'improve-{r["id"]}', 'info', 'Improvement filed for the next building session', r['brief'][:200],
                      href='/engine#improvements', cta='Open')
            for r in requests() if r['status'] == 'new' and time.time() - r['at'] < SHOW_SECONDS]


# ---- the brief for one Ask turn ----

def _sentence(text):
    from . import judge
    return ' '.join(judge.scrub(str(text)).split())[:SENTENCE]


def _outcome(tool, content, controls):
    """How one tool answered, in a few words. Lookups only say they read; controls and hand-offs say how they
    answered, in Hub's own words for anything that wasn't done."""
    if tool not in controls:
        return f'{tool} (read)'
    if not isinstance(content, dict):
        return f'{tool} (no clear answer)'
    kind = next((k for k in OUTCOMES if content.get(k)), None)
    if kind in ('refused', 'unavailable', 'nothing_matched', 'ask_first', 'stopped', 'not_confirmed', 'not_done'):
        detail = content[kind]
        detail = '; '.join(map(str, detail)) if isinstance(detail, list) else detail
        return f'{tool} ({kind.replace("_", " ")}: “{_sentence(detail)}”)'
    if kind == 'needs_tap':
        return f'{tool} (left something waiting for the owner’s tap)'
    return f'{tool} ({"done" if kind else "answered"})'


def brief_for(turn_messages, when=None, trace=None):
    """One paragraph for an Ask turn (its model messages), or None when there is nothing to say. With the turn's
    trace (ask_trace.turn), the tools and their outcomes come from it, and a failed answer says how it failed."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from . import catalog
    controls = {c.slug for c in catalog.controls()} | {h.slug for h in catalog.handoffs()}
    returns = {p.tool_call_id: p.content for m in turn_messages if isinstance(m, ModelRequest)
               for p in m.parts if isinstance(p, ToolReturnPart)}
    used = [_outcome(p.tool_name, returns.get(p.tool_call_id), controls) for m in turn_messages
            if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, ToolCallPart)]
    reply = ''.join(p.content for m in turn_messages if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, TextPart))
    when = when or datetime.now(home.PACIFIC)
    can = ', '.join(sorted(controls))
    from .. import ask_trace
    traced = ask_trace.failure(trace)
    failed = bool(trace and trace['status'] in ('failed', 'stopped'))
    if trace and trace['tool_calls']:
        used_line = ''                        # the trace names every tool and how it answered
    else:
        used_line = ('Hub used: ' + '; '.join(dict.fromkeys(used)) + '.') if used else 'Hub answered without using a tool.'
    parts = [f'From Ask on {when:%b} {when.day}: the owner tapped “Improve this” on an answer.',
             used_line, traced or '',
             'The answer said Hub couldn’t do something.' if SAYS_CANT.search(reply) and not failed else '',
             f'Ask can change things only through: {can}.',
             'The owner’s message and the answer are not included; ask the owner what they wanted.']
    return ' '.join(p for p in parts if p)
