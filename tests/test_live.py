"""Live checks, on a made-up household: each check passes when the system is well and fails for the reason
it was written for, results and history are kept, a failing check reaches Today, and nothing that changes the
house runs unless the owner switched it on."""
import json
import sys

import pytest
from fastapi.testclient import TestClient

from hub import bridge, config
from hub.live import __main__ as cli
from hub.live import checks, routes, runner
from hub.tools.house import broker, control, music
from tests import golden_world

SIGNED_IN = {'tailscale-user-login': 'me@example.com'}
LAMP = 'light.reading_lamp'


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(checks, 'SEARCHES', (('piano', 'playlist'), ('light', 'track')))
    return golden_world.install(monkeypatch)


def _settings(**chosen):
    path = config.DATA_DIR / 'household.json'
    found = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**found, 'checks': chosen}))


def _only(monkeypatch, *made):
    """Replace the checks with made-up ones: (id, effect, function)."""
    monkeypatch.setattr(checks, 'CHECKS', tuple(checks.Check(i, i.title(), 'About ' + i, effect, run, 5) for i, effect, run in made))


def _fail(words='It broke.'):
    def run():
        raise checks.Failed(words)
    return run


# ---- the checks themselves ----

def test_the_house_and_speaker_checks_pass_on_a_working_house_and_say_why_when_it_is_down(world, monkeypatch):
    assert 'entities' in checks.house() and 'speakers are reachable' in checks.speakers()

    def down(message, timeout):
        raise broker.NotSent('The Home Assistant broker on this Mac isn’t answering.')
    monkeypatch.setattr(broker, '_exchange', down)
    with pytest.raises(checks.Failed, match='broker'):
        checks.house()
    with pytest.raises(checks.Failed, match='broker'):
        checks.speakers()


def test_the_music_check_fails_when_hub_would_refuse_what_music_assistant_found(world, monkeypatch):
    assert 'would play all' in checks.music_search()
    # The bug of 2026-11-14: the address check knew no provider instances, so every catalog find was refused.
    monkeypatch.setattr(broker, 'MEDIA_URI', __import__('re').compile(r'^library://(?P<kind>track|playlist)/\d+$'))
    with pytest.raises(checks.Failed, match='refuse to play'):
        checks.music_search()


def test_the_music_check_fails_on_an_empty_search_and_is_skipped_without_an_address(world, monkeypatch):
    monkeypatch.setattr(checks, 'SEARCHES', (('nothing-is-called-this', 'playlist'),))
    with pytest.raises(checks.Failed, match='found nothing'):
        checks.music_search()
    (config.DATA_DIR / 'household.json').write_text('{}')
    with pytest.raises(checks.Skipped):
        checks.music_search()


def test_the_mail_check_fails_if_the_helper_could_ever_send(monkeypatch):
    with pytest.raises(checks.Failed, match='isn’t answering'):
        checks.mail()
    monkeypatch.setattr(bridge, 'health', lambda: True)
    monkeypatch.setattr(bridge, 'get', lambda path, **kw: {'sending_available': False})
    assert 'can’t send' in checks.mail()
    monkeypatch.setattr(bridge, 'get', lambda path, **kw: {'sending_available': True})
    with pytest.raises(checks.Failed, match='never should'):
        checks.mail()


def test_the_pages_check_names_a_page_that_fails_or_is_slow(monkeypatch):
    monkeypatch.setattr(checks, '_get', lambda path, timeout=0: (200, 0.2))
    assert 'pages loaded' in checks.pages()
    monkeypatch.setattr(checks, '_get', lambda path, timeout=0: (500 if path == '/today' else 200, 0.2))
    with pytest.raises(checks.Failed, match=r'/today \(500\)'):
        checks.pages()
    monkeypatch.setattr(checks, '_get', lambda path, timeout=0: (200, 30.0 if path == '/gmail' else 0.2))
    with pytest.raises(checks.Failed, match='/gmail'):
        checks.pages()
    monkeypatch.setattr(checks, '_get', lambda path, timeout=0: (302, 0.1))     # a redirect is an answer
    assert 'pages loaded' in checks.pages()


def test_every_check_has_a_known_effect_and_a_unique_id():
    assert len({c.id for c in checks.CHECKS}) == len(checks.CHECKS)
    assert {c.effect for c in checks.CHECKS} <= {'read', 'spend', 'undo'}
    assert [c.id for c in checks.CHECKS if c.effect == 'undo'] == ['house.light']


# ---- the one real change ----

def test_the_light_check_is_off_until_named_and_never_runs_in_quiet_hours(world, monkeypatch):
    assert checks.quiet(22) and checks.quiet(3) and not checks.quiet(12)
    with pytest.raises(checks.Skipped, match='No test light'):
        checks.light()
    _settings(undo=True, undo_light=LAMP)
    monkeypatch.setattr(checks, 'quiet', lambda hour=None: True)
    with pytest.raises(checks.Skipped, match='Quiet hours'):
        checks.light()
    assert world.sent == []


def test_the_light_check_changes_the_named_light_and_puts_it_back(world, monkeypatch):
    _settings(undo=True, undo_light=LAMP)
    monkeypatch.setattr(checks, 'quiet', lambda hour=None: False)
    assert checks.light() == 'The test light changed and was put back.'
    services = [m['path'].rsplit('/', 1)[-1] for kind, m in world.sent if kind == 'home']
    assert sorted(services) == ['turn_off', 'turn_on'] and all(m['body']['entity_id'] == LAMP for _, m in world.sent)
    rows = control.recent()
    assert {r['via'] for r in rows} == {'check', 'undo'} and {r['login'] for r in rows} == {checks.LOGIN}


def test_a_run_only_makes_a_real_change_when_allowed_and_switched_on(world, monkeypatch):
    monkeypatch.setattr(checks, 'quiet', lambda hour=None: False)
    _settings(undo_light=LAMP)                                   # named, but not switched on
    assert runner.allowed(with_undo=True) == {'read', 'spend'}
    _settings(undo=True, undo_light=LAMP)
    assert runner.allowed(with_undo=False) == {'read', 'spend'}  # the page's button never allows it
    assert runner.allowed(with_undo=True) == {'read', 'spend', 'undo'}
    result = runner.run('shell', only=('house.light',), effects=runner.allowed())
    assert result['results'][0]['status'] == 'skip' and world.sent == []


# ---- running and remembering ----

def test_a_run_records_each_result_and_survives_a_check_that_breaks_or_hangs(monkeypatch):
    import time

    def broken():
        raise RuntimeError('with a private detail in it')
    _only(monkeypatch, ('a.ok', 'read', lambda: 'Fine.'), ('b.fails', 'read', _fail('Wrong answer.')), ('c.breaks', 'read', broken),
          ('d.hangs', 'read', lambda: time.sleep(30)), ('e.spends', 'spend', lambda: 'Called.'))
    monkeypatch.setattr(checks, 'CHECKS', tuple(c if c.id != 'd.hangs' else checks.Check(c.id, c.name, c.about, c.effect, c.run, 0.2)
                                                for c in checks.CHECKS))
    result = runner.run('shell', effects={'read'})
    got = {r['id']: (r['status'], r['words']) for r in result['results']}
    assert got == {'a.ok': ('pass', 'Fine.'), 'b.fails': ('fail', 'Wrong answer.'),
                   'c.breaks': ('fail', 'The check stopped with RuntimeError.'),      # the type, never the message
                   'd.hangs': ('fail', 'No answer within 0.2 s.'), 'e.spends': ('skip', 'Left out of this run.')}
    assert (result['passed'], result['failed'], result['skipped']) == (1, 3, 1)
    latest = runner.latest()
    assert latest['id'] == result['id'] and latest['trigger'] == 'shell' and len(latest['results']) == 5
    assert (config.DATA_DIR / runner.FILE).stat().st_mode & 0o777 == 0o600
    assert not runner.running()


def test_history_dates_a_failure_to_the_run_and_version_it_started_in(monkeypatch):
    state = {'ok': True, 'version': 'aaaaaaa'}

    def flaky():
        if not state['ok']:
            raise checks.Failed('It broke.')
        return 'Fine.'
    _only(monkeypatch, ('music.search', 'read', flaky))
    monkeypatch.setattr(runner, 'version', lambda: state['version'])
    runner.run('morning')
    assert runner.failing() == []
    state.update(ok=False, version='bbbbbbb')
    runner.run('deploy')
    runner.run('morning')
    failing = runner.failing()
    assert len(failing) == 1 and failing[0]['new'] and failing[0]['runs'] == 2
    assert failing[0]['since_version'] == 'bbbbbbb' and failing[0]['last_pass_version'] == 'aaaaaaa'
    assert [h['status'] for h in runner.history()['music.search']] == ['pass', 'fail', 'fail']
    state['ok'] = True
    runner.run('manual')
    assert runner.failing() == []


def test_a_skipped_result_never_hides_a_failure(monkeypatch):
    _only(monkeypatch, ('x.one', 'read', _fail()))
    runner.run('morning')
    monkeypatch.setattr(checks, 'CHECKS', (checks.Check('x.one', 'X', 'About', 'spend', lambda: 'Fine.', 5),))
    runner.run('morning', effects={'read'})                     # left out this time
    assert [f['id'] for f in runner.failing()] == ['x.one']


def test_old_history_is_dropped(monkeypatch):
    import time
    _only(monkeypatch, ('x.one', 'read', lambda: 'Fine.'))
    real = time.time
    monkeypatch.setattr(runner.time, 'time', lambda: real() - (runner.KEEP_DAYS + 1) * 86400)
    runner.run('morning')
    monkeypatch.setattr(runner.time, 'time', real)
    runner.run('morning')
    assert len(runner.history()['x.one']) == 1


def test_only_one_run_at_a_time(monkeypatch):
    import os
    _only(monkeypatch, ('x.one', 'read', lambda: 'Fine.'))
    runner._lock().write_text(str(os.getpid()))
    with pytest.raises(runner.Busy):
        runner.run('manual')
    runner._lock().write_text('999999999')                       # a run that died: its mark doesn't block the next
    assert runner.run('manual')['passed'] == 1


# ---- the page, Today, the morning job and the command line ----

def test_the_checks_page_shows_history_and_starts_a_run_in_its_own_process(monkeypatch):
    from hub.main import app
    started = []
    monkeypatch.setattr(routes.subprocess, 'Popen', lambda args, **kw: started.append((args, kw['env']['HUB_DATA_DIR'])))
    _only(monkeypatch, ('a.ok', 'read', lambda: 'Fine.'), ('b.fails', 'read', _fail('Wrong answer.')))
    client = TestClient(app)
    assert 'No run yet' in client.get('/checks', headers=SIGNED_IN).text
    runner.run('manual')
    page = client.get('/checks', headers=SIGNED_IN).text
    assert '1 failing' in page and 'Wrong answer.' in page and 'it has never passed' in page and 'class="pass"' in page
    assert client.get('/checks', headers={'tailscale-user-login': 'stranger@example.com'}).status_code == 403
    assert client.post('/checks/run', headers={**SIGNED_IN, 'origin': 'http://evil.example'}).status_code == 403
    reply = client.post('/checks/run', headers={**SIGNED_IN, 'origin': 'http://testserver'}, follow_redirects=False)
    assert reply.status_code == 303
    assert started == [([sys.executable, '-m', 'hub.live', 'run', '--trigger', 'manual'], str(config.DATA_DIR))]
    assert 'Checks' in client.get('/settings', headers=SIGNED_IN).text


def test_a_failing_check_is_an_item_on_today_until_it_passes(monkeypatch):
    from hub.engine import catalog
    state = {'ok': False}

    def flaky():
        if not state['ok']:
            raise checks.Failed('It broke.')
        return 'Fine.'
    _only(monkeypatch, ('music.search', 'read', flaky))
    assert catalog.live_items() == []
    runner.run('morning')
    item, = catalog.live_items()
    assert item['title'] == '1 live check failing' and item['href'] == '/checks' and 'Music.Search' in item['why']
    state['ok'] = True
    runner.run('morning')
    assert catalog.live_items() == []


def test_the_morning_job_runs_the_checks_only_when_the_owner_switched_them_on(monkeypatch):
    started = []
    monkeypatch.setattr(routes.subprocess, 'Popen', lambda args, **kw: started.append(args[2:]))
    assert routes.morning() is False and started == []
    _settings(daily=True)
    assert routes.morning() is True
    _settings(daily=True, undo=True, undo_light=LAMP)
    routes.morning()
    assert started == [['hub.live', 'run', '--trigger', 'morning'], ['hub.live', 'run', '--trigger', 'morning', '--with-undo']]


def test_the_command_line_gates_on_a_failure(monkeypatch, capsys):
    state = {'ok': True}

    def flaky():
        if not state['ok']:
            raise checks.Failed('Wrong answer.')
        return 'Fine.'
    _only(monkeypatch, ('a.ok', 'read', lambda: 'Fine.'), ('b.fails', 'read', flaky), ('c.down', 'read', _fail('Switched off.')))
    assert cli.main(['run', '--gate']) == 0                      # c.down has never passed: it was down before this version
    assert cli.main(['run', '--gate-all']) == 1
    state['ok'] = False
    assert cli.main(['run', '--gate', '--only', 'a.ok']) == 0
    assert cli.main(['run', '--gate']) == 1                      # b.fails passed last time and fails now: the gate shuts
    assert cli.main(['run', '--gate']) == 0                      # still failing is not newly failing
    assert cli.main(['run']) == 0                                # without a gate a failure is reported, not an error
    assert cli.main(['run', '--only', 'no.such']) == 2
    out = capsys.readouterr().out
    assert '✗ b.fails' in out and 'Wrong answer.' in out and '1 passed, 2 failed' in out
    assert 'Passed last time, fails now: b.fails' in out
    assert cli.main(['failing']) == 0 and 'b.fails' in capsys.readouterr().out
    assert cli.main(['list']) == 0


# ---- the checks that call the model, and the smaller connections ----

def test_the_model_check_makes_one_call_with_invented_data_and_records_it(monkeypatch):
    from hub import home, luna
    from hub.engine import state
    with pytest.raises(checks.Failed, match='key'):
        checks.model()
    sent = []

    def call(request):
        sent.append(json.dumps(request))
        return ({'unsupported': None, 'skip_inbox': False, 'mark_read': False, 'label': 'Receipts', 'future': True, 'existing': False,
                 'older_than_days': None, 'subject_include': [], 'subject_exclude': []}, {'tokens_in': 300, 'tokens_out': 40, 'cost': 0.0001})
    monkeypatch.setattr(luna, 'configured', lambda: True)
    monkeypatch.setattr(luna, 'call', call)
    assert 'private route' in checks.model()
    assert len(sent) == 1 and 'example.invalid' in sent[0]
    assert state.spent(home.today().isoformat(), checks.KIND)['month_cost'] == pytest.approx(0.0001)

    def down(request):
        raise luna.LunaError('Luna couldn’t be reached')
    monkeypatch.setattr(luna, 'call', down)
    with pytest.raises(checks.Failed, match='reached'):
        checks.model()


def test_the_ask_check_runs_the_ask_in_the_app_not_the_older_chat(world, monkeypatch):
    from hub.app.ask import trial
    with pytest.raises(checks.Skipped):                          # no key: nothing to run, not a failure of Ask
        checks.ask()
    ran = []
    monkeypatch.setattr(trial, 'run', lambda kind='setup': ran.append(kind) or {'ran': 4, 'failed': [], 'left_out': [], 'busy': [], 'cost': 0.004})
    assert checks.ask() == '4 everyday Ask requests went the right way as a dry run ($0.004).' and ran == ['core']


def test_the_calendar_and_kitchen_checks(world, monkeypatch, tmp_path):
    from hub.tools.calendar import read
    from hub.tools.kitchen import client
    monkeypatch.setattr(read, 'week', lambda: [])
    assert checks.calendar() == 'The calendar week was read.'

    def lapsed():
        raise read.SignInNeeded()
    monkeypatch.setattr(read, 'week', lapsed)
    with pytest.raises(checks.Failed, match='sign-in'):
        checks.calendar()
    monkeypatch.setattr(config, 'KITCHEN_SOCKET', tmp_path / 'absent.sock')
    with pytest.raises(checks.Skipped):
        checks.kitchen()
    (tmp_path / 'kitchen.sock').touch()
    monkeypatch.setattr(config, 'KITCHEN_SOCKET', tmp_path / 'kitchen.sock')
    assert checks.kitchen() == 'Kitchen answered.'

    def down(method, path, body=None, timeout=10):
        raise client.KitchenDown('OSError')
    monkeypatch.setattr(client, 'call', down)
    with pytest.raises(checks.Failed, match='Kitchen'):
        checks.kitchen()


def test_the_door_checks_are_skipped_until_the_door_is_set_up_then_test_the_gate_and_the_sign_in(monkeypatch):
    import io
    import urllib.error
    from hub.live.checks import urllib as lib
    with pytest.raises(checks.Skipped):
        checks.door_gate()
    with pytest.raises(checks.Skipped):
        checks.door_sign_in()
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'door': {'upstream': 'http://192.0.2.10:8123', 'logins': ['me@example.com']}}))

    class Opener:
        def __init__(self, status):
            self.status = status

        def open(self, request, timeout=0):
            assert 'Tailscale-user-login' not in request.headers            # the check itself carries no login
            if self.status >= 400:
                raise urllib.error.HTTPError(request.full_url, self.status, 'x', {}, io.BytesIO(b''))
            return io.BytesIO(b'')
    monkeypatch.setattr(lib.request, 'build_opener', lambda *a: Opener(403))
    assert 'turns away' in checks.door_gate()
    monkeypatch.setattr(lib.request, 'build_opener', lambda *a: Opener(502))
    with pytest.raises(checks.Failed, match='502'):
        checks.door_gate()

    def providers(kinds):
        body = io.BytesIO(json.dumps({'providers': [{'type': k} for k in kinds]}).encode())
        monkeypatch.setattr(lib.request, 'urlopen', lambda url, timeout=0: body)
    providers(['trusted_networks', 'homeassistant'])
    assert 'without a password' in checks.door_sign_in()
    providers(['homeassistant'])
    with pytest.raises(checks.Failed, match='password'):
        checks.door_sign_in()
