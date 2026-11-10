"""Runs the plan in a background thread so it finishes even if the phone page is closed."""
import threading

from . import apply, store

_lock = threading.Lock()
_thread = None
state = {"paused": False}


def running():
    return _thread is not None and _thread.is_alive()


def start():
    """Begin (or resume) applying. Returns False if a worker is already running."""
    global _thread
    with _lock:
        if running():
            return False
        state["paused"] = False
        _thread = threading.Thread(target=_loop, name="plan-worker", daemon=True)
        _thread.start()
        return True


def wait(timeout=30):
    thread = _thread
    if thread:
        thread.join(timeout)


def _loop():
    try:
        while True:
            outcome = apply.run()
            if outcome["paused"]:
                state["paused"] = True
                return
            if outcome["remaining"] == 0:
                return
    except Exception as err:
        store.log("stopped unexpectedly", type(err).__name__)


def resume_if_interrupted():
    """After a restart, continue work that had already begun. Never starts untouched plan items."""
    if any(i["status"] in ("applying", "undoing") for i in store.items()):
        start()
