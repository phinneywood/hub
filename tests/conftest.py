import pytest

from hub import bridge, config, worker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "ALLOWED_LOGINS", {"me@example.com"})
    bridge.clear_cache()


@pytest.fixture(autouse=True)
def quiet_worker():
    yield
    worker.wait(10)


@pytest.fixture(autouse=True)
def no_live_credentials_or_network(monkeypatch):
    import socket
    from hub import luna, model_key
    from preschool.support import luna as preschool_luna
    monkeypatch.setattr(luna, "key", lambda: None)
    monkeypatch.setattr(preschool_luna, "key", lambda: None)
    monkeypatch.setattr(model_key, "_keychain", lambda service: None)
    monkeypatch.setattr(model_key, "_onepassword", lambda: None)
    monkeypatch.setattr(model_key, "_exists", lambda service: False)
    model_key._cache.update(value=None, source=None, at=float("-inf"))
    monkeypatch.setattr(bridge, "health", lambda: False)
    def denied(*args, **kwargs):
        raise AssertionError("Tests must not connect to live services")
    monkeypatch.setattr(socket.socket, "connect", denied)
