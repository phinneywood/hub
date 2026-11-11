"""Keeps a demo server on this machine and away from real secrets. Used by the browser checks' demo servers.

Call `apply(patch)` before the app is imported or anything is served. After it, Hub's model-key lookup
(1Password, then the Keychain) finds nothing without looking, the mail bridge reports itself absent, and
any connection to another machine is refused. A guard against accidents, not a sandbox.
"""
import socket

LOCAL = ('127.0.0.1', '::1', 'localhost')


def apply(patch):
    from hub import bridge, model_key
    from preschool.support import luna as preschool_luna
    patch.setattr(model_key, '_keychain', lambda service: None)
    patch.setattr(model_key, '_exists', lambda service: False)
    patch.setattr(model_key, '_onepassword', lambda: None)
    model_key._cache.update(value=None, source=None, at=float('-inf'))
    patch.setattr(preschool_luna, 'key', lambda: None)
    patch.setattr(bridge, 'health', lambda: False)
    connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in LOCAL:
            raise OSError('The demo server reaches no other machine.')
        return connect(sock, address)

    patch.setattr(socket.socket, 'connect', local_only)
