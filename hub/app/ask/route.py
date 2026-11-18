"""The model Ask uses for the house, and the one way it is reached.

Owner decision (2026-11-14): Ask runs on Claude Sonnet through the owner's own Anthropic API key, at high
reasoning. What goes there: what the owner typed, the state of the lights and speakers, what Hub remembers, and
(owner decision, 2026-11-15) the bounded mail, calendar, school and today lookups in `private.py`, and (owner
decision, 2026-11-17) a search of the person's own mail and up to three emails read in full (`mailbox.py`). What
never does: Kitchen's replies, and anything from a sender with AI switched off. The key is read from 1Password by reference and is never logged or returned.
The family archive (owner's decision, 2026-11-16): what `search` and `read` in `tools.py` find, mail and calendar
results and masked passages of documents and ChatGPT history, unless the owner switched those off; never anything
about a photo.

The request guard below looks at every request on its way out and refuses any that isn't this model at this
address, so no setting, library update or mistake elsewhere can send Ask's words another way.
"""
import asyncio
import json
import os
import threading
import time

import httpx2 as httpx

from ... import household, model_key

MODEL = 'claude-sonnet-5-5'
HOST = 'api.anthropic.com'
PATH = '/v1/messages'
REFERENCE = os.environ.get('HUB_ANTHROPIC_REF', 'op://Hub/Anthropic/credential')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
EFFORT = 'high'             # the owner's choice; "ask": {"effort": "medium"} in household.json changes it
MAX_OUTPUT_TOKENS = 6000    # thinking and the answer together; answers themselves are a sentence or two
TIMEOUT = 60
KEY_SECONDS = 300           # the key is kept in memory this long, so a request doesn't wait on 1Password
MISSING_SECONDS = 60        # and a missing key is asked for again this often
_key, _lock = {'value': None, 'at': float('-inf')}, threading.Lock()
_built = {}                 # the model build() made last, kept so each request doesn't open a new connection
# Dollars per million tokens (Claude Sonnet 5.5): plain input, output, input read from the cache, input written to it.
PRICE = {'in': 2.0, 'out': 10.0, 'cache_read': 0.2, 'cache_write': 2.5}


class NotSetUp(Exception):
    """Ask can't run yet. The message is safe to show."""


class WrongRoute(Exception):
    """A request was about to leave for somewhere other than Ask's one model. It was not sent."""


def key():
    """Ask's key, or None. Held in memory only; never logged, returned to a page or written anywhere."""
    with _lock:
        if time.monotonic() - _key['at'] < (KEY_SECONDS if _key['value'] else MISSING_SECONDS):
            return _key['value']
    value = model_key.secret(REFERENCE)
    with _lock:
        _key.update(value=value, at=time.monotonic())
    return value


def effort():
    try:
        chosen = household.section('ask').get('effort')
    except ValueError:
        chosen = None
    return chosen if chosen in EFFORTS else EFFORT


async def guard(request: httpx.Request):
    try:
        body = json.loads(request.content)
    except ValueError:
        body = {}
    if request.url.scheme != 'https' or request.url.host != HOST or request.url.path != PATH or request.method != 'POST' \
            or body.get('model') != MODEL:
        raise WrongRoute('Ask refused a request that was not on its one model route.')


def client(transport=None, timeout=TIMEOUT):
    """The only HTTP client Ask's model may use (and the career desk's worker, hub/app/career/worker.py, which
    takes longer). `transport` is for tests; the guard stays on either way."""
    return httpx.AsyncClient(follow_redirects=False, timeout=timeout, event_hooks={'request': [guard]}, transport=transport)


def ready():
    """Why Ask can't run, in plain words, or None when it can."""
    try:        # the SDK is an extra: a Hub without it simply has no Ask yet. Asking here also has it loaded in time.
        import pydantic_ai.models.anthropic  # noqa: F401
    except ImportError:
        return 'Ask isn’t part of this version of Hub yet.'
    if not key():
        return 'Ask isn’t set up yet: its key isn’t in 1Password.'
    return None


def build(http_client=None):
    """The model for one turn. Raises NotSetUp."""
    problem = ready()
    if problem:
        raise NotSetUp(problem)
    from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
    from pydantic_ai.providers.anthropic import AnthropicProvider
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    made_with = (key(), effort(), loop)         # a new key, another effort or another loop makes a new one
    if http_client is None and _built.get('with') == made_with:
        return _built['model']
    provider = AnthropicProvider(api_key=key(), http_client=http_client or client())
    model = AnthropicModel(MODEL, provider=provider, settings=AnthropicModelSettings(
        max_tokens=MAX_OUTPUT_TOKENS, anthropic_thinking={'type': 'adaptive'}, anthropic_effort=effort(),
        anthropic_cache_instructions=True, anthropic_cache_tool_definitions=True))
    if http_client is None:
        _built.update({'with': made_with, 'model': model})
    return model


def cost(usage):
    """Dollars for one turn, from the token counts the model reported."""
    read = getattr(usage, 'cache_read_tokens', 0) or 0
    written = getattr(usage, 'cache_write_tokens', 0) or 0
    plain = max(0, (getattr(usage, 'input_tokens', 0) or 0) - read - written)
    out = getattr(usage, 'output_tokens', 0) or 0
    return (plain * PRICE['in'] + out * PRICE['out'] + read * PRICE['cache_read'] + written * PRICE['cache_write']) / 1_000_000
