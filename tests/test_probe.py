import builtins
import importlib.util
import pytest
from fastapi.testclient import TestClient

from hub import bridge, delayed, luna, probe, store, worker


def test_probe_import_does_not_import_application_modules(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        level = kwargs.get("level", args[3] if len(args) > 3 else 0)
        if name == "hub" or name.startswith("hub.") or level:
            raise AssertionError("Probe must not import application modules")
        return original(name, *args, **kwargs)

    # Execute the actual packaged module again with application imports rejected.
    spec = importlib.util.spec_from_file_location("isolated_probe", probe.__file__)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(builtins, "__import__", guarded)
    spec.loader.exec_module(module)
    assert module.app is not probe.app


def test_probe_lifecycle_has_no_application_effects(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Probe must not access application effects")

    for module, names in ((bridge, ("get", "post", "health")),
                          (store, ("db",)), (luna, ("key",)),
                          (worker, ("start", "resume_if_interrupted")),
                          (delayed, ("start", "recover_interrupted", "run_due"))):
        for name in names:
            monkeypatch.setattr(module, name, denied)
    # TestClient itself needs a portal thread; guard application entrypoints here.
    with TestClient(probe.app) as client:
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "mode": "probe-only", "production_ready": False}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/", "/status", "/gmail", "/network", "/mobility",
                                  "/docs", "/redoc", "/openapi.json"])
def test_probe_does_not_expose_dashboard_routes(path):
    with TestClient(probe.app) as client:
        assert client.get(path).status_code == 404


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_probe_rejects_mutations(method):
    with TestClient(probe.app) as client:
        assert getattr(client, method)("/readyz").status_code == 405
