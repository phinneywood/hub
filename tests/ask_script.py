"""A stand-in for Ask's model, for the tests and the demo server: it reads the owner's words and picks a tool,
as the real model would, by a few plain rules. No model is called. Every tool, check and change underneath is
Hub's real one, working on the made-up house (tests/app_world.py).

It knows the made-up rooms, floors and scenes, a handful of verbs, and one band to fall back on when a search
finds nothing. For the house's setup it knows a few plain sentences ("move the reading lamp to the bedroom",
"remove the dining room", "unlock the front door", "every evening at 6:45 turn on the hall light"), and for the
list a few more ("add sponges and milk to the list", "what's on the list", "order the groceries for tomorrow
morning"). Anything else gets one sentence saying what Hub can do.
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
EVERY = re.compile(rf'^every (evening|morning|day) at (\d{{1,2}}):(\d{{2}}),? turn (on|off) {THE}(.+)$', re.I)
SOUND = re.compile(r'\b(play|music|songs?|jazz|piano|playlist|pause|resume|skip|next|previous|quieter|louder|volume|too|as well)\b')
LIGHTS = re.compile(r'\b(dim|turn|switch|lights?|lamps?|on|off)\b')
FILLER = re.compile(r'\b(please|play|put on|some|a bit of|softly|quietly|music|in|on|the|speakers?)\b')
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


def _setup(spoken):
    """The setup tool one of the few plain sentences means, or None."""
    plain = ' '.join(spoken.split()).rstrip('.?!')
    for pattern, make in SETUP + LIST:
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
    setup = _setup(spoken)
    if back and back[0].tool_name == 'look_up' or not back and '<data>' in given:
        yield 'From what Hub can see, ' + ('the dentist is Tuesday at three.' if back else 'two emails need you.')
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
    elif COOKING.search(words):
        yield call('kitchen')
    elif words.startswith('remember'):
        note = re.sub(r'^remember( to| that)?\s*', '', spoken.strip(), flags=re.I)
        yield call('remember', note=note[:1].upper() + note[1:], **({'together': places} if len(places) > 1 else {}))
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
    else:
        yield 'Hub can switch and dim lights, run scenes, play music and change how the house is set up. What would you like?'


MODEL = FunctionModel(stream_function=scripted, model_name='scripted')
