"""Owner choices only; history remains in Home Assistant Recorder."""
import json
import os
import sqlite3
from contextlib import closing

from ... import config

ROLES = ('wan', 'reachability', 'latency', 'loss', 'download_rate', 'upload_rate', 'download_total', 'upload_total')


def db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = config.DATA_DIR / 'network.db'
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute('create table if not exists settings(key text primary key, value text not null)')
    return conn


def get(key, default=None):
    with closing(db()) as conn:
        row = conn.execute('select value from settings where key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(key, value):
    with closing(db()) as conn, conn:
        conn.execute('insert into settings values(?,?) on conflict(key) do update set value=excluded.value',
                     (key, json.dumps(value)))
