"""One person's career desk: a folder of Markdown files, read and kept by Hub.

Where: `<DATA_DIR>/career/<account>/`, where <account> is the person's first account in hub/people.py ("default"
for the owner). household.json can put it elsewhere, such as a checkout of the person's own private career
repository: `"career": {"folders": {"default": "~/path/to/career"}}`. No login, or a person with no account of
their own, has no desk: a desk is never the household's.

What is in it (each file may be missing):
- brand.md      the thesis: front matter `thesis:` and `headline:`, else the first paragraph under a heading that
                says "Thesis", else the first paragraph.
- plan.md       the plan: headings, and steps as `- [ ]` lines. A tick in the app or from Ask writes `- [x]`.
- narrative.md  notes, oldest first, each under a `## YYYY-MM-DD HH:MM` heading. Ask adds to it, in the person's
                own words.
- field/        field notes on the field, with their sources. The desk's worker files these (worker.py).
- writing/      pieces being written. Front matter: title, date, status (idea, draft, ready, published), form.
- work/         evidence from the person's own work, for case studies and drafts.
- market.md     what roles in the field ask for, and the few worth a look. Hub applies to nothing.
- ip.md         the person's notes on what may be published. Never shown to a model.
- ip-words.txt  words that must never appear in a published piece (an employer's name, an internal product's),
                one a line. A piece holding one is flagged, and they are replaced by "[withheld]" in anything a model
                is given. The list itself is never shown or given to a model.

Nothing on the desk is published, posted or sent by Hub: "published" is a label the person sets.
"""
import hashlib
import os
import re
import tempfile
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from ... import config, home, household, people, store
from . import markdown

BRAND, PLAN, NOTES, MARKET, IP, IP_WORDS = 'brand.md', 'plan.md', 'narrative.md', 'market.md', 'ip.md', 'ip-words.txt'
SHELVES = ('field', 'writing', 'work')
STATUSES = ('idea', 'draft', 'ready', 'published')
FILE_BYTES = 300_000            # a file bigger than this is read only this far
SHELF_ITEMS = 80                # the newest this many files of a shelf are read
NOTE_LENGTH = 2000
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$')
DATED = re.compile(r'^(\d{4}-\d{2}-\d{2})-?')
STEP = re.compile(r'^(\s*[-*+]\s+)\[( |x|X)\](\s+)(.*\S)\s*$')
NOTE_HEAD = re.compile(r'^##\s+(\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2})?)\s*(.*)$')
SMALL = frozenset('a an and are as at be by for from has have i in is it its me my of on or our that the their this to was '
                  'we what when where which who will with you your about into how do does can could should would'.split())
SCHEMA = 'create table if not exists career_desks (account text primary key, model integer not null, at real not null);'


class NoDesk(Exception):
    """There is no desk for this request. The message is safe to show."""


class Refused(Exception):
    """The desk didn't do that. The message is safe to show."""


# ---- where, and reading ----

def account(login):
    """Whose desk a login opens: the person's first account, or None."""
    person = people.who(login)
    return person['accounts'][0] if person and person['accounts'] else None


def folder_of(key):
    """The desk folder for an account, whether it exists or not."""
    try:
        chosen = (household.section('career').get('folders') or {}).get(key)
    except (ValueError, AttributeError, OSError):
        chosen = None
    if isinstance(chosen, str) and chosen.strip():
        return Path(chosen.strip()).expanduser()
    return config.DATA_DIR / 'career' / key


def desk(login):
    """(the account, its folder) for a login. Raises NoDesk."""
    if not login:
        raise NoDesk('Open your career desk from your phone or laptop.')
    key = account(login)
    if key is None:
        raise NoDesk('Hub doesn’t know whose desk this is: your login has no account of its own in Hub.')
    return key, folder_of(key)


def _inside(root, path):
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _read(root, name):
    """A file of the desk as text, or ''. Only files inside the desk, and only so much of each."""
    path = root / name
    if not _inside(root, path) or not path.is_file():
        return ''
    try:
        with open(path, 'rb') as f:
            return f.read(FILE_BYTES).decode('utf-8', 'replace')
    except OSError:
        return ''


def _write(path, text):
    """Write one file of the desk whole, so a reader never sees half of it. The one way the desk is changed (the
    dry run in hub/golden/dryrun.py holds it back)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name, suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _words(text):
    return {w.rstrip('s') for w in re.findall(r"[a-z0-9]+", str(text).lower()) if w not in SMALL and len(w) > 1}


def _day(meta, stem, path):
    for value in (meta.get('date'), (DATED.match(stem) or [None, None])[1]):
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except (TypeError, ValueError):
            continue
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, home.PACIFIC).date().isoformat()
    except OSError:
        return None


def _title(meta, body, stem):
    if meta.get('title'):
        return markdown.plain(meta['title'])[:160]
    for line in body.split('\n'):
        found = markdown.HEADING.match(line)
        if found:
            return markdown.plain(found.group(2))[:160]
    return DATED.sub('', stem).replace('-', ' ').replace('_', ' ').strip().capitalize() or stem


def _summary(meta, body):
    if meta.get('summary'):
        return markdown.plain(meta['summary'])[:300]
    for block in markdown.blocks(body):
        if block['t'] == 'p':
            text = ''.join(span['text'] for span in block['text'])
            return markdown.plain(text)[:300]
    return ''


def ip_words(root):
    """The words a published piece must not hold, lower-cased."""
    words = []
    for line in _read(root, IP_WORDS).split('\n'):
        word = line.split('#', 1)[0].strip()
        if len(word) >= 2:
            words.append(word.lower())
    return sorted(set(words))


def flags(text, words):
    """Which of the words the text holds, as whole words, any case."""
    low = str(text).lower()
    return [w for w in words if re.search(r'(?<![a-z0-9])' + re.escape(w) + r'(?![a-z0-9])', low)]


def _entry(root, shelf, path, words):
    meta, body = markdown.front(_read(root, f'{shelf}/{path.name}'))
    entry = {'id': path.stem, 'kind': shelf, 'title': _title(meta, body, path.stem), 'date': _day(meta, path.stem, path),
             'summary': _summary(meta, body)}
    if shelf == 'writing':
        status = str(meta.get('status', 'draft')).strip().lower()
        entry.update(status=status if status in STATUSES else 'draft', form=markdown.plain(meta.get('form', ''))[:40],
                     flags=len(flags(body + ' ' + meta.get('title', ''), words)), by=markdown.plain(meta.get('by', ''))[:60])
    if shelf == 'field':
        entry['sources'] = len(markdown.links(body))
    return entry


def shelf(root, name, words=()):
    """The files of one shelf, newest first."""
    where = root / name
    if not where.is_dir() or not _inside(root, where):
        return []
    found = [p for p in where.glob('*.md') if p.is_file() and NAME.match(p.stem) and not p.name.startswith('.')]
    found.sort(key=lambda p: (DATED.match(p.stem) is not None, p.stem), reverse=True)
    entries = [_entry(root, name, p, words) for p in found[:SHELF_ITEMS]]
    return sorted(entries, key=lambda e: e['date'] or '', reverse=True)


def thesis(root):
    """(the thesis sentence, the headline) from brand.md."""
    meta, body = markdown.front(_read(root, BRAND))
    said = markdown.plain(meta.get('thesis', ''))
    if not said:
        under, first = False, ''
        for block in markdown.blocks(body):
            words = ''.join(span['text'] for span in block.get('text', ()) if isinstance(span, dict))
            if block['t'] == 'h':
                under = 'thesis' in words.lower()
            elif block['t'] in ('p', 'quote'):
                first = first or words
                if under:
                    said = words
                    break
        said = markdown.plain(said or first)
    return said[:400], markdown.plain(meta.get('headline', ''))[:200]


def _key(text, seen):
    key = hashlib.sha1(markdown.plain(text).lower().encode()).hexdigest()[:10]
    while key in seen:
        key = hashlib.sha1(key.encode()).hexdigest()[:10]
    seen.add(key)
    return key


def plan(root):
    """The plan as sections: [{'title', 'steps': [{'key', 'text', 'plain', 'done', 'line'}], 'about'}]. Steps keep
    their key when ticked, so a tick from one device finds the same step on another."""
    meta, body = markdown.front(_read(root, PLAN))
    offset = _read(root, PLAN).count('\n') - body.count('\n')
    sections, seen = [{'title': '', 'steps': [], 'about': ''}], set()
    for n, line in enumerate(body.split('\n')):
        heading, step = markdown.HEADING.match(line), STEP.match(line)
        if heading and len(heading.group(1)) <= 3:
            sections.append({'title': markdown.plain(heading.group(2))[:120], 'steps': [], 'about': ''})
        elif step:
            sections[-1]['steps'].append({'key': _key(step.group(4), seen), 'text': markdown.inline(step.group(4)),
                                          'plain': markdown.plain(step.group(4))[:300], 'done': step.group(2) != ' ',
                                          'line': n + offset})
        elif line.strip() and not sections[-1]['about'] and not line.lstrip().startswith(('-', '*', '+', '>', '|')):
            sections[-1]['about'] = markdown.plain(line)[:300]
    return [s for s in sections if s['steps'] or (s['title'] and s['about'])]


def notes(root, last=None):
    """The person's notes, newest first: [{'at', 'text', 'plain'}]."""
    meta, body = markdown.front(_read(root, NOTES))
    found, current = [], None
    for line in body.split('\n'):
        head = NOTE_HEAD.match(line)
        if head:
            current = {'at': head.group(1).replace('T', ' '), 'lines': [head.group(2)] if head.group(2) else []}
            found.append(current)
        elif current is not None and line.strip():
            current['lines'].append(line.strip())
    out = [{'at': n['at'], 'text': markdown.inline(' '.join(n['lines'])), 'plain': markdown.plain(' '.join(n['lines']))[:1200]}
           for n in reversed(found) if n['lines']]
    return out[:last] if last else out


def _market(root):
    text = _read(root, MARKET)
    if not text.strip():
        return None
    meta, body = markdown.front(text)
    return {'id': 'market', 'kind': 'market', 'title': _title(meta, body, 'market'), 'summary': _summary(meta, body),
            'date': _day(meta, 'market', root / MARKET)}


# ---- whether Hub's model may read the desk ----

def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def model_reads(key):
    """Whether Hub's model may read this desk: the person's own switch, on unless they turned it off."""
    with closing(_db()) as conn:
        row = conn.execute('select model from career_desks where account=?', (key,)).fetchone()
    if row is not None:
        return bool(row['model'])
    try:
        return household.section('career').get('model', True) is not False
    except ValueError:
        return False


def set_model_reads(login, on):
    key, _ = desk(login)
    with closing(_db()) as conn, conn:
        conn.execute('insert or replace into career_desks(account, model, at) values(?,?,?)', (key, 1 if on else 0,
                                                                                               datetime.now().timestamp()))
    return 'Hub’s model may read your desk' if on else 'Your desk is kept from Hub’s model'


# ---- the screen ----

def picture(login):
    """The Career screen as data, for one person. Reads files only; calls no model."""
    from . import worker
    try:
        key, root = desk(login)
    except NoDesk as error:
        return {'ok': False, 'why': 'signed-out' if not login else 'nobody', 'said': str(error)}
    words = ip_words(root)
    said, headline = thesis(root)
    sections = plan(root)
    steps = [s for section in sections for s in section['steps']]
    shelves = {name: shelf(root, name, words) for name in SHELVES}
    kept = notes(root)
    empty = not (said or steps or kept or any(shelves.values()) or _market(root))
    return {
        'ok': True, 'empty': empty, 'where': str(root).replace(str(Path.home()), '~', 1),
        'thesis': said, 'headline': headline,
        'next': [_step(s) for s in steps if not s['done']][:4],
        'plan': [{'title': s['title'], 'about': s['about'], 'steps': [_step(x) for x in s['steps']],
                  'open': sum(not x['done'] for x in s['steps'])} for s in sections],
        'open': sum(not s['done'] for s in steps), 'done': sum(s['done'] for s in steps),
        **shelves, 'market': _market(root), 'notes': kept[:3], 'more_notes': max(0, len(kept) - 3),
        'ip': {'words': len(words)}, 'model': model_reads(key),
        'jobs': worker.jobs(key), 'budget': worker.budget(),
    }


def _step(step):
    return {'key': step['key'], 'text': step['text'], 'plain': step['plain'], 'done': step['done']}


def piece(login, kind, name):
    """One file of the desk to read: a field note, a piece of writing, evidence, or the market. Raises NoDesk."""
    key, root = desk(login)
    if kind == 'market':
        relative = MARKET
    elif kind in SHELVES and NAME.match(str(name)):
        relative = f'{kind}/{name}.md'
    else:
        raise NoDesk('There is no such thing on the desk.')
    text = _read(root, relative)
    if not text:
        raise NoDesk('That isn’t on the desk any more.')
    meta, body = markdown.front(text)
    words = ip_words(root)
    out = {'id': name if kind != 'market' else 'market', 'kind': kind, 'title': _title(meta, body, Path(relative).stem),
           'date': _day(meta, Path(relative).stem, root / relative), 'blocks': markdown.blocks(body),
           'sources': [{'text': t, 'href': h} for t, h in markdown.links(body)][:40] if kind == 'field' else []}
    if kind == 'writing':
        status = str(meta.get('status', 'draft')).lower()
        out.update(status=status if status in STATUSES else 'draft', form=markdown.plain(meta.get('form', '')),
                   by=markdown.plain(meta.get('by', '')), flags=flags(body + ' ' + meta.get('title', ''), words),
                   words=len(markdown.plain(body).split()))
    return out


# ---- what a tap or Ask changes ----

def tick(login, key, done=True):
    """Mark one step of the plan done, or open again. Returns the step's words. Raises NoDesk, Refused."""
    _, root = desk(login)
    text = _read(root, PLAN)
    found = next((s for section in plan(root) for s in section['steps'] if s['key'] == key), None)
    if found is None:
        raise Refused('That step isn’t in the plan any more.')
    if found['done'] == bool(done):
        raise Refused('That step is already done.' if done else 'That step is already open.')
    lines = text.split('\n')
    line = STEP.match(lines[found['line']])
    if not line or markdown.plain(line.group(4))[:300] != found['plain']:
        raise Refused('The plan changed just now. Look again.')
    lines[found['line']] = f'{line.group(1)}[{"x" if done else " "}]{line.group(3)}{line.group(4)}'
    _write(root / PLAN, '\n'.join(lines))
    return found['plain']


def step_named(login, words):
    """The one open step of the plan the words name, or None: every word said is in it, or most of them are and
    no other step does as well."""
    _, root = desk(login)
    asked = _words(words)
    if not asked:
        return None
    scored = []
    for section in plan(root):
        for step in section['steps']:
            if not step['done']:
                shared = asked & _words(step['plain'] + ' ' + section['title'])
                scored.append((len(shared) / len(asked), step))
    scored.sort(key=lambda s: s[0], reverse=True)
    if not scored or scored[0][0] < 0.6 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return scored[0][1]


def note(login, text):
    """Add one note, in the person's words, to the end of their notes. Returns {'at', 'text'}."""
    _, root = desk(login)
    said = ' '.join(str(text or '').split())
    if not said:
        raise Refused('Say what to note.')
    if len(said) > NOTE_LENGTH:
        raise Refused(f'Keep a note under {NOTE_LENGTH} characters.')
    at = datetime.now(home.PACIFIC).strftime('%Y-%m-%d %H:%M')
    kept = _read(root, NOTES)
    if not kept.strip():
        kept = '# Notes\n\nWhat happened, what I decided and what I am thinking, newest last.\n'
    _write(root / NOTES, kept.rstrip('\n') + f'\n\n## {at}\n\n{said}\n')
    return {'at': at, 'text': said}


def set_status(login, name, status):
    """Set a piece's status: idea, draft, ready or published (a label only: Hub publishes nothing)."""
    _, root = desk(login)
    if status not in STATUSES:
        raise Refused('A piece is an idea, a draft, ready or published.')
    if not NAME.match(str(name)):
        raise Refused('There is no such piece.')
    text = _read(root, f'writing/{name}.md')
    if not text:
        raise Refused('That piece isn’t on the desk any more.')
    meta, body = markdown.front(text)
    meta['status'] = status
    _write(root / 'writing' / f'{name}.md', markdown.with_front(meta, body))
    return _title(meta, body, name)


def filed(root, shelf_name, title, text):
    """Put a new file on a shelf, named by today's date and its title, never over another. Returns its id."""
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:60] or 'note'
    stem = f'{home.today().isoformat()}-{slug}'
    name, n = stem, 1
    while (root / shelf_name / f'{name}.md').exists():
        n += 1
        name = f'{stem}-{n}'
    _write(root / shelf_name / f'{name}.md', text)
    return name


# ---- what the model may be given ----

PASSAGE = 700
GIVEN = 7000


def passages(key, root, text):
    """What a question about the desk needs: the thesis, the open steps, what is on each shelf, and the passages
    that share the most words with the question. Bounded. Never ip.md or the word list."""
    asked = _words(text)
    said, headline = thesis(root)
    sections = plan(root)
    found = []
    for name, label in (('field', 'field note'), ('writing', 'piece'), ('work', 'evidence')):
        for entry in shelf(root, name)[:30]:
            meta, body = markdown.front(_read(root, f'{name}/{entry["id"]}.md'))
            for block in markdown.blocks(body):
                if block['t'] in ('p', 'quote', 'ul', 'ol'):
                    words = ' '.join(''.join(s['text'] for s in item['text']) for item in block['items']) if block['t'] in ('ul', 'ol') \
                        else ''.join(s['text'] for s in block['text'])
                    found.append((f'{label}: {entry["title"]}', markdown.plain(words)))
    for note_ in notes(root, 40):
        found.append((f'note, {note_["at"]}', note_['plain']))
    market = _read(root, MARKET)
    if market:
        for block in markdown.blocks(markdown.front(market)[1]):
            if block['t'] in ('p', 'ul', 'ol'):
                words = ' '.join(''.join(s['text'] for s in item['text']) for item in block['items']) if block['t'] in ('ul', 'ol') \
                    else ''.join(s['text'] for s in block['text'])
                found.append(('the market', markdown.plain(words)))
    scored = sorted(((len(asked & _words(words)), where, words) for where, words in found if words), key=lambda s: s[0], reverse=True)
    picked, size = [], 0
    for score, where, words in scored:
        if score == 0 or size > GIVEN:
            break
        picked.append({'from': where, 'text': words[:PASSAGE]})
        size += min(len(words), PASSAGE)
    return _masked({
        'thesis': said, 'headline': headline,
        'plan': [{'section': s['title'], 'open': [x['plain'] for x in s['steps'] if not x['done']][:8],
                  'done': len([x for x in s['steps'] if x['done']])} for s in sections][:10],
        'field_notes': [f'{e["date"]}: {e["title"]}' for e in shelf(root, 'field')[:15]],
        'pieces': [f'{e["title"]} ({e["status"]})' for e in shelf(root, 'writing')[:15]],
        'evidence': [e['title'] for e in shelf(root, 'work')[:15]],
        'recent_notes': [f'{n["at"]}: {n["plain"][:300]}' for n in notes(root, 3)],
        'passages': picked,
    }, ip_words(root))


WITHHELD = '[withheld]'


def _masked(found, words):
    """What goes to a model, with every word of the person's list (an employer's name, an internal product's)
    replaced: those never leave the desk, whatever a piece or a note says."""
    if not words:
        return found
    pattern = re.compile(r'(?<![A-Za-z0-9])(' + '|'.join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r')(?![A-Za-z0-9])', re.I)

    def mask(value):
        if isinstance(value, str):
            return pattern.sub(WITHHELD, value)
        if isinstance(value, list):
            return [mask(v) for v in value]
        if isinstance(value, dict):
            return {k: mask(v) for k, v in value.items()}
        return value
    return mask(found)


def for_model(login, text):
    """The desk for Ask's model, when its person lets the model read it."""
    try:
        key, root = desk(login)
    except NoDesk as error:
        return {'note': str(error)}
    if not model_reads(key):
        return {'note': 'The owner keeps their career desk from the model. Say they can turn that on in the desk.'}
    return passages(key, root, text)
