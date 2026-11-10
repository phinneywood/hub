"""Account-specific fixed read routes; shared mailbox never has write routes."""
from .support import bridge
from . import store

SHARED_READS = {
    '/gmail/profile': '/gmail/shared/profile',
    '/gmail/search': '/gmail/shared/search',
    '/gmail/message': '/gmail/shared/message',
    '/gmail/calendar-attachment': '/gmail/shared/calendar-attachment',
    '/gmail/document-attachment': '/gmail/shared/document-attachment',
}


def get(path, **params):
    if store.current_account() == 'shared':
        if path not in SHARED_READS:
            raise bridge.BridgeError('Shared Gmail operation unavailable')
        path = SHARED_READS[path]
    return bridge.get(path, **params)
