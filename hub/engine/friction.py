"""Friction: where Hub got in the owner's way today, kept as short one-liners, offered as fix cards each morning.

During the day Hub notes four kinds of friction, each as one scrubbed line (links and addresses taken out,
clipped), never mail or a whole chat:

  cant        an Ask control or hand-off that refused, wasn't available, matched nothing or wasn't confirmed
              (in Hub's own words), or an answer that said Hub couldn't do something (no words of it kept)
  undo        a change Ask made that the owner undid within a few minutes
  correction  the owner's next message pushed back ("no, …", "not that", "I said …") right after Ask changed
              something; only that it happened and which tool, never the message
  wish        the owner said what Hub should do ("Hub should …", "I wish Hub …"): that sentence, scrubbed and clipped

Each line carries a key (kind and tool), so the same snag counts once however often it happens. With the morning
job (`prepare`, no model), the day's lines are grouped by key, ranked by how often and how recently they came up,
and at most CARDS become fix cards on Today: Build files a brief for the Manager (improvement-requests.json, the
same file as "Improve this"); Not now hides it until the snag comes up again; Never keeps that key off the cards
for good. Hub never edits or deploys its own code.
"""
import json
import re
import time
import uuid

from .. import home, store

LINE = 160
KEEP_DAYS = 30
QUICK_UNDO_SECONDS = 10 * 60
CARDS = 2                    # fix cards offered on one morning
LOOK_BACK_DAYS = 3           # friction older than this doesn't make a card
SCHEMA = """
create table if not exists friction(
  id integer primary key autoincrement, ts real not null, day text not null, kind text not null, key text not null,
  line text not null);
create table if not exists friction_cards(
  id text primary key, day text not null, key text not null, title text not null, lines text not null,
  count integer not null, status text not null default 'new', decided_at real);
"""
KINDS = ('cant', 'undo', 'correction', 'wish')
NOT_DONE = ('refused', 'unavailable', 'nothing_matched', 'stopped', 'not_confirmed', 'not_done', 'songs_not_found')
SAYS_CANT = re.compile(r"\b(can'?t|can’t|cannot|couldn'?t|couldn’t|unable to|not able to)\b", re.I)
WISH = re.compile(r"^\s*(?:hub|you|it)\s+(?:should|ought to|needs? to)\b|\bi wish (?:hub|you|it)\b|"
                  r"\bit would be (?:nice|great|good) if (?:hub|you)\b|\bcan you (?:learn|start) to\b", re.I)
CORRECTION = re.compile(r"^\s*(?:no\b|nope\b|not that\b|wrong\b|that'?s not\b|that’s not\b|i said\b|i meant\b|"
                        r"undo\b|stop\b|why did you\b)", re.I)
TITLES = {'cant': 'Ask couldn’t do this', 'undo': 'You undid what Ask did', 'correction': 'You corrected Ask',
          'wish': 'You asked Hub to do more'}


def _db():
    conn = store.db()
    conn.executescript(SCHEMA)
    return conn


def _clip(text):
    from . import judge
    return ' '.join(judge.scrub(str(text)).split())[:LINE]


def note(kind, key, line):
    """Keep one line of friction. Never raises: noting friction must not break what it watches."""
    try:
        assert kind in KINDS
        now = time.time()
        with _db() as conn:
            conn.execute('delete from friction where ts < ?', (now - KEEP_DAYS * 86400,))
            conn.execute('insert into friction(ts, day, kind, key, line) values (?,?,?,?,?)',
                         (now, home.today().isoformat(), kind, f'{kind}:{key}'[:80], _clip(line)))
    except Exception:  # noqa: BLE001
        pass


def lines(days=LOOK_BACK_DAYS):
    with _db() as conn:
        return [dict(r) for r in conn.execute('select * from friction where ts > ? order by ts desc',
                                              (time.time() - days * 86400,))]


# ---- noticing it ----

def from_turn(text, new_messages, earlier):
    """After an Ask turn: what in it was friction. `earlier` is the thread before this turn. Never raises."""
    try:
        _from_turn(text, new_messages, earlier)
    except Exception:  # noqa: BLE001 - noticing friction must never break a chat
        pass


def _from_turn(text, new_messages, earlier):
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from . import catalog
    controls = {c.slug for c in catalog.controls()} | {h.slug for h in catalog.handoffs()}
    returns = {p.tool_call_id: p.content for m in new_messages if isinstance(m, ModelRequest)
               for p in m.parts if isinstance(p, ToolReturnPart)}
    calls = [(p.tool_name, returns.get(p.tool_call_id)) for m in new_messages if isinstance(m, ModelResponse)
             for p in m.parts if isinstance(p, ToolCallPart)]
    snagged = False
    for tool, content in calls:
        if tool not in controls or not isinstance(content, dict):
            continue
        kind = next((k for k in NOT_DONE if content.get(k)), None)
        if kind:
            detail = content[kind]
            detail = '; '.join(map(str, detail)) if isinstance(detail, list) else detail
            note('cant', f'{tool}:{kind}', f'{tool} {kind.replace("_", " ")}: {detail}')
            snagged = True
    reply = ''.join(p.content for m in new_messages if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, TextPart))
    if not snagged and SAYS_CANT.search(reply):
        used = ', '.join(dict.fromkeys(t for t, _ in calls)) or 'no tool'
        note('cant', 'answer', f'Ask said it couldn’t help (it used {used})')
    if WISH.search(str(text)):
        note('wish', 'owner', str(text))
    acted = [p.tool_name for m in earlier[-6:] if isinstance(m, ModelResponse) for p in m.parts
             if isinstance(p, ToolCallPart) and p.tool_name in controls]
    if acted and CORRECTION.search(str(text)):
        note('correction', acted[-1], f'The owner pushed back right after {acted[-1]}')


def undone(rows, via):
    """After an Undo: changes Ask made that the owner put back within minutes. rows: the change rows undone;
    via: who undid them (a golden test's own put-back is not friction)."""
    if via == 'golden':
        return
    now = time.time()
    for row in rows:
        if row.get('via') == 'ask' and now - row['ts'] <= QUICK_UNDO_SECONDS:
            minutes = max(1, round((now - row['ts']) / 60))
            note('undo', row.get('action', 'change'), f'Undid “{row.get("words", "")}” {minutes} min after Ask did it')


# ---- the morning cards ----

def prepare():
    """With the morning job: turn recent friction into at most CARDS ranked fix cards. No model. Never raises."""
    try:
        _prepare()
    except Exception:  # noqa: BLE001 - a missing card is better than a broken morning
        store.log('friction_cards_failed', '')


def _prepare():
    day = home.today().isoformat()
    with _db() as conn:
        if conn.execute('select 1 from friction_cards where day = ?', (day,)).fetchone():
            return
        never = {r['key'] for r in conn.execute("select key from friction_cards where status = 'never'")}
        built = {r['key']: r['decided_at'] for r in conn.execute(
            "select key, max(decided_at) decided_at from friction_cards where status in ('built', 'later') group by key")}
    groups = {}
    for row in lines():
        if row['key'] in never or row['ts'] <= (built.get(row['key']) or 0):
            continue                     # never again, or nothing new since the owner last decided
        groups.setdefault(row['key'], []).append(row)
    now = time.time()
    ranked = sorted(groups.items(), key=lambda kv: -(len(kv[1]) + sum(1 for r in kv[1] if now - r['ts'] < 86400)))
    with _db() as conn:
        conn.execute("update friction_cards set status = 'expired' where status = 'new'")
        for key, rows in ranked[:CARDS]:
            kind = key.split(':')[0]
            sample = list(dict.fromkeys(r['line'] for r in rows))[:3]
            conn.execute('insert into friction_cards(id, day, key, title, lines, count) values (?,?,?,?,?,?)',
                         (uuid.uuid4().hex[:12], day, key, TITLES.get(kind, 'Friction'), json.dumps(sample), len(rows)))


def cards():
    """Today's fix cards that still wait for a decision."""
    with _db() as conn:
        return [{**dict(r), 'lines': json.loads(r['lines'])} for r in conn.execute(
            "select * from friction_cards where status = 'new' and day = ? order by count desc", (home.today().isoformat(),))]


def today_items():
    from .. import today
    out = []
    for c in cards():
        times = f'{c["count"]} time{"s" * (c["count"] != 1)}' if c['count'] > 1 else 'once'
        out.append(today.rec(f'friction-{c["id"]}', 'info', c['title'], f'{c["lines"][0]} ({times} lately).',
                             href='/engine#improvements', cta='Build',
                             acts=[{'post': f'/engine/friction/{c["id"]}/{choice}', 'label': label, 'primary': choice == 'build'}
                                   for choice, label in (('build', 'Build'), ('later', 'Not now'), ('never', 'Never'))]))
    return out


def brief(card):
    """The brief a Build tap files: the friction lines only, in Hub's words or the owner's own wish."""
    lines_ = '; '.join(card['lines'])
    return (f'From Hub’s friction log, offered on Today on {card["day"]}: {card["title"].lower()} '
            f'({card["count"]} time{"s" * (card["count"] != 1)} in the last {LOOK_BACK_DAYS} days). Lines: {lines_}. '
            'No message or mail text is included beyond the owner’s own “Hub should …” words; ask the owner for more.')


class Refused(Exception):
    pass


def decide(card_id, choice, *, login=None):
    """The owner's tap on a fix card: 'build' files a brief for the Manager, 'later' or 'never'. Returns the card."""
    from . import improve
    if choice not in ('build', 'later', 'never'):
        raise Refused('Choose Build, Not now or Never.')
    with _db() as conn:
        row = conn.execute("select * from friction_cards where id = ? and status = 'new'", (str(card_id)[:12],)).fetchone()
    if row is None:
        raise Refused('That card isn’t waiting any more.')
    card = {**dict(row), 'lines': json.loads(row['lines'])}
    if choice == 'build':
        improve.file(brief(card), source='friction', login=login)
    with _db() as conn:
        conn.execute('update friction_cards set status = ?, decided_at = ? where id = ?',
                     ({'build': 'built', 'later': 'later', 'never': 'never'}[choice], time.time(), card['id']))
    return card
