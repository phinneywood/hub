"""Today's cards, and what “Ask about this” hands to Ask Hub.

A card is something `today.py` or `home.py` already found, laid out for one decision: a short preview,
the one-tap step where the list already allows one, a link to the tool's own page, and a question for
Ask Hub. Nothing here reads a source of its own, writes, or calls a model.

“Ask about this” passes a short reference, never text. Hub turns the reference into the question, so a
link from outside can only ever fill the box with one of Hub's own questions, and the owner still sends it.
"""
from . import home, today

REF_LENGTH = 200

# What Ask Hub can be asked about a tool or a chart, and the lookups (catalog slugs) that answer it.
TOPICS = {
    'tool:gmail': ('Gmail', 'What did the last mail sort find, and which emails need me?', ('sorted_mail',)),
    'tool:calendar': ('Calendar', 'What’s on my calendar this week, and does anything overlap?', ('calendar_week',)),
    'tool:trip': ('Trip', 'How is the trip plan coming along, and what’s left to decide?', ('trip_status',)),
    'tool:network': ('Network', 'How is the home network doing right now?', ('network_status',)),
    'insight:inbox': ('Inbox size', 'How has the size of my inbox changed lately?', ('inbox_trend',)),
    'insight:mail': ('What’s in the inbox', 'What kinds of mail are sitting in my inbox, and which need me?', ('sorted_mail',)),
    'insight:spend': ('AI spend', 'How much has Hub spent on AI this month, against its limits?', ('ai_spend',)),
    'insight:network': ('Network', 'How is the home network doing right now?', ('network_status',)),
}
LIST = ('Today’s list', 'What needs me today?', ('today_list',))
ALSO = {'gmail-': 'sorted_mail', 'calendar-': 'calendar_week', 'trip-': 'trip_status'}   # a list item's own source, by its key


def _known(slugs):
    from .engine import catalog
    names = {lookup.slug: lookup.name for lookup in catalog.lookups()}
    return [names[slug] for slug in slugs if slug in names]


def askable(ref):
    """Whether a topic has a lookup behind it today, so the card only offers a question Hub can answer."""
    return ref in TOPICS and bool(_known(TOPICS[ref][2]))


def _owner(href):
    """The tool a list item belongs to, from the page it points at."""
    from .tools import TOOLS
    return next((t['name'] for t in TOOLS if t['slug'] == _slug(href)), 'Hub')


def decisions(needs):
    """One card per “Needs you” item. The one-tap step is the list's own: `today._settle` already decided it."""
    return [{**n, 'owner': _owner(n['href']), 'ask': 'need:' + n['key']} for n in needs]


def _slug(href):
    return href.strip('/').split('/')[0].split('?')[0]


def tools(tiles, decided=()):
    """One card per tool, from its home tile. Ask is a main view, not a card, and a tool that already has
    a decision card (`decided`: those cards) is not shown twice."""
    above = {_slug(d['href']) for d in decided}
    found = []
    for t in tiles:
        if t['slug'] == 'ask' or t['slug'] in above:
            continue
        ref = 'tool:' + t['slug']
        found.append({**t, 'ask': ref if askable(ref) else None})
    return found


def about(ref):
    """What a reference stands for: a label for the page, the question, and what Hub will look at. None if unknown."""
    from .engine import judge
    ref = str(ref or '')[:REF_LENGTH]
    kind, _, key = ref.partition(':')
    topic = TOPICS.get(ref) if askable(ref) else None
    if kind == 'need' and key:
        slug = key.removeprefix('tile-')
        if key.startswith('tile-') and askable('tool:' + slug):
            topic = TOPICS['tool:' + slug]
        else:
            try:
                item = next((n for n in today.needs_you() if n['key'] == key), None)
            except Exception:  # noqa: BLE001 - the list failing must not break the Ask page
                item = None
            if item:
                # The title only, as the list lookup gives it: a mail item's second line names senders.
                title = judge.scrub(item['title'])[:160]
                more = tuple(slug for prefix, slug in ALSO.items() if key.startswith(prefix))
                topic = (title, f'On today’s list: “{title}”. What is this about, and what are my options?', LIST[2] + more)
            else:
                topic = LIST
    if not topic:
        return None
    label, question, slugs = topic
    return {'label': label, 'question': question, 'looks_at': _known(slugs),
            'from': 'Insights' if kind == 'insight' else 'Today'}


def page():
    """Everything the Today page shows, gathered once: the list, then a card per tool."""
    from .tools import TOOLS
    jobs = today.start()
    tiles = home.tiles(TOOLS)
    decided = decisions(today.collect(jobs, tiles))
    return {'decisions': decided, 'tools': tools(tiles, decided)}
