"""A made-up household for offline golden runs: a fake Home Assistant behind the broker's socket door, a fake
Music Assistant behind its API door, a fake Kitchen, and the made-up mail, calendar and Today list from the
Ask tests. Everything Hub reads comes through the same doors a live run uses, so read.py, control.py,
music.py and the broker's own checks all run for real. Writes should never arrive here: the dry-run layer
stops them first, and `sent` proves it.
"""
import copy
import json

from hub import config
from hub.tools.house import broker, control, music
from hub.tools.kitchen import client

AREAS = [{'area_id': 'living', 'name': 'Living Room'}, {'area_id': 'kitchen', 'name': 'Kitchen'},
         {'area_id': 'bedroom', 'name': 'Bedroom'}]
ENTITIES = [
    ('light.reading_lamp', 'living', 'Reading Lamp', 'on', {'brightness': 200}),
    ('light.living_ceiling', 'living', 'Ceiling Light', 'on', {'brightness': 255}),
    ('light.living_room', 'living', 'Living room', 'on', {'brightness': 120}),     # a light named like its room
    ('light.kitchen_pendant', 'kitchen', 'Kitchen Pendant', 'on', {'brightness': 180}),
    ('light.bedside', 'bedroom', 'Bedside Lamp', 'off', {}),
    ('switch.coffee', 'kitchen', 'Coffee Maker', 'off', {}),
    ('scene.movie_night', 'living', 'Movie Night', 'scening', {}),
    ('media_player.living_room', 'living', 'Living Room Speaker', 'idle', {'volume_level': 0.4}),
    ('media_player.kitchen', 'kitchen', 'Kitchen Speaker', 'idle', {'volume_level': 0.5}),
    ('media_player.bedroom', 'bedroom', 'Bedroom Speaker', 'idle', {'volume_level': 0.3}),
]
PLAYLISTS = {'7': ('Calm Dinner', 'builtin'), '8': ('Road Trip', 'apple_music')}
CATALOG = {'playlists': [('Calm Piano Evening', 'apple_music://playlist/p1'), ('Gentle Piano Lullabies', 'apple_music://playlist/p2')],
           'albums': [('Quiet Hours', 'apple_music://album/a1')],
           'tracks': [('Morning Light', 'apple_music://track/1'), ('Slow River', 'apple_music://track/2')]}


class World:
    def __init__(self):
        self.sent = []          # every write that reached a fake service: must stay empty in a dry run

    # Home Assistant, as the broker answers it
    def exchange(self, message, timeout):
        if message['op'] == 'request':
            self.sent.append(('home', message))
            return []
        answers = {
            'config/area_registry/list': AREAS,
            'config/device_registry/list': [],
            'config/label_registry/list': [],
            'config/entity_registry/list': [{'entity_id': e, 'area_id': a, 'name': n,
                                             'platform': 'music_assistant' if e.startswith('media_player.') else 'demo'}
                                            for e, a, n, _, _ in ENTITIES],
            'get_states': [{'entity_id': e, 'state': s, 'attributes': {'friendly_name': n, **copy.deepcopy(attrs),
                                                                       **({'group_members': [e]} if e.startswith('media_player.') else {})}}
                           for e, _, n, s, attrs in ENTITIES],
        }
        return [answers[c['type']] for c in message['commands']]

    # Music Assistant's API
    def music(self, command, args, timeout):
        if command in music.WRITES:
            self.sent.append(('music', command, args))
            return {'item_id': '99'}
        if command == 'music/playlists/library_items':
            wanted = str(args.get('search', '')).lower()
            return [{'item_id': pid, 'name': name, 'is_editable': True, 'uri': f'library://playlist/{pid}',
                     'provider_mappings': [{'provider_domain': where}]}
                    for pid, (name, where) in PLAYLISTS.items() if wanted in name.lower()]
        if command == 'music/search':
            words = args['search_query'].lower().split()
            return {kind: [{'name': n, 'uri': u, 'artists': [{'name': 'The Example Band'}]}
                           for n, u in items if all(w in n.lower() for w in words)] for kind, items in CATALOG.items()}
        if command == 'music/recently_played_items':
            return [{'name': 'Quiet Hours', 'media_type': 'album'}]
        if command == 'music/playlists/playlist_tracks':
            return []
        return []

    def kitchen(self, method, path, body=None, timeout=10):
        if method != 'GET':
            self.sent.append(('kitchen', method, path, body))
            return 200, {'id': 1}
        return 200, {'messages': []}


def install(monkeypatch):
    """Put the made-up household behind Hub's doors. Returns the World."""
    from tests.test_ask import fictional
    world = World()
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'home': {'music_url': 'https://music.example:8095'}}))
    monkeypatch.setattr(broker, '_exchange', world.exchange)
    monkeypatch.setattr(music, '_send', world.music)
    monkeypatch.setattr(client, 'call', world.kitchen)
    monkeypatch.setattr(control, '_recent', type(control._recent)())
    fictional(monkeypatch)
    return world
