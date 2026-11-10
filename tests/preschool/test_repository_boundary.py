import pytest
from preschool.support import bridge
from hub.main import app


def test_preschool_cannot_request_gmail_mutations():
    assert not bridge.WRITE_PATHS
    with pytest.raises(bridge.BridgeError):
        bridge.post("/gmail/cleanup/apply", {})


def test_integrated_preschool_has_no_startup_scanner_or_second_service():
    import importlib.util
    from hub.tools import TOOLS
    assert any(tool['slug'] == 'preschool' for tool in TOOLS)
    assert importlib.util.find_spec('preschool.service') is None
    assert importlib.util.find_spec('preschool.main') is None
