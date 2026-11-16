"""House health (hub/caretaker.py): Hub reads the caretaker's folder and leaves it request notes, nothing more.
The folder is the made-up one in caretaker_world; the house is the made-up one in app_world; nothing real is read."""
import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from hub import caretaker, config, home
from hub.engine import catalog
from hub.live import checks
from hub.main import app

from . import app_world, caretaker_world

client = TestClient(app)
OWNER = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}


def at(day_offset, hour, minute=0, now=None):
    today = (now or datetime.now(timezone.utc)).astimezone(home.PACIFIC).date() + timedelta(days=day_offset)
    return caretaker_world.local(today, hour, minute)


@pytest.fixture
def folder(tmp_path, monkeypatch):
    path = tmp_path / 'caretaker'
    monkeypatch.setattr(config, 'CARETAKER_DIR', path)
    return caretaker_world.night(path)


@pytest.fixture
def morning(tmp_path, monkeypatch):
    """The same made-up night, seen from 6:30 this morning, whatever time the tests run."""
    path = tmp_path / 'caretaker-morning'
    monkeypatch.setattr(config, 'CARETAKER_DIR', path)
    when = at(0, 6, 30)
    return caretaker_world.night(path, now=when), when


@pytest.fixture
def house(monkeypatch):
    return app_world.install(monkeypatch)


def queued(folder):
    return [json.loads(p.read_text()) for p in sorted((folder.path / 'queue').glob('*.json'))]


def test_the_screen_reads_the_night_the_week_and_what_needs_you(morning):
    _folder, when = morning
    picture = caretaker.picture(when)
    assert picture['alive'] is True
    assert [n['title'] for n in picture['needs']] == ['Sonos keeps failing']
    need = picture['needs'][0]
    assert need['ask'] == 'ha.restart' and 'Restarting Home Assistant may clear it' in need['words']
    tones = [line['tone'] for line in picture['night']['lines']]
    assert 'fixed' in tones and 'by_itself' in tones and 'needs_you' not in tones
    assert len(picture['week']) == 7 and all(day['watched'] for day in picture['week'])
    assert sum(day['fixed'] for day in picture['week']) >= 2
    hub = next(s for s in picture['systems'] if s['id'] == 'hub')
    assert len(hub['days']) == 7 and hub['history'][0]['outcome'] == 'fixed'
    assert [d['name'] for d in picture['drills']][0] == 'hub-down'
    assert [s['key'] for s in picture['sturdier']] == ['hue-zigbee', 'ha-backups-local']
    assert picture['ignoring'] == [{'name': 'Travel speaker', 'why': 'set aside in settings'}]


def test_without_a_caretaker_the_screen_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CARETAKER_DIR', tmp_path / 'absent')
    picture = caretaker.picture()
    assert picture == {'set_up': False, 'alive': False, 'headline': 'The caretaker isn’t set up on this Mac yet.',
                       'sub': picture['sub']}
    assert caretaker.note() is None and caretaker.needs() == []
    with pytest.raises(checks.Skipped):
        checks.caretaker_alive()


def test_a_caretaker_that_stopped_looking_is_said_plainly_and_fails_its_check(folder):
    beat = datetime.now(timezone.utc) - timedelta(minutes=12)
    folder.status(beat)
    watching, words = caretaker.alive()
    assert watching is False and words == f'The caretaker hasn’t looked since {caretaker.clock(beat)}.'
    assert caretaker.picture()['headline'] == words
    assert caretaker.top()['alive'] is False
    with pytest.raises(checks.Failed) as failed:
        checks.caretaker_alive()
    assert 'hasn’t looked since' in str(failed.value)
    folder.status(datetime.now(timezone.utc))
    assert checks.caretaker_alive().startswith('The caretaker looked at')


def test_the_morning_note_covers_nine_pm_until_noon(morning):
    _folder, when = morning
    found = caretaker.note(when)
    assert found['headline'] == 'I fixed 1 thing overnight; 1 needs you.'
    assert [line['tone'] for line in found['lines']] == ['fixed', 'by_itself']
    assert found['needs'] == 1
    assert caretaker.note(when.replace(hour=13)) is None       # 1 PM: no note
    early = caretaker.note(when.replace(hour=3, minute=0))      # 3 AM: Sonos is held for the morning
    assert early['needs'] == 0 and early['headline'] == 'I fixed 1 thing overnight.'


def test_drills_and_needs_you_lines_never_reach_the_note_or_today(morning):
    folder, when = morning
    folder.incident('hub_down', 'hub', 'Hub isn’t answering', when - timedelta(minutes=5), needs='A drill’s words', drill='hub-down')
    folder.event(when - timedelta(minutes=1), 'hub', 'fixed', 'A drill fixed it.', drill='hub-down')
    folder.incident('apple_unreachable', 'apple_home', 'Side door lock isn’t responding', when - timedelta(hours=1),
                    needs='Change the batteries.', today=0)
    assert [n['title'] for n in caretaker.needs(when)] == ['Sonos keeps failing']
    lines = caretaker.note(when)['lines']
    assert 'A drill fixed it.' not in [line['text'] for line in lines]
    assert all(line['tone'] in ('fixed', 'by_itself', 'info') for line in lines)


def test_today_items_open_house_health(folder):
    items = catalog.caretaker_items()
    released = [n for n in caretaker.needs()]
    assert [i['key'] for i in items] == [f'care-{n["id"]}' for n in released]
    for item in items:
        assert (item['tone'], item['href'], item['cta']) == ('warn', '/app/#health', 'Open')
    assert catalog.source('caretaker').items == (catalog.caretaker_items,)


def test_a_request_is_checked_and_left_in_the_queue(folder):
    [sonos] = caretaker.picture()['needs']
    assert client.post('/app/api/health/fix', json={'target': f'care-{sonos["id"]}'}).status_code == 403
    answer = client.post('/app/api/health/fix', json={'target': f'care-{sonos["id"]}'}, headers=OWNER)
    assert answer.status_code == 200 and answer.json()['said'] == 'Asked the caretaker to restart Home Assistant.'
    [note] = queued(folder)
    assert note['do'] == 'fix' and note['target'] == f'care-{sonos["id"]}' and note['by'] == 'me@example.com'
    assert note['id'] == answer.json()['request'] and note['id'].startswith('hub-')
    [path] = list((folder.path / 'queue').glob('*.json'))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not list((folder.path / 'queue').glob('.*.tmp'))


@pytest.mark.parametrize('do, body, said', [
    ('unlock', {'target': 'care-1'}, 'The caretaker doesn’t do that.'),
    ('fix', {'target': 'care-999'}, 'That isn’t open any more.'),
    ('fix', {'target': '../../etc'}, 'That isn’t open any more.'),
    ('drill', {'target': 'open-the-garage'}, 'There is no drill called that.'),
    ('pause', {'target': '-5'}, 'Pause for a number of minutes, or until you resume.'),
    ('fix', {'target': 'sturdier:hue-zigbee'}, 'A suggestion can be marked done or set aside.'),
    ('done', {'target': 'sturdier:nothing-like-it'}, 'That suggestion isn’t open.'),
])
def test_requests_hub_wont_pass_on(folder, do, body, said):
    answer = client.post(f'/app/api/health/{do}', json=body, headers=OWNER)
    assert answer.status_code == 409 and answer.json()['error'] == said
    assert queued(folder) == []


def test_a_closed_or_tell_only_incident_cannot_be_asked_to_fix(folder):
    now = datetime.now(timezone.utc)
    lamp = folder.incident('hue_lamp_out', 'hue', 'Porch lantern is off the Hue network', now - timedelta(hours=3),
                           needs='Switch it off and on at the wall.')
    folder.status(now)
    answer = client.post('/app/api/health/fix', json={'target': f'care-{lamp}'}, headers=OWNER)
    assert answer.status_code == 409 and answer.json()['error'] == 'The caretaker can’t fix that one; it needs you.'
    done = client.post('/app/api/health/done', json={'target': f'care-{lamp}'}, headers=OWNER)
    assert done.status_code == 200 and done.json()['said'] == 'Marked as sorted.'


def test_pause_resume_drill_and_sturdier_requests(folder):
    for do, body in (('pause', {'target': '60'}), ('pause', {'target': 'forever'}), ('resume', {}),
                     ('drill', {'target': 'hub-down'}), ('done', {'target': 'sturdier:hue-zigbee'}),
                     ('ignore', {'target': 'sturdier:ha-backups-local'})):
        assert client.post(f'/app/api/health/{do}', json=body, headers=OWNER).status_code == 200, do
    assert [n['do'] for n in queued(folder)].count('pause') == 2 and len(queued(folder)) == 6


def test_hub_only_reads_the_journal_and_the_status(folder):
    db, status = folder.path / 'caretaker.db', folder.path / 'status.json'
    before = (db.read_bytes(), status.read_bytes(), db.stat().st_mtime_ns)
    caretaker.picture()
    caretaker.note()
    caretaker.needs()
    caretaker.top()
    catalog.caretaker_items()
    assert (db.read_bytes(), status.read_bytes(), db.stat().st_mtime_ns) == before
    with caretaker._db() as conn:
        with pytest.raises(Exception):
            conn.execute("delete from incidents")


def test_the_app_gets_the_count_for_the_top_button_and_the_screen(folder, house):
    home_now = client.get('/app/api/home').json()
    assert home_now['care']['set_up'] is True and home_now['care']['alive'] is True
    assert home_now['care']['needs'] == len(caretaker.needs())
    assert client.get('/app/api/health').json()['headline'] in ('One thing needs you.', 'All quiet.')
    note = client.get('/app/api/health/note').json()
    assert note is None or 'headline' in note


def test_the_live_check_is_listed_as_a_read():
    found = checks.by_id('caretaker.alive')
    assert found.effect == 'read'
