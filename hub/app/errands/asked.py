"""The taps on an errand's card, asked for in words: Ask's errand tool (hub/app/ask/tools.py).

Done, Tomorrow, Not this, putting its things on the shopping list, putting one back, and where replies open: the things
Hub does itself, each with Undo. Sending a reply, paying, booking and adding to a calendar stay buttons on the card.

An errand's title comes from mail, and the model that calls this has read it. So the person's own words decide, checked
here and not by the model, and when in doubt nothing changes: the card's buttons are always there.
- Each name the model passes must be in the message, and counts as the person's whole phrase at that spot ("water
  bill", when the model passed "bill"). Every word of it must fit the errand's title, kind or sender (for the list, its
  things too), never the email's text, and it may fit one errand only.
- Each name must be tied by where it stands to the words asking for that tap: right after them ("I paid the power
  bill", "skip the dentist", "undo the power bill"), right before them ("the power bill is paid", "the photo form can
  wait"), or between their two halves ("mark the permission form done"), with only small words between. Another name
  joins only through "and" or a comma right after one already tied, and only where the sentence ends with it ("I paid
  the power bill and the water bill", not "... and the dentist can wait").
- In the part of the sentence holding the name or those words, a question ("did I pay the power bill?", "is the power
  bill paid", "who sent the photo form"), a condition ("if", "when", "once", "wish") or a "not" outside the asking
  words themselves ("I haven't paid the power bill", "don't skip the dentist") asks for nothing.
- If any name fails, nothing changes. Only errands the asker may see are looked at (store.rows_for).
"""
import re

from ..words import counted
from . import picture, store, taps

BATCH = 'r'                  # an Undo under an Ask answer: 'r12.15', the errands it changed; 'rcmail', where replies opened
COMPOSE = BATCH + 'c'
NAMED_MOST = 8
NEAR = 3                     # at most this many small words between a name and the words asking for its tap
GAP = None                   # in a pattern: where a name stands between the two halves of the asking words


def _set(words):
    return frozenset(words.split())


SMALL = _set('the a an my our your his her their this that these those for of off about as some')   # trigger, then name
LINK = _set('is was are were been be has have had all now already just finally also too as goes go things thing stuff')
TRAIL = _set('too also both well as already today now please')          # what may follow a name joined by "and"
CONJ = _set('and or but then so while though although nor')
AUX = _set('did do does has have had is was were are am be been being should shall can could will would may might must')
WH = _set('who what when where why how which whose whom')
CONDITION = _set('if whether unless when once wish whenever')
NEGATION = _set('not never no yet')
OPENERS = _set('oh ok okay well also and but so then')
IMPERATIVE = _set('open use set switch change make put send')
STOP = (SMALL | LINK | TRAIL | CONJ | AUX | WH | CONDITION | NEGATION | _set(
    'i we you me it they he she us them i\'m i\'ve we\'ve i\'ll we\'ll it\'s that\'s please errand errands '
    'in on at by with from to till until before after into onto over under during since '
    'today tomorrow yesterday tonight morning evening week weekend later'))
MARK = _set('mark marked tick ticked cross crossed check checked')
ASKS = {
    'done': [[_set("paid done finished sent replied answered rsvped rsvp'd signed returned booked renewed bought handled completed")],
             [_set('took'), _set('care'), _set('of')], [_set('dealt'), _set('with')],
             [MARK, GAP, _set('done off complete completed finished')]],
    'later': [[_set('tomorrow later snooze snoozed')], [_set('not'), _set('now today')], [_set('another'), _set('day')],
              [_set('remind'), _set('me'), _set('about of')], [_set('remind'), _set('me')], [_set('can'), _set('wait')],
              [_set('set put leave'), GAP, _set('aside')], [_set('set put'), _set('aside')]],
    'not': [[_set('skip ignore dismiss drop')], [_set("don't dont doesn't"), _set('need')],
            [_set('do does'), _set('not'), _set('need')], [_set('no'), _set('need'), _set('for to')],
            [_set('not'), _set('needed')], [_set("isn't aren't"), _set('needed')], [_set('never'), _set('mind')],
            [_set('forget'), _set('about')], [_set('not'), _set('going'), _set('to')], [_set("won't"), _set('go'), _set('to')],
            [_set("can't"), _set('make'), _set('it'), _set('to')], [_set('not'), _set('interested'), _set('in')]],
    'list': [[_set('on onto to'), _set('the my our'), _set('list')], [_set('on onto to'), _set('the my our'), _set('shopping'), _set('list')],
             [_set('on onto to'), _set('list')], [_set('on onto to'), _set('shopping'), _set('list')]],
    'back': [[_set('undo undid restore reopen')], [_set('put bring'), GAP, _set('back')], [_set('put bring'), _set('back')]],
}
ASKING = frozenset(word for patterns in ASKS.values() for pattern in patterns for item in pattern if item for word in item)
REPLIES = {'gmail': r'\bgmail\b', 'mail': r"\bmail app\b|\b(?:apple|iphone|ios|mac) mail\b"}
TOLD = {'done': ('Marked done.', '{} marked done.'),
        'later': ('Set aside until tomorrow morning.', '{} set aside until tomorrow morning.'),
        'not': ('Gone from your errands.', '{} gone from your errands.'),
        'list': ('Its things are on the shopping list.', '{} have their things on the shopping list.'),
        'back': ('Put back as it was.', '{} put back as they were.')}


class Unclear(taps.Refused):
    """A name fits several of the asker's errands, or none. `found`: the titles that fit, or the ones they have, as
    Ask's model may be told them (picture.titles), so it can ask which."""

    def __init__(self, words, found=()):
        super().__init__(words)
        self.found = list(found)


def _plain(text):
    return re.sub(r"'s\b", '', str(text or '').lower().replace('’', "'").replace('‘', "'"))


def _tokens(text):
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?|,", _plain(text))


def _negative(word):
    return word in NEGATION or word.endswith("n't")


def _content(word):
    return word != ',' and word not in STOP and word not in ASKING and not _negative(word)


class _Sentence:
    """One sentence of the message: its words (commas kept), which part of it each word is in, and whether it asks."""

    def __init__(self, text):
        self.words = _tokens(text)
        self.asking = not (text.rstrip().endswith('?') and not self._request(self.words))
        self.part, n = [], 0
        for word in self.words:
            if word == ',' or word in CONJ:
                n += 1
                self.part.append(None)
            else:
                self.part.append(n)
        self.parts = {}
        for at, n in enumerate(self.part):
            if n is not None:
                self.parts.setdefault(n, []).append(at)

    @staticmethod
    def _request(words):
        return bool(words) and (words[0] == 'please' or (len(words) > 1 and words[0] in ('can', 'could', 'would', 'will')
                                                         and words[1] == 'you'))

    def plain(self, n, outside=range(0)):
        """Whether part `n` can ask for something: not a question, a condition, or a "not" outside `outside`."""
        at = self.parts.get(n, [])
        words = [self.words[i] for i in at]
        first = [w for w in words if w not in OPENERS]
        if first and first[0] in AUX | WH and not self._request(first):
            return False
        return not any(w in CONDITION or (_negative(w) and i not in outside) for i, w in zip(at, words))

    def calm(self, *spans):
        """Whether every part these word spans touch can ask, with the first span (the asking words) left out of the
        "not" check."""
        if not self.asking:
            return False
        asking = range(*spans[0])
        touched = {self.part[i] for start, end in spans for i in range(start, end) if self.part[i] is not None}
        return all(self.plain(n, asking) for n in touched)


def sentences(said):
    return [_Sentence(text) for text in re.findall(r'[^.;!?\n]+[.;!?]*', _plain(said)) if text.strip()]


def _matches(pattern, words):
    """Each place a pattern of asking words fits: (start, end, gap), gap the (start, end) between its halves or None."""
    found = []
    for start in range(len(words)):
        at, gap = start, None
        for n, item in enumerate(pattern):
            if item is GAP:
                stop = next((j for j in range(at + 1, min(at + 8, len(words))) if words[j] in pattern[n + 1]), None)
                if stop is None:
                    break
                gap, at = (at, stop), stop
            elif at < len(words) and words[at] in item:
                at += 1
            else:
                break
        else:
            found.append((start, at, gap))
    return found


def _places(name, words):
    """Where the model's name for an errand stands in the sentence, widened to the person's whole phrase there:
    ((start, end), whether the phrase is the name exactly)."""
    named = [w for w in _tokens(name) if _content(w)]
    found = []
    for start in range(len(words)):
        at, n = start, 0
        while n < len(named) and at < len(words):
            if words[at] == named[n]:
                n, at = n + 1, at + 1
            elif n and words[at] in SMALL:
                at += 1
            else:
                break
        if named and n == len(named) and words[start] == named[0]:
            begin, end = start, at
            while begin > 0 and _content(words[begin - 1]):
                begin -= 1
            while end < len(words) and _content(words[end]):
                end += 1
            found.append(((begin, end), (begin, end) == (start, at)))
    return found


def _between(words, start, end, allowed, most=NEAR):
    return 0 <= end - start <= most and all(w in allowed for w in words[start:end])


def _tied(sentence, span, do):
    """The asking words for `do` this phrase is tied to, as their (start, end), or None."""
    words, (begin, end) = sentence.words, span
    for pattern in ASKS[do]:
        for start, stop, gap in _matches(pattern, words):
            if gap and gap[0] <= begin and end <= gap[1]:
                tied = _between(words, gap[0], begin, SMALL) and _between(words, end, gap[1], SMALL)
            else:
                tied = (stop <= begin and _between(words, stop, begin, SMALL)) or (end <= start and _between(words, end, start, LINK | SMALL))
            if tied and sentence.calm((start, stop), span):
                return start, stop
    return None


def _joined(sentence, span, held):
    """Whether this phrase joins one already tied through "and" or a comma right after it, and ends the sentence (or
    another such join follows). Only a name the person said exactly as the model passed it can join."""
    words, (begin, end) = sentence.words, span
    after = words[end:]
    ends = not after or after[0] in (',', 'and') or all(w in TRAIL for w in after)
    for (start, stop), asking in held:
        if stop <= begin and _between(words, stop, begin, SMALL | {'and', ','}, NEAR + 1) \
                and {'and', ','} & set(words[stop:begin]) and ends and sentence.calm(asking, span):
            return asking
    return None


def _errand_words(errand, do):
    """What an errand can be called by: what its card shows as its own, never what the email said."""
    said = [errand['title'], picture._who(errand), picture.NAMES.get(errand['kind'], '')]
    if do == 'list':
        said += [thing for way in errand['choices'] for thing in way.get('buy') or ()]
    return {w for w in _tokens(' '.join(said)) if w != ','}


def _fits(phrase, words):
    """Whether each word of the person's phrase is one of the errand's words, or begins as one does ("dentist" and
    "dental")."""
    return all(any(w == v or (len(w) >= 4 and v.startswith(w[:4])) for v in words) for w in phrase)


def _open_to(do, login):
    """The errands of this person's that `do` can change."""
    if do == 'back':
        return store.rows_for(login, "state != 'skipped' and undo is not null")
    found = store.rows_for(login, "state in ('ready', 'opened', 'later')")
    return [e for e in found if any(way.get('buy') for way in e['choices'])] if do == 'list' else found


def _phrases(which, said, do):
    """The person's own phrase for each name, all tied to asking words for `do`. Raises Refused."""
    if not which:
        raise taps.Refused('Say which errand, in the person’s own words, like “the power bill”.')
    if len(which) > NAMED_MOST:
        raise taps.Refused(f'Name at most {NAMED_MOST} errands at once.')
    places, parts = {}, sentences(said)
    for n, name in enumerate(which):
        places[n] = [(sentence, span, exact) for sentence in parts for span, exact in _places(name, sentence.words)]
        if not places[n]:
            raise taps.Refused('Name each errand in the words of the person’s own message, as they said it.')
    held, found = {}, {}
    while True:
        more = False
        for n, spots in places.items():
            for sentence, span, exact in spots if n not in found else ():
                asking = _tied(sentence, span, do) or (exact and _joined(sentence, span, held.get(id(sentence), [])))
                if asking:
                    found[n] = [w for w in sentence.words[span[0]:span[1]] if _content(w)]
                    held.setdefault(id(sentence), []).append((span, asking))
                    more = True
                    break
        if not more:
            break
    for n, name in enumerate(which):
        if n not in found:
            raise taps.Refused(f'Their message doesn’t ask for that for “{name}”, so nothing was changed.')
    return [found[n] for n in range(len(which))]


def _pick(which, said, do, login):
    """The one errand each name picks, all or none. Raises Refused or Unclear."""
    phrases, open_now, picked = _phrases(which, said, do), _open_to(do, login), {}
    for phrase in phrases:
        fits = [e for e in open_now if _fits(phrase, _errand_words(e, do))]
        if not fits:
            raise Unclear(f'None of your errands fits “{" ".join(phrase)}”.', picture.titles(open_now))
        if len(fits) > 1:
            raise Unclear(f'“{" ".join(phrase)}” fits {len(fits)} errands: ask which one.', picture.titles(fits))
        picked.setdefault(fits[0]['id'], fits[0])
    return list(picked.values())


def asked(do, which, said, login, replies_in=None, now=None):
    """Do what the message `said` asks of the errands it names (`which`, in its words), as taps on their cards would.
    `do`: done, later, not, list or back; or replies, with `replies_in` 'mail' or 'gmail'. Returns {'told': what to tell
    them, 'ids': the errands changed, 'batch': their Undo, or None}. Raises Refused or Unclear: then nothing changed."""
    if do == 'replies':
        return _replies(replies_in, said, login)
    if do not in ASKS:
        raise taps.Refused('An errand can be done, set aside until tomorrow, put away, put on the list or put back.')
    changed, problems = [], []
    for errand in _pick(which, said, do, login):
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


def _replies(replies_in, said, login):
    """Where replies open, when one part of a sentence says so: it starts by telling Hub to ("open replies in Gmail",
    "use the mail app for replies") or names replies and where."""
    def says(sentence, n):
        words = [sentence.words[i] for i in sentence.parts[n]]
        start = next((w for w in words if w not in OPENERS | {'please'}), '')
        return sentence.asking and sentence.plain(n) and re.search(REPLIES[replies_in], ' '.join(words)) \
            and (start in IMPERATIVE or any(w.startswith('repl') for w in words))
    if replies_in not in REPLIES or not any(says(s, n) for s in sentences(said) for n in s.parts):
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
