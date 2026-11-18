"""The door: Home Assistant (and Music Assistant inside it) shown in Hub with Hub's sign-in. A made-up Home
Assistant stands behind it. The door lets the listed logins through and nobody else, never passes on who
asked or a forwarded address, and Hub frames nothing but the door."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from hub import config, door
from hub.main import app
from hub.tools.house import music, read

HOST = 'home.example.test:9443'
HA = 'https://' + HOST
OWNER = {'tailscale-user-login': 'me@example.com', 'host': HOST}
SERVE = {'x-forwarded-for': '100.64.0.9', 'x-forwarded-host': HOST, 'x-forwarded-proto': 'https', 'tailscale-user-name': 'Alex'}


def settings(door_section=None, **home_section):
    found = {'home': home_section}
    if door_section is not None:
        found['door'] = door_section
    (config.DATA_DIR / 'household.json').write_text(json.dumps(found))


def set_up(**more):
    settings({'upstream': 'http://192.0.2.10:8123', 'logins': ['Me@Example.com'], **more}, ha_url=HA)


class FakeHome:
    """What Home Assistant would see and say."""

    def __init__(self):
        self.seen = []

    async def __call__(self, scope, receive, send):
        body = b''
        while True:
            message = await receive()
            body += message.get('body', b'')
            if not message.get('more_body'):
                break
        self.seen.append({'method': scope['method'], 'path': scope['path'], 'query': scope['query_string'].decode(),
                          'headers': {k.decode(): v.decode() for k, v in scope['headers']}, 'body': body})
        await send({'type': 'http.response.start', 'status': 200, 'headers': [
            (b'content-type', b'text/html'), (b'x-frame-options', b'SAMEORIGIN'), (b'set-cookie', b'ingress_session=abc; Path=/api/hassio_ingress/')]})
        await send({'type': 'http.response.body', 'body': b'<html>the frontend</html>'})


@pytest.fixture
def house(monkeypatch):
    fake = FakeHome()
    monkeypatch.setattr(door, '_client', lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=fake)))
    return fake


def ask(method, path, headers, content=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=door.app), base_url='http://door') as client:
            return await client.request(method, path, headers=headers, content=content)
    return asyncio.run(go())


# ---- who may pass ----

def test_the_door_refuses_everything_until_it_is_set_up(house):
    assert not door.configured() and door.start() is None
    set_up()
    assert door.configured() and door.start() is None           # set up, but this isn't the deployed Hub: nothing listens
    settings()
    assert ask('GET', '/', OWNER).status_code == 503
    for broken in ({'upstream': 'http://192.0.2.10:8123'}, {'upstream': 'http://192.0.2.10:8123', 'logins': []},
                   {'upstream': 'ftp://192.0.2.10', 'logins': ['me@example.com']},
                   {'upstream': 'http://192.0.2.10:8123/path', 'logins': ['me@example.com']},
                   {'upstream': 'http://user:pw@192.0.2.10:8123', 'logins': ['me@example.com']}):
        settings(broken, ha_url=HA)
        assert not door.configured(), broken
    assert house.seen == []


def test_only_a_listed_login_passes_and_a_request_with_no_login_never_does(house):
    set_up()
    assert ask('GET', '/', {**OWNER, **SERVE}).status_code == 200
    assert ask('GET', '/', {'host': HOST}).status_code == 403                       # a program on this Mac: no login at all
    assert ask('GET', '/', {**OWNER, 'tailscale-user-login': 'sam@example.com'}).status_code == 403
    assert len(house.seen) == 1


def test_being_allowed_into_hub_is_not_being_allowed_through_the_door(house, monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com', 'sam@example.com'})
    set_up()
    assert ask('GET', '/', {**OWNER, 'tailscale-user-login': 'sam@example.com'}).status_code == 403


def test_home_assistant_never_learns_who_asked_or_sees_a_forwarded_address(house):
    set_up()
    reply = ask('POST', '/api/thing?x=1&y=%2F', {**OWNER, **SERVE, 'origin': HA, 'cookie': 'ingress_session=abc',
                                               'authorization': 'Bearer browser-token', 'forwarded': 'for=100.64.0.9'}, content=b'{"a": 1}')
    assert reply.status_code == 200
    seen = house.seen[0]
    assert not [k for k in seen['headers'] if k.startswith(('x-forwarded', 'tailscale', 'forwarded'))]
    assert seen['headers']['host'] == '192.0.2.10:8123'
    assert (seen['path'], seen['query'], seen['body']) == ('/api/thing', 'x=1&y=%2F', b'{"a": 1}')
    assert seen['headers']['cookie'] == 'ingress_session=abc' and seen['headers']['authorization'] == 'Bearer browser-token'


def test_another_site_can_t_make_the_browser_change_things_or_be_handed_a_sign_in(house):
    set_up()
    assert ask('POST', '/api/services/light/turn_on', {**OWNER, 'origin': 'https://evil.example'}, content=b'{}').status_code == 403
    assert ask('POST', '/auth/login_flow', {**OWNER, 'sec-fetch-site': 'cross-site'}, content=b'{}').status_code == 403
    assert ask('GET', '/auth/authorize?client_id=https%3A%2F%2Fevil.example%2F&redirect_uri=https%3A%2F%2Fevil.example%2Fcb', OWNER).status_code == 403
    assert house.seen == []
    assert ask('GET', f'/auth/authorize?client_id=https%3A%2F%2F{HOST.replace(":", "%3A")}%2F', OWNER).status_code == 200
    assert ask('POST', '/auth/login_flow', {**OWNER, 'origin': HA}, content=b'{}').status_code == 200
    assert ask('POST', '/api/webhook/x', OWNER, content=b'{}').status_code == 200   # the phone app: no origin, the owner's login


def test_only_this_machine_s_own_name_may_frame_the_door(house):
    set_up()
    reply = ask('GET', '/', OWNER)
    assert 'x-frame-options' not in reply.headers
    assert reply.headers['content-security-policy'] == "frame-ancestors 'self' https://home.example.test:*"
    assert 'ingress_session=abc' in reply.headers['set-cookie']                     # Home Assistant's own cookies pass


def test_a_home_assistant_that_isn_t_answering_is_said_plainly(monkeypatch):
    def down(request):
        raise httpx.ConnectError('refused')
    monkeypatch.setattr(door, '_client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(down)))
    set_up()
    reply = ask('GET', '/', OWNER)
    assert reply.status_code == 502 and 'isn’t answering' in reply.text


# ---- live connections ----

def _socket(headers, origin=HA):
    scope = {'type': 'websocket', 'path': '/api/websocket', 'raw_path': b'/api/websocket', 'query_string': b'', 'subprotocols': [],
             'headers': [(k.encode(), v.encode()) for k, v in {**headers, **({'origin': origin} if origin else {})}.items()]}
    return scope


def _drive(scope, incoming):
    sent = []

    async def go():
        queue = asyncio.Queue()
        for message in incoming:
            queue.put_nowait(message)

        async def send(message):
            sent.append(message)
        await asyncio.wait_for(door.app(scope, queue.get, send), 5)
    asyncio.run(go())
    return sent


def test_a_live_connection_needs_the_login_and_the_door_s_own_origin(monkeypatch):
    import websockets.asyncio.client as ws
    monkeypatch.setattr(ws, 'connect', lambda *a, **k: pytest.fail('a refused connection must not reach Home Assistant'))
    set_up()
    connect = [{'type': 'websocket.connect'}]
    for scope in (_socket({'host': HOST}), _socket({**OWNER, 'tailscale-user-login': 'sam@example.com'}),
                  _socket(OWNER, origin='https://evil.example'), _socket({'host': HOST}, origin=None)):
        assert _drive(scope, connect) == [{'type': 'websocket.close', 'code': 1008}]


def test_a_live_connection_is_passed_both_ways_without_who_asked(monkeypatch):
    import websockets.asyncio.client as ws
    made = {}

    class Upstream:
        subprotocol = None

        def __init__(self):
            self.sent, self.closed = [], False
            self.incoming = asyncio.Queue()
            self.incoming.put_nowait('{"type": "auth_required"}')

        async def send(self, message):
            self.sent.append(message)
            self.incoming.put_nowait(None)          # then Home Assistant hangs up

        def __aiter__(self):
            return self

        async def __anext__(self):
            message = await self.incoming.get()
            if message is None:
                raise StopAsyncIteration
            return message

        async def close(self):
            self.closed = True

    async def connect(url, **options):
        made.update(url=url, headers=dict(options['additional_headers']), up=Upstream())
        return made['up']
    monkeypatch.setattr(ws, 'connect', connect)
    set_up()
    sent = _drive(_socket({**OWNER, **SERVE, 'cookie': 'ingress_session=abc'}),
                  [{'type': 'websocket.connect'}, {'type': 'websocket.receive', 'text': '{"type": "auth", "access_token": "t"}'}])
    assert made['url'] == 'ws://192.0.2.10:8123/api/websocket'
    assert not [k for k in made['headers'] if k.startswith(('x-forwarded', 'tailscale', 'sec-websocket', 'host'))]
    assert made['headers']['cookie'] == 'ingress_session=abc'
    assert sent[0] == {'type': 'websocket.accept', 'subprotocol': None}
    assert {'type': 'websocket.send', 'text': '{"type": "auth_required"}'} in sent
    assert made['up'].sent == ['{"type": "auth", "access_token": "t"}'] and made['up'].closed
    # Home Assistant's phone app names no origin; with the owner's login it passes too.
    sent = _drive(_socket(OWNER, origin=None), [{'type': 'websocket.connect'}, {'type': 'websocket.receive', 'text': 'x'}])
    assert sent[0]['type'] == 'websocket.accept'


# ---- Hub's side: the Home and Music screens ----

SIGNED_IN = {'tailscale-user-login': 'me@example.com'}


def test_without_the_door_nothing_changes(monkeypatch):
    settings(ha_url=HA, music_url='https://music.example.test:8095')
    client = TestClient(app)
    assert read.framed() == {} and read.opens()['ha_url'] == HA
    assert client.get('/house/assistant', headers=SIGNED_IN, follow_redirects=False).headers['location'] == '/house/changes'
    assert client.get('/house', headers=SIGNED_IN, follow_redirects=False).headers['location'] == HA


def test_with_the_door_home_assistant_and_music_assistant_are_screens_of_hub():
    set_up(music_path='/app/abc123_music_assistant')
    client = TestClient(app)
    assert read.framed() == {'ha': HA + '/', 'music': HA + '/app/abc123_music_assistant'}
    home = client.get('/house/assistant', headers=SIGNED_IN)
    assert home.status_code == 200 and f'<iframe class="frame-view" src="{HA}/"' in home.text
    assert home.headers['content-security-policy'] == f"default-src 'self'; frame-src {HA}; frame-ancestors 'none'"
    assert 'music.js' not in home.text and 'mp-bar' not in home.text               # the frame gets the whole screen
    music_page = client.get('/house/player', headers=SIGNED_IN)
    assert f'src="{HA}/app/abc123_music_assistant"' in music_page.text
    nav = client.get('/today', headers=SIGNED_IN).text
    assert 'href="/house/assistant">Home Assistant</a>' in nav and 'href="/house/player">Music Assistant</a>' in nav
    assert client.get('/house', headers=SIGNED_IN, follow_redirects=False).headers['location'] == '/house/assistant'
    assert client.get('/house/assistant', headers={'tailscale-user-login': 'stranger@example.com'}).status_code == 403


def test_every_other_page_still_refuses_frames():
    set_up()
    policy = TestClient(app).get('/today', headers=SIGNED_IN).headers['content-security-policy']
    assert policy == "default-src 'self'; frame-ancestors 'none'"


def test_a_music_path_that_isn_t_a_plain_path_is_ignored():
    for bad in ('https://evil.example/', '//evil.example', '/app/x?y=1', 'app/x', '/app/x"onload='):
        set_up(music_path=bad)
        assert 'music' not in read.framed(), bad


def test_hub_s_own_calls_to_music_assistant_can_use_a_home_network_address():
    settings(ha_url=HA, music_url='https://music.example.test:8095')
    assert music._base() == 'https://music.example.test:8095'
    settings(ha_url=HA, music_api='http://192.0.2.10:8095')
    assert music.ready() and music._base() == 'http://192.0.2.10:8095' and 'music_url' not in read.opens()
