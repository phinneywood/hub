"""Explicit private, durable reservations for a bounded inference campaign.

No default path, automatic reset, credential lookup, retries or refunds. An
uncertain request blocks further requests until separately reconciled. Creating
this journal does not authorize inference or alter standing processing quotas.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from . import private_files as access


class Journal:
    def __init__(self, directory):
        self.directory = access.private_directory(Path(directory))
        self.path = self.directory / 'inference.sqlite3'

    def initialize(self, *, max_calls, max_cost_micros):
        if any(type(value) is not int or value < 1 for value in (max_calls, max_cost_micros)):
            raise ValueError('Invalid inference campaign limits.')
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with closing(self._db()) as conn, conn:
            conn.execute('create table limits (max_calls integer, max_cost integer)')
            conn.execute('insert into limits values (?,?)', (max_calls, max_cost_micros))
            conn.execute('create table calls (digest text primary key, reserved integer, phase text, actual integer, result text)')

    def _db(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError('Inference journal must remain private.')
        finally:
            os.close(fd)
        conn = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)
        conn.execute('pragma synchronous=FULL')
        return conn

    def reserve(self, digest, cost_micros):
        if (not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest) or
                type(cost_micros) is not int or cost_micros < 1):
            raise ValueError('Invalid inference reservation.')
        try:
            with closing(self._db()) as conn, conn:
                conn.execute('begin immediate')
                limits = conn.execute('select max_calls,max_cost from limits').fetchall()
                if len(limits) != 1:
                    raise ValueError()
                count, reserved = conn.execute('select count(*),coalesce(sum(reserved),0) from calls').fetchone()
                if (conn.execute("select 1 from calls where phase!='confirmed'").fetchone() or
                        count >= limits[0][0] or reserved + cost_micros > limits[0][1]):
                    raise ValueError()
                conn.execute("insert into calls values (?,?,'in_flight',null,null)", (digest, cost_micros))
            return digest
        except (sqlite3.Error, OSError, ValueError):
            raise ValueError('Inference reservation blocked; preserve accounting and reconcile uncertainty.') from None

    def cached(self, digest):
        """Only confirmed, exact-request metadata may be reused; never retry uncertainty."""
        try:
            with closing(self._db()) as conn:
                if conn.execute("select 1 from calls where phase!='confirmed'").fetchone():
                    raise ValueError()
                row = conn.execute("select result from calls where digest=? and phase='confirmed'", (digest,)).fetchone()
                return json.loads(row[0]) if row and row[0] is not None else None
        except (sqlite3.Error, OSError, ValueError):
            raise ValueError('Inference cache is unavailable; reconcile accounting privately.') from None

    def confirm(self, digest, actual_micros, *, result=None):
        if type(actual_micros) is not int or actual_micros < 0:
            raise ValueError('Invalid inference cost receipt.')
        if result is not None:
            from .family_gate import validate_scores
            from .automatic_review import QUESTIONS
            if (not isinstance(result, dict) or not 1 <= len(result) <= 4 or
                    not set(result) <= {name for questions in QUESTIONS.values() for name in questions}):
                raise ValueError('Invalid inference result receipt.')
            result = validate_scores(result, result)
        try:
            with closing(self._db()) as conn, conn:
                conn.execute('begin immediate')
                row = conn.execute('select reserved,phase from calls where digest=?', (digest,)).fetchone()
                if not row or row[1] != 'in_flight' or actual_micros > row[0]:
                    raise ValueError()
                conn.execute("update calls set phase='confirmed',actual=?,result=? where digest=?",
                             (actual_micros, json.dumps(result) if result is not None else None, digest))
        except (sqlite3.Error, OSError, ValueError):
            raise ValueError('Inference receipt is unconfirmed; stop and reconcile privately.') from None
