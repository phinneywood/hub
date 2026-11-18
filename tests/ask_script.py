"""A stand-in for Ask's model, for the tests and the demo server: it reads the owner's words and picks a tool,
as the real model would, by a few plain rules. No model is called. Every tool, check and change underneath is
Hub's real one, working on the made-up house (tests/app_world.py).

It knows the made-up rooms, floors and scenes, a handful of verbs, and one band to fall back on when a search
finds nothing. For the house's setup it knows a few plain sentences ("move the reading lamp to the bedroom",
"remove the dining room", "unlock the front door", "every evening at 6:45 turn on the hall light"), and for the
list a few more ("add sponges and milk to the list", "what's on the list", "order the groceries for tomorrow
morning"). Anything with "errands" in it shows the errands Hub got ready, and a few sentences finish one ("I paid the
power bill", "the photo form can wait till tomorrow", "I don't need the dental cleaning", "the pumpkin goes on the
list", "undo the power bill", "open my replies in Gmail"). "Search my mail for the plumber" searches the
person's mail, "open the first one" reads one it found, and "archive the first one", "label the second one
Receipts" or "mark the first one as read" put a card up for the tap. For the house's caretaker it knows a question or two
("how's the house?", "what broke last night?", "why does the reading lamp keep dropping off?"), answered from House
health as Hub gave it, and a few things to ask of it ("pause the caretaker for an hour", "run the hub drill", "I
changed the hue channel"). Anything else gets one sentence saying what Hub can do.
"""
import json
import re

from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from hub.tools.house import music
from tests import app_world

SEEN = []           # every request this stand-in was sent: what would have left for the house model
PLACES = sorted([name for _, name, _ in app_world.AREAS] + [f['name'] for f in app_world.FLOORS], key=len, reverse=True)
SCENES = [name for name, _ in app_world.SCENES.values()] + ['All off']
EVERYWHERE = re.compile(r'\b(everything|everywhere|all the lights|every light|whole house)\b')
COOKING = re.compile(r'\b(cook|recipes?|grocer(y|ies)|shopping list|meal|cart)\b')
NOT_HERE = re.compile(r'\b(uninstall|backup|add-on|pair)\b')
THE = r'(?:the |my )?'
# The house's setup, as a few plain sentences: (what was said, the tool, how its arguments are made from the words).
SETUP = [
    (rf'^what does {THE}(.+?) do$', lambda m: call('details', thing=m[1])),
    (rf'^move {THE}(.+?) (?:to|into) {THE}(.+)$', lambda m: call('arrange', things=[m[1]], room=m[2])),
    (rf'^rename {THE}(.+?) to (.+)$', lambda m: call('arrange', things=[m[1]], name=m[2])),
    (rf'^hide {THE}(.+)$', lambda m: call('arrange', things=[m[1]], hidden=True)),
    (rf'^(disable|enable) {THE}(.+)$', lambda m: call('arrange', things=[m[2]], enabled=m[1].lower() == 'enable')),
    (rf'^reload {THE}(.+?)(?: integration)?$', lambda m: call('integration', name=m[1])),
    (rf'^make a dashboard (?:called|for) {THE}(.+?) (?:with|showing) {THE}(.+)$', lambda m: call('dashboard', do='add', name=m[1], rooms=THINGS(m[2]))),
    (rf'^(?:remove|delete) {THE}(.+?) dashboard$', lambda m: call('dashboard', do='remove', name=m[1])),
    (r'^add a room called (.+?)(?: (upstairs|downstairs))?$', lambda m: call('rooms', kind='room', do='add', name=m[1], **({'floor': m[2]} if m[2] else {}))),
    (rf'^(?:remove|delete) {THE}(.+?) room$', lambda m: call('rooms', kind='room', do='remove', name=m[1])),
    (rf'^(?:set|turn) {THE}thermostat to (\d+)', lambda m: call('device', action='climate.set_temperature', things=['thermostat'], data={'temperature': int(m[1])})),
    (rf'^(lock|unlock) {THE}(.+)$', lambda m: call('device', action='lock.' + m[1].lower(), things=[m[2]])),
    (rf'^disarm {THE}(.+)$', lambda m: call('device', action='alarm_control_panel.alarm_disarm', things=[m[1]])),
    (rf'^(open|close) {THE}garage(?: door)?$', lambda m: call('device', action=f'cover.{m[1].lower()}_cover', things=['garage door'])),
    (rf'^(?:switch|turn) {THE}(.+?) automation (on|off)$', lambda m: call('routine', kind='automation', do=m[2].lower(), name=m[1])),
    (rf'^(?:remove|delete) {THE}(.+?) automation$', lambda m: call('routine', kind='automation', do='remove', name=m[1])),
    (rf'^in {THE}(.+?) scene,? set {THE}(.+?) to (\d+)', lambda m: call('scene_setup', do='save', name=m[1], set=[{'thing': m[2], 'level': int(m[3])}])),
    (rf'^save {THE}(.+?) as a scene called (.+)$', lambda m: call('scene_setup', do='save', name=m[2], as_now=[m[1]])),
    (r'^restart home assistant$', lambda m: call('device', action='homeassistant.restart')),
    (rf'^every (?:evening|morning|day) at (\d{{1,2}}):(\d{{2}}),? turn (on|off) {THE}(.+?)(?: lights?)?$', lambda m: call('find', words=m[4], kind='light')),
]
# Errands, as a few plain sentences: what the owner did, or wants done with one, naming it in their own words.
ERRAND = [
    (r'^i (?:paid|finished|sent|signed|returned|renewed|booked) (.+)$', lambda m: call('errand', do='done', which=[m[1]])),
    (r'^(.+?) can wait (?:until|till) tomorrow$', lambda m: call('errand', do='later', which=[m[1]])),
    (r"^(?:i|we) don.t need (.+)$", lambda m: call('errand', do='not', which=[m[1]])),
    (r'^(.+?) goes on the (?:shopping )?list$', lambda m: call('errand', do='list', which=[m[1]])),
    (r'^undo (.+)$', lambda m: call('errand', do='back', which=[m[1]])),
    (r'^open (?:my )?replies in gmail$', lambda m: call('errand', do='replies', replies_in='gmail')),
]
# The list and the cart, as a few plain sentences.
THINGS = lambda said: [t.strip() for t in re.split(r',| and ', said) if t.strip()]      # noqa: E731
LIST = [
    (r'^(?:add|put) (.+?) (?:to|on|in) (?:the |our |my )?(?:shopping |grocery )?(?:list|cart|groceries)$', lambda m: call('groceries', do='add', items=THINGS(m[1]))),
    (r'^we(?:.re| are) out of (.+)$', lambda m: call('groceries', do='add', items=THINGS(m[1]))),
    (r'^we need (.+)$', lambda m: call('groceries', do='add', items=THINGS(m[1]))),
    (r'^(?:take|remove) (.+?) (?:off|from|out of) (?:the |our )?(?:shopping |grocery )?(?:list|cart)$', lambda m: call('groceries', do='remove', items=THINGS(m[1]))),
    (r'^(?:what.?s|what is|show me|show) (?:on )?(?:the |our )?(?:shopping |grocery )?list$', lambda m: call('groceries', do='show')),
    (r'^(?:what.?s|what is|show me|show) in (?:the |our |my )?cart$', lambda m: call('groceries', do='cart')),
    (r'^(?:order the groceries|check out|place the order)(?: for (.+))?$', lambda m: call('groceries', do='order', **({'when': m[1]} if m[1] else {}))),
]
ERRANDS = re.compile(r'\berrands?\b')
SEARCH_MAIL = re.compile(r'^search my (?:mail|email) for (.+?)[.?!]?$', re.I)
OPEN_MAIL = re.compile(r'^(?:open|read) (?:the )?(first|second|third|\d)(?: one| email)?\b', re.I)
ORDINAL = {'first': 1, 'second': 2, 'third': 3}
# The house's caretaker, as a few plain sentences: what to ask of it. Questions are answered from House health itself.
HOURS = {'an hour': 60, 'half an hour': 30, 'two hours': 120, 'a day': 1440}
CARE = [
    (r'^pause the caretaker(?: for (an hour|half an hour|two hours|a day|\d+ minutes))?$',
     lambda m: call('caretaker', do='pause', **({'minutes': HOURS.get(m[1].lower()) or int(m[1].split()[0])} if m[1] else {}))),
    (r'^(?:resume|unpause|start) the caretaker(?: again)?$', lambda m: call('caretaker', do='resume')),
    (r'^run (?:the |a )?(.+?) drill$', lambda m: call('caretaker', do='drill', about=m[1])),
    (r'^i (?:changed|moved) the (hue (?:bridge.s )?(?:zigbee )?channel)$', lambda m: call('caretaker', do='sturdier_done', about='hue zigbee')),
    (r'^we don.t need (?:a second place for )?(?:the )?(backups?)(?: suggestion)?$', lambda m: call('caretaker', do='sturdier_ignore', about='backups')),
    (r'^(?:try|fix) (?:the )?(.+?) again$', lambda m: call('caretaker', do='try_again', about=m[1])),
    (r'^(?:i fixed|i sorted) (?:the )?(.+?)$', lambda m: call('caretaker', do='done', about=m[1])),
    (r'^(?:ignore|stop watching) (?:the )?(.+?)(?: problem)?$', lambda m: call('caretaker', do='ignore', about=m[1])),
    (r'^(?:any trouble at home|read house health)$', lambda m: call('house_health')),
]
RESTART = re.compile(r'^restart home assistant$', re.I)
CHANGE_MAIL = re.compile(r'^(archive|label|mark) (?:the )?(first|second|third|\d)(?: one| email)?(?: as (read|unread)| (.+?))?[.!]?$', re.I)
# The career desk, as a few plain sentences.
CAREER = [
    (r'^(?:show|open) my (?:career )?desk$', lambda m: call('career', do='show')),
    (r'^note for my career:? (.+)$', lambda m: call('career', do='note', words=m[1])),
    (r'^(?:look into|research) (.+?)(?: for my career)?$', lambda m: call('career', do='research', words=m[1])),
    (r'^draft (?:an? )?(?:essay|post|piece) (?:on|about) (.+)$', lambda m: call('career', do='draft', words=m[1])),
    (r'^(?:tick|check) off (?:the )?(.+?)(?: step)?$', lambda m: call('career', do='tick', words=m[1])),
    (r'^mark (?:the )?(.+?) (?:piece )?as (idea|draft|ready|published)$', lambda m: call('career', do='status', piece=m[1], status=m[2].lower())),
]
EVERY = re.compile(rf'^every (evening|morning|day) at (\d{{1,2}}):(\d{{2}}),? turn (on|off) {THE}(.+)$', re.I)
LIBRARY_ASKED = re.compile(r'\b(what playlists|playlists do (i|we) have|my playlists)\b')
SPEND_ASKED = re.compile(r'\b(ai|model)\b.*\b(cost|spend|spent)\b')
REMEMBERS = re.compile(r'\b(what do you remember|our conversation|that playlist you made)\b')
SOUND = re.compile(r'\b(play|music|songs?|jazz|piano|playlist|pause|resume|skip|next|previous|quieter|louder|volume|too|as well)\b')
LIGHTS = re.compile(r'\b(dim|turn|switch|lights?|lamps?|on|off)\b')
FILLER = re.compile(r'\b(please|play|put on|some|a bit of|softly|quietly|music|in|on|the|speakers?|my|playlist)\b')
ARCHIVE = re.compile(r'\b(photos?|pictures?|videos?|when did we|last time|invoice|receipt|warranty|show me|find)\b')
ARCHIVE_FILLER = set('show me find the a an of at in on when did we last time what did say photos photo pictures videos our'.split())
SYNONYMS = {'furnace': ['furnace service', 'HVAC maintenance', 'heating tune-up']}
LIBRARY = ['Evening Jazz', 'Kitchen Dance Party', 'Sunday Morning', 'Quiet Piano', 'Rainy Day', 'Songs for the Car']
BAND = {'name': 'The Example Band', 'uri': 'apple_music://artist/77'}


def library(patch, world):
    """A made-up music library behind Hub's own lookups: six playlists, and one band the catalog knows."""
    world.names = {f'library://playlist/{n}': name for n, name in enumerate(LIBRARY, 1)}
    patch.setattr(music, 'ready', lambda: True)
    patch.setattr(music, 'playlists', lambda search=None: [
        {'id': uri.rsplit('/', 1)[1], 'name': name, 'where': 'music', 'editable': True, 'uri': uri}
        for uri, name in world.names.items() if not search or search.lower() in name.lower()])
    patch.setattr(music, 'search', lambda query, kinds=('track',), limit=5: {
        k: [music.remember({'pos': None, 'name': BAND['name'], 'artist': '', 'uri': BAND['uri']}, 'artist')]
        if k == 'artist' and BAND['name'].lower() in query.lower() else [] for k in kinds})


def call(tool, **args):
    return {0: DeltaToolCall(name=tool, json_args=json.dumps(args), tool_call_id='call_' + tool)}


def said(messages):
    """The owner's latest words, as the model was given them."""
    text = [p.content for m in messages for p in m.parts if p.part_kind == 'user-prompt'][-1]
    return text.rsplit('<said>', 1)[1].removesuffix('</said>')


def _places(words):
    """The rooms and floors named, in the order they were said."""
    plain = words.replace("'", '’')
    found = []
    for place in PLACES:
        at = re.search(r'\b' + re.escape(place.lower()) + r'\b', plain)
        if at and not any(a <= at.start() < b for _, a, b in found):
            found.append((place, at.start(), at.end()))
    return [place for place, _, _ in sorted(found, key=lambda f: f[1])]


def _number(words, otherwise):
    found = re.search(r'\b(\d{1,3})\s*(%|percent)?(?!\d)', words)
    return int(found.group(1)) if found and int(found.group(1)) <= 100 else otherwise


def _music(words, places):
    if re.search(r'\b(pause|stop)\b', words):
        return call('music', do='pause')
    if re.search(r'\b(resume|carry on)\b', words):
        return call('music', do='resume')
    if re.search(r'\b(skip|next)\b', words):
        return call('music', do='next')
    if re.search(r'\bprevious\b', words):
        return call('music', do='previous')
    if re.search(r'\b(quieter|louder|volume)\b', words):
        level = _number(words, 10 if 'quieter' in words else 40)
        return call('music', do='volume', volume=level, **({'rooms': places} if places else {}))
    if re.search(r'\b(too|as well)\b', words) and places:
        return call('music', do='join', rooms=places)
    what = words
    for place in places:
        what = re.sub(r'\b' + re.escape(place.lower()) + r'\b', ' ', what.replace("'", '’'))
    what = ' '.join(FILLER.sub(' ', what).replace(',', ' ').split())
    args = {'do': 'play', 'what': [what or 'something']}
    if places:
        args['rooms'] = places
    if re.search(r'\b(softly|quietly)\b', words):
        args['volume'] = 15
    return call('music', **args)


def _lights(words, places):
    where = ['everywhere'] if EVERYWHERE.search(words) else places
    if not where:               # a place this stand-in doesn't know: pass it on, and Hub says whether it does
        named = re.sub(r'\b(please|turn|switch|dim|on|off|the|lights?|lamps?|in)\b', ' ', words).split()
        where = [' '.join(named)] if named else ['everywhere']
    if 'dim' in words:
        return call('lights', where=where, change='dim', level=_number(words, 40))
    return call('lights', where=where, change='off' if re.search(r'\boff\b', words) else 'on')


def _health(spoken, account):
    """What a model says from House health as Hub gave it: the line or two the question is about."""
    lines = [line.removeprefix('- ') for line in account if line]
    asked = ' '.join(spoken.lower().split())
    why = re.match(r'why (?:does|do|is|are) (?:the |my )?(.+?) (?:keep|still|always)', asked)
    if why:                     # the advice that names the thing asked about, or what needs the owner about it
        words = {w.rstrip('s') for w in re.findall(r'[a-z]+', why[1])} - {'the', 'my', 'a', 'light', 'lamp'} or {w.rstrip('s') for w in re.findall(r'[a-z]+', why[1])}
        for start in ('To make the house sturdier', 'Needs the owner', 'Trouble now'):
            found = [line for line in lines if line.startswith(start) and any(w in line.lower() for w in words)]
            if found:
                return found[0].split(': ', 1)[1]
        return 'House health says nothing about that.'
    if re.search(r'\b(broke|went wrong|failed|last night|overnight)\b', asked):
        start = next((n for n, line in enumerate(lines) if line.startswith(('Last night:', 'Tonight, from'))), None)
        if start is not None:
            night = [lines[start].split(': ', 1)[1]]
            for line in account[start + 1:]:
                if not line.startswith('- '):
                    break
                night.append(line.removeprefix('- '))
            return ' '.join(night)
    first = lines[0].removeprefix('House health: ') if lines else 'Hub can’t read House health.'
    needing = [line.split(': ', 1)[1] for line in lines if line.startswith('Needs the owner')]
    return ' '.join([first, *needing[:2]])


def _archive(words):
    """A search of the family archive, as a model would ask for it: several phrasings, and dates when given."""
    args = {}
    if re.search(r'\b(photos?|pictures?|videos?)\b', words):
        args['sources'] = ['photos']
    if re.search(r'\bkids\b', words):
        args['people'] = ['kids']
    year = re.search(r'\b(20\d\d)\b', words)
    if year:
        args.update(after=f'{year.group(1)}-01-01', before=f'{int(year.group(1)) + 1}-01-01')
    if re.search(r'\b(last|latest)\b', words):
        args['newest_first'] = True
    terms = ' '.join(w for w in re.findall(r"[a-z0-9-]+", words.split(' and turn')[0])
                     if w not in ARCHIVE_FILLER and not re.fullmatch(r'20\d\d', w) and w != 'kids')
    searches = next((v for k, v in SYNONYMS.items() if k in terms), [terms or 'photos'])
    return call('search', searches=searches, **args)


def _archive_settings(words):
    """The archive's own pictures and settings, as a model would ask for them, or None."""
    if re.search(r"\b(best photos|month.?s best|best of the month)\b", words):
        month = next((m for m in ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
                                  'october', 'november', 'december', 'last month') if m in words), None)
        return call('archive', do='best', **({'month': month} if month else {}))
    if re.search(r"\bon this day\b", words):
        return call('archive', do='on_this_day')
    found = re.search(r"\b(stop|don.?t|start|you can|you may)\b.*\bread(ing)?\b.*\b(chatgpt|chats|documents)\b", words)
    if found:
        return call('archive', do='read', what='documents' if found.group(3) == 'documents' else 'chats',
                    on=found.group(1) in ('start', 'you can', 'you may'))
    found = re.search(r"\b(\w+) (is|isn.?t|is not) one of the kids\b", words)
    if found:
        return call('archive', do='kid', name=found.group(1).capitalize(), on=found.group(2) == 'is')
    return None


def _from_archive(found):
    """An answer from what search returned, saying where it came from."""
    if isinstance(found.get('mail'), list) and found['mail']:
        first = found['mail'][0]
        return f'The last one was an email from {first["from"]} on {first["date"]}: “{first["subject"]}”.'
    for source in ('documents', 'chats', 'calendar'):
        if isinstance(found.get(source), list) and found[source]:
            first = found[source][0]
            return f'It’s {first["title"]}, from {first["date"]}.'
    if any(isinstance(v, str) and 'card' in v for k, v in found.items() if k != 'photos'):
        return 'There are matches on the card for you to open.'
    return 'Nothing in the archive matched.'


def _setup(spoken, given=''):
    """The setup tool one of the few plain sentences means, or None."""
    plain = ' '.join(spoken.split()).rstrip('.?!')
    if RESTART.match(plain) and 'restarting Home Assistant may clear it' in given:
        return call('caretaker', do='restart_home_assistant')      # the caretaker asks for it: it does it, and looks again
    for pattern, make in CARE + ERRAND + SETUP + LIST + CAREER:
        found = re.match(pattern, plain, re.I)
        if found:
            return make(found)
    return None


async def scripted(messages, info):
    SEEN.append(messages)
    spoken = said(messages)
    words = ' '.join(spoken.lower().split())
    back = [p for p in messages[-1].parts if p.part_kind == 'tool-return'] if messages[-1].kind == 'request' else []
    places = _places(words)
    given = [p.content for m in messages for p in m.parts if p.part_kind == 'user-prompt'][-1]
    setup = _setup(spoken, given)
    if not back and '"career_desk"' in given and not setup:
        desk = json.loads(given.split('<data>\n', 1)[1].split('\n</data>', 1)[0])['career_desk']
        steps = [step for section in desk.get('plan', ()) for step in section['open']]
        yield f'From your desk, the next step is: {steps[0]}' if steps else 'From your desk: ' + desk.get('note', 'nothing is open.')
    elif back and back[0].tool_name == 'career' and 'data' in back[0].content:
        yield 'Your thesis: ' + back[0].content['data'].get('thesis', '')
    elif back and back[0].tool_name == 'search':
        tries = len([m for m in messages if m.kind == 'response'])
        if re.search(r'\b(lights?|lamps?)\b', words) and tries < 2:
            yield _lights(words.split(' and ', 1)[-1], places)              # what a model might do next: Hub says no
        elif 'read' in words and tries < 2 and isinstance(back[0].content.get('mail'), list) and back[0].content['mail']:
            yield call('read', ref=back[0].content['mail'][0]['ref'])
        else:
            yield _from_archive(back[0].content)
    elif back and back[0].tool_name == 'read':
        got = back[0].content
        yield 'It says: ' + str(got.get('text') or got.get('not_done'))[:200]
    elif back and back[0].tool_name in ('mail_search', 'mail_read', 'mail_change'):
        seen = back[0].content
        if 'not_yours' in seen:
            yield seen['not_yours']
        elif 'email' in seen:
            yield f'{seen["email"]["from"]} wrote: {seen["email"]["text"].split(". ")[0].strip(".")}.'
        elif seen.get('found'):
            first = next((row for row in seen['found'] if 'from' in row), None)
            yield (f'{len(seen["found"])} emails match. The newest is from {first["from"]}: {first["subject"]}.' if first
                   else 'Only emails Hub may not read match.')
        else:
            yield 'Hub couldn’t do that. ' + str(seen.get('not_done') or seen.get('note') or '')
    elif not back and SEARCH_MAIL.match(spoken.strip()):
        yield call('mail_search', query=SEARCH_MAIL.match(spoken.strip()).group(1))
    elif not back and CHANGE_MAIL.match(spoken.strip()):
        verb, which, flag, label = CHANGE_MAIL.match(spoken.strip()).groups()
        n = ORDINAL.get(which.lower()) or int(which)
        yield call('mail_change', do=flag.lower() if flag else verb.lower(), which=[n], **({'label': label} if label else {}))
    elif not back and OPEN_MAIL.match(spoken.strip()):
        which = OPEN_MAIL.match(spoken.strip()).group(1).lower()
        yield call('mail_read', which=ORDINAL.get(which) or int(which))
    elif not back and '"pregnancy": {' in given:
        yield 'Week 15: fourteen weeks and six days, in the second trimester, with 176 days to go.'
    elif not back and '"network": {' in given:
        yield 'The internet is up: 14 ms ping, and one short drop overnight.'
    elif back and back[0].tool_name == 'music_library':
        names = [p['name'] for p in back[0].content.get('playlists', ())]
        yield f'You have {len(names)} playlists:\n' + '\n'.join(names) if names else 'Hub couldn’t read the library.'
    elif back and back[0].tool_name == 'hub_info':
        seen = back[0].content
        yield (f'This month Hub’s AI has cost {", ".join(m["spent"] for m in seen["limits"])}.' if 'limits' in seen
               else str(next(iter(seen.values()), '')))
    elif back and back[0].tool_name == 'conversation':
        seen = back[0].content
        yield (f'From our conversation: {seen["summary"]}' if not seen['summary'].startswith('(none')
               else 'Hub has no record of that in our conversation. Hub can list your playlists if that helps.')
    elif not back and LIBRARY_ASKED.search(words):
        yield call('music_library')
    elif not back and SPEND_ASKED.search(words):
        yield call('hub_info', what='ai_spend')
    elif not back and REMEMBERS.search(words):
        yield call('conversation', do='show')
    elif back and back[0].tool_name == 'look_up' or not back and '<data>' in given and not setup:
        yield 'From what Hub can see, ' + ('the dentist is Tuesday at three.' if back else 'two emails need you.')
    elif back and back[0].tool_name == 'house_health' and 'house_health' in back[0].content:
        yield _health(spoken, back[0].content['house_health'])
    elif not back and not setup and '<health>' in given:      # a question about the house's health, with House health beside it
        yield _health(spoken, given.split('<health>\n', 1)[1].split('\n</health>', 1)[0].split('\n'))
    elif back and back[0].tool_name == 'find' and back[0].content.get('found') and EVERY.match(' '.join(spoken.split()).rstrip('.')):
        # What a model does with what it found: write the automation with the id, in Home Assistant's own words.
        when, hour, minute, turn, what = EVERY.match(' '.join(spoken.split()).rstrip('.')).groups()
        hour = int(hour) + (12 if when.lower() == 'evening' and int(hour) < 12 else 0)
        light = back[0].content['found'][0]
        lamp = f'{light.get("room", "")} {light["name"]}'.strip().lower()
        name = f'{lamp.capitalize()} {turn.lower()} at {int(hour) % 12 or 12}:{minute} {"pm" if hour >= 12 else "am"}'
        yield call('routine', kind='automation', do='save', name=name, config={
            'alias': name, 'description': f'Turns the {lamp} {turn.lower()} every day.', 'mode': 'single',
            'triggers': [{'trigger': 'time', 'at': f'{hour:02d}:{minute}:00'}], 'conditions': [],
            'actions': [{'action': f'light.turn_{turn.lower()}', 'target': {'entity_id': light['id']}}]})
    elif back and back[0].tool_name == 'details' and 'not_done' not in back[0].content:
        seen = back[0].content
        yield (seen.get('config') or {}).get('description') or f'{seen.get("name", "It")} is {seen.get("state", "there")}.'
    elif back and back[0].tool_name == 'errands' and 'not_done' not in back[0].content:
        count = back[0].content.get('count', 0)
        yield (f'{count} errand{"s are" if count != 1 else " is"} ready for your tap. First: {back[0].content["errands"][0]["what"]}.'
               if count else 'Nothing is waiting for you.')
    elif back:                  # a tool said "not done": for music try a band once, as a model would; else explain
        tries = len([m for m in messages if m.kind == 'response'])
        if back[0].tool_name == 'music' and tries < 2 and 'Nothing matched' in str(back[0].content):
            yield call('music', do='play', what=[BAND['name']], kind='artist', **({'rooms': places} if places else {}))
        else:
            told = str(back[0].content.get('not_done', ''))
            asks = told.startswith('Which') and back[0].tool_name not in ('lights', 'scene', 'music')       # a setup tool's question is passed on as it is
            yield told if asks else 'Hub couldn’t do that. ' + told
    elif setup:
        yield setup
    elif ERRANDS.search(words):
        yield call('errands', show='ready')
    elif COOKING.search(words):
        yield call('kitchen')
    elif words.startswith('remember'):
        note = re.sub(r'^remember( to| that)?\s*', '', spoken.strip(), flags=re.I)
        yield call('remember', note=note[:1].upper() + note[1:], **({'together': places} if len(places) > 1 else {}))
    elif _archive_settings(words):
        yield _archive_settings(words)
    elif ARCHIVE.search(words):
        yield _archive(words)
    elif NOT_HERE.search(words):
        yield 'That is done in Home Assistant itself.'
    elif words.startswith('when is'):
        yield call('look_up', topics=['calendar'])
    elif SOUND.search(words) and not re.search(r'\b(lights?|lamps?|dim)\b', words):
        yield _music(words, places)
    elif re.search(r'\b(dim|turn|switch|lights?|lamps?)\b', words) or EVERYWHERE.search(words) or (places and LIGHTS.search(words)):
        yield _lights(words, places)
    elif any(name.lower() in words for name in SCENES):
        yield call('scene', name=next(name for name in SCENES if name.lower() in words))
    elif words.startswith('give me the short version'):        # a model that writes marks: the app draws them, never shows them
        yield 'Three things:\n- **Milk** for the morning\n- Eggs\n- Bread'
    else:
        yield 'Hub can switch and dim lights, run scenes, play music and change how the house is set up. What would you like?'


MODEL = FunctionModel(stream_function=scripted, model_name='scripted')
