"""Accidental-effect guards for synthetic installed-wheel tests, not a sandbox."""
import importlib
import importlib.util
import os
from pathlib import Path
import pwd
import sys

root = Path(sys.argv[1]).resolve()
package = sys.argv[2]
assert Path(sys.prefix).resolve() == root / 'venv'
assert Path.home().resolve() == root / 'home' and not list(Path.home().iterdir())
real_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
runtime = Path(sys.base_prefix).resolve()
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
os.environ['HUB_DATA_DIR'] = str(root / 'synthetic-data')
os.environ['HUB_BRIDGE_TOKEN_FILE'] = str(root / 'absent-token')


def guard(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.bind', 'subprocess.Popen', 'os.system', 'os.posix_spawn'}:
        raise AssertionError('Verification cannot contact services, listen or run subprocesses.')
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if path.is_relative_to(real_home) and not path.is_relative_to(runtime) and not path.is_relative_to(root):
            raise AssertionError('Verification cannot read the real home directory.')


sys.addaudithook(guard)
for other in {'family_site'}:
    assert importlib.util.find_spec(other) is None, other
for name in ('hub', 'preschool'):
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(root / 'venv')
import pytest
raise SystemExit(pytest.main(['--import-mode=importlib', '-q', str(root / 'tests'),
                             '--basetemp', str(root / 'pytest-tmp'),
                             '-o', 'cache_dir=' + str(root / 'pytest-cache')]))
