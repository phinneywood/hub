import pytest
from preschool.support import bridge, config
from tests.preschool.fictional_school import write_school

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "ALLOWED_LOGINS", {"me@example.com"})
    write_school(tmp_path)
    bridge.clear_cache()


@pytest.fixture(autouse=True)
def no_live_credentials_or_network(monkeypatch):
    import socket
    from preschool.support import luna
    monkeypatch.setattr(luna, "key", lambda: None)
    def denied(*args, **kwargs):
        raise AssertionError("Tests must not connect to live services")
    monkeypatch.setattr(socket.socket, "connect", denied)
