"""The live feed: every open app hears about a change in the house within a few seconds, without asking.

While at least one app is open, one thread reads the house (the same read as everything else) and hands the
picture to every listener when it differs from the last one sent. Nobody open, nothing read: this is not a
background job. After Hub itself changes something, `poke()` reads again at once, and twice more shortly after,
because speakers take a moment to start.

Today the reading is asked for every few seconds. When the broker can pass on Home Assistant's own change
events (home-ops), `_read` is the one place that changes.
"""
import asyncio
import json
import threading
import time

from . import model

EVERY = 3.0             # seconds between readings while someone is listening
AFTER = (0.0, 1.0, 3.0)  # readings after a change Hub made: now, then as speakers and scenes settle
LINGER = 20.0           # how long the reader outlives its last listener, so a reload doesn't restart it


class Feed:
    def __init__(self, read=None, every=EVERY):
        self._read = read or (lambda: model.now(fresh=True))
        self._every = every
        self._lock = threading.Lock()
        self._listeners = {}        # queue -> its event loop
        self._thread = None
        self._wake = threading.Event()
        self._due = []              # extra readings owed after a change, as monotonic times
        self._last = None           # the last picture sent, as text
        self._empty_since = None

    def listen(self, loop):
        """A queue that receives each new picture as JSON text. Call `leave` with it when done."""
        queue = asyncio.Queue(maxsize=8)
        with self._lock:
            self._listeners[queue] = loop
            self._empty_since = None
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name='house-feed', daemon=True)
                self._thread.start()
        return queue

    def leave(self, queue):
        with self._lock:
            self._listeners.pop(queue, None)
            if not self._listeners:
                self._empty_since = time.monotonic()

    def last(self):
        """The last picture sent, as text, or None before the first reading."""
        with self._lock:
            return self._last

    def poke(self):
        """Hub just changed the house: read again now and as it settles."""
        now = time.monotonic()
        with self._lock:
            self._due = [now + after for after in AFTER]
        self._wake.set()

    def _send(self, text):
        with self._lock:
            if text == self._last:
                return
            self._last, listeners = text, list(self._listeners.items())
        for queue, loop in listeners:
            try:
                loop.call_soon_threadsafe(_offer, queue, text)
            except RuntimeError:        # that listener's loop has closed
                self.leave(queue)

    def _run(self):
        while True:
            with self._lock:
                idle = self._empty_since is not None and time.monotonic() - self._empty_since > LINGER
                if idle:
                    self._thread, self._last, self._due = None, None, []
                    return
                listening = bool(self._listeners)
            if listening:
                self._send(json.dumps(self._read(), separators=(',', ':'), sort_keys=True))
            now = time.monotonic()
            with self._lock:
                self._due = [t for t in self._due if t > now]
                wait = min([self._every] + [t - now for t in self._due])
            self._wake.wait(max(0.05, wait))
            self._wake.clear()


def _offer(queue, text):
    """Runs on the listener's loop. A listener that has fallen behind only needs the newest picture."""
    while queue.full():
        queue.get_nowait()
    queue.put_nowait(text)


feed = Feed()
