"""The live feed: every open app hears about a change in the house, without asking.

While at least one app is open, one thread reads the house (the same read as everything else) and hands the
picture to every listener when it differs from the last one sent. Nobody open, nothing read: this is not a
background job. After Hub itself changes something, `poke()` reads again at once, and twice more shortly after,
because speakers take a moment to start.

When to read: a second thread listens to the broker, which says which device changed the moment it does
(`broker.listen`; the device, never its state), and each such word brings a reading a moment later. While that
line is up the house is otherwise read only now and then, as a safety net. When it isn't (an older broker, or
the broker away), the house is read every few seconds instead, as before.
"""
import asyncio
import json
import threading
import time

from ..tools.house import broker
from . import model

EVERY = 3.0             # seconds between readings while someone is listening and the broker isn't passing changes on
RELAXED = 30.0          # seconds between readings while it is: only a safety net
SOON = 0.15             # a change the broker reports is read this much later, so a burst makes one reading
RETRY = 60.0            # how long to wait before asking the broker to pass changes on again
AFTER = (0.0, 1.0, 3.0)  # readings after a change Hub made: now, then as speakers and scenes settle
LINGER = 20.0           # how long the reader outlives its last listener, so a reload doesn't restart it


class Feed:
    def __init__(self, read=None, every=EVERY, hear=None, relaxed=RELAXED, retry=RETRY):
        self._read = read or (lambda: model.now(fresh=True))
        self._hear = hear or broker.listen      # an iterator: a device's id when it changes, None for a beat
        self._every, self._relaxed, self._retry = every, relaxed, retry
        self._heard = False         # the broker is passing changes on right now
        self._watcher = None
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
            if self._watcher is None or not self._watcher.is_alive():
                self._watcher = threading.Thread(target=self._watch, name='house-watch', daemon=True)
                self._watcher.start()
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

    def nudge(self):
        """The house changed by itself (the broker said so): read it in a moment."""
        soon = time.monotonic() + SOON
        with self._lock:
            if not any(t <= soon for t in self._due):
                self._due.append(soon)
        self._wake.set()

    def listening_live(self):
        """Whether changes arrive from the broker as they happen (else the house is read on a timer)."""
        return self._heard

    def _wanted(self):
        with self._lock:
            return bool(self._listeners)

    def _watch(self):
        """Listen to the broker for as long as an app is open. Any failure just means reading on the timer."""
        stop = threading.Event()
        while self._wanted():
            try:
                for changed in self._hear():
                    self._heard = True
                    if changed is not None:
                        self.nudge()
                    if not self._wanted():
                        break
            except Exception:       # noqa: BLE001  listening is an extra; without it the timer does the work
                pass
            self._heard = False
            if self._wanted():
                stop.wait(self._retry)

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
                wait = min([self._relaxed if self._heard else self._every] + [t - now for t in self._due])
            self._wake.wait(max(0.05, wait))
            self._wake.clear()


def _offer(queue, text):
    """Runs on the listener's loop. A listener that has fallen behind only needs the newest picture."""
    while queue.full():
        queue.get_nowait()
    queue.put_nowait(text)


feed = Feed()
