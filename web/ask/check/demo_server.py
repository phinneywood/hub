"""Hub with the Ask page on made-up data and a scripted model, for the browser check. Never for real use.

Run from the repository root:  .venv/bin/python web/ask/check/demo_server.py
It uses a throwaway data folder, points every connector at nothing, calls no model, needs no key and
looks in no key store (web/check_guard.py).
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

scratch = tempfile.mkdtemp(prefix='hub-ask-check-')
os.environ.update(HUB_DATA_DIR=scratch, HUB_BACKGROUND_JOBS='disabled', HUB_BRIDGE_URL='http://127.0.0.1:9',
                  HUB_BRIDGE_TOKEN_FILE=str(Path(scratch) / 'absent'), HUB_KITCHEN_SOCKET=str(Path(scratch) / 'absent.sock'),
                  HUB_ALLOWED_LOGINS='me@example.com', PYDANTIC_AI_NO_BANNER='1')
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import check_guard  # noqa: E402
import pytest  # noqa: E402

patch = pytest.MonkeyPatch()
check_guard.apply(patch)   # before the app exists: no key store, no other machine

import uvicorn  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402

from hub import luna  # noqa: E402
from hub.agent import model  # noqa: E402
from hub.main import app  # noqa: E402
from tests import test_ask  # noqa: E402

test_ask.fictional(patch)
patch.setattr(luna, 'key', lambda: None if os.environ.get('ASK_CHECK_NO_KEY') else 'synthetic-key')


async def remembering(messages, info):
    """“Remember …” proposes a note (its card has Save and Not now); everything else is the Ask tests' script."""
    import json
    from pydantic_ai.models.function import DeltaToolCall
    text, results = test_ask._turn(messages)
    if 'remember' in text and 'remember' not in results:
        note = text.removeprefix('remember').strip(' .')
        yield {0: DeltaToolCall(name='remember', json_args=json.dumps({'note': note}), tool_call_id='call_remember')}
    elif 'remember' in results:
        for piece in test_ask._say('Nothing is saved yet. Tap Save on the card to keep it.'):
            yield piece
    else:
        async for piece in test_ask.scripted(messages, info):
            yield piece


# Music for the Now playing card: a made-up house (tests/golden_world.py) whose speakers follow what Hub sends,
# through the golden runs' own overlay. Never a real speaker.
from hub.golden import dryrun  # noqa: E402
from hub.tools.house import broker, control, read  # noqa: E402
from tests import golden_world  # noqa: E402

world, house = golden_world.install(patch), {'ledger': dryrun.Ledger()}
reads = broker._exchange


def speakers(message, timeout):
    if message['op'] == 'request':
        domain, service = message['path'].removeprefix('/api/services/').split('/')
        house['ledger']._apply(domain, service, message['body'])
        return []
    results = reads(message, timeout)
    types = [c['type'] for c in message['commands']]
    if 'get_states' in types:
        results[types.index('get_states')] = house['ledger'].laid_over(results[types.index('get_states')])
    return results


patch.setattr(broker, '_exchange', speakers)
patch.setattr(control, 'CHECK_WAIT', 0.2)
ids = __import__('itertools').count()
MUSIC = (   # (words in the message, the play_music calls a model would make, in order)
    ('play calm piano in the living room', [dict(speaker='Living Room Speaker', action='play_media', what='calm piano')]),
    ('add the kitchen', [dict(speaker='Living Room Speaker', action='group', with_speakers=['Kitchen Speaker'])]),
    ('a bit louder', [dict(speaker='Living Room Speaker', action='volume', volume_percent=25),
                      dict(speaker='Kitchen Speaker', action='volume', volume_percent=25)]),
    ('play morning light in the bedroom', [dict(speaker='Bedroom Speaker', action='play_media', what='morning light',
                                                group_too=False)]),
)


async def playing(messages, info):
    import json
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall
    text, _ = test_ask._turn(messages)
    calls = next((c for words, c in MUSIC if words in text), None)
    if calls is None:
        async for piece in remembering(messages, info):
            yield piece
        return
    done = 0
    for message in messages:
        if isinstance(message, ModelRequest):
            done = 0 if any(type(p).__name__ == 'UserPromptPart' for p in message.parts) else \
                done + sum(isinstance(p, ToolReturnPart) for p in message.parts)
    if done == 0 and calls is MUSIC[0][1]:       # each browser run starts from a quiet house
        house['ledger'] = dryrun.Ledger()
        control._recent.clear()
        read.clear_cache()
    if done < len(calls):
        yield {0: DeltaToolCall(name='play_music', json_args=json.dumps(calls[done]), tool_call_id=f'call_music_{next(ids)}')}
    else:
        for piece in test_ask._say('Done. The Now playing card shows the speakers as they are now.'):
            yield piece


async def paced(messages, info):
    async for piece in playing(messages, info):
        await asyncio.sleep(0.03 if isinstance(piece, str) else 0.3)
        yield piece


patch.setattr(model, 'build', lambda http_client=None: FunctionModel(stream_function=paced, model_name='scripted'))

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('ASK_CHECK_PORT', '8792')), log_level='warning')
