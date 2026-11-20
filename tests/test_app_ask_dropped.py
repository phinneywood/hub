"""A phone that loses the connection while Hub answers (a locked screen, a weak signal): the turn carries on and is
kept, so the app can read the answer back and the next request has it as context."""
import asyncio

from starlette.requests import Request

from hub.app import routes
from hub.app.ask import brain

LOGIN = 'me@example.com'


def request():
    return Request({'type': 'http', 'method': 'POST', 'path': '/app/api/ask', 'query_string': b'',
                    'headers': [(b'tailscale-user-login', LOGIN.encode()), (b'accept', b'text/event-stream')]})


def test_a_turn_finishes_after_the_phone_drops_the_connection_and_a_new_request_waits_for_it(monkeypatch):
    finished = []

    async def run(text, login, build=None):
        yield {'t': 'say', 'text': 'Finding some jazz'}
        await asyncio.sleep(0.05)               # the model and the speakers still at work when the phone goes
        finished.append((text, login))          # where the turn is kept
        yield {'t': 'done', 'id': 7, 'ms': 50, 'status': 'ok'}

    monkeypatch.setattr(brain, 'run', run)

    async def scenario():
        response = await routes.ask(request(), {'text': 'play some jazz in the kitchen'})
        stream = response.body_iterator
        first = await stream.__anext__()
        await stream.aclose()                   # the phone went away after the first words
        waits = (await routes.ask(request(), {'text': 'keep it going'})).status_code
        for _ in range(100):                    # the turn finishes on its own, with nobody listening
            if LOGIN not in routes._asking:
                break
            await asyncio.sleep(0.01)
        return first, waits

    first, waits = asyncio.run(scenario())
    assert '"Finding some jazz"' in first
    assert waits == 409                         # the next request waits for this one rather than starting without it
    assert finished == [('play some jazz in the kitchen', LOGIN)]
    assert LOGIN not in routes._asking


def test_a_turn_that_breaks_still_frees_the_person_to_ask_again(monkeypatch):
    async def run(text, login, build=None):
        yield {'t': 'say', 'text': 'Looking'}
        raise RuntimeError('a tool fell over')

    monkeypatch.setattr(brain, 'run', run)

    async def scenario():
        response = await routes.ask(request(), {'text': 'what is on tomorrow'})
        return [chunk async for chunk in response.body_iterator]

    events = asyncio.run(scenario())
    assert len(events) == 1 and '"Looking"' in events[0]       # no "done": the app reads the turn back, then says so
    assert LOGIN not in routes._asking
