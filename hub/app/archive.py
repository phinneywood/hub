"""The Archive: the household's photos, documents and ChatGPT history, mail and calendar history, in one search.

- **Photos, documents, ChatGPT history** come from the archive program on this Mac (deploy/archive), the only thing
  that opens the Photos library, the documents and the export. Hub asks it over its Unix socket, on fixed read-only
  routes, by id; it never names a file. Until it is installed, every part says so and nothing else fails.
- **Mail** is searched live in Gmail through the bridge: each phrase its own query, newest first, ten at most.
  Opening one reads it, and a PDF attached to it, through the bridge's own read-only routes, on a tap.
- **Calendar history** is copied into Hub's own database from the primary calendar, month by month as far back as
  it answers, once; after that the past year and the next are read again every night, and the weeks around now
  every hour.

A search is read here, with no model (`understand`): years, months, seasons, "last summer", the people Photos knows
by name, "the kids", and words that name a source ("pdf", "email", "chat"). The rest are the words to look for.

Who sees what (owner decisions, 2026-11-16; `may_see`, hub/people.py): the owner sees everything in the archive. Any
other signed-in login sees the photos in the iCloud Shared Library and nothing else of it: not the owner's own
library, documents or ChatGPT history. Mail and the calendar are each person's own Google account's, and a program
on the Mac with no login sees nothing. What Ask's model may be given is decided in hub/app/ask/tools.py (`search`,
`read`, `archive`) and set out in "Archive" in docs/app.md: never anything about a photo.
"""
import datetime as dt
import email.utils
import os
import re
import threading
import time
from pathlib import Path

import httpx

from .. import bridge, config, home, household, people as household_people, store

QUICK = 4                   # seconds Hub waits on the archive for a search or an item
DRAWING = 20                # and for a picture it makes
MAIL_MOST = 10
CALENDAR_MOST = 20
PAGE = 48
MONTHS = {'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3, 'april': 4, 'apr': 4, 'may': 5,
          'june': 6, 'jun': 6, 'july': 7, 'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'sept': 9,
          'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12}
SEASONS = {'spring': (3, 4, 5), 'summer': (6, 7, 8), 'fall': (9, 10, 11), 'autumn': (9, 10, 11), 'winter': (12, 1, 2)}
SOURCE_WORDS = {'photos': ('photo', 'photos', 'picture', 'pictures', 'pic', 'pics', 'video', 'videos'),
                'documents': ('document', 'documents', 'doc', 'docs', 'file', 'files', 'pdf', 'pdfs', 'scan', 'scans'),
                'mail': ('email', 'emails', 'e-mail', 'e-mails', 'mail', 'mails', 'gmail', 'inbox'),
                'calendar': ('calendar', 'event', 'events', 'appointment', 'appointments', 'meeting', 'meetings'),
                'chats': ('chat', 'chats', 'chatgpt', 'conversation', 'conversations', 'gpt')}
KINDS = {'video': 'video', 'videos': 'video', 'pdf': 'pdf', 'pdfs': 'pdf', 'spreadsheet': 'sheet', 'spreadsheets': 'sheet'}
KIDS = ('kids', 'kid', 'children', 'child', 'baby', 'babies')
SOURCES = ('photos', 'documents', 'mail', 'calendar', 'chats')
LEAD = re.compile(r"^\s*(please\s+)?(can you\s+|could you\s+)?(show( me)?|find( me)?|search( for)?|look( for| up)?|get( me)?|"
                  r"pull up|open|where('s| is| are)|what('s| is| are| was| were)|when('s| is| was| were| did)|"
                  r"do (we|i) have|have (we|i)|did (we|i))\b\s*", re.I)
NEWEST = re.compile(r"\b(last time|latest|most recent(ly)?|recently|lately|newest|last)\b", re.I)
OLDEST = re.compile(r"\b(first time|earliest|oldest|first)\b", re.I)
FILLER = set("""a an the of at in on from with for to about me my our we us i you your all any some that those these this there
ever time times did do does was were is are be been have has had can could would get got show find search look it its
by during around when where what which who and or but then please like""".split())
_calendar_lock = threading.Lock()
_started = False


class Away(Exception):
    """The archive program isn't installed or isn't answering. The message is safe to show."""


class Refused(Exception):
    """A tap Hub won't make, with words for the person."""


# ---- the archive program, over its socket ----

def settings():
    try:
        return household.section('archive')
    except ValueError:
        return {}


def socket_path():
    named = os.environ.get('HUB_ARCHIVE_SOCKET') or settings().get('socket')
    return Path(named) if named else Path.home() / 'Library' / 'Application Support' / 'HubArchive' / 'archive.sock'


def _client(timeout):
    return httpx.Client(transport=httpx.HTTPTransport(uds=str(socket_path())), base_url='http://archive',
                        timeout=timeout, follow_redirects=False)


def _get(path, params=(), timeout=QUICK):
    """One answer from the archive program. Raises Away; a 404 is None."""
    if not socket_path().exists():
        raise Away('The archive isn’t set up on this Mac yet.')
    try:
        with _client(timeout) as client:
            response = client.get(path, params=list(params))
    except httpx.HTTPError:
        raise Away('The archive isn’t answering right now.') from None
    if response.status_code == 404:
        return None
    if response.status_code == 503:
        raise Away('The archive is still reading everything for the first time.')
    if response.status_code != 200:
        raise Away('The archive couldn’t answer that.')
    return response


def fetch(path, params=()):
    response = _get(path, params)
    return response.json() if response is not None else None


def picture(path, params=()):
    """(bytes, content type, disposition, whether it is a sharp photo) of a preview, a photo, a page or a document,
    or None."""
    response = _get(path, params, timeout=DRAWING)
    if response is None:
        return None
    return response.content, response.headers.get('content-type', 'application/octet-stream'), \
        response.headers.get('content-disposition'), response.headers.get('x-sharp')


ITEM = re.compile(r'[pdcg]:[A-Za-z0-9_-]{6,80}')
MAIL_ID = re.compile(r'm:[0-9a-f]{6,40}')
EVENT_ID = re.compile(r'e:[0-9]{1,12}')


def owner(login):
    """Whether this login is the owner's: the person whose Google account is the bridge's main one (hub/people.py)."""
    return household_people.owner(login)


def unowned():
    """Whether Hub hasn't been told yet whose archive this is: more than one login may sign in and the private
    settings file names nobody as the owner. Until it does, every login sees Shared Library photos only."""
    return not any(household_people.owner(login) for login in config.ALLOWED_LOGINS)


def accounts(login):
    """The Google accounts whose mail and calendar this login may see: only its own (owner rule, 2026-11-16: each
    person sees their own Google account's mail and calendar and no one else's)."""
    return sorted(household_people.accounts(login))


def sees(login, account=household_people.DEFAULT):
    return household_people.sees(login, account)


def may_see(login, item):
    """Owner decision (2026-11-16, "Shared Library photos only"): the owner sees everything in the archive; any other
    signed-in login sees the photos in the iCloud Shared Library and nothing else of it, not the owner's own library,
    documents or ChatGPT history. Mail and the calendar are each person's own Google account's (`accounts`). A program
    on the Mac with no login sees nothing."""
    if not login:
        return False
    return owner(login) or (item.get('source') == 'photos' and bool(item.get('shared')))


def scope(login):
    """`may_see` said to the archive program, so its lists, counts and answers by id are cut the same way."""
    return [] if owner(login) else [('shared', '1')]


def sources_for(login):
    """What this login's searches reach: the archive by `may_see`, mail and the calendar by its own accounts."""
    if not login:
        return ()
    mine = {'photos'} | ({'documents', 'chats'} if owner(login) else set()) | ({'mail', 'calendar'} if sees(login) else set())
    return tuple(s for s in SOURCES if s in mine)


# ---- what a search means, read without a model ----

def _span(year, months):
    """[first day, day after the last) of these months of `year` (winter runs into the next year)."""
    first = dt.date(year, months[0], 1)
    last_month, last_year = months[-1], year + (1 if months[-1] < months[0] else 0)
    end = dt.date(last_year + (last_month == 12), last_month % 12 + 1, 1)
    return first, end


def _season(name, which, today):
    """The span of "last summer", "this winter", "summer 2024"."""
    months = SEASONS[name]
    if isinstance(which, int):
        return _span(which, months)
    year = today.year
    start, end = _span(year, months)
    if months[0] == 12 and today.month <= 2:
        start, end = _span(year - 1, months)            # January's "this winter" began last December
    if which == 'last' and not end <= today:
        start, end = _span(start.year - 1, months)
    return start, end


def _people_in(text, people):
    """The people named in the words, longest names first, and the words without them."""
    found = []
    for person in sorted(people, key=lambda p: -len(p)):
        names = [person] + ([person.split()[0]] if ' ' in person and sum(p.split()[0] == person.split()[0] for p in people) == 1 else [])
        for name in names:
            pattern = re.compile(r"\b" + re.escape(name) + r"(['’]s)?\b", re.I)
            if pattern.search(text):
                found.append(person)
                text = pattern.sub(' ', text)
                break
    return found, text


def understand(text, people=(), kids=(), today=None):
    """What a search asks for: {'terms', 'sources', 'kind', 'people', 'kids', 'after', 'before', 'months', 'sort',
    'about'}. `people`: the names Photos knows; `kids`: those who are kids. `about` is the search in a few words, for
    the sentence ("Kids at the beach in 2024")."""
    today = today or home.today()
    said = ' '.join(str(text or '').split())[:300]
    rest = re.sub(r'^(and|or|so|but|also)\s+', '', LEAD.sub('', said.rstrip('?!. ')), flags=re.I)
    about = re.sub(r'^((we|i|you|they)\s+)?((last|ever|first|recently)\s+)?', '', rest, flags=re.I)    # "we last serviced..."
    about = re.sub(r'^(the |all (the )?|any |some )', '', re.sub(r'^(photos?|pictures?|pics?|videos?) (of|from|with) ', '', about,
                                                              flags=re.I), flags=re.I)
    out = {'terms': '', 'sources': [], 'kind': [], 'people': [], 'kids': False, 'after': None, 'before': None,
           'months': [], 'sort': None, 'about': about[:1].upper() + about[1:]}
    low = ' ' + rest.lower() + ' '
    span = None
    # seasons and months, with a year, "last" or "this", or alone
    for name in SEASONS:
        found = re.search(rf"\b(last|this|past)\s+{name}\b|\b{name}\s+(of\s+)?(\d{{4}})\b|\b{name}\b", low)
        if found:
            which = 'last' if found.group(1) in ('last', 'past') else 'this' if found.group(1) == 'this' else \
                int(found.group(3)) if found.group(3) else None
            if which is None:
                out['months'] = list(SEASONS[name])
            else:
                span = _season(name, which, today)
            low = low[:found.start()] + ' ' + low[found.end():]
            break
    for name, number in MONTHS.items():
        found = re.search(rf"\b(last|this)\s+{name}\b|\b{name}\s+(of\s+)?(\d{{4}})\b|\b{name}\b", low)
        if not found or (name == 'may' and not found.group(3) and not found.group(1)):
            continue                                            # "may" alone is a word, not a month
        if found.group(3):
            span = _span(int(found.group(3)), (number,))
        elif found.group(1):
            year = today.year if (found.group(1) == 'this' or number < today.month) else today.year - 1
            span = _span(year, (number,))
        else:
            out['months'] = [number]
        low = low[:found.start()] + ' ' + low[found.end():]
        break
    relative = {'last year': (dt.date(today.year - 1, 1, 1), dt.date(today.year, 1, 1)),
                'this year': (dt.date(today.year, 1, 1), dt.date(today.year + 1, 1, 1)),
                'last month': (_span(today.year - (today.month == 1), ((today.month - 2) % 12 + 1,))),
                'this month': _span(today.year, (today.month,)),
                'last week': (today - dt.timedelta(days=today.weekday() + 7), today - dt.timedelta(days=today.weekday())),
                'this week': (today - dt.timedelta(days=today.weekday()), today + dt.timedelta(days=7 - today.weekday())),
                'yesterday': (today - dt.timedelta(days=1), today), 'today': (today, today + dt.timedelta(days=1))}
    for words, value in relative.items():
        if re.search(rf"\b{words}\b", low):
            span = value
            low = re.sub(rf"\b{words}\b", ' ', low)
            break
    ago = re.search(r"\b(\d{1,2}|a|one|two|three|four|five|ten) years? ago\b", low)
    if ago:
        count = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'ten': 10}.get(ago.group(1)) or int(ago.group(1))
        span = (dt.date(today.year - count, 1, 1), dt.date(today.year - count + 1, 1, 1))
        low = low[:ago.start()] + ' ' + low[ago.end():]
    years = sorted({int(y) for y in re.findall(r"\b(19[3-9]\d|20[0-9]\d)\b", low)})
    if years and span is None:
        span = (dt.date(years[0], 1, 1), dt.date(years[-1] + 1, 1, 1))
    low = re.sub(r"\b(19[3-9]\d|20[0-9]\d)\b", ' ', low)
    if span:
        out['after'], out['before'] = span[0].isoformat(), span[1].isoformat()
    if NEWEST.search(low):
        out['sort'] = 'newest'
        low = NEWEST.sub(' ', low)
    elif OLDEST.search(low):
        out['sort'] = 'oldest'
        low = OLDEST.sub(' ', low)
    out['people'], low = _people_in(low, people)
    if re.search(r"\b(the |our |my )?(" + '|'.join(KIDS) + r")\b", low):
        out['kids'] = True
        low = re.sub(r"\b(" + '|'.join(KIDS) + r")\b", ' ', low)
    for source, words in SOURCE_WORDS.items():
        for word in words:
            if re.search(rf"\b{re.escape(word)}\b", low):
                out['sources'].append(source)
                if word in KINDS:
                    out['kind'].append(KINDS[word])
                low = re.sub(rf"\b{re.escape(word)}\b", ' ', low)
    out['sources'] = list(dict.fromkeys(out['sources']))
    out['kind'] = list(dict.fromkeys(out['kind']))
    words = [w for w in re.findall(r"[\w’'-]+|\"[^\"]+\"", low) if w.strip("'’-").lower() not in FILLER]
    out['terms'] = ' '.join(words)
    return out


SHOW = re.compile(r"^\s*(please\s+)?(can (you|i)\s+)?(show|let me see|see|look at|pull up)\b", re.I)


def wants_photos(text, about_people=False):
    """Whether the words ask for photos: they name photos, pictures or videos, or ask to be shown people ("show me
    the kids at the beach")."""
    return 'photos' in understand(text)['sources'] or bool(about_people and SHOW.search(str(text or '')))


def counted(n, one, many=None):
    return f'{n} {one if n == 1 else (many or one + "s")}'


def photo_sentence(about, found):
    """“Kids at the beach in 2024: 48 photos from 6 days.” Written by Hub from what it found; without `about`, just
    “48 photos from 6 days.”"""
    count, days = found.get('count', 0), found.get('days', 0)
    if not count:
        return f'{about}: no photos.' if about else 'No photos.'
    videos = sum(1 for p in found.get('items', ()) if p.get('kind') == 'video')
    what = counted(count, 'video') if videos and videos == len(found.get('items', ())) == count else counted(count, 'photo')
    span = f'{what} from {counted(days, "day")}.' if days > 1 else f'{what} from one day.'
    return f'{about}: {span}' if about else span[:1].upper() + span[1:]


# ---- one search across everything ----

def people(login=None):
    """The people Photos knows by name, with the kid flag as the archive sees it and as a person here set it, counted
    in what this login may see."""
    try:
        found = (fetch('/people', scope(login)) or {}).get('people', [])
    except Away:
        return []
    with store.db() as conn:
        conn.execute(KIDS_SCHEMA)
        chosen = {r['name'].lower(): bool(r['kid']) for r in conn.execute('select name, kid from archive_kids')}
    for person in found:
        person['auto'] = person['kid']
        person['kid'] = chosen.get(person['name'].lower(), person['kid'])
    return found


KIDS_SCHEMA = 'create table if not exists archive_kids(name text primary key collate nocase, kid integer not null, by text, at real)'


def set_kid(name, kid, login):
    """A person here says whether someone is one of the kids."""
    known = {p['name'].lower(): p['name'] for p in people(login)}
    if str(name or '').lower() not in known:
        raise Refused('The archive doesn’t know that person.')
    with store.db() as conn:
        conn.execute(KIDS_SCHEMA)
        conn.execute('insert or replace into archive_kids(name, kid, by, at) values (?, ?, ?, ?)',
                     (known[name.lower()], int(bool(kid)), str(login or '')[:200], time.time()))
    return f'{known[name.lower()]} {"is" if kid else "isn’t"} one of the kids now.'


def _local_params(meaning, sources, limit, offset, sort=None, kids=(), searches=None):
    params = [('sources', ','.join(sources)), ('limit', str(limit)), ('offset', str(offset))]
    if meaning.get('place'):
        params.append(('place', meaning['place']))
    for words in searches if searches is not None else ([meaning['terms']] if meaning['terms'] else []):
        params.append(('q', words))
    for name in meaning['people']:
        params.append(('people', name))
    if meaning['kids']:
        params += [('anyof', name) for name in kids] + [('young', '1')]
    for name in ('after', 'before'):
        if meaning[name]:
            params.append((name, meaning[name]))
    if meaning['months']:
        params.append(('months', ','.join(map(str, meaning['months']))))
    if meaning['kind']:
        params.append(('kind', ','.join(meaning['kind'])))
    if sort or meaning['sort']:
        params.append(('sort', sort or meaning['sort']))
    return params


def local(meaning, sources, login, limit=PAGE, offset=0, sort=None, searches=None, kids=None):
    """Photos, documents and ChatGPT history that match, from the archive program. `kids`: the kids' names, when
    the search is about the kids."""
    allowed = [s for s in sources if s in ('photos', 'documents', 'chats') and s in sources_for(login)]
    if not allowed:
        return {}
    if meaning['kids'] and kids is None:
        kids = [p['name'] for p in people(login) if p['kid']]
    return fetch('/search', _local_params(meaning, allowed, limit, offset, sort, kids or (), searches) + scope(login)) or {}


def search(text, login, source=None, offset=0, limit=PAGE):
    """The Archive screen's search: what it understood, and what matched in photos, documents, ChatGPT history and
    the calendar. Mail is asked for on its own (`mail`), since it takes Gmail a moment."""
    known = people(login)
    meaning = understand(text, [p['name'] for p in known], [p['name'] for p in known if p['kid']])
    allowed = sources_for(login)
    wanted = [s for s in (meaning['sources'] or allowed) if s in allowed]
    if source:
        wanted = [source] if source in allowed else []
    if (meaning['people'] or meaning['kids']) and not meaning['sources'] and not meaning['terms']:
        wanted = [s for s in wanted if s == 'photos']                          # people alone are found in photos
    out = {'q': text, 'understood': {k: meaning[k] for k in ('people', 'kids', 'after', 'before', 'months', 'sources', 'terms', 'sort')},
           'about': meaning['about'], 'sources': wanted, 'ready': True}
    try:
        found = local(meaning, wanted, login, limit, offset, kids=[p['name'] for p in known if p['kid']])
    except Away as away:
        found, out['ready'], out['away'] = {}, False, str(away)
    for name in ('photos', 'documents', 'chats'):
        if name in found:
            out[name] = found[name]
    out['loose'] = bool(found.get('loose'))
    if 'photos' in out:
        out['said'] = photo_sentence(meaning['about'], out['photos'])
    if 'calendar' in wanted and offset == 0 and (meaning['terms'] or meaning['after']):
        out['calendar'] = calendar(meaning, login)
    out['mail'] = 'mail' in wanted and bool(meaning['terms'] or meaning['people'] or meaning['after'])
    return out


# ---- mail, live through the bridge ----

def _sender(raw):
    name, address = email.utils.parseaddr(str(raw or ''))
    return (name or address.split('@')[0] or 'Someone').strip('"')[:80], address.lower()


def _mail_day(raw):
    try:
        moment = email.utils.parsedate_to_datetime(str(raw))
        return moment.astimezone(home.PACIFIC)
    except (TypeError, ValueError, IndexError):
        return None


def gmail_queries(meaning, searches=None):
    """Each phrase its own Gmail query, with the people named and the dates as after: and before:. None without
    words or people: a date alone would list every email of those days."""
    phrases = [' '.join(s.split()) for s in (searches if searches is not None else [meaning['terms']]) if s and s.strip()]
    named = ' '.join(meaning['people'])
    if not phrases and not named:
        return []
    dates = ' '.join(f'{name}:{meaning[name].replace("-", "/")}' for name in ('after', 'before') if meaning[name])
    return list(dict.fromkeys(' '.join(x for x in (phrase, named, dates) if x) for phrase in phrases or ['']))[:4]


def mail(meaning, login, searches=None, most=MAIL_MOST):
    """Mail that matches, newest first, ten at most: sender's name, subject, date, Gmail's snippet. Only for a login
    that sees the bridge's mailbox: its owner's."""
    if not sees(login):
        return []
    found = {}
    for query in gmail_queries(meaning, searches):
        try:
            reply = bridge.get('/gmail/search', ttl=60, timeout=20, query=query[:900], limit=most)
        except bridge.BridgeError:
            continue
        for message in reply.get('messages', []):
            if message.get('id') and re.fullmatch(r'[0-9a-f]{6,40}', message['id']):
                found.setdefault(message['id'], message)
    out = []
    for message in found.values():
        name, address = _sender(message.get('from'))
        when = _mail_day(message.get('date'))
        out.append({'id': 'm:' + message['id'], 'from': name, 'address': address, 'subject': str(message.get('subject') or '')[:200],
                    'at': when.timestamp() if when else None, 'day': when.date().isoformat() if when else None,
                    'snippet': ' '.join(str(message.get('snippet') or '').split())[:240]})
    out.sort(key=lambda m: -(m['at'] or 0))
    return out[:most]


def mail_message(ident, login):
    """One email, read through the bridge: who, when, its text and its attachments. None for a login that doesn't
    see that mailbox, the same as for one that isn't there."""
    if not MAIL_ID.fullmatch(ident or '') or not sees(login):
        return None
    found = bridge.get('/gmail/message', timeout=30, message_id=ident[2:])
    name, address = _sender(found.get('from'))
    when = _mail_day(found.get('date'))
    return {'id': ident, 'source': 'mail', 'from': name, 'address': address, 'subject': str(found.get('subject') or '')[:300],
            'at': when.timestamp() if when else None, 'day': when.date().isoformat() if when else None,
            'text': str(found.get('text') or '')[:20000], 'cut': bool(found.get('truncated')),
            'attachments': [{'part': a.get('part_id'), 'name': str(a.get('filename') or 'Attachment')[:200],
                             'type': a.get('mime_type'), 'bytes': a.get('bytes'),
                             'readable': bool(a.get('readable')) and a.get('mime_type') in ('application/pdf', 'text/plain')}
                            for a in found.get('attachments', []) if not a.get('inline')]}


def mail_attachment(ident, part, login):
    """The text of one PDF or text attachment, through the bridge's isolated parser."""
    if not MAIL_ID.fullmatch(ident or '') or not re.fullmatch(r'(?:root|[0-9.]{1,100})', str(part or '')) or not sees(login):
        return None
    found = bridge.get('/gmail/document-attachment', timeout=60, message_id=ident[2:], part_id=part)
    return {'name': str(found.get('filename') or 'Attachment')[:200], 'text': str(found.get('text') or '')[:60000],
            'cut': bool(found.get('truncated'))}


# ---- calendar history, kept here ----

CALENDAR_SCHEMA = """
create table if not exists archive_events(
  id integer primary key, account text not null default 'default', key text not null unique, day text not null,
  start text, end_ text, all_day integer not null default 0, title text not null default '', location text not null default '',
  seen real);
create index if not exists archive_events_day on archive_events(account, day);
create table if not exists archive_state(name text primary key, value text);
"""
CALENDAR_FTS = """
create virtual table if not exists archive_events_fts using fts5(title, location, content='archive_events',
  content_rowid='id', tokenize='porter unicode61 remove_diacritics 2');
create trigger if not exists archive_events_added after insert on archive_events begin
  insert into archive_events_fts(rowid, title, location) values (new.id, new.title, new.location); end;
create trigger if not exists archive_events_removed after delete on archive_events begin
  insert into archive_events_fts(archive_events_fts, rowid, title, location) values ('delete', old.id, old.title, old.location); end;
create trigger if not exists archive_events_changed after update on archive_events begin
  insert into archive_events_fts(archive_events_fts, rowid, title, location) values ('delete', old.id, old.title, old.location);
  insert into archive_events_fts(rowid, title, location) values (new.id, new.title, new.location); end;
"""
CALENDAR_EMPTY_YEARS = 2        # going back stops after this many years without an event
CALENDAR_EARLIEST = 2000
HOURLY_DAYS = (14, 28)          # the weeks around now, read every hour: two back, four ahead
NIGHTLY_MONTHS = 12             # and every night, a year back and a year ahead
CALENDAR_ACCOUNTS = {'default': '/calendar/events'}    # each Google account the bridge reads a calendar of, by its path


def _calendar_db():
    conn = store.db()
    have = {r['name'] for r in conn.execute('pragma table_info(archive_events)')}
    if have and 'account' not in have:          # kept before events had an account: they were all the bridge's own
        conn.execute("alter table archive_events add column account text not null default 'default'")
    conn.executescript(CALENDAR_SCHEMA)
    try:
        conn.executescript(CALENDAR_FTS)
    except Exception:  # noqa: BLE001 - a Python without FTS5 searches the slow way
        pass
    return conn


def _has_fts(conn):
    return conn.execute("select 1 from sqlite_master where name = 'archive_events_fts'").fetchone() is not None


def _state(conn, name, value=None):
    if value is None:
        row = conn.execute('select value from archive_state where name = ?', (name,)).fetchone()
        return row['value'] if row else None
    conn.execute('insert or replace into archive_state(name, value) values (?, ?)', (name, str(value)))
    return value


def _event(raw):
    """(key, day, start, end, all_day, title, location) of one event, or None for a cancelled one."""
    if raw.get('status') == 'cancelled' or 'start' not in raw or not raw.get('id'):
        return None
    start = raw['start'].get('dateTime') or raw['start'].get('date')
    end = (raw.get('end') or {}).get('dateTime') or (raw.get('end') or {}).get('date')
    try:
        day = dt.datetime.fromisoformat(start.replace('Z', '+00:00')).astimezone(home.PACIFIC).date() \
            if 'T' in start else dt.date.fromisoformat(start)
    except (AttributeError, ValueError):
        return None
    return (str(raw['id'])[:200], day.isoformat(), start, end, int('date' in raw['start']), str(raw.get('summary') or '(No title)')[:300],
            str(raw.get('location') or '')[:300])


def _read_window(conn, start, end, account='default'):
    """Copy one account's primary calendar between two dates into Hub, a month at most each time (the bridge's
    limit), and let go of events no longer there. Returns how many were read."""
    seen = 0
    while start < end:
        upto = min(end, start + dt.timedelta(days=30))
        seen += _read_part(conn, start, upto, account)
        start = upto
    return seen


def _read_part(conn, start, end, account):
    begin = dt.datetime.combine(start, dt.time(), home.PACIFIC)
    stop = dt.datetime.combine(end, dt.time(), home.PACIFIC)
    keys, token, stamp = set(), None, time.time()
    for _ in range(20):
        params = dict(start=begin.isoformat(), end=stop.isoformat(), time_zone=home.PACIFIC.key, limit=100)
        if token:
            params['page_token'] = token
        reply = bridge.get(CALENDAR_ACCOUNTS[account], ttl=0, timeout=30, keep=False, **params)
        for raw in reply.get('events', []):
            event = _event(raw)
            if event:
                key = f'{account}:{event[0]}'
                conn.execute('insert into archive_events(account, key, day, start, end_, all_day, title, location, seen) '
                             'values (?,?,?,?,?,?,?,?,?) on conflict(key) do update set day = excluded.day, start = excluded.start, '
                             'end_ = excluded.end_, all_day = excluded.all_day, title = excluded.title, location = excluded.location, '
                             'seen = excluded.seen', (account, key, *event[1:], stamp))
                keys.add(key)
        token = reply.get('next_page_token')
        if not token:
            break
    if not token:               # the whole window was read: what it no longer holds was moved or deleted
        gone = [r['key'] for r in conn.execute('select key from archive_events where account = ? and day >= ? and day < ?',
                                                (account, start.isoformat(), end.isoformat())) if r['key'] not in keys]
        conn.executemany('delete from archive_events where key = ?', [(k,) for k in gone])
    return len(keys)


def _months_back(day, count):
    """The first of the month `count` months before `day`'s (after it, for a negative count)."""
    index = day.year * 12 + day.month - 1 - count
    return dt.date(index // 12, index % 12 + 1, 1)


def calendar_collect(now=None, pause=0.3):
    """Bring the calendar history up to date, account by account: the weeks around now always, the past and next
    year once a night, and, until it is done, one more stretch of the far past each time. Returns what it did."""
    if not _calendar_lock.acquire(blocking=False):
        return {'busy': True}
    try:
        today = (now or dt.datetime.now(home.PACIFIC)).date()
        did = {}
        with _calendar_db() as conn:
            for account in CALENDAR_ACCOUNTS:
                did[account] = _collect(conn, account, today, pause)
        return did
    except bridge.BridgeError as error:
        store.log('archive calendar', type(error).__name__)
        return {'error': 'calendar'}
    finally:
        _calendar_lock.release()


def _collect(conn, account, today, pause):
    did = {}
    state = lambda name, value=None: _state(conn, f'{name}:{account}', value)  # noqa: E731
    did['recent'] = _read_window(conn, today - dt.timedelta(days=HOURLY_DAYS[0]), today + dt.timedelta(days=HOURLY_DAYS[1]), account)
    if state('nightly') != today.isoformat():
        month = dt.date(today.year, today.month, 1)
        for back in range(NIGHTLY_MONTHS, -NIGHTLY_MONTHS - 1, -1):      # a year back to a year ahead
            did['year'] = did.get('year', 0) + _read_window(conn, _months_back(month, back), _months_back(month, back - 1), account)
            time.sleep(pause)
        state('nightly', today.isoformat())
    back = state('back')
    if back != 'done':
        upto = dt.date.fromisoformat(back) if back else _months_back(dt.date(today.year, today.month, 1), 12)
        empty = int(state('empty') or 0)
        for _ in range(60):                     # five years of months a run, until the calendar runs out
            start = _months_back(upto, 1)
            found = _read_window(conn, start, upto, account)
            did['back'] = did.get('back', 0) + found
            empty = 0 if found else empty + 1
            upto = start
            if empty >= 12 * CALENDAR_EMPTY_YEARS or upto.year < CALENDAR_EARLIEST:
                state('back', 'done')
                break
            state('back', upto.isoformat())
            state('empty', empty)
            time.sleep(pause)
    state('read_at', time.time())
    return did


def _theirs(login):
    """The calendar accounts this login sees, as an SQL condition on archive_events (as `e`) and its values."""
    mine = [a for a in accounts(login) if a in CALENDAR_ACCOUNTS]
    return f'e.account in ({",".join("?" * len(mine))})', mine


def calendar(meaning, login, searches=None, most=CALENDAR_MOST):
    """Events in the calendar history of this login's own accounts that match, newest first."""
    phrases = [s for s in (searches if searches is not None else [meaning['terms']]) if s and s.strip()]
    mine, values = _theirs(login)
    if not values:
        return []
    where = [mine]
    if meaning['after']:
        where.append('e.day >= ?')
        values.append(meaning['after'])
    if meaning['before']:
        where.append('e.day < ?')
        values.append(meaning['before'])
    if meaning['months']:
        where.append(f'cast(substr(e.day, 6, 2) as integer) in ({",".join("?" * len(meaning["months"]))})')
        values += meaning['months']
    with _calendar_db() as conn:
        if phrases and _has_fts(conn):
            match = ' OR '.join('(' + ' AND '.join(f'"{w}"' for w in re.findall(r'\w+', p.lower())) + ')'
                                for p in phrases if re.findall(r'\w+', p))
            if not match:
                return []
            rows = conn.execute('select e.* from archive_events_fts f join archive_events e on e.id = f.rowid '
                                f'where archive_events_fts match ? {"and " + " and ".join(where) if where else ""} '
                                'order by e.day desc limit ?', (match, *values, most)).fetchall()
        elif phrases:
            like = ' or '.join('(' + ' and '.join('(e.title like ? or e.location like ?)' for _ in re.findall(r'\w+', p)) + ')'
                               for p in phrases if re.findall(r'\w+', p))
            words = [f'%{w}%' for p in phrases for w in re.findall(r'\w+', p) for _ in (0, 1)]
            rows = conn.execute(f'select e.* from archive_events e where ({like}) {"and " + " and ".join(where) if where else ""} '
                                'order by e.day desc limit ?', (*words, *values, most)).fetchall() if like else []
        elif len(where) > 1:            # a date or a month to go on, besides whose calendar it is
            rows = conn.execute(f'select e.* from archive_events e where {" and ".join(where)} order by e.day desc limit ?',
                                (*values, most)).fetchall()
        else:
            return []
    return [{'id': f'e:{r["id"]}', 'title': r['title'], 'day': r['day'], 'start': r['start'], 'end': r['end_'],
             'all_day': bool(r['all_day']), 'location': r['location']} for r in rows]


def calendar_event(ident, login):
    """One event, for a login whose account it is in; None otherwise, the same as for one that isn't there."""
    if not EVENT_ID.fullmatch(ident or ''):
        return None
    with _calendar_db() as conn:
        row = conn.execute('select * from archive_events where id = ?', (int(ident[2:]),)).fetchone()
    if row is None or row['account'] not in accounts(login):
        return None
    return {'id': ident, 'source': 'calendar', 'title': row['title'], 'day': row['day'], 'start': row['start'], 'end': row['end_'],
            'all_day': bool(row['all_day']), 'location': row['location']}


def calendar_status(login):
    """How much calendar history this login's own accounts have here, or None when it has none."""
    mine, values = _theirs(login)
    if not values:
        return None
    with _calendar_db() as conn:
        row = conn.execute(f'select count(*) as n, min(day) as first, max(day) as last from archive_events e where {mine}',
                           values).fetchone()
        done = all(_state(conn, f'back:{a}') == 'done' for a in values)
        read = [float(_state(conn, f'read_at:{a}') or 0) for a in values]
        return {'events': row['n'], 'first': row['first'], 'last': row['last'], 'complete': done, 'read_at': max(read) or None}


def start():
    """Keep the calendar history: every hour from Hub's start."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        threading.Event().wait(60)          # let Hub settle first
        while True:
            try:
                calendar_collect()
            except Exception as error:      # noqa: BLE001 - the loop must outlive one bad reading
                store.log('archive calendar stopped', type(error).__name__)
            threading.Event().wait(3600)
    threading.Thread(target=loop, name='archive-calendar', daemon=True).start()


# ---- one item, opened ----

def item(ident, login, passages=()):
    """One result in full, for the screen: a photo's facts, a document's text by page, a conversation, an event or
    an email. With `passages` (searches), a document's or a conversation's matching passages only, for Ask's model.
    None for one this login may not open."""
    if MAIL_ID.fullmatch(ident or ''):
        return mail_message(ident, login)
    if EVENT_ID.fullmatch(ident or ''):
        return calendar_event(ident, login)
    if not login or not ITEM.fullmatch(ident or '') or (ident[0] in 'dcg' and not owner(login)):
        return None
    return fetch(f'/item/{ident}', [('q', words) for words in passages] + scope(login))


def mail_search(text, login):
    """The Archive screen's mail, asked for after the rest has been shown."""
    if 'mail' not in sources_for(login):
        return []
    known = people(login)
    meaning = understand(text, [p['name'] for p in known], [p['name'] for p in known if p['kid']])
    return mail(meaning, login)


# ---- what Ask may read, and the landing ----

SWITCHES = ('documents', 'chats')
SWITCH_SCHEMA = 'create table if not exists archive_switches(name text primary key, on_ integer not null, by text, at real)'


def switches():
    """What the owner allows Ask's model to read from the archive: mail and calendar (owner decision, 2026-11-15);
    the passages of documents and ChatGPT history that match what was asked, with card, account and ID-like numbers
    masked (owner decision, 2026-11-16: on, and a tap turns either off); photos never."""
    with store.db() as conn:
        conn.execute(SWITCH_SCHEMA)
        on = {r['name']: bool(r['on_']) for r in conn.execute('select name, on_ from archive_switches')}
    return {name: on.get(name, True) for name in SWITCHES}


def switch(name, on, login):
    if name not in SWITCHES:
        raise Refused('There is no such switch.')
    if not owner(login):
        raise Refused('Only the owner can change what Ask may read.')
    with store.db() as conn:
        conn.execute(SWITCH_SCHEMA)
        conn.execute('insert or replace into archive_switches(name, on_, by, at) values (?, ?, ?, ?)',
                     (name, int(bool(on)), str(login)[:200], time.time()))
    what = {'documents': 'documents', 'chats': 'ChatGPT history'}[name]
    return f'Ask may read {what} now.' if on else f'Ask won’t read {what}.'


# What can be wrong, in fixed sentences for the owner: what it is (`say`) and what to do (`how`). Never a path or an
# error's own words. `loud`: something of the archive can't be read at all.
NEEDS_ACCESS = ('HubArchive needs Full Disk Access on the Mac. In System Settings, open Privacy & Security, then Full Disk '
                'Access, add HubArchive and switch it on. The next reading starts tonight.')
WHAT = {'photos': 'photos', 'documents': 'documents', 'chats': 'ChatGPT history'}
NOT_FINISHED = 'It is tried again tonight.'
SHARP = {'waiting': ('Full-size photos come after your OK on the Mac.',
                     'The first time, HubArchive asks on the Mac’s screen whether it may use Photos.'),
         'not allowed': ('Full-size photos are switched off on the Mac.',
                         'In System Settings, open Privacy & Security, then Automation, and switch on Photos under HubArchive.')}
NO_ACCOUNT = {'say': 'Your mail and calendar aren’t here yet.', 'how': 'They come once your Google account is added to Hub.', 'loud': False}
UNOWNED = {'say': 'Hub doesn’t know yet whose archive this is.',
           'how': 'It is set once on the Mac. Until then everyone sees the Shared Library’s photos only.', 'loud': True}


def _listed(names):
    names = list(names)
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


def troubles(runs, sharp, login):
    """What is wrong with the archive, for the owner (and, while Hub doesn't know who that is, for anyone signed in,
    so that it is never said to nobody): a source the last reading couldn't open or didn't finish, and full-size
    photos not yet allowed. Each is {'say', 'how', 'loud'}."""
    if not login or not (owner(login) or unowned()):
        return []
    out, shut, failed = [], [], []
    for source in ('photos', 'documents', 'chats'):
        run = runs.get(source) or {}
        errors = run.get('errors') or {}
        if 'NoAccess' in errors:
            shut.append(WHAT[source])
        elif errors and (run.get('finished') or 0) > (run.get('ok') or 0):      # its last run stopped on an error
            failed.append(WHAT[source])
    if shut:
        said = _listed(shut)
        out.append({'say': f'{said[:1].upper()}{said[1:]} can’t be read yet.', 'how': NEEDS_ACCESS, 'loud': True})
    if failed:
        out.append({'say': f'The last reading of {_listed(failed)} didn’t finish.', 'how': NOT_FINISHED, 'loud': True})
    if sharp in SHARP and 'photos' not in shut:
        out.append({'say': SHARP[sharp][0], 'how': SHARP[sharp][1], 'loud': False})
    return out


def on_this_day(login):
    """Photos from this day in earlier years that this login may see, newest year first. Raises Away."""
    if not login:
        return []
    today = home.today()
    return (fetch('/onthisday', [('day', today.strftime('%m-%d')), ('year', str(today.year))] + scope(login)) or {}).get('years', [])


def landing(login):
    """The Archive screen before a search: what it holds, on this day in earlier years, the people, the switches, and
    what is wrong. Cut to what this login may see: another login's landing shows nothing of the owner's."""
    mine = owner(login)
    out = {'ready': True, 'owner': mine, 'switches': switches() if mine else {}, 'calendar': calendar_status(login),
           'sources': list(sources_for(login)), 'google': sees(login),
           'trouble': [UNOWNED] if unowned() else [] if sees(login) else [NO_ACCOUNT]}
    try:
        status = fetch('/status', scope(login)) or {}
        out['counts'] = {k: v for k, v in status.get('counts', {}).items() if k in sources_for(login)}
        runs = status.get('runs', {})
        out['indexed_at'] = max((r.get('ok') or 0 for r in runs.values()), default=0) or None
        out['trouble'] = troubles(runs, status.get('sharp'), login) + out['trouble']
        out['onthisday'] = on_this_day(login)
        out['people'] = people(login)
    except Away as away:
        out.update(ready=False, away=str(away), counts={}, onthisday=[], people=[])
    return out
