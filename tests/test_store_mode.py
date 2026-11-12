import stat
from contextlib import closing

from hub import config, store


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_new_database_is_owner_only():
    with closing(store.db()):
        pass
    assert mode(config.DATA_DIR / "hub.db") == 0o600


def test_existing_loose_database_is_tightened():
    path = config.DATA_DIR / "hub.db"
    path.touch()
    path.chmod(0o644)
    with closing(store.db()):
        pass
    assert mode(path) == 0o600
