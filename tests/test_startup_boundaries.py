import pytest
from fastapi.testclient import TestClient

from hub import bridge, config, delayed, luna, main, store, worker


@pytest.mark.parametrize("value, expected", [("enabled", True), ("disabled", False)])
def test_background_setting_is_explicit(value, expected):
    assert config.background_jobs_enabled(value) is expected


@pytest.mark.parametrize("value", ["", "false", "true", "0", "1", "DISABLED", "disabled ", None])
def test_invalid_background_setting_fails_closed(value):
    with pytest.raises(ValueError, match="HUB_BACKGROUND_JOBS"):
        config.background_jobs_enabled(value)


def test_disabled_startup_and_readiness_have_no_application_effects(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Controlled startup/readiness must not access effects")

    monkeypatch.setattr(config, "BACKGROUND_JOBS_ENABLED", False)
    for module, names in ((bridge, ("get", "post", "health", "clear_cache")),
                          (store, ("db",)), (luna, ("key",)),
                          (worker, ("start", "resume_if_interrupted")),
                          (delayed, ("start", "recover_interrupted", "run_due"))):
        for name in names:
            monkeypatch.setattr(module, name, denied)
    monkeypatch.setattr(main, "warm_cache", denied)
    with TestClient(main.app) as client:
        for path in ("/readyz", "/readyz?refresh=1"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.json() == {"ok": True, "mode": "dashboard", "production_ready": False,
                                       "background_jobs_enabled": False}
            assert response.headers["cache-control"] == "no-store"
        assert client.get("/readyz", headers={"Tailscale-User-Login": "other@example.invalid"}).status_code == 403


def test_enabled_startup_preserves_existing_sequence_without_real_jobs(monkeypatch):
    calls = []

    class FakeThread:
        def __init__(self, *, target, daemon):
            assert target is main.warm_cache
            assert daemon is True

        def start(self):
            calls.append("warm-cache-thread")

    monkeypatch.setattr(config, "BACKGROUND_JOBS_ENABLED", True)
    monkeypatch.setattr(main.threading, "Thread", FakeThread)
    monkeypatch.setattr(worker, "resume_if_interrupted", lambda: calls.append("resume"))
    monkeypatch.setattr(delayed, "start", lambda: calls.append("delayed"))
    main.startup()
    assert calls == ["warm-cache-thread", "resume", "delayed"]
