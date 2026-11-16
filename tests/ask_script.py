"""A stand-in for Ask's model, for the tests and the demo server: it reads the owner's words and picks a tool,
as the real model would, by a few plain rules. No model is called. Every tool, check and change underneath is
Hub's real one, working on the made-up house (tests/app_world.py).

It knows the made-up rooms, floors and scenes, a handful of verbs, and one band to fall back on when a search
finds nothing. Anything else gets one sentence saying what Hub can do.
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
NOT_HERE = re.compile(r'\b(lock|unlock|alarm|garage|thermostat)\b')
SOUND = re.compile(r'\b(play|music|songs?|jazz|piano|playlist|pause|resume|skip|next|previous|quieter|louder|volume|too|as well)\b')
LIGHTS = re.compile(r'\b(dim|turn|switch|lights?|lamps?|on|off)\b')
FILLER = re.compile(r'\b(please|play|put on|some|a bit of|softly|quietly|music|in|on|the|speakers?)\b')
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


async def scripted(messages, info):
    SEEN.append(messages)
    spoken = said(messages)
    words = ' '.join(spoken.lower().split())
    back = [p for p in messages[-1].parts if p.part_kind == 'tool-return'] if messages[-1].kind == 'request' else []
    places = _places(words)
    given = [p.content for m in messages for p in m.parts if p.part_kind == 'user-prompt'][-1]
    if back and back[0].tool_name == 'search':
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
    elif back and back[0].tool_name == 'look_up' or not back and '<data>' in given:
        yield 'From what Hub can see, ' + ('the dentist is Tuesday at three.' if back else 'two emails need you.')
    elif back:                  # a tool said "not done": for music try a band once, as a model would; else explain
        tries = len([m for m in messages if m.kind == 'response'])
        if back[0].tool_name == 'music' and tries < 2 and 'Nothing matched' in str(back[0].content):
            yield call('music', do='play', what=[BAND['name']], kind='artist', **({'rooms': places} if places else {}))
        else:
            yield 'Hub couldn’t do that. ' + str(back[0].content.get('not_done', ''))
    elif COOKING.search(words):
        yield call('kitchen')
    elif words.startswith('remember'):
        note = re.sub(r'^remember( to| that)?\s*', '', spoken.strip(), flags=re.I)
        yield call('remember', note=note[:1].upper() + note[1:], **({'together': places} if len(places) > 1 else {}))
    elif ARCHIVE.search(words):
        yield _archive(words)
    elif NOT_HERE.search(words):
        yield 'Locks, the alarm, the garage and the thermostat can’t be changed from here.'
    elif words.startswith('when is'):
        yield call('look_up', topics=['calendar'])
    elif SOUND.search(words) and not re.search(r'\b(lights?|lamps?|dim)\b', words):
        yield _music(words, places)
    elif re.search(r'\b(dim|turn|switch|lights?|lamps?)\b', words) or EVERYWHERE.search(words) or (places and LIGHTS.search(words)):
        yield _lights(words, places)
    elif any(name.lower() in words for name in SCENES):
        yield call('scene', name=next(name for name in SCENES if name.lower() in words))
    else:
        yield 'Hub can switch and dim lights, run scenes and play music. What would you like?'


MODEL = FunctionModel(stream_function=scripted, model_name='scripted')
