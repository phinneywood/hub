"""One run of the indexer: each source in turn, with its counts, time and kinds of error kept in the index's status
table. One run at a time: a second one started meanwhile says so and stops.

A source that can't be read leaves what the index holds of it as it was: rows are removed only by a run that read
the source and found them gone.
"""
import fcntl
import json
import os
import time

from . import chats, documents, photos, store

PASSES = {'photos': photos.index, 'documents': documents.index, 'chats': chats.index}


def run(sources=None, say=print):
    """Index the sources named (all of them by default). Returns {source: counts}."""
    store.owner_folder(store.home())
    lock = os.fdopen(os.open(store.home() / 'index.lock', os.O_CREAT | os.O_WRONLY, 0o600), 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        say('Another run is indexing already.')
        lock.close()
        return {}
    done = {}
    try:
        conn = store.connect()
        for source in sources or PASSES:
            started = time.time()
            store.set_status(conn, source, started=started)
            conn.commit()
            try:
                counts, errors = PASSES[source](conn, say=say)
            except Exception as error:  # noqa: BLE001 - one source failing leaves the others to run
                conn.rollback()
                store.set_status(conn, source, finished=time.time(), seconds=round(time.time() - started, 1),
                                 errors={type(error).__name__: 1})
                conn.commit()
                say(json.dumps({source: 'failed', 'error': type(error).__name__}))
                done[source] = None
                continue
            store.set_status(conn, source, finished=time.time(), ok=time.time(), seconds=round(time.time() - started, 1),
                             items=counts.get('items', 0), errors=errors, note=counts)
            conn.commit()
            say(json.dumps({source: counts, 'errors': errors, 'seconds': round(time.time() - started, 1)}))
            done[source] = counts
        conn.execute("insert into fts(fts) values ('optimize')")
        conn.commit()
        conn.execute('pragma wal_checkpoint(truncate)')
        conn.close()
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return done
