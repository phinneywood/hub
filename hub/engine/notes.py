"""Remember by tap: Ask proposes a short standing note, and only the owner's tap saves it.

The note is proposed only when the owner's latest message asks Hub to remember something, and it must be
made of that message's own words, so text from mail, pages or device names can never become a note. A
proposal waits on the Notes page (`/engine/notes`) for a day. Save puts it into the standing instructions
(`state.add_instruction`), which steer the overnight review and are given to Ask on later chats. A note about
speakers playing together keeps those speakers as a zone (`hub/tools/house/zones.py`).
"""
import json
import re
import time
import uuid

from .. import store
from . import judge, state

NOTE_LENGTH = 160
WAIT_SECONDS = 24 * 3600
# The owner's own message must ask for something to be remembered, in words like these.
ASKED = re.compile(r"\b(remember|from now on|keep in mind|don'?t forget|do not forget|make a note|note that|"
                   r"always|whenever|every time|in future|in the future|going forward)\b", re.I)
SMALL = {'a', 'an', 'the', 'and', 'or', 'to', 'of', 'in', 'on', 'at', 'is', 'are', 'be', 'it', 'its', 'for', 'with',
         'there', 'that', 'this', 'when', 'so', 'as', 'by', 'from', 'one', 'same', 'together', 'both', 'all', 'my',
         'our', 'your', 'please', 'should', 'always', 'never', 'not', 'no', 'do', 'don', 't', 's'}
SCHEMA = """
create table if not exists ask_note_proposals(
  id text primary key, ts real not null, login text, text text not null, said text not null,
  zone text not null default '[]', status text not null default 'waiting');
"""


class Refused(Exception):
    """Nothing was proposed. The message is safe to show."""


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _words(text):
    return {w.rstrip('s') for w in re.findall(r"[a-z0-9]+", str(text).lower()) if w not in SMALL}


def grounded(note, said):
    """Whether the note is made of the owner's own words: at least two of its words, and most of them, come from
    the message the owner typed. A note copied from an email or a page fails this."""
    mine, own = _words(note), _words(said)
    shared = mine & own
    return len(shared) >= 2 and len(shared) * 3 >= len(mine) * 2


def propose(note, said, *, login, zone=()):
    """Keep a proposal for the owner's tap. Returns {'id', 'text', 'zone'}. Raises Refused."""
    text = ' '.join(str(note or '').split())
    if not text:
        raise Refused('Say what to remember, in one short sentence.')
    if len(text) > NOTE_LENGTH:
        raise Refused(f'Keep the note under {NOTE_LENGTH} characters: one short sentence.')
    if judge.scrub(text) != text:
        raise Refused('A note can’t hold links or email addresses.')
    if not ASKED.search(str(said or '')):
        raise Refused('The owner’s latest message doesn’t ask Hub to remember anything, so nothing was proposed.')
    if not grounded(text, said):
        raise Refused('A note must say what the owner said, in their words. Nothing was proposed.')
    zone = sorted(set(zone))
    if len(zone) == 1:
        raise Refused('A zone needs at least two speakers.')
    found = {'id': uuid.uuid4().hex[:12], 'text': text, 'zone': zone}
    with _db() as conn:
        conn.execute('insert into ask_note_proposals(id, ts, login, text, said, zone) values (?,?,?,?,?,?)',
                     (found['id'], time.time(), login, text, ' '.join(str(said).split())[:600], json.dumps(zone)))
    store.log('ask_note', 'proposed')
    return found


def waiting():
    """Proposals still waiting for a tap, newest first."""
    with _db() as conn:
        rows = conn.execute("select * from ask_note_proposals where status = 'waiting' and ts > ? order by ts desc",
                            (time.time() - WAIT_SECONDS,)).fetchall()
    return [{**dict(r), 'zone': json.loads(r['zone'])} for r in rows]


def _waiting(proposal_id):
    return next((p for p in waiting() if p['id'] == str(proposal_id)[:12]), None)


def save(proposal_id):
    """The owner's tap: the note becomes a standing instruction, with its zone. Raises Refused."""
    from ..tools.house import zones
    found = _waiting(proposal_id)
    if found is None:
        raise Refused('That note isn’t waiting any more. Ask again in the chat.')
    instruction_id = state.add_instruction(found['text'])
    if not instruction_id:
        raise Refused(f'Hub keeps at most {state.MAX_INSTRUCTIONS} notes. Remove one first.')
    if found['zone']:
        zones.add(instruction_id, found['zone'])
    with _db() as conn:
        conn.execute("update ask_note_proposals set status = 'saved' where id = ?", (found['id'],))
    store.log('ask_note', 'saved')
    return found


def skip(proposal_id):
    with _db() as conn:
        conn.execute("update ask_note_proposals set status = 'skipped' where id = ? and status = 'waiting'",
                     (str(proposal_id)[:12],))


def for_ask():
    """The saved notes, for Ask's instructions on each turn, or ''."""
    saved = state.instructions()
    if not saved:
        return ''
    return ('Standing notes the owner saved with their own tap. Follow them unless the owner\'s latest message '
            'says otherwise:\n' + '\n'.join(f'- {i["text"]}' for i in saved))
