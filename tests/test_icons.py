"""Hub's icons must be the ones built from the one drawing, and every page must point at them.

`tools/build-icons` writes hub/static/icons.json: a fingerprint of hub/static/icon.svg and of each
file made from it. Here both are recomputed, so a changed drawing without a rebuild, or an icon
changed by hand, fails.
"""
import hashlib
import json
import struct
from pathlib import Path

from fastapi.testclient import TestClient

import hub
from hub.main import app

STATIC = Path(hub.__file__).parent / 'static'
client = TestClient(app)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_icons_are_the_ones_built_from_the_drawing():
    record = json.loads((STATIC / 'icons.json').read_text())
    assert record['source'] == {'icon.svg': _sha(STATIC / 'icon.svg')}, 'icon.svg changed since the last build; run tools/build-icons and commit'
    assert set(record['built']) == {'apple-touch-icon.png', 'icon-192.png', 'icon-512.png', 'icon-maskable-192.png',
                                    'icon-maskable-512.png', 'favicon.svg', 'favicon.ico'}
    for name, digest in record['built'].items():
        assert _sha(STATIC / name) == digest, f'{name} was changed after the build'


def test_each_icon_is_the_size_its_name_says():
    for name, side in (('apple-touch-icon.png', 180), ('icon-192.png', 192), ('icon-512.png', 512),
                       ('icon-maskable-192.png', 192), ('icon-maskable-512.png', 512)):
        assert struct.unpack('>II', (STATIC / name).read_bytes()[16:24]) == (side, side), name
    ico = (STATIC / 'favicon.ico').read_bytes()
    assert struct.unpack('<HHH', ico[:6]) == (0, 1, 3)
    assert [ico[6 + 16 * n] for n in range(3)] == [16, 32, 48]


def test_every_page_declares_the_icons_and_they_are_served():
    for path in ('/', '/today', '/insights', '/tools', '/settings'):
        page = client.get(path).text
        for link in ('<link rel="icon" href="/favicon.ico" sizes="48x48">',
                     '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml" sizes="any">',
                     '<link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">',
                     '<link rel="manifest" href="/manifest.webmanifest">'):
            assert link in page, f'{path} is missing {link}'
    for path, kind in (('/favicon.ico', 'image/'), ('/apple-touch-icon.png', 'image/png'), ('/apple-touch-icon-precomposed.png', 'image/png'),
                       ('/static/favicon.svg', 'image/svg+xml'), ('/static/apple-touch-icon.png', 'image/png')):
        answer = client.get(path)
        assert answer.status_code == 200 and answer.headers['content-type'].startswith(kind), path
        assert answer.headers['x-content-type-options'] == 'nosniff'
    assert client.get('/favicon.ico').content == (STATIC / 'favicon.ico').read_bytes()


def test_the_manifest_lists_icons_that_exist():
    manifest = client.get('/manifest.webmanifest').json()
    assert {icon['purpose'] for icon in manifest['icons']} == {'any', 'maskable'}
    for icon in manifest['icons']:
        assert client.get(icon['src']).status_code == 200, icon['src']


def test_a_root_icon_is_answered_like_a_static_file():
    """Like /static, with or without a sign-in header: an icon carries nothing private."""
    assert client.get('/favicon.ico', headers={'Tailscale-User-Login': 'stranger@example.com'}).status_code == \
        client.get('/static/favicon.ico', headers={'Tailscale-User-Login': 'stranger@example.com'}).status_code == 200
