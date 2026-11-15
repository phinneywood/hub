"""The app on a made-up house, for trying it by hand, for screenshots and for the browser check. Never for real use.

Run from the repository root:  .venv/bin/python web/app/check/demo_server.py
The real app and the real change code run against a fictional house that follows what it is sent
(tests/app_world.py): tap a room and the next reading shows it. It uses a throwaway data folder, calls no
model, looks in no key store and reaches no other machine and no service on this one (web/check_guard.py).
Every room, name and song here is invented.
"""
import json
import os
import sys
import tempfile
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

from hub.app import routes  # noqa: E402
from hub.main import app  # noqa: E402
from hub.tools.house import control, music, read  # noqa: E402
from tests import app_world  # noqa: E402

(Path(scratch) / 'household.json').write_text(json.dumps({'app': {'front': True}}))
world = app_world.install(patch)
patch.setattr(control, 'CHECK_WAIT', 0.2)

# A made-up library, so "Play something else" has something to offer.
LIBRARY = ['Evening Jazz', 'Kitchen Dance Party', 'Sunday Morning', 'Quiet Piano', 'Rainy Day', 'Songs for the Car']
world.names = {f'library://playlist/{n}': name for n, name in enumerate(LIBRARY, 1)}
patch.setattr(music, 'ready', lambda: True)
patch.setattr(music, 'playlists', lambda search=None: [
    {'id': uri.rsplit('/', 1)[1], 'name': name, 'where': 'music', 'editable': True, 'uri': uri} for uri, name in world.names.items()])
routes._picks.update(at=float('-inf'), found=[])


# For the browser check only: put the made-up house back as it started, or make it stop answering.
@app.post('/demo/{what}')
def stage(what: str):
    if what == 'reset':
        world.states, world.down = app_world._states(), False
        world.sent.clear()
        control._taps.clear()
    elif what in ('down', 'up'):
        world.down = what == 'down'
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
