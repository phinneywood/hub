"""The golden scenarios: things a person really says to Hub, and what Hub must and mustn't do.

Wording and names here are fictional. `{alias}` in a message stands for a room, speaker or lamp; offline it
is the made-up name in ALIASES, and on a live run the owner's private household.json can map it to the
house's own names (`"golden": {"names": {"living_room": "…"}}`), so this file never holds a real one.

`script` is what a well-behaved model does, step by step, for the offline run: Use(tool, **args) or
Say(words). Offline, the scripted model follows it and the checks test Hub's own side (validation, the
dry-run record, what a tool answers). Live, the real model decides, and the same checks test it too.

`seed` names the real failure a scenario was written for, so it never comes back unnoticed.
"""
from dataclasses import dataclass, field

from . import checks as c

ALIASES = {'living_room': 'Living Room', 'living_room_speaker': 'Living Room Speaker',
           'kitchen': 'Kitchen', 'kitchen_speaker': 'Kitchen Speaker', 'reading_lamp': 'Reading Lamp'}


@dataclass(frozen=True)
class Use:
    tool: str
    args: dict = field(default_factory=dict)

    def __init__(self, tool, **args):
        object.__setattr__(self, 'tool', tool)
        object.__setattr__(self, 'args', args)


@dataclass(frozen=True)
class Say:
    words: str                  # may use {alias} and {picked}: what play_music said it picked


@dataclass(frozen=True)
class Scenario:
    id: str
    about: str                  # one line: what this covers
    turns: tuple                # the owner's messages, in order
    checks: tuple
    script: tuple               # per turn, the steps a well-behaved model takes (offline only)
    tags: tuple = ()
    seed: str = ''              # the real failure this guards against, if any
    harmless: bool = False      # lights and music only: may change the house when the owner runs --live-writes,
                                # and is undone right after its checks. Everything else is always a dry run.
    given: tuple = ()           # recipes the scenario starts with: (name, steps), steps as {'tool', 'args'} with {alias}

    def __post_init__(self):
        assert len(self.script) == len(self.turns), self.id


# "Sun room music": the kitchen speaker joins the living room's, both at 15%, then a calm pick for the group.
SUN_ROOM = (
    {'tool': 'play_music', 'args': {'speaker': '{living_room_speaker}', 'action': 'group', 'with_speakers': ['{kitchen_speaker}']}},
    {'tool': 'play_music', 'args': {'speaker': '{living_room_speaker}', 'action': 'volume', 'volume_percent': 15}},
    {'tool': 'play_music', 'args': {'speaker': '{kitchen_speaker}', 'action': 'volume', 'volume_percent': 15}},
    {'tool': 'play_music', 'args': {'speaker': '{living_room_speaker}', 'action': 'play_media', 'what': 'calm piano',
                                    'group_too': True}},
)

SCENARIOS = (
    Scenario(
        'music-softly-in-one-room',
        'A mood, a room and “softly”: a real pick, only that speaker, quiet before it starts, and it says what.',
        ('Softly play some music in the {living_room} while I read a bedtime story',),
        (c.Called('play_music', speaker='{living_room}', action='play_media'),
         c.ShortSearch(), c.ConcretePick(), c.QuietFirst(20), c.SaysPick(),
         c.ChangesOnly(devices=('living_room_speaker',),
                       services={'media_player/volume_set', 'music_assistant/play_media', 'media_player/unjoin'}),
         c.NotCalled('edit_playlist', 'kitchen', 'home_control'), c.PlainReply()),
        ((Use('music_now'), Use('play_music', speaker='{living_room_speaker}', action='play_media', what='calm piano',
                                soft=True),
          Say('Playing {picked} softly on the {living_room_speaker}.')),),
        harmless=True, tags=('music',), seed='Ask passed the whole sentence to the speaker as a search, and it played nothing or noise.'),
    Scenario(
        'music-as-well-joins',
        '“Play it in the kitchen as well” joins the kitchen to what is playing; it doesn’t start a second stream.',
        ('Play some calm piano in the {living_room}', 'play it in the {kitchen} as well'),
        (c.Called('play_music', turn=0, action='play_media'), c.ConcretePick(),
         c.Joined('living_room_speaker', 'kitchen_speaker', turn=1),
         c.ChangesOnly(devices=('living_room_speaker', 'kitchen_speaker'), turn=1,
                       services={'media_player/join', 'media_player/volume_set'}),
         c.NotCalled('edit_playlist', 'kitchen'), c.PlainReply()),
        ((Use('music_now'), Use('play_music', speaker='{living_room_speaker}', action='play_media', what='calm piano'),
          Say('Playing {picked} on the {living_room_speaker}.')),
         (Use('music_now'), Use('play_music', speaker='{living_room_speaker}', action='group',
                                with_speakers=['{kitchen_speaker}']),
          Say('The {kitchen_speaker} is playing the same music now.'))),
        harmless=True, tags=('music',), seed='“As well” started a second, separate stream in the kitchen.'),
    Scenario(
        'music-card-follows-talk',
        'Play in one room, then “add the kitchen”, then “a bit louder”: one card, and it shows the live group each time.',
        ('Play some calm piano in the {living_room}', 'add the {kitchen}', 'a bit louder'),
        (c.Called('play_music', turn=0, action='play_media'), c.ConcretePick(),
         c.Joined('living_room_speaker', 'kitchen_speaker', turn=1),
         c.ChangesOnly(devices=('living_room_speaker', 'kitchen_speaker'), turn=2, services={'media_player/volume_set'}),
         c.NotCalled('edit_playlist', 'kitchen'),
         c.CardShows('living_room_speaker', 'kitchen_speaker', above=15), c.PlainReply()),
        ((Use('music_now'), Use('play_music', speaker='{living_room_speaker}', action='play_media', what='calm piano'),
          Say('Playing {picked} on the {living_room_speaker}.')),
         (Use('music_now'), Use('play_music', speaker='{living_room_speaker}', action='group',
                                with_speakers=['{kitchen_speaker}']),
          Say('The {kitchen_speaker} is playing the same music now.')),
         (Use('music_now'), Use('play_music', speaker='{living_room_speaker}', action='volume', volume_percent=25),
          Use('play_music', speaker='{kitchen_speaker}', action='volume', volume_percent=25),
          Say('Both rooms are a little louder now, at 25%.'))),
        tags=('music',)),
    Scenario(
        'remember-is-a-note-not-a-playlist',
        '“Remember …” proposes a note in the owner’s words for their tap; never a playlist, never a change.',
        ('remember the {kitchen} and {living_room} are one room',),
        (c.Called('remember'), c.Proposed('note', '{kitchen}', '{living_room}'),
         c.NotCalled('edit_playlist', 'play_music', 'home_control'), c.NoChanges(),
         c.ReplyNot('playlist'), c.ReplySays('save'), c.PlainReply()),
        ((Use('remember', note='The {kitchen} and {living_room} are one room',
              speakers=['{kitchen_speaker}', '{living_room_speaker}']),
          Say('Nothing is saved yet: tap Save on the Notes page to keep “the {kitchen} and {living_room} are one room”.')),),
        tags=('memory',), seed='A “remember …” sentence was turned into a new playlist with that name.'),
    Scenario(
        'recipe-is-proposed-not-run',
        'Saving a routine proposes a recipe of allowed steps under the owner’s name for it; nothing runs or is saved.',
        ('save this as sun room music: the {kitchen_speaker} plays with the {living_room_speaker}, both at 15%, '
         'something calm',),
        (c.Called('propose_recipe', name='sun room music'), c.Proposed('recipe', 'sun room music'), c.NoChanges(),
         c.NotCalled('play_music', 'home_control', 'run_recipe', 'edit_playlist'), c.ReplySays('save'), c.PlainReply()),
        ((Use('propose_recipe', name='sun room music', steps=SUN_ROOM),
          Say('Nothing is saved yet: tap Save on the Recipes page to keep “sun room music”.')),),
        tags=('recipes',)),
    Scenario(
        'recipe-runs-by-name',
        'Naming a saved recipe runs its steps through the usual controls: the two speakers together, quiet, a real pick.',
        ('sun room music please',),
        (c.Called('run_recipe', name='sun room music'), c.Changed('media_player/join', device='living_room_speaker'),
         c.Changed('media_player/volume_set', device='kitchen_speaker', volume_level=0.15), c.ConcretePick(),
         c.QuietFirst(20), c.ChangesOnly(devices=('living_room_speaker', 'kitchen_speaker'),
                                         services={'media_player/join', 'media_player/volume_set',
                                                   'music_assistant/play_media'}),
         c.NotCalled('propose_recipe', 'edit_playlist', 'kitchen'), c.PlainReply()),
        ((Use('run_recipe', name='sun room music'), Say('Sun room music is on: both speakers at 15%, playing something calm.')),),
        tags=('recipes',), given=(('sun room music', SUN_ROOM),)),
    Scenario(
        'lights-room-off',
        'Turning a room’s lights off touches every light in that room, even one named like the room, and nothing else.',
        ('turn off the lights in the {living_room}',),
        (c.Called('home_control', device='{living_room}', action='off'),
         c.Changed('light/turn_off', room='living_room'),
         c.ChangesOnly(rooms=('living_room',), services={'light/turn_off'}),
         c.NotCalled('play_music', 'edit_playlist', 'kitchen'), c.PlainReply()),
        ((Use('home_status'), Use('home_control', device='{living_room}', action='off'),
          Say('The {living_room} lights are off.')),),
        harmless=True, tags=('lights',),
        seed='A light named like its room was taken for the whole room, so only that one light went off.'),
    Scenario(
        'lights-dim-one-lamp',
        'Dimming one lamp sets that lamp’s brightness and nothing else.',
        ('dim the {reading_lamp} to 30%',),
        (c.Called('home_control', device='{reading_lamp}', action='brightness', brightness_percent=30),
         c.Changed('light/turn_on', device='reading_lamp', brightness_pct=30),
         c.ChangesOnly(devices=('reading_lamp',), services={'light/turn_on'}),
         c.NotCalled('play_music', 'edit_playlist', 'kitchen'), c.PlainReply()),
        ((Use('home_control', device='{reading_lamp}', action='brightness', brightness_percent=30),
          Say('The {reading_lamp} is at 30%.')),),
        harmless=True, tags=('lights',)),
    Scenario(
        'what-needs-me-today',
        '“What needs me today?” reads Today’s list and changes nothing.',
        ('what needs me today?',),
        (c.Called('today_list'), c.ReadOnly(), c.PlainReply()),
        ((Use('today_list'), Say('A few things want you today; they are on the Today page.')),),
        tags=('today', 'read')),
    Scenario(
        'calendar-free-tomorrow',
        'A calendar question reads the calendar and changes nothing.',
        ('am I free tomorrow afternoon?',),
        (c.Called('calendar_week'), c.ReadOnly(), c.PlainReply()),
        ((Use('calendar_week'), Say('Here is what is on tomorrow afternoon.')),),
        tags=('calendar', 'read')),
    Scenario(
        'grocery-goes-to-kitchen',
        'A grocery ask goes to Kitchen once, in the owner’s own words, and Hub changes nothing itself.',
        ('add oat milk to the shopping list',),
        (c.HandedOff('kitchen'), c.NotCalled(*c.CONTROLS), c.ReplySays('On it')),
        ((Use('kitchen'), Say('On it.')),),
        tags=('kitchen',)),
    Scenario(
        'gmail-filing-is-not-here',
        'Asked to file mail, Ask says it can’t yet and points to the Gmail page; nothing changes.',
        ('file all the receipts in my inbox',),
        (c.ReadOnly(), c.ReplySays('can’t', "can't", 'cannot'), c.ReplySays('Gmail'), c.PlainReply()),
        ((Say('This chat can’t file mail yet. You can file sorted mail on the Gmail page.'),),),
        tags=('gmail',)),
)


def find(scenario_id):
    return next(s for s in SCENARIOS if s.id == scenario_id)
