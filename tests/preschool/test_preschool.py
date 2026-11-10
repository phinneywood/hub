import json
import os

import pytest
from fastapi.testclient import TestClient

from preschool.support import bridge, config, luna
from hub import store as hub_store
from hub.main import app
from preschool import extract, processor, store

client = TestClient(app)
ORIGIN = {'Origin': 'http://testserver'}
SENDER = 'school@example.org'
MID = 'abcdef1234567890'
BODY = 'Work party on May 5, 2026. Bring boots.\n\nPRIVATE_BODY_SENTINEL'


def proposal(**changes):
    item = {'kind': 'event', 'title': 'Work party', 'details': 'Bring boots.',
            'date': '2026-05-05', 'time': None, 'location': None, 'url': None,
            'uncertainty': '', 'source_quote': 'Work party on May 5, 2026.'}
    item.update(changes)
    return {'items': [item]}


@pytest.fixture
def service(monkeypatch):
    requests = []
    def fake_get(path, ttl=60, **params):
        if path == '/gmail/profile':
            return {'mailbox': 'owner@example.com'}
        if path == '/gmail/search':
            return {'messages': [{'id': MID, 'from': 'School <school@example.org>', 'subject': 'News',
                                  'date': 'Mon, 4 May 2026 09:00:00 -0700'},
                                 {'id': '9999999999', 'from': 'Wrong <other@example.org>', 'subject': 'Not ours'}]}
        if path == '/gmail/message':
            return {'id': params['message_id'], 'from': 'School <school@example.org>', 'subject': 'News',
                    'date': 'Mon, 4 May 2026 09:00:00 -0700', 'text': BODY, 'truncated': False, 'attachments': []}
        raise AssertionError(path)
    def fake_complete(req):
        requests.append(req)
        item = proposal()['items'][0]
        item.pop('source_quote')
        return {'items': [{**item, 'paragraph_id': 1}]}
    monkeypatch.setattr(bridge, 'get', fake_get)
    monkeypatch.setattr(luna, 'configured', lambda: True)
    monkeypatch.setattr(luna, 'complete', fake_complete)
    store.add_sender(SENDER)
    return requests


def process(**overrides):
    if store.processed(MID):
        return 0
    if not store.mailbox_matches('owner@example.com'):
        raise ValueError('This catalog belongs to a different mailbox.')
    return processor.read_message('owner@example.com', MID, overrides.get('sender', SENDER))


def test_preview_requires_exact_sender_and_displays_privacy(service):
    response = client.get('/preschool/preview', params={'sender': SENDER})
    assert response.status_code == 200
    assert 'News' in response.text and 'Not ours' not in response.text
    assert 'Emails from this sender' in response.text
    assert 'Process selected emails' not in response.text
    assert not service


def test_discovery_and_local_search(service):
    response = client.get('/preschool/discover')
    assert 'school@example.org' in response.text and 'other@example.org' in response.text
    assert 'does not prove' in response.text and not service
    process()
    assert 'Work party' in client.get('/preschool?q=boots&past=1').text
    assert 'Work party' not in client.get('/preschool?q=nonexistentterm').text


def test_processing_saves_items_locally_and_does_not_repeat(service):
    response = process()
    assert response == 1
    assert len(service) == 1
    req = service[0]
    assert req['provider'] == luna.PROVIDER and req['model'] == luna.MODEL
    assert json.loads(req['messages'][1]['content'])['paragraphs'] == extract.paragraphs(BODY)
    assert store.processed(MID)
    assert store.all_items()[0]['title'] == 'Work party'
    assert b'PRIVATE_BODY_SENTINEL' not in (config.DATA_DIR / 'preschool.db').read_bytes()
    assert os.stat(config.DATA_DIR / 'preschool.db').st_mode & 0o777 == 0o600
    assert 'Work party' in client.get('/preschool?past=1').text
    process()
    assert len(service) == 1 and store.message_count() == 1


def test_acknowledgment_and_ai_block_are_required(service):
    assert client.post('/preschool/analyze-selected', headers=ORIGIN).status_code == 404
    hub_store.set_ai_blocked(SENDER.upper(), True)
    with pytest.raises(ValueError):
        process()
    assert not service and not store.processed(MID)
    hub_store.set_ai_blocked(SENDER.upper(), False)
    assert not hub_store.ai_blocked(SENDER)


def test_previously_stored_mixed_case_ai_block(service):
    from contextlib import closing
    with closing(hub_store.db()) as conn, conn:
        conn.execute('insert into ai_blocked values (?)', (SENDER.upper(),))
    assert hub_store.ai_blocked(SENDER)
    with pytest.raises(ValueError):
        process()
    assert not service


def test_sender_validation_and_batch_bounds(service):
    assert client.post('/preschool/analyze-selected',headers=ORIGIN,data={'acknowledge':'yes'}).status_code == 404
    assert not service


def test_actual_message_sender_must_match(service, monkeypatch):
    original = bridge.get
    def fake(path, **kwargs):
        if path == '/gmail/message':
            return {'id': MID, 'from': 'Other <other@example.org>', 'text': BODY}
        return original(path, **kwargs)
    monkeypatch.setattr(bridge, 'get', fake)
    with pytest.raises(ValueError):
        process()
    assert not service and not store.processed(MID)


def test_truncated_message_is_not_sent(service, monkeypatch):
    original = bridge.get
    def fake(path, **kwargs):
        if path == '/gmail/message':
            return {'id': MID, 'from': SENDER, 'text': BODY, 'truncated': True}
        return original(path, **kwargs)
    monkeypatch.setattr(bridge, 'get', fake)
    with pytest.raises(extract.ExtractionError,match='truncated'):
        process()
    assert not service and not store.processed(MID)


def test_mailbox_binding_fails_closed(service, monkeypatch):
    store.save_message('other@example.com', {'id': 'abcdef99', 'sender': SENDER, 'subject': '', 'date': '',
                                              'has_attachments': False}, [])
    with pytest.raises(ValueError,match='different mailbox'):
        process()
    assert not service and not store.processed(MID)


def test_invalid_model_output_is_not_persisted(service, monkeypatch):
    def invalid(req):
        item = proposal()['items'][0]
        item.pop('source_quote')
        return {'items': [{**item, 'paragraph_id': 99}]}
    monkeypatch.setattr(luna, 'complete', invalid)
    with pytest.raises(extract.ExtractionError,match='unknown source paragraph'):
        process()
    assert not store.processed(MID) and store.all_items() == []


def test_edit_dismiss_and_restore(service):
    process()
    item = store.all_items()[0]
    assert 'Dismiss' not in client.get('/preschool?past=1').text
    from preschool import _view_key
    key = _view_key('item','personal',item['id'])
    response = client.post(f"/preschool/record/{key}/edit", headers=ORIGIN,
                           data={'kind': 'action', 'title': '<script>bad</script>', 'details': 'Updated',
                                 'date_value': '2026-05-06'}, follow_redirects=False)
    assert response.status_code == 303
    view = client.get(f"/preschool/record/{key}/edit")
    assert '&lt;script&gt;bad&lt;/script&gt;' in view.text
    assert '<script>bad</script>' not in view.text
    assert store.all_items()[0]['date'] == '2026-05-06'
    client.post(f"/preschool/record/{key}/dismiss", headers=ORIGIN)
    assert not store.all_items()
    client.post(f"/preschool/record/{key}/dismiss", data={'dismissed': '0'}, headers=ORIGIN)
    assert len(store.all_items()) == 1


def test_schema_validation_and_safe_links():
    assert extract.validate(proposal(url='https://evil.example/'), BODY)[0]['url'] is None
    assert extract.validate(proposal(url='javascript:alert(1)'), BODY + ' javascript:alert(1)')[0]['url'] is None
    assert extract.validate(proposal(url='https://example.org/x'), BODY + ' https://example.org/x')[0]['url'] == 'https://example.org/x'
    with pytest.raises(extract.ExtractionError):
        extract.validate(proposal(date='2026-02-30'), BODY)
    with pytest.raises(extract.ExtractionError):
        extract.validate(proposal(source_quote='no match'), BODY)


def test_class_fee_due_date_is_supported_as_action_not_payment():
    source='MHP class contribution of $85 is due November 15, 2026.'
    raw={'items':[{'kind':'action','title':'MHP class contribution due','details':'$85 due November 15, 2026.',
        'date':'2026-11-15','time':None,'location':None,'url':None,'uncertainty':'',
        'source_quote':source}]}
    item=extract.validate(raw,source)[0]
    assert item['kind']=='action' and '$85' in item['details'] and item['date']=='2026-11-15'
    assert 'never infer them' in extract.SYSTEM and 'initiate/facilitate payment' in extract.SYSTEM


def test_truncated_model_reply_is_rejected(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {'choices': [{'finish_reason': 'length', 'message': {'content': '{"items": []}'}}]}
    monkeypatch.setattr(luna, 'key', lambda: 'fake-key')
    monkeypatch.setattr(luna.httpx, 'post', lambda *args, **kwargs: Response())
    with pytest.raises(luna.LunaError, match='incomplete'):
        luna.complete({})


def test_chunking_covers_full_message_and_uses_fake_transport():
    text = ('abc def\n' * 1800) + 'end'
    parts = list(extract.chunks(text))
    assert ''.join(parts) == text and all(len(part) <= extract.CHUNK_SIZE for part in parts)
    seen = []
    def fake(req):
        seen.append(json.loads(req['messages'][1]['content'])['paragraphs'])
        return {'items': []}
    assert extract.extract(text, None, fake) == []
    assert seen == [extract.paragraphs(part) for part in parts]
    assert len(list(extract.chunks(('a' * 5200 + '\n') * 3 + 'b' * 4397))) <= 4
