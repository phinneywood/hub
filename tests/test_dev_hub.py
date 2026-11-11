"""deploy/dev-hub: the dev Hub's settings and data copy. Fictional paths only; nothing is started."""
import importlib.machinery
import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'deploy' / 'dev-hub'


def load():
    loader = importlib.machinery.SourceFileLoader('dev_hub', str(SCRIPT))
    spec = importlib.util.spec_from_loader('dev_hub', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


dev_hub = load()

LIVE = {'HUB_ALLOWED_LOGINS': 'alex@example.com', 'HUB_DATA_DIR': '/srv/example/live', 'HUB_PORT': '8780',
        'HUB_RELEASES_DIR': '/srv/example/releases', 'HUB_BACKGROUND_JOBS': 'enabled'}


def test_dev_env_keeps_sign_in_and_drops_the_deployer():
    env = dev_hub.dev_env(LIVE, Path('/srv/example/dev'))
    assert env['HUB_ALLOWED_LOGINS'] == 'alex@example.com'
    assert 'HUB_RELEASES_DIR' not in env
    assert env['HUB_BACKGROUND_JOBS'] == 'disabled'
    assert env['HUB_DATA_DIR'] == '/srv/example/dev'
    assert env['HUB_PORT'] == str(dev_hub.PORT) != LIVE['HUB_PORT']
    assert LIVE['HUB_RELEASES_DIR'] == '/srv/example/releases'  # the live settings are not changed


def test_service_listens_on_loopback_only_and_reloads():
    args = dev_hub.service(dev_hub.dev_env(LIVE, Path('/srv/example/dev')), repo=Path('/srv/example/hub'))['ProgramArguments']
    assert args[args.index('--host') + 1] == '127.0.0.1'
    assert '--reload' in args and args[0] == '/srv/example/hub/.venv/bin/python'


@pytest.mark.parametrize('data', ['live', 'live/dev'])
def test_refuses_a_data_copy_inside_live_data(tmp_path, data):
    (tmp_path / 'live').mkdir()
    with pytest.raises(SystemExit):
        dev_hub.copy_data(tmp_path / 'live', tmp_path / data)


def test_copies_files_and_databases_but_not_deploy_backups(tmp_path):
    live, data = tmp_path / 'live', tmp_path / 'dev'
    (live / 'trip-preview').mkdir(parents=True)
    (live / 'deploy-backups').mkdir()
    (live / 'deploy-backups' / 'old.json').write_text('{}')
    (live / 'settings.json').write_text('{"name": "Sam"}')
    db = sqlite3.connect(live / 'trip-preview' / 'trip.db')
    db.execute('create table trips (name text)')
    db.execute("insert into trips values ('Example City')")
    db.commit()
    db.close()
    (live / 'trip-preview' / 'trip.db-wal').write_text('')

    dev_hub.copy_data(live, data)

    assert (data / 'settings.json').read_text() == '{"name": "Sam"}'
    assert not (data / 'deploy-backups').exists()
    assert not (data / 'trip-preview' / 'trip.db-wal').exists()
    copy = sqlite3.connect(data / 'trip-preview' / 'trip.db')
    assert copy.execute('select name from trips').fetchall() == [('Example City',)]
    copy.close()
    assert oct(data.stat().st_mode & 0o777) == '0o700'
    assert oct((data / 'settings.json').stat().st_mode & 0o777) == '0o600'


def test_machine_names_from_tailscale_status():
    status = {'Self': {'DNSName': 'example-mini.tail0000.ts.net.'}}  # privacy-scan: allow (fictional)
    assert dev_hub.names(status)[0] == 'example-mini'
    assert dev_hub.names(status)[1].endswith('.ts.net') and not dev_hub.names(status)[1].endswith('.')


def test_the_dev_kitchen_is_used_only_when_asked_for():
    assert 'HUB_KITCHEN_SOCKET' not in dev_hub.dev_env(LIVE, Path('/srv/example/dev'))
    env = dev_hub.dev_env(LIVE, Path('/srv/example/dev'), '/srv/example/KitchenDev/kitchen.sock')
    assert env['HUB_KITCHEN_SOCKET'] == '/srv/example/KitchenDev/kitchen.sock'
