"""The app on a made-up house, for trying it by hand, for screenshots and for the browser check. Never for real use.

Run from the repository root:  .venv/bin/python web/app/check/demo_server.py
The real app and the real change code run against a fictional house that follows what it is sent
(tests/app_world.py): tap a room and the next reading shows it. It uses a throwaway data folder, calls no
model, looks in no key store and reaches no other machine and no service on this one (web/check_guard.py).
Every room, name and song here is invented.

Ask works here too, on a stand-in for its model (tests/ask_script.py) that picks a tool from a few plain words:
"dim the kitchen", "everything off", "some jazz in the bedroom, softly", "play it in the dining room too",
"pause", "movie night", "remember to start music softly", "what should we cook this week?", "what needs me today?".
The stand-in is only a script; how well Ask understands is the real model's job and can't be judged here.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

scratch = tempfile.mkdtemp(prefix='hub-app-check-')
os.environ.update(HUB_DATA_DIR=scratch, HUB_BACKGROUND_JOBS='disabled', HUB_BRIDGE_URL='http://127.0.0.1:9',
                  HUB_BRIDGE_TOKEN_FILE=str(Path(scratch) / 'absent'), HUB_KITCHEN_SOCKET=str(Path(scratch) / 'absent.sock'),
                  HUB_HA_BROKER_SOCKET=str(Path(scratch) / 'absent-broker.sock'), HUB_ALLOWED_LOGINS='me@example.com',
                  PYDANTIC_AI_NO_BANNER='1')
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import check_guard  # noqa: E402
import pytest  # noqa: E402

patch = pytest.MonkeyPatch()
check_guard.apply(patch)   # before the app exists: no key store, no other machine, no local service

import uvicorn  # noqa: E402

from hub.app import levels, routes, scenes  # noqa: E402
from hub.app.ask import private, route, turns  # noqa: E402
from hub.main import app  # noqa: E402
from hub.tools.house import control, read  # noqa: E402
from hub.tools.kitchen import client as kitchen  # noqa: E402
from tests import app_world, ask_script  # noqa: E402

(Path(scratch) / 'household.json').write_text(json.dumps({'app': {'front': True}}))
world = app_world.install(patch)
patch.setattr(control, 'CHECK_WAIT', 0.2)

# A made-up library, so "Play something else" and Ask have something to offer.
ask_script.library(patch, world)
routes._picks.update(at=float('-inf'), found=[])

# Ask, without a model: the stand-in picks the tool; the private desk and Kitchen answer with made-up words.
patch.setattr(route, 'build', lambda http_client=None: ask_script.MODEL)
patch.setattr(route, 'ready', lambda: None)
patch.setattr(private, 'answer', lambda text, topics=None: (
    'Two things need you today. The plumber comes at ten, and Robin’s class trip form is due on Friday.',
    {'tokens_in': 0, 'tokens_out': 0, 'cost': 0.0}))
KITCHEN_TAKES = 4.0     # seconds the made-up Kitchen takes to answer
KITCHEN_SAYS = {'role': 'assistant', 'text': 'Three dinners you don’t usually make. Tell me which and I’ll add what you’re missing to the cart.',
                'recipes': [{'title': 'Miso-glazed eggplant with sesame rice', 'total': '40 minutes'},
                            {'title': 'Chicken with preserved lemon and olives', 'total': '55 minutes'},
                            {'title': 'Black bean and squash tacos', 'total': '30 minutes'}]}
asked_kitchen = []      # when each message was passed on; message n is its 1-based place, doubled


def made_up_kitchen(method, path, body=None, timeout=10):
    """Stands where the kitchen service would: takes a message, and a few seconds later has an answer to it."""
    if (method, path) == ('POST', '/messages'):
        asked_kitchen.append(time.monotonic())
        return 200, {'id': len(asked_kitchen) * 2 - 1}
    if method == 'GET' and path.startswith('/messages?after='):
        after, now, messages = int(path.rsplit('=', 1)[1]), time.monotonic(), []
        for n, at in enumerate(asked_kitchen, 1):
            if n * 2 - 1 > after:
                messages.append({'id': n * 2 - 1, 'role': 'member', 'text': '', 'author': 'me'})
            if n * 2 > after and now - at >= KITCHEN_TAKES:
                messages.append(dict(KITCHEN_SAYS, id=n * 2))
        return 200, {'messages': messages, 'busy': any(now - at < KITCHEN_TAKES for at in asked_kitchen), 'plans': {}, 'orders': {}}
    raise kitchen.KitchenDown('made up')


patch.setattr(kitchen, 'call', made_up_kitchen)


def a_habit():
    """Two earlier evenings on which the same thing was asked at about this time, so Ask offers it again."""
    for days in (1, 2):
        turns.save('me@example.com', 'Dim the living room to 30', 'The living room is at 30%.', [{'kind': 'rooms', 'rooms': ['living-room']}],
                   at=time.time() - days * 86400)


a_habit()


# For the browser check only: put the made-up house back as it started, make it stop answering, or take Ask
# away as on a Hub where it isn't set up yet.
@app.post('/demo/{what}')
def stage(what: str):
    if what == 'reset':
        world.states, world.down = app_world._states(), False
        world.sent.clear()
        control._taps.clear()
        control._recent.clear()
        levels.forget()
        asked_kitchen.clear()
        for scene in scenes.kept():
            scenes.forget(scene['id'])
        for note in turns.remembered():
            turns.forget(note['id'])
        with turns._db() as conn:
            conn.execute('delete from app_ask')
        a_habit()
    elif what in ('down', 'up'):
        world.down = what == 'down'
    elif what in ('ask-away', 'ask-back'):
        patch.setattr(route, 'ready', (lambda: 'Ask isn’t set up yet: its key isn’t in 1Password.') if what == 'ask-away' else (lambda: None))
    else:
        return {'ok': False}
    read.clear_cache()
    routes.feed.poke()
    return {'ok': True}


class SignedIn:
    """Every request counts as the owner's phone, so taps work. The demo has only a made-up house to change."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and not any(name == b'tailscale-user-login' for name, _ in scope['headers']):
            scope = dict(scope, headers=[*scope['headers'], (b'tailscale-user-login', b'me@example.com')])
        await self.inner(scope, receive, send)


demo = SignedIn(app)

if __name__ == '__main__':
    # APP_CHECK_HOST: another address to listen on, such as this machine's private tailnet address, to try the
    # demo from a phone. It has no sign-in of its own: anyone who can reach that address can tap the made-up house.
    host, port = os.environ.get('APP_CHECK_HOST', '127.0.0.1'), int(os.environ.get('APP_CHECK_PORT', '8912'))
    uvicorn.run(demo, host=host, port=port, log_level='warning')
