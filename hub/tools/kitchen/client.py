"""Talks to the separate kitchen service over its owner-only Unix socket. Never reaches the network."""
import http.client
import json
import socket

from ... import config


class KitchenDown(Exception):
    pass


class _Unix(http.client.HTTPConnection):
    def __init__(self, timeout):
        super().__init__("kitchen", timeout=timeout)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(config.KITCHEN_SOCKET))


def call(method, path, body=None, timeout=10):
    """(status, JSON) from the service. Raises KitchenDown when it isn't answering."""
    conn = _Unix(timeout)
    try:
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise KitchenDown(type(exc).__name__) from None
    finally:
        conn.close()
