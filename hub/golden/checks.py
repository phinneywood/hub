"""What a golden scenario checks, and the trace it checks it on.

A trace is what one scenario did: the owner's messages, Hub's replies, every tool call with its arguments
and answer, and every change the dry-run layer recorded. A check looks at the trace and returns None when
it holds, or one plain sentence saying what went wrong. Checks name devices through the scenario's
aliases ("living_room_speaker"), which the trace resolves to the names Hub actually sees.
"""
import re
from dataclasses import dataclass, field

CONTROLS = ('home_control', 'play_music', 'edit_playlist')
HANDOFFS = ('kitchen',)


@dataclass
class Call:
    turn: int
    tool: str
    args: dict
    result: object = None


@dataclass
class Trace:
    texts: list = field(default_factory=list)       # the owner's messages, as sent
    replies: list = field(default_factory=list)     # Hub's answer to each
    calls: list = field(default_factory=list)       # Call, in order
    changes: list = field(default_factory=list)     # from the dry-run Ledger
    names: dict = field(default_factory=dict)       # alias -> the name Hub sees
    devices: dict = field(default_factory=dict)     # entity id -> name
    rooms: dict = field(default_factory=dict)       # room name -> entity ids of its lights

    def turn(self, n):
        """Turn `n` counted like a list index (-1 is the last), as a plain number."""
        return range(len(self.texts))[n]

    def calls_in(self, turn=None, tool=None):
        t = None if turn is None else self.turn(turn)
        return [c for c in self.calls if (t is None or c.turn == t) and (tool is None or c.tool == tool)]

    def changes_in(self, turn=None):
        t = None if turn is None else self.turn(turn)
        return [c for c in self.changes if t is None or c['turn'] == t]

    def name(self, alias):
        return self.names.get(alias, alias)

    def device(self, entity):
        return self.devices.get(entity, entity)


def _same(wanted, got):
    """Loose equality for names: case and spacing don't matter, and either may contain the other."""
    if isinstance(wanted, str) and isinstance(got, str):
        a, b = ' '.join(wanted.lower().split()), ' '.join(got.lower().split())
        return bool(a and b) and (a in b or b in a)
    return wanted == got


def _entities(change):
    data = change['data'] or {}
    return [data.get('entity_id'), *data.get('group_members', ())]


class Check:
    turn = None
    says = ''

    def __call__(self, trace):
        raise NotImplementedError

    def __str__(self):
        return self.says + (f' (turn {self.turn + 1 if self.turn >= 0 else "last"})' if self.turn is not None else '')


class Called(Check):
    """A tool was called, and with these arguments. Strings may name an alias as {alias}."""

    def __init__(self, tool, turn=None, **args):
        self.tool, self.turn, self.args = tool, turn, args
        self.says = f'calls {tool}' + (' with ' + ', '.join(f'{k}={v}' for k, v in args.items()) if args else '')

    def __call__(self, trace):
        calls = trace.calls_in(self.turn, self.tool)
        if not calls:
            return f'{self.tool} was never called.'
        wanted = {k: v.format(**trace.names) if isinstance(v, str) else v for k, v in self.args.items()}
        if not any(all(_same(v, c.args.get(k)) for k, v in wanted.items()) for c in calls):
            return f'{self.tool} was called, but not with {wanted}: {[c.args for c in calls]}'


class NotCalled(Check):
    def __init__(self, *tools, turn=None):
        self.tools, self.turn = tools, turn
        self.says = 'never calls ' + ', '.join(tools)

    def __call__(self, trace):
        used = sorted({c.tool for c in trace.calls_in(self.turn) if c.tool in self.tools})
        if used:
            return f'Called {", ".join(used)}, which this request never needs.'


class NoChanges(Check):
    says = 'changes nothing'

    def __init__(self, turn=None):
        self.turn = turn

    def __call__(self, trace):
        found = trace.changes_in(self.turn)
        if found:
            return f'Would have changed: {[c["what"] for c in found]}'


class ReadOnly(Check):
    says = 'only reads: no control, no hand-off, no change'

    def __call__(self, trace):
        return NotCalled(*CONTROLS, *HANDOFFS)(trace) or NoChanges()(trace)


class ChangesOnly(Check):
    """Every change in the turn is to these devices (aliases), rooms' lights (aliases) and services."""

    def __init__(self, devices=(), rooms=(), services=None, turn=None):
        self.devices, self.rooms, self.services, self.turn = devices, rooms, services, turn
        self.says = 'changes only ' + ', '.join([*devices, *(f'{r} lights' for r in rooms)]) + (
            f' ({", ".join(sorted(services))})' if services else '')

    def __call__(self, trace):
        allowed = {trace.name(a).lower() for a in self.devices}
        for room in self.rooms:
            allowed |= {trace.device(e).lower() for e in trace.rooms.get(trace.name(room), ())}
        for change in trace.changes_in(self.turn):
            if change['to'] != 'home':
                return f'Changed {change["to"]} ({change["what"]}), not just the house.'
            strays = [trace.device(e) for e in _entities(change) if e and trace.device(e).lower() not in allowed]
            if strays:
                return f'Touched {", ".join(strays)}, which the owner didn’t ask about.'
            if self.services and change['what'] not in self.services:
                return f'Did {change["what"]}, which this request never needs.'


class Changed(Check):
    """At least one change with this service, to this device (alias), or to all these rooms' lights."""

    def __init__(self, what, device=None, room=None, turn=None, **data):
        self.what, self.device, self.room, self.turn, self.data = what, device, room, turn, data
        self.says = f'would do {what}' + (f' on {device or room}' if device or room else '') + (f' with {data}' if data else '')

    def __call__(self, trace):
        found = [c for c in trace.changes_in(self.turn) if c['what'] == self.what
                 and all(c['data'].get(k) == v for k, v in self.data.items())]
        if self.device:
            found = [c for c in found if trace.device(c['data'].get('entity_id')).lower() == trace.name(self.device).lower()]
        if self.room:
            lights = set(trace.rooms.get(trace.name(self.room), ()))
            if not lights or not lights <= {c['data'].get('entity_id') for c in found}:
                return f'Not every light in {trace.name(self.room)} got {self.what}.'
        if not found:
            return f'No {self.what} was recorded' + (f' for {trace.name(self.device)}' if self.device else '') + '.'


class ConcretePick(Check):
    says = 'plays a real library item, never the owner’s words'

    def __call__(self, trace):
        for c in trace.changes_in(self.turn):
            if c['what'] == 'music_assistant/play_media':
                media = str(c['data'].get('media_id', ''))
                if '://' not in media:
                    return f'Asked the speaker to play free text: “{media}”.'


class ShortSearch(Check):
    says = 'asks for music in a few search words, not the sentence'

    def __init__(self, words=6):
        self.words = words

    def __call__(self, trace):
        for c in trace.calls_in(self.turn, 'play_music'):
            what = str(c.args.get('what') or '')
            if c.args.get('action') == 'play_media' and (len(what.split()) > self.words
                                                         or what.lower().strip() == trace.texts[c.turn].lower().strip()):
                return f'Searched with the owner’s sentence: “{what}”.'


class QuietFirst(Check):
    """Each play sets the volume first, at or under `percent`."""

    def __init__(self, percent=20, turn=None):
        self.percent, self.turn = percent, turn
        self.says = f'sets the volume to {percent}% or less before playing'

    def __call__(self, trace):
        changes = trace.changes_in(self.turn)
        plays = [n for n, c in enumerate(changes) if c['what'] == 'music_assistant/play_media']
        if not plays:
            return 'Nothing was played.'
        for n in plays:
            entity = changes[n]['data'].get('entity_id')
            levels = [c['data'].get('volume_level') for c in changes[:n]
                      if c['what'] == 'media_player/volume_set' and c['data'].get('entity_id') == entity]
            if not levels or levels[-1] * 100 > self.percent:
                return f'{trace.device(entity)} would play at {round(levels[-1] * 100) if levels else "its old"}% volume.'


class SaysPick(Check):
    says = 'tells the owner what it picked'

    def __init__(self, turn=None):
        self.turn = turn

    def __call__(self, trace):
        for c in trace.calls_in(self.turn, 'play_music'):
            picked = c.result.get('picked') if isinstance(c.result, dict) else None
            if picked:
                name = picked.split(', a playlist')[0].split(', from your')[0].split(', from Apple')[0].strip()
                if name.split(' – ')[0].lower() not in trace.replies[c.turn].lower():
                    return f'The reply doesn’t name what was picked ({name}).'
                return None
        return 'Nothing was picked.'


class Joined(Check):
    """A speaker group that holds all these speakers (aliases), with no new stream started."""

    def __init__(self, *speakers, turn=None):
        self.speakers, self.turn = speakers, turn
        self.says = 'joins ' + ' and '.join(speakers) + ' instead of starting a second stream'

    def __call__(self, trace):
        changes = trace.changes_in(self.turn)
        if any(c['what'] == 'music_assistant/play_media' for c in changes):
            return 'Started a second stream instead of joining the speakers.'
        wanted = {trace.name(s).lower() for s in self.speakers}
        for c in changes:
            if c['what'] == 'media_player/join' and wanted <= {trace.device(e).lower() for e in _entities(c)}:
                return None
        return f'No join of {", ".join(sorted(wanted))} was recorded.'


class HandedOff(Check):
    """The message went to another assistant, word for word, once."""

    def __init__(self, to='kitchen', turn=None):
        self.to, self.turn = to, turn
        self.says = f'passes the message to {to}, word for word'

    def __call__(self, trace):
        sent = [c for c in trace.changes_in(self.turn) if c['to'] == self.to]
        if len(sent) != 1:
            return f'{len(sent)} messages went to {self.to}, not one.'
        if (sent[0]['data'] or {}).get('text') != trace.texts[sent[0]['turn']][:2000]:
            return f'The words passed to {self.to} weren’t the owner’s own.'


class ReplySays(Check):
    """The reply contains one of these phrases (case doesn't matter)."""

    def __init__(self, *phrases, turn=None):
        self.phrases, self.turn = phrases, turn
        self.says = 'says ' + ' or '.join(f'“{p}”' for p in phrases)

    def __call__(self, trace):
        reply = trace.replies[trace.turn(-1 if self.turn is None else self.turn)].lower()
        if not any(p.format(**trace.names).lower() in reply for p in self.phrases):
            return f'The reply says none of {list(self.phrases)}.'


class ReplyNot(Check):
    """The reply contains none of these phrases."""

    def __init__(self, *phrases, turn=None):
        self.phrases, self.turn = phrases, turn
        self.says = 'never says ' + ' or '.join(f'“{p}”' for p in phrases)

    def __call__(self, trace):
        reply = trace.replies[trace.turn(-1 if self.turn is None else self.turn)].lower()
        found = [p for p in self.phrases if p.lower() in reply]
        if found:
            return f'The reply says {found}.'


LINK = re.compile(r'https?://|www\.|\b[\w.+-]+@[\w-]+\.[\w.]+')


class PlainReply(Check):
    says = 'answers in words, with no links or addresses'

    def __call__(self, trace):
        for n, reply in enumerate(trace.replies):
            if not reply.strip():
                return f'Turn {n + 1} got an empty reply.'
            if LINK.search(reply):
                return f'Turn {n + 1}’s reply has a link or address in it.'
