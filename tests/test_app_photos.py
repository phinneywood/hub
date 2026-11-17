"""The Photos screen in the app (hub/app/photos.py): a month the curator chose, its previews and the marks.
A fictional curator folder only; Hub never opens a Photos library."""
import json

from fastapi.testclient import TestClient

from hub import config
from hub.main import app
from tests import archive_standin

client = TestClient(app)
OWNER = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
A, B, C = (f'{c * 8}-0000-0000-0000-000000000000' for c in 'ABC')


def curator_folder(tmp_path):
    here = tmp_path / 'curator'
    (here / 'months').mkdir(parents=True)
    (here / 'thumbs').mkdir()
    for month, picks in (('2026-09', [C]), ('2026-10', [A])):
        (here / 'months' / f'{month}.json').write_text(json.dumps({
            'month': month, 'album': 'October 2026' if month == '2026-10' else 'September 2026', 'looked_at': 40,
            'picks': [{'id': p, 'taken': '2026-10-03T12:00:00-07:00', 'people': ['Alex'], 'favorite': True}
                      for p in picks], 'more': [{'id': B, 'taken': '2026-10-04T09:00:00-07:00'}] * (month == '2026-10')}))
    (here / 'thumbs' / f'{A}.jpg').write_bytes(b'\xff\xd8 a fictional preview')
    archive_standin.household(photos={'dir': str(here)})
    return here


def test_photos_are_for_signed_in_devices_only(tmp_path):
    curator_folder(tmp_path)
    away = {'origin': 'http://testserver'}
    assert client.get('/app/api/photos', headers=away).status_code == 403
    assert client.get(f'/app/api/photos/thumb/{A}', headers=away).status_code == 403
    assert client.post('/app/api/photos/keep', json={'key': A}, headers=away).status_code == 403


def test_the_newest_month_with_marks_and_earlier_months(tmp_path):
    curator_folder(tmp_path)
    seen = client.get('/app/api/photos', headers=OWNER).json()
    assert seen['ready'] and seen['month'] == '2026-10' and seen['name'] == 'October 2026'
    assert [m['month'] for m in seen['months']] == ['2026-10', '2026-09']
    assert [p['id'] for p in seen['picks']] == [A] and [p['id'] for p in seen['more']] == [B]
    assert client.get('/app/api/photos?month=2026-09', headers=OWNER).json()['picks'][0]['id'] == C
    preview = client.get(f'/app/api/photos/thumb/{A}', headers=OWNER)
    assert preview.status_code == 200 and preview.headers['content-type'] == 'image/jpeg'
    assert preview.headers['cache-control'] == 'no-store'


def test_marks_reach_the_curator_and_only_for_chosen_photos(tmp_path):
    here = curator_folder(tmp_path)
    assert client.post('/app/api/photos/print', json={'key': A}, headers=OWNER).json()['ok']
    marks = json.loads((here / 'marks.json').read_text())
    assert marks[A]['mark'] == 'print' and marks[A]['by'] == 'me@example.com'
    seen = client.get('/app/api/photos', headers=OWNER).json()
    assert seen['picks'][0]['mark'] == 'print' and seen['counts']['print'] == 1 and seen['waiting'] == 1
    (here / 'applied.json').write_text(json.dumps({'album': [A], 'print': [A]}))
    assert client.get('/app/api/photos', headers=OWNER).json()['waiting'] == 0
    assert client.post('/app/api/photos/clear', json={'key': A}, headers=OWNER).json()['ok']
    assert A not in json.loads((here / 'marks.json').read_text())
    other = 'EEEEEEEE-0000-0000-0000-000000000000'
    assert client.post('/app/api/photos/keep', json={'key': other}, headers=OWNER).status_code == 409
    assert client.post('/app/api/photos/delete', json={'key': A}, headers=OWNER).status_code == 409
    assert client.get('/app/api/photos/thumb/..%2F..%2Fsecret', headers=OWNER).status_code == 404


def test_before_the_first_month_the_screen_says_so(tmp_path):
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'photos': {'dir': str(tmp_path / 'none')}}))
    seen = client.get('/app/api/photos', headers=OWNER).json()
    assert seen == {'ok': True, 'ready': False, 'months': [], 'month': '', 'picks': [], 'more': []}


def test_another_login_sees_the_shared_librarys_picks_alone(tmp_path, monkeypatch):
    here = curator_folder(tmp_path)
    month = json.loads((here / 'months' / '2026-10.json').read_text())
    month['picks'].append({'id': C, 'taken': '2026-10-05T12:00:00-07:00', 'people': [], 'shared': True})
    (here / 'months' / '2026-10.json').write_text(json.dumps(month))
    (here / 'thumbs' / f'{C}.jpg').write_bytes(b'\xff\xd8 a fictional preview')
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com', 'sam@example.com'})
    other = {'origin': 'http://testserver', 'tailscale-user-login': 'sam@example.com'}
    theirs = client.get('/app/api/photos', headers=other).json()
    assert [p['id'] for p in theirs['picks']] == [C] and theirs['more'] == []
    assert client.get(f'/app/api/photos/thumb/{A}', headers=other).status_code == 404
    assert client.post('/app/api/photos/keep', json={'key': A}, headers=other).status_code == 409
    assert client.get(f'/app/api/photos/thumb/{C}', headers=other).status_code == 200
