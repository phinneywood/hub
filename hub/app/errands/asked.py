"""The taps on an errand's card, asked for in words: Ask's errand tool (hub/app/ask/tools.py).

Done, Tomorrow, Not this, putting its things on the shopping list, putting one back, and where replies open: the things
Hub does itself, each with Undo. Sending a reply, paying, booking and adding to a calendar stay buttons on the card.

An errand's title comes from mail, and the model that calls this has read it. So the person's own words decide, checked
here and not by the model. One sentence of the message they typed must both ask for the tap and name the errand, in
its words and their order ("I paid the power bill"). A question ("did I pay the power bill?"), a sentence with "if" or
"whether", and a "not" or "yet" ("I haven't paid the power bill yet") ask for nothing, except in "I don't need it",
which is the ask. A name is matched against what the card shows as the errand's own (its title, kind and who it is
from, and for the list its things), never the email's text, and fits one errand at most: a name that fits none or
several changes nothing, and nor does any other name sent with it. Only errands the asker may see are looked at
(store.rows_for), so another person's are not there to name.
"""
import re

from ..words import counted
from . import picture, store, taps

BATCH = 'r'                  # an Undo under an Ask answer: 'r12.15', the errands it changed; 'rcmail', where replies opened
COMPOSE = BATCH + 'c'
NAMED_MOST = 8
ASKS = {
    'done': r"\b(?:done|paid|finished|sent|replied|answered|rsvp(?:'?d|ed)|signed|returned|booked|renewed|bought|handled|"
            r"took care of|dealt with|completed?|(?:mark|tick|cross)(?:ed)?\b.*\b(?:done|off))\b",
    'later': r"\b(?:tomorrow|later|not (?:now|today)|another day|remind me|set (?:it |that |them )?aside|snooze)\b",
    'not': r"\b(?:not this|(?:don't|do not|doesn't|does not) need|no need|not needed|ignore|dismiss|skip|drop|never ?mind|"
           r"forget about|not going|won't go|can't make it|not interested)\b",
    'list': r"\b(?:shopping|(?:on|to|onto) (?:the |my |our )?list)\b",
    'back': r"\b(?:undo|put (?:it |that |them )?back|bring (?:it |that |them )?back|restore|reopen)\b",
}
REPLIES = {'gmail': r'\bgmail\b', 'mail': r"\bmail app\b|\b(?:apple|iphone|ios|mac) mail\b"}
SET = re.compile(r'\b(?:open|opens|use|set|switch|change|make|put)\b')
SENTENCES = re.compile(r'[^.;!?\n]+[.;!?]*')
CLAUSES = re.compile(r',\s*(?=(?:and|but|then|so|not|though|although|while)\b)|\s+(?:but|then|though|although|while)\s+')
QUESTION = re.compile(r'^(?:and |but |so )?(?:did|do|does|have|has|had|is|was|were|are|am|should|shall)\s+(?:i|we|you|it|they)\b')
REQUEST = re.compile(r'^(?:(?:can|could|would|will) you|please)\b')
IFFY = re.compile(r'\b(?:if|whether|unless)\b')
NEGATED = re.compile(r"\b(?:not|never|yet)\b|n't\b")
# Words that name no errand: "the", and the words that ask for a tap ("I paid the power bill" names "power bill").
IGNORED = {'the', 'a', 'an', 'my', 'our', 'your', 'that', 'this', 'these', 'those', 'one', 'ones', 'it', 'errand', 'errands',
           'to', 'for', 'of', 'and', 'from', 'about', 'on', 'in', 'at', 'with', 'thing', 'things', 'please', 'i', 'we', 've',
           'done', 'paid', 'finished', 'sent', 'replied', 'answered', 'signed', 'returned', 'booked', 'renewed', 'bought',
           'handled', 'took', 'care', 'dealt', 'complete', 'completed', 'mark', 'marked', 'tick', 'ticked', 'cross',
           'crossed', 'off', 'tomorrow', 'later', 'set', 'aside', 'snooze', 'remind', 'ignore', 'dismiss', 'skip', 'drop',
           'undo', 'put', 'back', 'add', 'added', 'shopping', 'list'}
TOLD = {'done': ('Marked done.', '{} marked done.'),
        'later': ('Set aside until tomorrow morning.', '{} set aside until tomorrow morning.'),
        'not': ('Gone from your errands.', '{} gone from your errands.'),
        'list': ('Its things are on the shopping list.', '{} have their things on the shopping list.'),
        'back': ('Put back as it was.', '{} put back as they were.')}


class Unclear(taps.Refused):
    """A name fits several of the asker's errands, or none. `found`: the titles that fit, or the ones they have, so the
    model can ask which."""

    def __init__(self, words, found=()):
        super().__init__(words)
        self.found = list(found)


def _plain(text):
    return re.sub(r"'s\b", '', str(text or '').lower().replace('’', "'").replace('‘', "'"))


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+(?:'[a-z]+)?", _plain(text)) if w not in IGNORED]


def clauses(said):
    """The parts of a message that could each ask for something on their own, as (words, asks): `asks` is False for a
    question or an "if". A question with "can you" or "please" is a request."""
    found = []
    for sentence in SENTENCES.findall(_plain(said)):
        asking = sentence.rstrip().endswith('?') and not REQUEST.match(sentence.strip())
        for part in CLAUSES.split(sentence.strip().rstrip('.;!?')):
            part = ' '.join(part.split())
            found.append((part, not (asking or QUESTION.match(part) or IFFY.search(part))))
    return found


def _asks(part, do):
    """Whether this part of the message asks for `do`, and isn't "not yet"."""
    words, asks = part
    trigger = re.search(ASKS[do], words) if asks else None
    if not trigger:
        return False
    return do == 'not' or not NEGATED.search(words[:trigger.start()] + ' ' + words[trigger.end():])


def _in_order(name, part):
    """Whether each word of the name is in this part of the message, in the same order."""
    said = iter(re.findall(r"[a-z0-9]+(?:'[a-z]+)?", part[0]))
    return all(any(word == w for w in said) for word in name)


def _fits(name, words):
    """Whether each word of a name is one of the errand's words, or begins as one does ("dentist" and "dental")."""
    return all(any(w == v or (len(w) >= 4 and v.startswith(w[:4])) for v in words) for w in name)


def _its_words(errand, do):
    """What an errand can be called by: what its card shows as its own, never what the email said."""
    said = [errand['title'], picture._who(errand), picture.NAMES.get(errand['kind'], '')]
    if do == 'list':
        said += [thing for way in errand['choices'] for thing in way.get('buy') or ()]
    return set(_words(' '.join(said)))


def _open_to(do, login):
    """The errands of this person's that `do` can change."""
    if do == 'back':
        return store.rows_for(login, "state != 'skipped' and undo is not null")
    found = store.rows_for(login, "state in ('ready', 'opened', 'later')")
    return [e for e in found if any(way.get('buy') for way in e['choices'])] if do == 'list' else found


def _pick(which, parts, do, login):
    """The one errand each name picks, all or none. Raises Refused or Unclear."""
    open_now, picked = _open_to(do, login), {}
    if not which:
        raise Unclear('Say which errand, in the person’s own words, like “the power bill”.', [e['title'] for e in open_now])
    if len(which) > NAMED_MOST:
        raise taps.Refused(f'Name at most {NAMED_MOST} errands at once.')
    for name in which:
        words = _words(name)
        if not words or not any(_in_order(words, part) for part in parts):
            raise taps.Refused('Name each errand in the words of the person’s own message, as they said it.')
        if not any(_in_order(words, part) and _asks(part, do) for part in parts):
            raise taps.Refused(f'Their message doesn’t ask for that for “{name}”, so nothing was changed.')
        fits = [e for e in open_now if _fits(words, _its_words(e, do))]
        if not fits:
            raise Unclear(f'None of your errands fits “{name}”.', [e['title'] for e in open_now])
        if len(fits) > 1:
            raise Unclear(f'“{name}” fits {len(fits)} errands: ask which one.', [e['title'] for e in fits])
        picked.setdefault(fits[0]['id'], fits[0])
    return list(picked.values())


def asked(do, which, said, login, replies_in=None, now=None):
    """Do what the message `said` asks of the errands it names (`which`, in its words), as taps on their cards would.
    `do`: done, later, not, list or back; or replies, with `replies_in` 'mail' or 'gmail'. Returns {'told': what to tell
    them, 'ids': the errands changed, 'batch': their Undo, or None}. Raises Refused or Unclear: then nothing changed."""
    parts = clauses(said)
    if do == 'replies':
        return _replies(replies_in, parts, login)
    if do not in ASKS:
        raise taps.Refused('An errand can be done, set aside until tomorrow, put away, put on the list or put back.')
    changed, problems = [], []
    for errand in _pick(which, parts, do, login):
        body = {'choice': next(n for n, way in enumerate(errand['choices']) if way.get('buy'))} if do == 'list' else {}
        try:
            taps.tap(errand['id'], do, body, login, now)
            changed.append(errand['id'])
        except taps.Refused as error:
            problems.append(str(error))
    if not changed:
        raise taps.Refused(problems[0] if problems else 'Nothing was changed.')
    one, several = TOLD[do]
    told = one if len(changed) == 1 else several.format(counted(len(changed), 'errand'))
    if problems:
        told += f' {counted(len(problems), "other")} couldn’t be: {problems[0]}'
    return {'told': told, 'ids': changed, 'batch': None if do == 'back' else BATCH + '.'.join(map(str, changed))}


def _replies(replies_in, parts, login):
    if replies_in not in REPLIES or not any(asks and SET.search(words) and re.search(REPLIES[replies_in], words)
                                            and not NEGATED.search(words) for words, asks in parts):
        raise taps.Refused('Change where replies open only when the person says where: their mail app or Gmail.')
    before = picture.compose(login)
    told = picture.set_compose(login, replies_in)
    if told is None:
        raise taps.Refused('Hub doesn’t read their Google account yet, so there are no replies to open.')
    return {'told': told + '.', 'ids': [], 'batch': COMPOSE + before}


def undo(batch, login):
    """The Undo under an Ask answer: each errand it changed put back, or replies opening where they did. Returns what to
    tell the person. Raises Refused."""
    batch = str(batch)
    if batch.startswith(COMPOSE):
        before = batch.removeprefix(COMPOSE)
        if before not in picture.COMPOSE or picture.set_compose(login, before) is None:
            raise taps.Refused('There is nothing left to undo there.')
        return 'Put back'
    put = 0
    for part in batch.removeprefix(BATCH).split('.')[:NAMED_MOST]:
        try:
            taps.tap(int(part), 'back', {}, login)
            put += 1
        except (taps.Refused, ValueError):
            continue
    if not put:
        raise taps.Refused('There is nothing left to undo there.')
    return 'Put back'
