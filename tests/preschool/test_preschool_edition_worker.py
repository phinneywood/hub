"""Complete scratch catalog + fixed read-only mailbox + metered mock inference."""
import copy
import fcntl
from datetime import datetime, timezone
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from preschool import (audience, automatic_review, edition_worker, publication,
                       quotas, scanner, store)
from preschool.support import config

DAY = '2027-05-01'
NOW = datetime(2027, 5, 1, 16, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def source():
    with store.account('shared'):
        store.set_setting('family_group_addresses', json.dumps(['class@example.invalid']))
    message = {'id': 'abcdef1234567890', 'from': 'teacher@example.invalid', 'subject': 'Class reminder',
               'date': 'Sat, 01 May 2027 09:00:00 -0700', 'text': 'Sprouts families: bring boots.',
               'recipients': {'to': 'class@example.invalid'}, 'attachments': [], 'links': []}
    item = {'kind': 'update', 'title': 'Bring boots', 'details': 'Bring boots.',
            'date': None, 'time': None, 'location': None, 'url': None,
            'uncertainty': '', 'source_quote': message['text']}
    with store.account('personal'):
        store.save_message('owner@example.invalid', {'id': message['id'], 'sender': 'teacher@example.invalid',
            'subject': message['subject'], 'date': message['date'], 'has_attachments': False,
            'sharing_evidence': audience.classify(message)}, [item])
    calls = []
    def read(path, **params):
        calls.append(path)
        return {'mailbox': 'owner@example.invalid'} if path == '/gmail/profile' else copy.deepcopy(message)
    adapter = edition_worker.CatalogSource(config.DATA_DIR / 'preschool.db', account='personal',
        mailbox='owner@example.invalid', read=read, blocked=lambda sender: False)
    return adapter, message, calls


def judge(stage, state):
    assert quotas.current('decision', DAY) >= 1
    return {key: .001 if key == 'personal_information' else .99 for key in automatic_review.QUESTIONS[stage]}


def prepare(source, tmp_path, **options):
    return edition_worker.prepare(source, options.pop('judge', judge), directory=tmp_path / 'worker',
        expected_revision=options.pop('expected_revision', 0), source_revision='1' * 40,
        enabled=options.pop('enabled', True), clock=lambda: NOW, **options)


def test_existing_catalog_through_complete_metered_edition_and_bundle(source, tmp_path):
    adapter, _, calls = source
    result = prepare(adapter, tmp_path)
    assert result['phase'] == 'prepared' and result['revision'] == 1
    assert len(result['snapshot_digest']) == 64
    assert calls == ['/gmail/profile', '/gmail/message']
    assert quotas.current('decision', DAY) == 4
    assert quotas.current('extraction', DAY) == 0
    assert publication.latest()['payload']['pages'][0]['body'] == 'Bring boots\nBring boots.'
    receipt = json.loads((tmp_path / 'worker/edition-1.json').read_text())
    assert receipt['snapshot_digest'] == result['snapshot_digest']
    assert 'source_quote' not in json.dumps(receipt)
    assert 'example.invalid' not in json.dumps(receipt)
    with store.account('personal'):
        assert store.setting('auto_enabled', '0') == '0'
        assert store.setting('backfill_enabled', '0') == '0'


def test_disabled_worker_does_not_touch_any_dependency(tmp_path):
    assert prepare(None, tmp_path, enabled=False)['reason'] == 'worker_disabled'
    assert not (tmp_path / 'worker').exists()


@pytest.mark.parametrize('failure', ['mailbox', 'sender', 'audience', 'truncated', 'attachment', 'quote', 'opt-out'])
def test_source_failure_precedes_all_inference_and_freezing(source, tmp_path, failure):
    adapter, message, _ = source
    if failure == 'mailbox': adapter.mailbox = 'other@example.invalid'
    elif failure == 'sender': message['from'] = 'other@example.invalid'
    elif failure == 'audience': message['recipients'] = {'to': 'private@example.invalid'}
    elif failure == 'truncated': message['truncated'] = True
    elif failure == 'attachment': message['attachments'] = [{'name': 'private.txt'}]
    elif failure == 'quote': message['text'] = 'A completely different notice.'
    elif failure == 'opt-out': adapter.blocked = lambda sender: True
    result = prepare(adapter, tmp_path, judge=lambda *args: pytest.fail('No model calls'))
    assert result['phase'] == 'held'
    assert publication.latest() is None
    assert quotas.current('decision', DAY) == 0


def test_exhausted_or_uncertain_decisions_cannot_freeze_partial_output(source, tmp_path):
    adapter, _, _ = source
    scanner.set_global_daily_count('jev_count_', DAY, scanner.DAILY_JEV_LIMIT)
    assert prepare(adapter, tmp_path, judge=lambda *args: pytest.fail('Quota precedes inference'))['phase'] == 'held'
    assert publication.latest() is None


def test_failed_inference_is_still_counted_and_never_retried_within_batch(source, tmp_path):
    def unavailable(*args): raise RuntimeError('SYNTHETIC_PRIVATE_ERROR')
    result = prepare(source[0], tmp_path, judge=unavailable)
    assert result['phase'] == 'held'
    assert quotas.current('decision', DAY) == 1
    assert 'SYNTHETIC' not in json.dumps(result)
    assert publication.latest() is None


def test_source_inventory_drift_blocks_freezing(source, tmp_path):
    adapter, _, _ = source
    def changed(stage, state):
        if stage == 'privacy':
            with store.account('personal'):
                store.set_setting('mailbox', 'other@example.invalid')
        return judge(stage, state)
    assert prepare(adapter, tmp_path, judge=changed)['phase'] == 'held'
    assert publication.latest() is None


def test_atomic_standing_quota_cannot_be_overspent_across_workers():
    scanner.set_global_daily_count('jev_count_', DAY, scanner.DAILY_JEV_LIMIT - 1)
    def reserve(_):
        try:
            quotas.reserve('decision', DAY)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, range(2))) == [False, True]
    assert quotas.current('decision', DAY) == scanner.DAILY_JEV_LIMIT


def test_catalog_keys_do_not_reassign_calendar_identity_when_an_item_is_dismissed(source):
    adapter, _, _ = source
    snapshot = adapter.snapshot()
    record = json.loads(snapshot.records_json)[0]
    assert record['keys'] == ['personal:1']
    assert snapshot.account == 'personal'


def test_another_worker_holds_the_run_lock_before_any_dependency(source, tmp_path):
    directory = tmp_path / 'worker'
    directory.mkdir(mode=0o700)
    with (directory / 'worker.lock').open('w') as stream:
        (directory / 'worker.lock').chmod(0o600)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = prepare(None, tmp_path)
        assert result == {'phase': 'held', 'reason': 'worker_busy'}
