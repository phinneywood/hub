"""Model key: 1Password first, older Keychain item as fallback, values never shown. Synthetic values only."""
import subprocess

from fastapi.testclient import TestClient

from hub import model_key
from hub.main import app



class Ran:
    def __init__(self, returncode=0, stdout=''):
        self.returncode, self.stdout = returncode, stdout


def _real(monkeypatch, keychain, op_reply):
    """Restore the real readers over a fake `subprocess.run` that records what would run."""
    import importlib
    fresh = importlib.reload(model_key)
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == '/usr/bin/security':
            value = keychain.get(args[args.index('-s') + 1])
            return Ran(0 if value else 44, (value or '') + '\n')
        return op_reply(kwargs['env'])
    monkeypatch.setattr(fresh.subprocess, 'run', run)
    return fresh, calls


def test_1password_is_used_first_with_the_token_only_in_ops_environment(monkeypatch):
    mk, calls = _real(monkeypatch, {'hub-1password-token': 'ops_synthetic', 'hub-openrouter': 'legacy-synthetic'},
                      lambda env: Ran(0, 'from-1password-synthetic'))
    assert mk.read() == 'from-1password-synthetic' and mk.source() == '1password'
    [_, (op_args, op_kwargs)] = calls
    assert op_args[1:] == ['read', '--no-newline', 'op://Hub/OpenRouter/credential']
    assert op_kwargs['env']['OP_SERVICE_ACCOUNT_TOKEN'] == 'ops_synthetic' and 'ops_synthetic' not in op_args
    assert set(op_kwargs['env']) == {'HOME', 'PATH', 'OP_SERVICE_ACCOUNT_TOKEN'} and op_kwargs['timeout'] <= 30
    mk.read()
    assert len(calls) == 2  # cached; 1Password isn't asked on every page view


def test_falls_back_to_the_older_keychain_item(monkeypatch):
    mk, calls = _real(monkeypatch, {'hub-1password-token': 'ops_synthetic', 'hub-openrouter': 'legacy-synthetic'},
                      lambda env: Ran(1, ''))
    assert mk.read() == 'legacy-synthetic' and mk.source() == 'keychain'
    mk, calls = _real(monkeypatch, {'hub-openrouter': 'legacy-synthetic'}, lambda env: 1 / 0)
    assert mk.read() == 'legacy-synthetic' and len(calls) == 2  # no token: op never runs
    mk, _ = _real(monkeypatch, {}, lambda env: 1 / 0)
    assert mk.read() is None and mk.source() is None


def test_op_that_hangs_or_is_missing_falls_back(monkeypatch):
    def hang(env):
        raise subprocess.TimeoutExpired('op', 20)
    mk, _ = _real(monkeypatch, {'hub-1password-token': 'ops_synthetic', 'hub-openrouter': 'legacy-synthetic'}, hang)
    assert mk.read() == 'legacy-synthetic'


def test_status_page_says_where_the_key_comes_from_never_what_it_is(monkeypatch):
    monkeypatch.setattr(model_key, 'source', lambda fresh=False: '1password')
    page = TestClient(app).get('/status').text
    assert 'Model key' in page and 'from 1Password' in page
    monkeypatch.setattr(model_key, 'source', lambda fresh=False: None)
    assert 'not set up' in TestClient(app).get('/status').text
