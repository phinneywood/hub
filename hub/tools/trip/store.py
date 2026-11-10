"""A single private, revisioned trip document. No network or external side effects."""
from copy import deepcopy
from contextlib import closing
from datetime import datetime, timezone
import json
import os
import sqlite3
from ... import config
from .domain import validate
from .seed import seed_trip


def _connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / 'trip.db'
    conn = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    conn.execute('CREATE TABLE IF NOT EXISTS trip (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS trip_versions (revision INTEGER PRIMARY KEY, saved_at TEXT NOT NULL, document TEXT NOT NULL)')
    return conn


def load():
    with closing(_connect()) as conn, conn:
        initial = seed_trip()
        conn.execute('INSERT OR IGNORE INTO trip VALUES (?, ?, ?)', (initial['id'], 1, json.dumps(initial)))
        row = conn.execute('SELECT document FROM trip WHERE id=?', (initial['id'],)).fetchone()
    return validate(json.loads(row[0]))


def save(data, expected_revision):
    """Replace by stable trip ID, retaining previous snapshots. Stale tabs fail closed."""
    value = validate(data)
    with closing(_connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT revision, document FROM trip WHERE id=?', (value['id'],)).fetchone()
        if row is None or row[0] != expected_revision:
            raise ValueError('Revision conflict: this trip changed in another tab. Reload before saving your changes.')
        old = json.loads(row[1])
        stamp = datetime.now(timezone.utc).isoformat()
        conn.execute('INSERT INTO trip_versions VALUES (?, ?, ?)', (row[0], stamp, row[1]))
        value['revision'] = row[0] + 1
        value['history'] = deepcopy(old.get('history', [])) + [{'revision': row[0], 'saved_at': stamp,
            'selected_scenario': old.get('selected_scenario'), 'selected_flight': old.get('selected_flight'),
            'notes': old.get('notes', '')}]
        conn.execute('UPDATE trip SET revision=?, document=? WHERE id=?', (value['revision'], json.dumps(value), value['id']))
    return value
