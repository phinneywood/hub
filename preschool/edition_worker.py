"""One bounded edition-preparation invocation; no scheduler or external publisher.

Connect the existing processed catalog to fresh read-only source verification,
standing quotas, automated review, local freezing and the private reader. Nothing
starts on import, and production connections/activation are explicit dependencies.
"""
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from . import private_files as access
from . import (audience, automatic_review, criteria, extract, mail, processor,
               publication, quotas, store)
from .support import config, senders, store as preferences


@dataclass(frozen=True)
class Inventory:
    account: str
    mailbox_digest: str
    binding_hash: str
    records_json: str = field(repr=False)

    @property
    def digest(self):
        return publication.fingerprint([self.account, self.mailbox_digest, self.binding_hash, self.records_json])


class CatalogSource:
    """Read the full eligible processed inventory, not a recent-message window.

    No mailbox search, backfill, source write, catalog migration or extraction.
    The existing scanner owns discovery/extraction and its standing three-email
    quota. Only exact current class-audience records enter the edition inventory.
    """
    def __init__(self, catalog, *, account, mailbox, read=None, blocked=None):
        if account not in ('personal', 'shared') or not senders.valid_sender(mailbox):
            raise ValueError('An explicit account-specific catalog binding is required.')
        self.path, self.account, self.mailbox = Path(catalog), account, mailbox.lower()
        expected = config.DATA_DIR / ('preschool-shared.db' if account == 'shared' else 'preschool.db')
        if not config.DATA_DIR.is_absolute() or not self.path.is_absolute() or self.path != expected:
            raise ValueError('The source catalog must match the explicit account and absolute runtime directory.')
        self.read = read
        self.blocked = preferences.ai_blocked if blocked is None else blocked

    def snapshot(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('The existing catalog must be a private regular file.')
        finally:
            os.close(fd)
        approved_hash = audience.binding_hash()
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            bound = conn.execute("select value from settings where key='mailbox'").fetchone()
            if not bound or bound[0].lower() != self.mailbox:
                raise ValueError('Catalog mailbox binding changed.')
            messages = conn.execute('''select m.id,m.sender,m.criteria_version from messages m
                join message_audiences a on a.message_id=m.id
                where a.scope='class' and a.policy=? and a.binding_hash=? order by m.id''',
                (audience.VERSION, approved_hash)).fetchall()
            if not 1 <= len(messages) <= 100:
                raise ValueError('A complete bounded eligible inventory is required.')
            records = []
            for message in messages:
                if message['criteria_version'] != criteria.VERSION or self.blocked(message['sender']):
                    raise ValueError('An inventory source requires fresh policy or opt-out review.')
                items = conn.execute('select * from items where message_id=? and dismissed=0 order by id',
                                     (message['id'],)).fetchall()
                if not 1 <= len(items) <= extract.MAX_ITEMS:
                    raise ValueError('A source has no complete bounded extraction.')
                records.append({'id': message['id'], 'sender': message['sender'],
                    'keys': [self.account + ':' + str(row['id']) for row in items],
                    'items': [{key: row[key] for key in extract.FIELDS} for row in items]})
        return Inventory(self.account, publication.fingerprint(self.mailbox), approved_hash,
                         publication.serialized(records))

    def message(self, record):
        with store.account(self.account):
            profile = self.read('/gmail/profile', ttl=0) if self.read else mail.get('/gmail/profile', ttl=0)
            if profile.get('mailbox', '').lower() != self.mailbox:
                raise ValueError('The current connection belongs to a different mailbox.')
            message = self.read('/gmail/message', message_id=record['id']) if self.read else mail.get('/gmail/message', message_id=record['id'])
        if (message.get('id') != record['id'] or processor.address(message.get('from')) != record['sender'] or
                message.get('truncated') or message.get('attachments') or message.get('links') or
                self.blocked(record['sender'])):
            raise ValueError('A source is changed, opted out, truncated or requires supporting-source review.')
        return json.loads(json.dumps(message))


def prepare(source, judge, *, directory, expected_revision, source_revision,
            enabled=False, stronger=None, clock=time.time):
    """Prepare exactly one complete edition; unresolved checks keep the old edition.

    Every cheap/strong decision reserves the existing shared quota BEFORE calling.
    Existing processed extractions are reused and revalidated, not re-requested.
    The result describes local artifacts only; it never authorizes deployment.
    """
    if enabled is not True:
        return {'phase': 'held', 'reason': 'worker_disabled'}
    if (type(expected_revision) is not int or expected_revision < 0 or
            not isinstance(source_revision, str) or not re.fullmatch(r'[a-f0-9]{40}', source_revision)):
        raise ValueError('Supply an exact local edition and software revision.')
    if not config.DATA_DIR.is_absolute() or not Path(directory).is_absolute():
        raise ValueError('Use explicitly configured absolute private runtime directories.')
    directory = access.private_directory(Path(directory))
    try:
        fd = os.open(directory / 'worker.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError:
        return {'phase': 'held', 'reason': 'unsafe_worker_lock'}
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        return {'phase': 'held', 'reason': 'unsafe_worker_lock'}
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return {'phase': 'held', 'reason': 'worker_busy'}
    try:
        current = publication.latest()
        if (current['revision'] if current else 0) != expected_revision:
            return {'phase': 'held', 'reason': 'edition_drift'}
        inventory = source.snapshot()
        if type(inventory) is not Inventory or inventory.binding_hash != audience.binding_hash():
            raise ValueError('Incomplete or stale source inventory.')
        day = datetime.fromtimestamp(clock(), publication.PACIFIC).date().isoformat()
        records = json.loads(inventory.records_json)
        batch, keys = [], {}
        for record in records:
            message = source.message(record)
            if audience.classify(message)['scope'] != 'class':
                raise ValueError('Current source audience changed.')
            # Revalidate all evidence before any model call for this edition.
            body = criteria.current_text(message.get('text') or '')
            message['date'] = processor.sent_context(message.get('date'))
            extract.validate({'items': record['items']}, body, message['date'])
            batch.append((message, {'items': record['items']}))
            keys[record['id']] = record['keys']

        def metered(delegate):
            def call(stage, state):
                if datetime.fromtimestamp(clock(), publication.PACIFIC).date().isoformat() != day:
                    raise ValueError('The edition crossed its accounting date.')
                quotas.reserve('decision', day)
                return delegate(stage, state)
            return call

        assessment = automatic_review.assess(batch, audience.bindings(), judge=metered(judge),
            stronger=metered(stronger) if stronger is not None else None,
            as_of_date=day, enabled=True, item_keys=keys)
        if assessment.diagnostics:
            return {'phase': 'held', 'reason': 'automated_checks_unresolved'}
        if source.snapshot() != inventory:
            return {'phase': 'held', 'reason': 'inventory_drift'}
        if datetime.fromtimestamp(clock(), publication.PACIFIC).date().isoformat() != day:
            return {'phase': 'held', 'reason': 'accounting_date_changed'}
        label = 'edition-' + publication.fingerprint([inventory.digest, expected_revision, assessment.proposal_json])[:48]
        frozen = access.mutate(label, lambda: {'version': automatic_review.save_preview(
            assessment, expected_revision, enabled=True)}, directory)
        revision = frozen['version']
        snapshot = publication.latest()['payload']
        digest = publication.fingerprint(snapshot)
        receipt = {'schema': 1, 'phase': 'prepared', 'revision': revision,
                   'inventory_digest': inventory.digest, 'snapshot_digest': digest,
                   'source_revision': source_revision}
        access.write_private(directory / ('edition-' + str(revision) + '.json'), json.dumps(receipt, sort_keys=True) + '\n')
        return {'phase': 'prepared', 'revision': revision, 'snapshot_digest': digest}
    except Exception:
        return {'phase': 'held', 'reason': 'worker_stopped_preserve_journals'}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
