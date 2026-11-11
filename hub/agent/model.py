"""The chat's model. Built here and nowhere else, from Luna's pinned route.

The route is `luna.URL`, `luna.MODEL` and `luna.PROVIDER`: one provider, zero data retention, no data
collection, no fallback. The guard below looks at every request on its way out and refuses any that
differs, so no setting, library update or mistake elsewhere can send chat text another way.
Nothing here logs a prompt, a reply or the key.
"""
import json

import httpx2 as httpx
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from .. import luna

MAX_OUTPUT_TOKENS = 900
TIMEOUT = 60


class NotSetUp(Exception):
    pass


class WrongRoute(Exception):
    """A request was about to leave on something other than the pinned route. It was not sent."""


async def guard(request: httpx.Request):
    try:
        body = json.loads(request.content)
    except ValueError:
        body = {}
    if (str(request.url) != luna.URL or request.method != 'POST' or body.get('model') != luna.MODEL
            or body.get('provider') != luna.PROVIDER or body.get('models')):
        raise WrongRoute('Ask Hub refused a request that was not on the pinned model route.')


def client(transport=None):
    """The only HTTP client the chat's model may use. `transport` is for tests; the guard stays on either way."""
    return httpx.AsyncClient(follow_redirects=False, timeout=TIMEOUT, event_hooks={'request': [guard]}, transport=transport)


def build(http_client=None):
    """The model for one chat run. Raises NotSetUp without the key."""
    token = luna.key()
    if not token:
        raise NotSetUp('The model key isn’t set up.')
    provider = OpenRouterProvider(api_key=token, app_title='Hub', http_client=http_client or client())
    return OpenRouterModel(luna.MODEL, provider=provider, settings=OpenRouterModelSettings(
        openrouter_provider=luna.PROVIDER, openrouter_reasoning={'effort': 'none'}, openrouter_usage={'include': True},
        max_tokens=MAX_OUTPUT_TOKENS))
