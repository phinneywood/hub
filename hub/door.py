"""The door: the whole Home Assistant, reached through Hub with Hub's sign-in. See docs/door.md.

Home Assistant can't be served under a path, so it keeps an address of its own: another Tailscale Serve port
on the same machine name, which Serve passes to this small proxy on loopback. The door checks the Tailscale
login on every request and every WebSocket (only the logins the owner listed for it; a request with no login
is refused too), then passes it on to Home Assistant unchanged, both ways, without buffering.

The door holds no Home Assistant credential. Home Assistant itself signs this Mac's address in as the owner
(its `trusted_networks` provider, set up by the owner), which is why the door is the gate and is strict:
- no login header, or a login not on the door's list: refused;
- a request another site made the browser send (anything but a plain GET), or a WebSocket from another
  origin: refused;
- a sign-in started for any site but the door's own address: refused, so no other page can be handed a code;
- `X-Forwarded-*` and Tailscale's headers never reach Home Assistant, which would otherwise refuse the request
  or learn who asked.

It also lets Hub, and only Hub's own machine name, show Home Assistant in a frame. Music Assistant runs as a
Home Assistant add-on, so it comes through the same door with no login of its own.

Settings (private household.json, section "door"): "upstream" (Home Assistant's address on the home network),
"logins" (who may pass). Without both the door refuses everything. It listens on 127.0.0.1:HUB_DOOR_PORT.
"""
import asyncio
import os
import threading
from urllib.parse import parse_qs, urlsplit

import httpx

from . import config, household

PORT = int(os.environ.get('HUB_DOOR_PORT', '8781'))
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}
NEVER_UP = {'host', 'forwarded', 'via'}                 # plus every x-forwarded-* and tailscale-* header
NEVER_DOWN = {'x-frame-options', 'content-security-policy', 'server', 'date'}
WS_NEVER_UP = {'host', 'connection', 'upgrade', 'forwarded', 'via', 'sec-websocket-key', 'sec-websocket-version',
               'sec-websocket-extensions', 'sec-websocket-protocol', 'sec-websocket-accept'}
CONNECT_SECONDS = 10
BODY = 512 * 1024 * 1024                                # one upload (a backup restore), streamed, never held


def settings():
    """{'upstream': 'http://host:port', 'logins': {...}} or None when the door isn't set up (it then refuses all)."""
    try:
        chosen = household.section('door')
    except ValueError:
        return None
    upstream, logins = chosen.get('upstream'), chosen.get('logins')
    if not isinstance(upstream, str) or not isinstance(logins, list):
        return None
    parts = urlsplit(upstream)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.path not in ('', '/') or parts.query or parts.username:
        return None
    people = {str(login).strip().lower() for login in logins if isinstance(login, str) and login.strip()}
    if not people:
        return None
    return {'upstream': f'{parts.scheme}://{parts.netloc}', 'logins': people}


def configured():
    return settings() is not None


def _headers(scope):
    return [(k.decode('latin-1').lower(), v.decode('latin-1')) for k, v in scope.get('headers', ())]


def _first(headers, name):
    return next((v for k, v in headers if k == name), None)


def refusal(scope, headers, chosen):
    """Why this request may not pass, as (status, words), or None."""
    if chosen is None:
        return 503, 'The door to Home Assistant isn’t set up.'
    login = _first(headers, 'tailscale-user-login')
    if login is None or login.strip().lower() not in chosen['logins']:
        return 403, 'Not allowed'
    host = _first(headers, 'host') or ''
    origin = _first(headers, 'origin')
    if scope['type'] == 'websocket':
        if not origin or urlsplit(origin).netloc != host:
            return 403, 'Not allowed'
        return None
    if scope['method'] not in ('GET', 'HEAD', 'OPTIONS'):
        same = (origin and urlsplit(origin).netloc == host) or (not origin and _first(headers, 'sec-fetch-site') in ('same-origin', 'none'))
        if not same:
            return 403, 'Not allowed'
    if scope['path'].startswith('/auth/authorize'):
        client = (parse_qs(scope.get('query_string', b'').decode('latin-1')).get('client_id') or [''])[0]
        if urlsplit(client).netloc != host:
            return 403, 'Not allowed'
    return None


def _up(headers, upstream, never=NEVER_UP):
    out = [(k, v) for k, v in headers
           if k not in HOP and k not in never and not k.startswith(('x-forwarded-', 'tailscale-', 'x-real-'))]
    return [('host', urlsplit(upstream).netloc), *out]


def _frame_rule(headers):
    """Only pages on this machine's own name (Hub, on its port) may show the door in a frame."""
    name = (_first(headers, 'host') or '').rsplit(':', 1)[0]
    return f"frame-ancestors 'self' https://{name}:*" if name and all(c.isalnum() or c in '.-' for c in name) else "frame-ancestors 'self'"


def _target(scope, upstream, ws=False):
    path = scope.get('raw_path') or scope['path'].encode()
    query = scope.get('query_string') or b''
    base = upstream.replace('http', 'ws', 1) if ws else upstream
    return base + path.decode('latin-1') + ('?' + query.decode('latin-1') if query else '')


_clients = {}


def _client():
    """One connection pool per event loop (the door's own loop in the running Hub; a test's loop in tests)."""
    loop = asyncio.get_running_loop()
    if loop not in _clients:
        _clients[loop] = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=CONNECT_SECONDS), follow_redirects=False,
                                           limits=httpx.Limits(max_connections=64, max_keepalive_connections=16), trust_env=False)
    return _clients[loop]


async def _plain(send, status, words):
    body = words.encode()
    await send({'type': 'http.response.start', 'status': status,
                'headers': [(b'content-type', b'text/plain; charset=utf-8'), (b'content-length', str(len(body)).encode()),
                            (b'cache-control', b'no-store')]})
    await send({'type': 'http.response.body', 'body': body})


async def _http(scope, receive, send):
    headers = _headers(scope)
    chosen = settings()
    refused = refusal(scope, headers, chosen)
    if refused:
        return await _plain(send, *refused)
    gone = asyncio.Event()

    async def body():
        size = 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                gone.set()
                raise httpx.ReadError('The browser went away.')
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > BODY:
                raise httpx.ReadError('Too large.')
            if chunk:
                yield chunk
            if not message.get('more_body'):
                return

    has_body = _first(headers, 'content-length') not in (None, '0') or _first(headers, 'transfer-encoding') is not None
    request = _client().build_request(scope['method'], _target(scope, chosen['upstream']), headers=_up(headers, chosen['upstream']),
                                      content=body() if has_body else None)
    try:
        reply = await _client().send(request, stream=True)
    except httpx.HTTPError:
        if not gone.is_set():
            await _plain(send, 502, 'Home Assistant isn’t answering.')
        return

    async def watch():                      # a stream (a camera, the log) ends when the browser leaves
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                gone.set()
                return

    watcher = asyncio.ensure_future(watch())
    try:
        down = [(k.lower(), v) for k, v in reply.headers.multi_items()]
        down = [(k, v) for k, v in down if k not in HOP and k not in NEVER_DOWN]
        down.append(('content-security-policy', _frame_rule(headers)))
        await send({'type': 'http.response.start', 'status': reply.status_code,
                    'headers': [(k.encode('latin-1'), v.encode('latin-1')) for k, v in down]})
        stream = reply.aiter_raw().__aiter__()
        while True:
            step = asyncio.ensure_future(stream.__anext__())
            await asyncio.wait({step, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if not step.done():
                step.cancel()
                break
            try:
                chunk = step.result()
            except StopAsyncIteration:
                break
            except httpx.HTTPError:
                break
            await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
        if not gone.is_set():
            await send({'type': 'http.response.body', 'body': b''})
    finally:
        watcher.cancel()
        await reply.aclose()


async def _websocket(scope, receive, send):
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed, WebSocketException
    headers = _headers(scope)
    chosen = settings()
    first = await receive()                 # websocket.connect
    if first['type'] != 'websocket.connect' or refusal(scope, headers, chosen):
        return await send({'type': 'websocket.close', 'code': 1008})
    try:
        upstream = await connect(_target(scope, chosen['upstream'], ws=True), subprotocols=scope.get('subprotocols') or None,
                                 additional_headers=_up(headers, chosen['upstream'], WS_NEVER_UP)[1:], origin=None,
                                 max_size=None, open_timeout=CONNECT_SECONDS, ping_interval=None, proxy=None,
                                 user_agent_header=None)
    except (OSError, WebSocketException, asyncio.TimeoutError):
        return await send({'type': 'websocket.close', 'code': 1011})
    await send({'type': 'websocket.accept', 'subprotocol': upstream.subprotocol})

    async def up():
        while True:
            message = await receive()
            if message['type'] == 'websocket.disconnect':
                return
            if message['type'] == 'websocket.receive':
                await upstream.send(message['text'] if message.get('text') is not None else message.get('bytes') or b'')

    async def down():
        async for message in upstream:
            await send({'type': 'websocket.send', **({'text': message} if isinstance(message, str) else {'bytes': message})})

    tasks = {asyncio.ensure_future(up()), asyncio.ensure_future(down())}
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await upstream.close()
        except ConnectionClosed:
            pass
        try:
            await send({'type': 'websocket.close', 'code': 1000})
        except Exception:  # noqa: BLE001 - the browser already left
            pass


async def app(scope, receive, send):
    if scope['type'] == 'http':
        await _http(scope, receive, send)
    elif scope['type'] == 'websocket':
        await _websocket(scope, receive, send)
    elif scope['type'] == 'lifespan':
        while True:
            message = await receive()
            if message['type'] == 'lifespan.startup':
                await send({'type': 'lifespan.startup.complete'})
            elif message['type'] == 'lifespan.shutdown':
                await send({'type': 'lifespan.shutdown.complete'})
                return


def start():
    """Serve the door beside Hub, on a thread with a loop of its own, when it is set up. Returns the thread or None.
    Loopback only: Tailscale Serve is the one way in, and it adds the login the door checks."""
    if not configured():
        return None
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host=config.HOST, port=PORT, log_level='warning', access_log=False,
                                           lifespan='off', server_header=False))
    thread = threading.Thread(target=server.run, daemon=True, name='door')
    thread.start()
    return thread
