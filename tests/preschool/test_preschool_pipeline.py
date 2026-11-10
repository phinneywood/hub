import csv
import io
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from preschool.support import bridge, luna, store as hub_store
from hub.main import app
from preschool import ical, jev, processor, scanner, school_calendar, sources, store

client = TestClient(app)
ORIGIN = {'Origin': 'http://testserver'}
MID = 'a123456789abcdef'
SENDER = 'parent@example.org'
NOW = datetime(2026, 11, 1, 17, 0, tzinfo=timezone.utc).timestamp()
SENT = 'Sun, 01 Nov 2026 17:00:00 +0000'
BODY = 'MHP work party on November 14. Please bring gloves.'


def fake_item():
    return {'items': [{'kind': 'event', 'title': 'Work party', 'details': 'Bring gloves.', 'date': '2026-11-14',
                       'time': None, 'location': None, 'url': None, 'uncertainty': '',
                       'paragraph_id': 1}]}


@pytest.fixture
def fake_services(monkeypatch):
    calls = []
    def fake_get(path, ttl=60, **params):
        calls.append((path, params))
        if path == '/gmail/profile': return {'mailbox': 'owner@example.com'}
        if path == '/gmail/search': return {'messages': [{'id': MID, 'from': 'Parent <parent@example.org>',
                                                           'subject': 'MHP work party', 'snippet': 'MHP work party', 'date': SENT}]}
        if path == '/gmail/message': return {'id': MID, 'from': SENDER, 'subject': 'MHP work party', 'date': SENT,
                                             'text': BODY, 'truncated': False, 'attachments': []}
        raise AssertionError(path)
    monkeypatch.setattr(bridge, 'get', fake_get)
    monkeypatch.setattr(luna, 'configured', lambda: True)
    model_calls = []
    def complete(request):
        model_calls.append(request)
        return fake_item()
    monkeypatch.setattr(luna, 'complete', complete)
    monkeypatch.setattr(school_calendar, 'refresh', lambda now: 0)
    return calls, model_calls


def test_scanner_finds_candidates_without_model_when_auto_off(fake_services):
    calls, model_calls = fake_services
    store.set_setting('auto_enabled', '0')
    assert scanner.run(now=NOW, force=True)
    assert len(store.candidates()) == 1
    assert not model_calls
    assert '{MHP ' in next(p['query'] for path, p in calls if path == '/gmail/search')
    calls.clear()
    assert not scanner.run(now=NOW + 899)
    assert not calls
    assert scanner.run(now=NOW + 900)
    assert not model_calls and len(store.candidates()) == 1


def test_added_tuition_discovery_term_resets_only_the_bounded_scan_cursor(fake_services):
    calls,_=fake_services
    store.set_setting('search_query','{MHP} after:1700000000')
    store.set_setting('scan_page','old-page-token')
    store.set_setting('scan_complete',NOW-10*86400)
    assert scanner.discover(now=NOW)
    query=next(params['query'] for path,params in calls if path=='/gmail/search')
    assert 'tuition' in query and 'newer_than:28d' in query
    assert store.setting('search_version')==scanner.SEARCH_VERSION
    assert store.setting('scan_query')=='' and store.setting('scan_page')==''
    assert store.setting('backfill_enabled','0')=='0'


def test_truncated_email_is_held_for_review_before_jev(fake_services,monkeypatch):
    _,model_calls=fake_services
    original=bridge.get
    def get(path,**params):
        result=original(path,**params)
        if path=='/gmail/message':result['truncated']=True
        return result
    monkeypatch.setattr(bridge,'get',get)
    monkeypatch.setattr(jev,'decide',lambda *a,**kw:pytest.fail('truncated email must not reach Jev'))
    store.set_setting('auto_started',NOW-60)
    store.set_setting('auto_enabled','1')
    scanner.run(now=NOW,force=True)
    candidate=store.candidate(MID)
    assert candidate['state']=='needs_review' and candidate['review_reason']=='source_truncated'
    assert model_calls==[]


def test_automatic_clear_new_message_only_once(fake_services):
    calls, model_calls = fake_services
    store.set_setting('auto_started', NOW - 60)
    store.set_setting('auto_enabled','1')
    with pytest.MonkeyPatch.context() as monkeypatch:
        jev_calls=[]
        monkeypatch.setattr(jev,'decide',lambda *a,**kw:jev_calls.append(1) or {'preschool_relevant':.97,'useful_information':.9})
        scanner.run(now=NOW, force=True)
        assert len(model_calls) == 1 and len(jev_calls) == 1
        assert store.processed(MID) and store.candidate(MID)['state'] == 'processed'
        scanner.run(now=NOW + 900, force=True)
        assert len(model_calls) == 1 and len(jev_calls) == 1


def test_historical_and_blocked_senders_never_auto_process(fake_services):
    calls, model_calls = fake_services
    store.set_setting('auto_started', NOW + 1)
    store.set_setting('auto_enabled','1')
    scanner.run(now=NOW, force=True)
    assert not model_calls and store.candidate(MID)['state'] == 'new'
    store.set_setting('auto_started', NOW - 60)
    hub_store.set_ai_blocked(SENDER.upper(), True)
    scanner.run(now=NOW + 900, force=True)
    assert not model_calls


def test_newsletter_is_not_automatically_sent(fake_services, monkeypatch):
    original = bridge.get
    def get(path, **kw):
        result = original(path, **kw)
        if path == '/gmail/message': result['text'] += '\nUnsubscribe from newsletter'
        return result
    monkeypatch.setattr(bridge, 'get', get)
    monkeypatch.setattr(jev,'decide',lambda *a,**kw:{'preschool_relevant':.5,'useful_information':.5})
    store.set_setting('auto_started', NOW - 60)
    store.set_setting('auto_enabled','1')
    scanner.run(now=NOW, force=True)
    assert not fake_services[1] and store.candidate(MID)['state'] == 'needs_review'


def test_model_failure_is_not_automatically_retried(fake_services, monkeypatch):
    calls = []
    def fail(request):
        calls.append(1)
        raise luna.LunaError('synthetic failure')
    monkeypatch.setattr(luna, 'complete', fail)
    monkeypatch.setattr(jev,'decide',lambda *a,**kw:{'preschool_relevant':.97,'useful_information':.9})
    store.set_setting('auto_started', NOW - 60)
    store.set_setting('auto_enabled','1')
    scanner.run(now=NOW, force=True)
    assert store.candidate(MID)['state'] == 'needs_review'
    scanner.run(now=NOW + 900, force=True)
    assert len(calls) == 1


def test_candidates_are_visible_but_manual_approval_is_not_an_app_route(fake_services):
    store.set_setting('auto_enabled', '0')
    scanner.run(now=NOW, force=True)
    assert 'MHP work party' not in client.get('/preschool').text
    response=client.get('/preschool/manage')
    assert 'MHP work party' in response.text
    assert 'Inclusion policy' in response.text
    assert 'Analyze selected' not in response.text and 'Not relevant' not in response.text
    assert client.post('/preschool/analyze-selected',headers=ORIGIN).status_code==404
    key='a'*64
    assert client.post(f'/preschool/review/{key}/process',headers=ORIGIN).status_code==404
    assert store.candidate(MID)['state'] == 'new'
    assert not fake_services[1]


def test_interrupted_processing_is_not_retried(fake_services):
    store.set_setting('auto_enabled', '0')
    scanner.run(now=NOW, force=True)
    assert store.claim_candidate(MID)
    store.recover_candidates()
    assert store.candidate(MID)['state'] == 'needs_review'
    assert not store.claim_candidate(MID)


def test_calendar_invitation_and_timezone():
    text = ('BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:School\\, Work Party\r\n'
            'DTSTART;TZID=America/Los_Angeles:20261114T090000\r\n'
            'DTEND;TZID=America/Los_Angeles:20261114T130000\r\nLOCATION:Red Room\r\n'
            'RRULE:FREQ=WEEKLY;COUNT=3\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')
    item = ical.parse(text)[0]
    assert item['title'] == 'School, Work Party'
    assert item['date'] == '2026-11-14' and item['time'] == '9:00 AM – 1:00 PM Pacific'
    assert 'Recurring' in item['uncertainty']
    with pytest.raises(ValueError): ical.parse('BEGIN:VCALENDAR\nBEGIN:VEVENT')


def test_calendar_all_day_and_floating_time():
    text = ('BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Holiday\nDTSTART;VALUE=DATE:20261111\n'
            'DTEND;VALUE=DATE:20261112\nEND:VEVENT\nBEGIN:VEVENT\nSUMMARY:Meeting\n'
            'DTSTART:20261114T100000\nEND:VEVENT\nEND:VCALENDAR')
    first, second = ical.parse(text)
    assert first['date'] == '2026-11-11' and not first['uncertainty']
    assert second['time'] is None and 'floating time' in second['uncertainty']


def test_calendar_cancel_method_is_flagged():
    text = ('BEGIN:VCALENDAR\nMETHOD:CANCEL\nBEGIN:VEVENT\nSUMMARY:Meeting\n'
            'DTSTART;VALUE=DATE:20261114\nEND:VEVENT\nEND:VCALENDAR')
    assert 'cancelled' in ical.parse(text)[0]['uncertainty']


def test_calendar_attachment_is_read_without_sending_ics_to_model(fake_services, monkeypatch):
    original = bridge.get
    ics = 'BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Class party\nDTSTART;VALUE=DATE:20261114\nEND:VEVENT\nEND:VCALENDAR'
    def get(path, **kw):
        if path == '/gmail/calendar-attachment': return {'text': ics, 'truncated': False}
        result = original(path, **kw)
        if path == '/gmail/message':
            result['attachments'] = [{'mime_type':'text/calendar', 'readable':True, 'attachment_id':'abc', 'part_id':'1'}]
        return result
    monkeypatch.setattr(bridge, 'get', get)
    processor.read_message('owner@example.com', MID, SENDER)
    assert len(fake_services[1]) == 1
    assert any(item['title'] == 'Class party' for item in store.all_items())
    assert store.messages()[0]['attachment_status'] == '1 of 1 attachments read; 0 linked sources read'


def test_document_attachment_uses_bounded_read_and_model(fake_services, monkeypatch):
    original = bridge.get
    def get(path, **kw):
        if path == '/gmail/document-attachment': return {'text': BODY, 'truncated': False}
        result = original(path, **kw)
        if path == '/gmail/message':
            result['attachments'] = [{'mime_type':'application/pdf', 'readable':True, 'attachment_id':'abc', 'part_id':'1'}]
        return result
    monkeypatch.setattr(bridge, 'get', get)
    processor.read_message('owner@example.com', MID, SENDER)
    assert len(fake_services[1]) == 2
    assert store.messages()[0]['attachment_status'] == '1 of 1 attachments read; 0 linked sources read'


def test_jev_preflight_receives_locally_read_attachments_and_allowlisted_links(fake_services, monkeypatch):
    original=bridge.get
    ics=('BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:MHP Parent Meeting\n'
         'DTSTART;VALUE=DATE:20261109\nEND:VEVENT\nEND:VCALENDAR')
    def get(path,**kw):
        if path=='/gmail/calendar-attachment':return {'text':ics,'truncated':False}
        result=original(path,**kw)
        if path=='/gmail/message':
            result['text']='Please see the MHP family resources: MHP family resources.'
            result['attachments']=[{'mime_type':'text/calendar','readable':True,'attachment_id':'fixed','part_id':'1'}]
            result['links']=[{'url':'https://maple-hill.example/kids/mhp/','label':'MHP family resources'}]
        return result
    monkeypatch.setattr(bridge,'get',get)
    monkeypatch.setattr(sources,'_read_response',lambda client,url:((b'MHP classroom setup dates','text/plain'),None))
    prepared=processor.prepare_message('owner@example.com',MID,SENDER)
    assert not prepared['evidence'].review
    texts=[x['text'] for x in prepared['supporting']]
    assert any('MHP Parent Meeting' in text for text in texts)
    assert any('classroom setup dates' in text for text in texts)
    assert len(prepared['supporting'])==2


def test_jev_preflight_holds_unreadable_resource_for_review(fake_services,monkeypatch):
    original=bridge.get
    def get(path,**kw):
        result=original(path,**kw)
        if path=='/gmail/message':
            result['text']='See the MHP calendar.'
            result['links']=[{'url':'https://outside.example/calendar','label':'MHP calendar'}]
        return result
    monkeypatch.setattr(bridge,'get',get)
    prepared=processor.prepare_message('owner@example.com',MID,SENDER)
    assert 'off_allowlist' in prepared['evidence'].review
    assert not fake_services[1]


def test_long_document_is_chunked_within_budget(fake_services, monkeypatch):
    original = bridge.get
    requests = []
    def get(path, **kw):
        if path == '/gmail/document-attachment': return {'text': 'Text section.\n' * 800, 'truncated': False}
        result = original(path, **kw)
        if path == '/gmail/message':
            result['attachments'] = [{'mime_type':'application/pdf', 'readable':True, 'attachment_id':'abc', 'part_id':'1'}]
        return result
    def complete(request):
        requests.append(request)
        return {'items': []}
    monkeypatch.setattr(bridge, 'get', get)
    monkeypatch.setattr(luna, 'complete', complete)
    processor.read_message('owner@example.com', MID, SENDER)
    assert 2 < len(requests) <= 8
    assert store.messages()[0]['attachment_status'] == '1 of 1 attachments read; 0 linked sources read'


def test_school_calendar_csv_parses_and_keeps_previous_on_invalid():
    data = '2026–2027 calendar,,,,\nDate,Event,Start Time,End Time,Location\nSaturday,\"Fall Work Party\",9:00 AM,1:00 PM,Room\n'
    rows = school_calendar.parse(data.encode())
    assert rows[0]['event'] == 'Fall Work Party'
    assert rows[0]['date_iso'] is None
    store.replace_school_calendar(rows, NOW)
    with pytest.raises(ValueError): school_calendar.parse(b'not,a,calendar')
    assert len(store.school_events()) == 1
    assert school_calendar.date_iso('Thursday, November 5, 2026') == '2026-11-05'
    assert school_calendar.date_iso('12/21/2026 - 1/3/2027') == '2026-12-21'


def test_sheet_refuses_offsite_redirect():
    class Response:
        status_code = 302
        headers = {'location': 'https://evil.example/steal'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Client:
        def __init__(self): self.calls = 0
        def stream(self, *args, **kwargs): self.calls += 1; return Response()
    client = Client()
    with pytest.raises(ValueError, match='not allowed'): school_calendar.download(client)
    assert client.calls == 1
