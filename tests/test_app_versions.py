"""The Versions screen's data (hub/app/versions.py) and the hand-kept list behind it."""
import json
import re

from fastapi.testclient import TestClient

from hub import config
from hub.app import versions
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
