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
                  HUB_ALLOWED_LOGINS='', PYDANTIC_AI_NO_BANNER='1')
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


async def paced(messages, info):
    async for piece in test_ask.scripted(messages, info):
        await asyncio.sleep(0.03 if isinstance(piece, str) else 0.3)
        yield piece


patch.setattr(model, 'build', lambda http_client=None: FunctionModel(stream_function=paced, model_name='scripted'))

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('ASK_CHECK_PORT', '8792')), log_level='warning')
