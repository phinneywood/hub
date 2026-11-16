"""The archive's service (deploy/archive/serve.py) over a real Unix socket, on the made-up archive: fixed read-only
routes, ids only, never a photo original, never where a file is."""
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest

from tests import archive_world as world

serve = world.program('serve')
store = world.program('store')


@pytest.fixture(scope='module')
def service(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        home, mac, uuids = world.build(tmp_path_factory.mktemp('served'), patch)
        patch.setattr(serve, 'macos', mac)
        socket = Path(tempfile.mkdtemp(prefix='arc-', dir='/tmp')) / 's.sock'    # macOS keeps socket paths short
        thread = threading.Thread(target=serve.serve, args=(socket,), daemon=True)
        thread.start()
        for _ in range(100):
            if socket.exists():
                break
            time.sleep(0.02)
        client = httpx.Client(transport=httpx.HTTPTransport(uds=str(socket)), base_url='http://archive', timeout=5)
        yield client, uuids, socket, mac
        client.close()


@pytest.fixture(autouse=True)
def our_socket_only(service, monkeypatch):
    world.allow(service[2], monkeypatch)


def get(service, path, **params):
    return service[0].get(path, params=params)


def test_health_and_the_socket_is_owner_only(service):
    assert get(service, '/health').json() == {'ok': True, 'ready': True}
    assert service[2].stat().st_mode & 0o777 == 0o600


def test_search_answers_source_by_source_and_never_says_where(service):
    found = service[0].get('/search', params=[('q', 'furnace'), ('sources', 'photos,documents,chats')]).json()
    assert {'photos', 'documents', 'chats', 'loose', 'ms'} <= set(found)
    assert found['documents']['count'] >= 1 and found['chats']['count'] == 1 and found['photos']['count'] == 1
    assert '"path"' not in httpx.Response(200, json=found).text


def test_people_kids_and_dates_through_the_query_string(service):
    found = service[0].get('/search', params=[('q', 'beach'), ('sources', 'photos'), ('anyof', 'Sam'), ('young', '1'),
                                              ('after', '2024-01-01'), ('before', '2025-01-01'), ('sort', 'best'),
                                              ('limit', '4')]).json()['photos']
    assert found['count'] == 9 and found['days'] == 2 and len(found['items']) == 4


@pytest.mark.parametrize('params', [{'sort': 'evil'}, {'after': '2024'}, {'limit': '0'}, {'limit': '9999'},
                                    {'sources': 'mail'}, {'kind': 'secret'}, {'offset': '-1'}])
def test_questions_out_of_bounds_are_refused(service, params):
    assert get(service, '/search', q='beach', **params).status_code == 400


def test_an_item_in_full_and_ids_only(service):
    found = get(service, '/search', q='tune-up', sources='documents').json()['documents']['items'][0]
    item = get(service, f'/item/{found["id"]}').json()
    assert item['title'] == 'Furnace service invoice.pdf' and 'annual tune-up' in item['pages_text'][0]['text']
    for nowhere in ('d:0000000000000000deadbeef', '..%2F..%2Fetc%2Fpasswd', 'd:../../etc', 'x:12345678', 'p:' + 'A' * 90):
        assert get(service, f'/item/{nowhere}').status_code == 404
    assert get(service, '/etc/passwd').status_code == 404


def test_pictures_previews_and_pages(service):
    client, uuids, _, mac = service
    photo = 'p:' + uuids['1']
    assert client.get(f'/thumb/{photo}').headers['content-type'] == 'image/png'
    shown = client.get(f'/photo/{photo}', params={'size': 1600})
    assert shown.status_code == 200 and shown.headers['x-width'] == '64'
    invoice = get(service, '/search', q='invoice', sources='documents').json()['documents']['items'][0]['id']
    assert client.get(f'/page/{invoice}/1').status_code == 200
    assert client.get(f'/page/{invoice}/9').status_code == 404
    assert client.get(f'/photo/{invoice}').status_code == 404              # a document is not a photo
    assert client.get(f'/photo/{photo}', params={'size': 99999}).status_code == 400


def test_a_documents_own_bytes_but_never_a_photos(service):
    client, uuids, _, _ = service
    invoice = get(service, '/search', q='invoice', sources='documents').json()['documents']['items'][0]['id']
    got = client.get(f'/file/{invoice}')
    assert got.status_code == 200 and got.content.startswith(b'%PDF')
    assert "filename*=UTF-8''Furnace%20service%20invoice.pdf" in got.headers['content-disposition']
    assert client.get(f'/file/p:{uuids["1"]}').status_code == 404
    numbers = get(service, '/search', q='budget', sources='documents').json()['documents']['items'][0]['id']
    assert client.get(f'/file/{numbers}').status_code == 404                # a Numbers document is a folder on disk


def test_people_on_this_day_and_status(service):
    assert {p['name']: p['kid'] for p in get(service, '/people').json()['people']} == {'Alex': False, 'Sam': True}
    years = get(service, '/onthisday', day='11-16', year='2026').json()['years']
    assert [y['year'] for y in years] == [2023, 2022]
    status = get(service, '/status').json()
    assert status['counts']['photos']['items'] == 39 and status['kids'] == 1 and 'bytes' in status


def test_it_only_answers_questions(service):
    assert service[0].post('/search', json={}).status_code == 405


def test_a_sharp_photo_comes_from_photos_once_and_is_kept(service, monkeypatch):
    client, uuids, _, mac = service
    sharp = world.program('sharp')
    monkeypatch.setattr(sharp, 'macos', mac)
    sharp._refused['at'] = float('-inf')
    photo = 'p:' + uuids['1']
    mac.exported = []
    first = client.get(f'/photo/{photo}', params={'sharp': '1'})
    assert first.headers['x-sharp'] == '1' and first.content.startswith(b'\x89PNG')
    again = client.get(f'/photo/{photo}', params={'sharp': '1'})
    assert again.headers['x-sharp'] == '1' and mac.exported == [uuids['1']]          # the second time is from the cache
    assert store.sharp_path(photo).stat().st_mode & 0o777 == 0o600


def test_when_photos_says_no_the_viewer_keeps_the_macs_own_picture(service, monkeypatch):
    client, uuids, _, mac = service
    sharp = world.program('sharp')
    monkeypatch.setattr(sharp, 'macos', mac)
    sharp._refused['at'] = float('-inf')
    monkeypatch.setattr(mac, 'refuse', True, raising=False)
    mac.exported = []
    photo = 'p:' + uuids['2']
    got = client.get(f'/photo/{photo}', params={'sharp': '1'})
    assert got.status_code == 200 and got.headers['x-sharp'] == '0'
    client.get(f'/photo/p:{uuids["3"]}', params={'sharp': '1'})
    assert mac.exported == [uuids['2']]                                               # and Photos isn't asked again for a while


def test_the_sharp_cache_lets_the_least_recently_opened_go(tmp_path, monkeypatch):
    import os
    store_ = world.program('store')
    sharp = world.program('sharp')
    monkeypatch.setenv('HUB_ARCHIVE_DIR', str(tmp_path))
    monkeypatch.setattr(sharp, 'MOST', 3000)
    monkeypatch.setattr(sharp, 'KEEP', 2000)
    for n in range(4):
        store_.write_bytes(store_.sharp_path(f'p:photo{n:04d}'), b'x' * 1000)
        os.utime(store_.sharp_path(f'p:photo{n:04d}'), (1000 + n, 1000 + n))
    sharp.cached('p:photo0000')                                                       # opened again: now the newest
    assert sharp.trim() == 2
    assert [store_.sharp_path(f'p:photo{n:04d}').exists() for n in range(4)] == [True, False, False, True]
