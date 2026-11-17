import pytest

from hub import bridge, config, worker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "ALLOWED_LOGINS", {"me@example.com"})
    bridge.clear_cache()
    from hub.tools.house import music, read as house
    house.clear_cache()
    music._found.clear()


@pytest.fixture(autouse=True)
def quiet_worker():
    yield
    worker.wait(10)


@pytest.fixture(autouse=True)
def no_live_credentials_or_network(monkeypatch):
    import socket
    from pathlib import Path
    from hub import luna, model_key
    from hub.tools.house import apple
    from preschool.support import luna as preschool_luna
    monkeypatch.setattr(luna, "key", lambda: None)
    monkeypatch.setattr(preschool_luna, "key", lambda: None)
    monkeypatch.setattr(model_key, "_keychain", lambda service: None)
    monkeypatch.setattr(model_key, "_onepassword", lambda reference=None: None)
    monkeypatch.setattr(model_key, "_exists", lambda service: False)
    model_key._cache.update(value=None, source=None, at=float("-inf"))
    monkeypatch.setattr(bridge, "health", lambda: False)
    monkeypatch.setattr(apple, "CLI", Path("/nonexistent/homeclaw-cli"))     # never this Mac's own HomeClaw
    def denied(*args, **kwargs):
        raise AssertionError("Tests must not connect to live services")
    monkeypatch.setattr(socket.socket, "connect", denied)
