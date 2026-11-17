"""Hub's morning message: one turn in each person's conversation, written by code, at most three things.

It replaces the separate morning voices in Ask (the "Needs you" rows under the greeting, the overnight review's
suggestions and the friction fix cards as cards of their own): they are one pool here, ranked in code, and the few
that matter most are said once, as plain numbered sentences the person answers by typing ("done", "not this 2",
"Thursday") or with the one button each has. Today stays the detail view with everything on it. No model is
called: the message is made from what Hub already keeps, the first time the person opens Ask after MORNING_HOUR.

The pool, for one person (`login`):
- the errands they may see (hub/app/errands/), with when each is due;
- Today's "Needs you" list (hub/app/today.py: mail that needs a person, the overnight review's suggestions, the fix
  cards, the house, Kitchen's cart, Hub's own health), cut as Today cuts it for them: only what the accounts they
  may see bring (people.py). The other person's things are never mentioned, not even as counts.

Ranking (`_rank`): house safety and money first, then what is overdue or due within a day, then the soonest due,
then the order Hub's lists already gave. A kind the person said "not this" to twice in QUIET_DAYS is left out for
QUIET_DAYS, said once; money, deadlines and the house never quiet that way. "What are you watching?" lists every
kind in plain words, and "stop watching ..." / "watch ... again" switch one off or on: the only way to quiet those.

What the person did with each item is counted per person (shown, done, not this, later, opened), in Hub's own
database, for the 14-day test (docs/app.md). Code reads the counts; no model sees them.

The message holds errand titles and mail senders, so it is kept as a turn answered from mail (`private`): the
model and the running summary see only that a morning message was given, never its lines (`thread.line`).
"""
import json
import re
import time
from contextlib import closing
from datetime import date, datetime, timedelta

from ... import home, people, store

MORNING_HOUR = 5            # Ask opened from this hour on makes the day's message
MOST = 3
QUIET_DAYS = 30
QUIET_AFTER = 2             # "not this" this many times for a low-stakes kind quiets it
LINE = 120
SCHEMA = """
create table if not exists app_ask_morning(
  person text not null, day text not null, n integer not null, key text not null, kind text not null,
  shown real not null, did text, at real, until text, primary key(person, day, n));
create table if not exists app_ask_morning_day(person text not null, day text not null, turn integer,
  primary key(person, day));
create table if not exists app_ask_watch(person text not null, kind text not null, off_until text not null,
  said integer not null default 0, primary key(person, kind));
"""

# What Hub looks after, in plain words. High stakes (money, the house, errands with their deadlines): never quieted
# by "not this", only switched off when asked.
KINDS = {
    'money': ('Bills and renewals from your mail', True),
    'house': ('The house: locks, batteries and devices that stop answering', True),
    'errands': ('Replies, forms, invitations and dates from your mail and the class guide', True),
    'mail': ('Email that needs you', False),
    'kitchen': ('Kitchen’s grocery cart', False),
    'suggestions': ('Ideas from the overnight review', False),
    'fixes': ('Where Hub got in your way', False),
    'hub': ('Hub itself: its morning job, checks and limits', False),
}
FIRST = ('house', 'money')                # house safety and money outrank everything else
HOUSEHOLD = ('house', 'kitchen')          # what anyone's morning can hold; the owner's mail and review add the rest
WORDS = {'money': ('bill', 'bills', 'renewal', 'renewals', 'money', 'pay'),
         'house': ('house', 'lock', 'locks', 'battery', 'batteries', 'device', 'devices'),
         'errands': ('errand', 'errands', 'reply', 'replies', 'form', 'forms', 'invitation', 'invitations', 'school', 'class'),
         'mail': ('mail', 'email', 'emails', 'inbox'),
         'kitchen': ('kitchen', 'grocery', 'groceries', 'cart', 'shopping'),
         'suggestions': ('idea', 'ideas', 'suggestion', 'suggestions', 'overnight', 'review'),
         'fixes': ('fix', 'fixes', 'friction', 'way'),
         'hub': ('hub', 'itself', 'checks', 'limits', 'health')}

NUMBER = r'(?:\s*(?:#|number\s+)?(?P<n>[123]|one|two|three|first|second|third|last)(?:\s+one)?)?'
DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
DONE = re.compile(r'(?:it\'?s\s+|that\'?s\s+)?(?:done|did it|did that|paid(?: it)?|handled(?: it)?|finished|sorted)' + NUMBER, re.I)
NOT = re.compile(r'(?:not this|not that|not needed|skip(?: it| that)?|no thanks|ignore(?: it| that)?|drop(?: it| that)?)' + NUMBER, re.I)
LATER = re.compile(r'(?:remind me\s+|later\s*|bring (?:it|that) back\s+|not (?:today|now)\s*)?(?:on\s+)?'
                   r'(?P<when>tomorrow|later|' + '|'.join(DAYS) + r'|next week)?' + NUMBER, re.I)
BUILD = re.compile(r'(?:build(?: it| that| the fix)?|fix (?:it|that))' + NUMBER, re.I)
WATCHING = re.compile(r"\bwhat (?:are|do) you (?:watching|watch|keeping an eye on|looking after|look after)\b", re.I)
STOP = re.compile(r'\b(?:stop|don\'?t keep) (?:watching|telling me about|looking after|an eye on)\s+(?P<what>.+)', re.I)
START = re.compile(r'\b(?:watch|look after|tell me about)\s+(?P<what>.+?)\s+again\b', re.I)
ORDINAL = {'1': 1, 'one': 1, 'first': 1, '2': 2, 'two': 2, 'second': 2, '3': 3, 'three': 3, 'third': 3}


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _person(login):
    from . import thread
    return thread.person(login)


def _clock(now=None):
    return datetime.fromtimestamp(now if now is not None else time.time(), home.PACIFIC)


def _clip(text):
    from ...engine import judge
    text = ' '.join(judge.scrub(str(text or '')).split())
    return text if len(text) <= LINE else text[:LINE - 1].rstrip() + '…'


def _sentence(*parts):
    """Parts joined as one sentence each, each ending in a stop."""
    return _clip(' '.join(p if p.endswith(('.', '!', '?', '…')) else p + '.' for p in (_clip(p) for p in parts) if p))


# ---- the pool ----

def _kind(key):
    if key.startswith('errand-'):
        return 'errands'
    if key.startswith(('mail:', 'gmail-')):
        return 'mail'
    if key.startswith('ai-'):
        return 'suggestions'
    if key.startswith(('friction-', 'improve-')):
        return 'fixes'
    if key.startswith(('house-', 'tile-')):
        return 'house'
    if key.startswith('kitchen-'):
        return 'kitchen'
    return 'hub'


def _errands(login, now):
    from ..errands import picture
    found = []
    for n, card in enumerate(picture.ready(now, login=login)):
        due = card['due'] or {}
        gap = _gap(card, now)
        kind = 'money' if card['kind'] in ('pay', 'renew') else 'errands'
        found.append({'key': card['key'], 'kind': kind, 'tone': due.get('tone') or 'info', 'gap': gap, 'order': n,
                      'text': _sentence(card['title'], (due.get('text') or '')),
                      'open': {'href': '/app/#errands', 'label': 'Open'}, 'errand': card['id']})
    return found


def _gap(card, now):
    from ..errands import store as errands
    row = errands.get(card['id'])
    on = row and (row['due'] or (row['facts'].get('event') or {}).get('date'))
    return (date.fromisoformat(on) - _clock(now).date()).days if on else None


def _needs(login):
    from .. import today
    needs, _, _ = today._list(people.accounts(login))      # cut to what this person may see (the brief, people.py)
    found = []
    for n, need in enumerate(needs):
        kind = _kind(need['key'])
        if kind == 'fixes':
            text = _sentence(f'Hub got in your way: {need["title"].rstrip(".")}', 'Say “build it” to file a fix')
        else:
            text = _sentence(need['title'], need['why'])
        found.append({'key': need['key'], 'kind': kind, 'tone': need['tone'], 'gap': None, 'order': 100 + n, 'text': text,
                      'open': need['open'], 'errand': None})
    return found


TONE = {'bad': 0, 'warn': 1, 'ok': 2, 'info': 3}


def _rank(item):
    soon = item['tone'] == 'bad' or (item['gap'] is not None and item['gap'] <= 1)
    return (item['kind'] not in FIRST, not soon, TONE.get(item['tone'], 9), item['gap'] if item['gap'] is not None else 99, item['order'])


def _off(login, day):
    """Kinds left out today for this person: switched off by them, or quieted by "not this"."""
    with closing(_db()) as conn:
        return {r['kind'] for r in conn.execute('select kind from app_ask_watch where person = ? and off_until >= ?',
                                                (_person(login), day))}


def _put_off(login, day):
    """Items this person said to bring back on a later day, still waiting for it."""
    with closing(_db()) as conn:
        return {r['key'] for r in conn.execute('select key from app_ask_morning where person = ? and until > ?',
                                               (_person(login), day))}


def pool(login, now=None):
    """Everything that could go in this person's morning message, most pressing first. Never raises."""
    now = time.time() if now is None else now
    day = _clock(now).date().isoformat()
    found = []
    for part in (lambda: _errands(login, now), lambda: _needs(login)):
        try:
            found += part()
        except Exception:  # noqa: BLE001 - one source that can't be read must not keep the others from the message
            continue
    off, later = _off(login, day), _put_off(login, day)
    found = [i for i in found if i['kind'] not in off and i['key'] not in later]
    return sorted(found, key=_rank)


# ---- the message ----

def _numbered(items):
    return '\n'.join(f'{n}. {item["text"]}' for n, item in enumerate(items, 1))


def message(login, now=None):
    """The day's morning message for this person, made and kept as a turn the first time they open Ask from
    MORNING_HOUR on. Returns the turn's number, or None (too early, already made today, nothing to say, or no
    login). Never raises."""
    from . import turns
    key = _person(login)
    moment = _clock(now)
    if key is None or moment.hour < MORNING_HOUR:
        return None
    day = moment.date().isoformat()
    try:
        if made(login, now, at_all=True):
            return None
        chosen = pool(login, now)[:MOST]
        if not chosen:          # nothing to say today: not looked for again on every opening
            with closing(_db()) as conn, conn:
                conn.execute('insert or ignore into app_ask_morning_day(person, day) values (?, ?)', (key, day))
            return None
        count = ('One thing', 'Two things', 'Three things')[len(chosen) - 1]
        answer = f'{_hello(moment)} {count} for today:\n{_numbered(chosen)}'
        card = {'kind': 'morning', 'things': [{'n': n, 'key': i['key'], 'text': i['text'], 'open': i['open']}
                                             for n, i in enumerate(chosen, 1)],
                'hint': 'Say “done”, “not this” or a day to bring it back' + (', with its number.' if len(chosen) > 1 else '.')}
        shown = time.time() if now is None else now
        # One transaction for the day's mark, the turn and its lines: two openings at once make one message.
        with closing(_db()) as conn, conn:
            conn.executescript(turns.SCHEMA)
            conn.execute('begin immediate')
            if conn.execute('select 1 from app_ask_morning_day where person = ? and day = ?', (key, day)).fetchone():
                return None         # another opening made it a moment ago
            number = conn.execute("insert into app_ask(ts, login, asked, answer, cards, private) values (?, ?, '', ?, ?, 1)",
                                  (shown, login, answer[:4000], json.dumps([card]))).lastrowid
            conn.execute('insert into app_ask_morning_day(person, day, turn) values (?, ?, ?)', (key, day, number))
            conn.executemany('insert into app_ask_morning(person, day, n, key, kind, shown) values (?,?,?,?,?,?)',
                             [(key, day, n, i['key'], i['kind'], shown) for n, i in enumerate(chosen, 1)])
        store.log('ask_morning', str(len(chosen)))
        return number
    except Exception:  # noqa: BLE001 - Ask opens without a morning message rather than not at all
        return None


def _hello(moment):
    return 'Good morning.' if moment.hour < 12 else 'Good afternoon.' if moment.hour < 17 else 'Good evening.'


def made(login, now=None, at_all=False):
    """Whether this person's morning message for today is in their conversation. `at_all`: or was looked for and
    there was nothing to say."""
    key = _person(login)
    if key is None:
        return False
    with closing(_db()) as conn:
        row = conn.execute('select turn from app_ask_morning_day where person = ? and day = ?',
                           (key, _clock(now).date().isoformat())).fetchone()
    return bool(row and (at_all or row['turn']))


def _open(login, day):
    """Today's items this person hasn't answered yet, by number."""
    with closing(_db()) as conn:
        rows = conn.execute('select * from app_ask_morning where person = ? and day = ? order by n', (_person(login), day)).fetchall()
    return [dict(r) for r in rows]


# ---- typed replies ----

def _which(match, items):
    word = (match.group('n') or '').lower()
    waiting = [i for i in items if not i['did']]
    if word == 'last':
        return items[-1] if items else None
    if word:
        return next((i for i in items if i['n'] == ORDINAL[word]), None)
    return waiting[0] if len(waiting) == 1 else None


def _until(when, today):
    when = (when or 'tomorrow').lower()
    if when in ('tomorrow', 'later'):
        return today + timedelta(days=1)
    if when == 'next week':
        return today + timedelta(days=7 - today.weekday())
    ahead = (DAYS.index(when) - today.weekday()) % 7
    return today + timedelta(days=ahead or 7)


def _mark(login, item, did, until=None, now=None):
    with closing(_db()) as conn, conn:
        conn.execute('update app_ask_morning set did = ?, at = ?, until = ? where person = ? and day = ? and n = ?',
                     (did, time.time() if now is None else now, until, _person(login), item['day'], item['n']))


def _quiet(login, kind, day):
    """Two "not this" for a low-stakes kind within QUIET_DAYS quiets it for QUIET_DAYS. Returns the words to say once."""
    if KINDS.get(kind, ('', True))[1]:
        return ''
    since = (date.fromisoformat(day) - timedelta(days=QUIET_DAYS)).isoformat()
    with closing(_db()) as conn, conn:
        count = conn.execute("select count(*) from app_ask_morning where person = ? and kind = ? and did = 'not' and day > ?",
                             (_person(login), kind, since)).fetchone()[0]
        if count < QUIET_AFTER:
            return ''
        until = (date.fromisoformat(day) + timedelta(days=QUIET_DAYS)).isoformat()
        conn.execute('insert into app_ask_watch(person, kind, off_until, said) values (?,?,?,0) on conflict(person, kind) '
                     'do update set off_until = excluded.off_until', (_person(login), kind, until))
    return (f' I’ll leave {KINDS[kind][0][0].lower() + KINDS[kind][0][1:]} out of the morning for {QUIET_DAYS} days. '
            '“Watch it again” brings it back.')


def _errand(item, do, login, until=None):
    from ..errands import store as errands, taps
    errand_id = int(item['key'].removeprefix('errand-'))
    taps.tap(errand_id, do, {}, login)
    if until:                       # a named day: the errand comes back that morning, not tomorrow's
        errands.update(errand_id, until=datetime.combine(until, datetime.min.time().replace(hour=taps.LATER_HOUR),
                                                         home.PACIFIC).timestamp())


def _need(item, do):
    from ...engine import friction, state
    from .. import today
    key = item['key']
    if do == 'build':
        friction.decide(key.removeprefix('friction-'), 'build')
    elif do == 'not' and key.startswith('ai-'):
        state.dismiss(key)
    elif do == 'not' and key.startswith('friction-'):
        friction.decide(key.removeprefix('friction-'), 'never')
    elif do in ('done', 'not'):
        today.tick(key)


def act(login, item, do, until=None, now=None):
    """The change a typed answer makes, and its count: the errand's own tap (taps.tap), or Today's tick, the engine's
    dismissal or the fix card's decision. Raises what they raise. The golden dry run stands in front of this."""
    if item['key'].startswith('errand-'):
        tomorrow = _clock(now).date() + timedelta(days=1)
        _errand(item, do, login, until if do == 'later' and until != tomorrow else None)
    elif do != 'later':
        _need(item, do)
    _mark(login, item, do, until.isoformat() if until else None, now)


def reply(text, login, now=None):
    """A short typed answer to today's morning message ("done", "not this 2", "Thursday", "build it"), done in code.
    Returns what to say, or None when the words aren't that (the model answers them as usual)."""
    words = ' '.join(str(text or '').lower().replace('’', "'").split()).strip(' .!?')
    if not words or len(words) > 40 or login is None:
        return None
    moment = _clock(now)
    day = moment.date().isoformat()
    items = _open(login, day)
    if not items:
        return None
    for pattern, do in ((BUILD, 'build'), (DONE, 'done'), (NOT, 'not'), (LATER, 'later')):
        match = pattern.fullmatch(words)
        if not match or (do == 'later' and not (match.group('when') or words.startswith(('later', 'remind', 'not today', 'not now')))):
            continue
        item = _which(match, items)
        if item is None:
            return 'Which one? Say it with its number, like “done 2”.'
        if item['did']:
            return f'Number {item["n"]} is answered already.'
        until = _until(match.group('when'), moment.date()) if do == 'later' else None
        if item['key'].startswith('errand-') and do == 'build':
            return 'That one is an errand. Say “done” or “not this”.'
        if do == 'build' and not item['key'].startswith('friction-'):
            return 'Only a fix can be built. Say “done” or “not this”.'
        try:
            act(login, item, do, until, now)
        except Exception:  # noqa: BLE001 - whatever the tap refused, nothing was changed; Today still has it
            return 'Hub couldn’t change that one. It’s still on Today.'
        if do == 'build':
            return f'Filed number {item["n"]} for the next building session.'
        if do == 'done':
            return f'Done, number {item["n"]} is off the list.'
        if do == 'not':
            return f'Number {item["n"]} is gone from the list.' + _quiet(login, item['kind'], day)
        return f'Number {item["n"]} comes back {_day_words(until, moment.date())}.'
    return None


def _day_words(until, today):
    gap = (until - today).days
    return 'tomorrow' if gap == 1 else f'on {until.strftime("%A")}' if gap < 7 else f'on {until.strftime("%A, %B %-d")}'


def opened(login, key, now=None):
    """The person tapped an item's button: counted as acted on. False when it isn't one of today's."""
    day = _clock(now).date().isoformat()
    item = next((i for i in _open(login, day) if i['key'] == str(key)[:200]), None)
    if item is None:
        return False
    if not item['did']:
        _mark(login, item, 'opened', None, now)
    return True


# ---- what Hub looks after ----

def _named(what):
    words = set(re.findall(r"[a-z]+", what.lower()))
    return [kind for kind, names in WORDS.items() if words & set(names)]


def switch(login, kinds, on):
    """Switch kinds of things off this person's morning message until they say otherwise, or back on."""
    person = _person(login)
    with closing(_db()) as conn, conn:
        for kind in kinds:
            if on:
                conn.execute('delete from app_ask_watch where person = ? and kind = ?', (person, kind))
            else:
                conn.execute("insert into app_ask_watch(person, kind, off_until, said) values (?,?,'9999-12-31',1) "
                             "on conflict(person, kind) do update set off_until='9999-12-31', said=1", (person, kind))


def watching(text, login, now=None):
    """"What are you watching?", "stop watching the overnight ideas", "watch mail again". Returns what to say, or
    None when the words aren't about that."""
    words = ' '.join(str(text or '').split())
    if not words or login is None or len(words) > 120:
        return None
    day = _clock(now).date().isoformat()
    stop, start = STOP.search(words), START.search(words)
    if stop or start:
        kinds = _named((stop or start).group('what'))
        if not kinds:
            return None
        switch(login, kinds, on=not stop)
        names = ', '.join(KINDS[k][0][0].lower() + KINDS[k][0][1:] for k in kinds)
        return (f'I’ll leave this out of your morning message until you say “watch it again”: {names}.' if stop
                else f'Back in your morning message: {names}.')
    if not WATCHING.search(words):
        return None
    off = _off(login, day)
    lines = [f'• {name}' + (' (off)' if kind in off else '') for kind, (name, _) in KINDS.items()
             if people.owner(login) or kind in HOUSEHOLD + ('money', 'errands')]
    return ('What I look after for you each morning, at most three things a day:\n' + '\n'.join(lines)
            + '\nSay “stop watching …” or “watch … again” to switch one.' + _tally(login, day))


def _tally(login, day):
    since = (date.fromisoformat(day) - timedelta(days=14)).isoformat()
    with closing(_db()) as conn:
        row = conn.execute("select count(*) as shown, sum(did is not null and did != 'later') as acted from app_ask_morning "
                           'where person = ? and day > ?', (_person(login), since)).fetchone()
    if not row['shown']:
        return ''
    return f'\nIn the last two weeks I mentioned {row["shown"]} things and you answered {row["acted"] or 0}.'


def counts(login, days=14, now=None):
    """This person's morning counts over the last `days`: shown, and each answer. For the 14-day test."""
    since = (_clock(now).date() - timedelta(days=days)).isoformat()
    with closing(_db()) as conn:
        rows = conn.execute("select coalesce(did, 'none') as did, count(*) as n from app_ask_morning where person = ? and day > ? "
                            'group by did', (_person(login), since)).fetchall()
    found = {r['did']: r['n'] for r in rows}
    return {'shown': sum(found.values()), **{k: found.get(k, 0) for k in ('done', 'not', 'later', 'opened', 'build')}}


def answer(text, login, now=None):
    """What code answers before the model: a reply to the morning message, or about what Hub looks after."""
    try:
        return reply(text, login, now) or watching(text, login, now)
    except Exception:  # noqa: BLE001 - the model answers instead
        return None
