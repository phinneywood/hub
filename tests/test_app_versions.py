"""The Versions screen's data (hub/app/versions.py) and the hand-kept list behind it."""
import json
import re

from fastapi.testclient import TestClient

from hub import config
from hub.app import routes, versions
from hub.engine import release
from hub.main import app

client = TestClient(app)
SHA = re.compile(r'[0-9a-f]{40}')
LIVE = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ')


def test_the_list_is_newest_first_and_only_the_newest_may_wait_for_its_commit():
    listed = versions.entries()
    assert listed and all(set(e) == {'sha', 'from', 'live', 'title', 'changes'} for e in listed)
    assert all(SHA.fullmatch(e['sha']) and LIVE.fullmatch(e['live']) for e in listed[1:])
    assert listed[0]['sha'] is None or SHA.fullmatch(listed[0]['sha'])
    assert all(e['changes'] and all(isinstance(c, str) and c for c in e['changes']) and e['title'] for e in listed)
    for newer, older in zip(listed, listed[1:]):
        assert newer['from'] == older['sha']                         # each release follows the one below it
        assert newer['live'] is None or newer['live'] > older['live']


def test_the_newest_entry_is_the_running_version_when_the_list_does_not_name_it(monkeypatch):
    monkeypatch.setattr(release, 'running', lambda: 'a' * 40)
    monkeypatch.setattr(versions, 'entries', lambda: [
        {'sha': None, 'from': 'b' * 40, 'live': None, 'title': 'Next', 'changes': ['New']},
        {'sha': 'b' * 40, 'from': 'c' * 40, 'live': '2026-01-02T03:04:05Z', 'title': 'Before', 'changes': ['Old']}])
    seen = client.get('/app/api/versions').json()
    assert seen['running'] == 'aaaaaaa' and seen['repo'] is None
    assert [(v['short'], v['running'], v['commit']) for v in seen['versions']] == [('aaaaaaa', True, None), ('bbbbbbb', False, None)]
    monkeypatch.setattr(release, 'running', lambda: 'b' * 40)       # an older release running: the newest isn't live yet
    first, second = client.get('/app/api/versions').json()['versions']
    assert first['sha'] is None and not first['running'] and second['running']


def test_links_go_to_the_repository_in_the_private_settings_only(monkeypatch):
    monkeypatch.setattr(release, 'running', lambda: None)
    monkeypatch.setattr(versions, 'entries', lambda: [
        {'sha': None, 'from': 'b' * 40, 'live': None, 'title': 'Next', 'changes': ['New']},
        {'sha': 'b' * 40, 'from': 'c' * 40, 'live': '2026-01-02T03:04:05Z', 'title': 'Before', 'changes': ['Old']}])
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'versions': {'repo': 'https://github.com/example/hub/'}}))
    first, second = client.get('/app/api/versions').json()['versions']
    assert first['commit'] is None and first['compare'] == 'https://github.com/example/hub/compare/bbbbbbbbbbbb...main'
    assert second['commit'] == 'https://github.com/example/hub/commit/' + 'b' * 40
    assert second['compare'] == 'https://github.com/example/hub/compare/cccccccccccc...bbbbbbbbbbbb'
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'versions': {'repo': 'javascript:alert(1)'}}))
    assert client.get('/app/api/versions').json()['repo'] is None


def test_deploy_from_the_app_needs_a_signed_in_owner_and_a_checked_version(tmp_path, monkeypatch):
    root = tmp_path / 'releases'
    (root / ('b' * 40) / 'venv').mkdir(parents=True)
    (root / 'queue').mkdir()
    monkeypatch.setattr(release, 'running', lambda: 'a' * 40)
    monkeypatch.delenv('HUB_RELEASES_DIR', raising=False)
    assert client.get('/app/api/versions').json()['deploy'] is None    # a development Hub has nothing to deploy
    monkeypatch.setenv('HUB_RELEASES_DIR', str(root))
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com'})
    owner = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
    assert client.get('/app/api/versions').json()['deploy'] == {'ready': None, 'asked': False, 'put_back': None, 'undo': None}
    assert client.post('/app/api/deploy', headers=owner).status_code == 409
    (root / 'pending.json').write_text(json.dumps({'sha': 'b' * 40, 'subjects': ['Synthetic change']}))
    assert routes._update_ready() is True                              # System says a version is waiting
    assert client.get('/app/api/versions').json()['deploy']['ready'] == {'sha': 'b' * 40, 'short': 'bbbbbbb', 'subjects': ['Synthetic change']}
    assert client.post('/app/api/deploy', headers={'origin': 'http://testserver'}).status_code == 403   # a program on this Mac
    assert not (root / 'queue' / 'request.json').exists()
    assert client.post('/app/api/deploy', headers=owner).json()['ok'] is True
    assert json.loads((root / 'queue' / 'request.json').read_text())['sha'] == 'b' * 40
    assert client.get('/app/api/versions').json()['deploy']['asked'] is True
    (root / 'last.json').write_text(json.dumps({'sha': 'b' * 40, 'status': 'rolled_back'}))
    assert client.get('/app/api/versions').json()['deploy']['put_back'] == 'bbbbbbb'


# ---- Undo, and what Hub says about an update it made by itself (the deployer's `auto`, home-ops) ----

def _deployed(root, how='auto', ago=600, previous='a' * 40):
    """A made-up release history: `previous` ran, then 'b' * 40 went live `ago` seconds ago, `how` as the deployer says."""
    from datetime import datetime, timezone
    (root / previous / 'venv').mkdir(parents=True, exist_ok=True)
    (root / ('b' * 40) / 'venv').mkdir(parents=True, exist_ok=True)
    (root / 'queue').mkdir(exist_ok=True)
    (root / 'current').unlink(missing_ok=True)
    (root / 'current').symlink_to('b' * 40)
    at = datetime.fromtimestamp(__import__('time').time() - ago, timezone.utc).isoformat()
    (root / 'last.json').write_text(json.dumps({'sha': 'b' * 40, 'previous': previous, 'status': 'accepted', 'how': how, 'at': at}))
    return at


def test_undo_asks_the_deployer_for_the_version_from_before_for_a_day(tmp_path, monkeypatch):
    root = tmp_path / 'releases'
    monkeypatch.setenv('HUB_RELEASES_DIR', str(root))
    at = _deployed(root)
    owner = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
    undo = client.get('/app/api/versions').json()['deploy']['undo']
    assert {k: undo[k] for k in ('short', 'previous', 'at', 'auto')} == {'short': 'bbbbbbb', 'previous': 'aaaaaaa', 'at': at, 'auto': True}
    assert 23 * 3600 < undo['until'] - __import__('time').time() < 24 * 3600
    assert client.post('/app/api/deploy/undo', headers={'origin': 'http://testserver'}).status_code == 403
    assert client.post('/app/api/deploy/undo', headers=owner).json()['ok'] is True
    asked = json.loads((root / 'queue' / 'request.json').read_text())
    assert (asked['sha'], asked['undo'], asked['by']) == ('a' * 40, True, 'me@example.com')
    assert 'already updating' in client.post('/app/api/deploy/undo', headers=owner).json()['error']
    (root / 'queue' / 'request.json').unlink()
    for how, ago, previous in (('undo', 600, 'a' * 40), (None, 600, 'a' * 40), ('tap', 2 * 86400, 'a' * 40), ('tap', 600, 'c' * 40)):
        _deployed(root, how, ago, previous)
        if previous == 'c' * 40:
            (root / previous / 'venv').rmdir()                      # the version from before is gone
        assert release.undoable() is None, (how, ago)
        assert client.post('/app/api/deploy/undo', headers=owner).status_code == 409
    _deployed(root, 'tap')
    assert release.undoable()['auto'] is False                      # an update the owner tapped can be undone too


def test_hub_says_what_it_did_by_itself_on_today_and_on_the_phone(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from hub.engine import catalog
    from hub.tools.house import broker
    root = tmp_path / 'releases'
    monkeypatch.setenv('HUB_RELEASES_DIR', str(root))
    monkeypatch.setattr(release, 'running', lambda: 'b' * 40)
    _deployed(root)
    note = lambda status, error=None: (root / 'auto.json').write_text(json.dumps(  # noqa: E731
        {'sha': 'b' * 40, 'status': status, 'error': error, 'at': datetime.now(timezone.utc).isoformat()}))
    assert release.notice() is None and catalog.hub_release() == [] and release.tell() == 'Nothing to tell.'
    note('started')
    assert release.notice() is None                                 # under way: nothing to say yet
    note('accepted')
    monkeypatch.setattr(versions, 'entries', lambda: [{'sha': None, 'from': 'a' * 40, 'title': 'A made-up release'}])
    title, words = release.notice()
    assert title == 'Hub updated itself' and words.startswith('A made-up release.')
    monkeypatch.setattr(versions, 'entries', lambda: [{'sha': None, 'from': 'c' * 40, 'title': 'An older release'}])
    (root / 'pending.json').write_text(json.dumps({'sha': 'b' * 40, 'subjects': ['Merge pull request #1', 'Kitchen: a made-up fix']}))
    assert release.notice()[1].startswith('Kitchen: a made-up fix.')    # the newest entry was written for another release
    [item] = catalog.hub_release()
    assert (item['key'], item['tone'], item['cta'], item['href']) == ('hub-release-auto', 'info', 'Undo', catalog.VERSIONS)
    assert release.tell() == 'Shown on Today; no phone is named in Hub’s settings.'
    sent = []
    monkeypatch.setattr(broker, '_exchange', lambda message, timeout: sent.append(message) or [])
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'deploy': {'notify': 'mobile_app_test_phone'}}))
    assert release.tell() == 'Told the owner on their phone and on Today.'
    assert sent == [{'op': 'request', 'method': 'POST', 'path': '/api/services/notify/mobile_app_test_phone',
                     'body': {'title': title, 'message': release.notice()[1]}}]
    _deployed(root, 'undo')
    assert catalog.hub_release() == []                              # undone: nothing more to undo
    note('checks_failed', 'its checks on GitHub did not all pass')
    [item] = catalog.hub_release()
    assert (item['tone'], item['title']) == ('warn', 'A Hub update wasn’t deployed')
    note('refused', 'the build check failed:\nmany lines of output')
    assert release.notice()[1] == 'the build check failed. Nothing changed.'
    note('rolled_back', 'a check after the switch passed before and fails now: ask.golden')
    assert release.notice()[0] == 'A Hub update didn’t hold' and catalog.hub_release() == []    # its own item, from last.json


def test_the_deployer_starts_the_notice_as_a_module(tmp_path, monkeypatch, capsys):
    import runpy
    import sys
    monkeypatch.setenv('HUB_RELEASES_DIR', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['release', 'tell'])
    monkeypatch.delitem(sys.modules, 'hub.engine.release', raising=False)    # run fresh, as python -m does
    runpy.run_module('hub.engine.release', run_name='__main__')
    assert capsys.readouterr().out.strip() == 'Nothing to tell.'
