import pytest
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit
from datetime import date, timedelta
from fastapi.testclient import TestClient

from preschool.support import bridge, luna
from hub.main import app
from preschool import mail, scanner, store, _view_key

client = TestClient(app)
ORIGIN = {'Origin': 'http://testserver'}
MID = 'abcdef1234567890'


def message(sender):
    return {'id': MID, 'sender': sender, 'subject': 'Private subject', 'date': '', 'has_attachments': False}


def item(title):
    return {'kind':'update', 'title':title, 'details':'', 'date':None, 'time':None, 'location':None,
            'url':None, 'uncertainty':'', 'source_quote':'synthetic evidence'}


def test_account_catalogs_and_identical_message_ids_are_isolated():
    store.save_message('personal@example.invalid', message('personal-sender@example.invalid'), [item('Personal note')])
    with store.account('shared'):
        store.save_message('family@example.invalid', message('shared-sender@example.invalid'), [item('Shared note')])
        assert store.message_count() == 1 and store.all_items()[0]['title'] == 'Shared note'
    assert store.message_count() == 1 and store.all_items()[0]['title'] == 'Personal note'
    personal = client.get('/preschool')
    shared = client.get('/preschool/shared')
    assert personal.status_code == shared.status_code == 404
    topic=client.get('/preschool/guide/updates')
    assert 'Personal note' in topic.text and 'Shared note' in topic.text
    assert personal.text==shared.text
    assert 'authuser=family%40example.invalid#all/' in topic.text
    assert 'Shared household' not in personal.text and 'Personal mailbox' not in personal.text


def test_versioned_criteria_page_documents_owner_decisions():
    response=client.get('/preschool/criteria')
    assert response.status_code==200
    for phrase in ('VERSION 1.4','saved class mailing list','MHP-specific class item','class-bill hint','quoted history',
                   'class/co-op financial obligations','MapleNews local-news blog','Editing a handbook entry changes only that entry'):
        assert phrase in response.text
    assert 'Shared household' not in response.text and 'Personal mailbox' not in response.text


def test_home_waits_for_a_frozen_edition_without_showing_source_catalog():
    response=client.get('/preschool')
    assert response.status_code == 404
    assert 'No class guide edition is ready' in response.text
    assert 'Email source' not in response.text


def test_school_calendar_is_a_separate_reference_not_the_class_homepage():
    rows=[{'id':f'handbook-cal-{i}','date_text':f'June {i}, 2099','date_iso':f'2099-06-{i:02d}',
           'event':f'Synthetic Calendar Event {i}','start_time':'','end_time':'','location':''}
          for i in range(1,6)]
    store.replace_school_calendar(rows,1_800_000_000)
    response=client.get('/preschool')
    assert response.status_code == 404
    assert 'Synthetic Calendar Event' not in response.text
    dates=client.get('/preschool/dates')
    assert 'School-wide calendar' in dates.text
    assert 'may not apply to every class' in dates.text
    for i in range(1,6):assert f'Synthetic Calendar Event {i}' in dates.text


def test_past_dated_notes_are_hidden_but_restorable_from_the_view():
    yesterday=(date.today()-timedelta(days=7)).isoformat()
    event={**item('Synthetic past event'),'date':yesterday}
    todo={**item('Synthetic past-due class fee'),'kind':'action','date':yesterday}
    store.save_message('personal@example.invalid',message('teacher@example.invalid'),[event,todo])
    page=client.get('/preschool')
    assert 'Synthetic past event' not in page.text
    assert 'Synthetic past-due class fee' not in page.text
    assert page.status_code == 404  # Unreviewed catalog does not become the reader edition.
    historical=client.get('/preschool?past=1')
    assert 'Synthetic past event' in historical.text
    assert 'Synthetic past-due class fee' in historical.text
    assert 'Past due · check status' in historical.text
    assert 'Current class website' in historical.text
    assert store.message_count()==1 and len(store.all_items())==2


def test_handbook_search_includes_published_calendar_entries():
    store.replace_school_calendar([{'id':'synthetic-handbook-event','date_text':'June 14, 2099',
        'date_iso':'2099-06-14','event':'Synthetic Unique Class Closure', 'start_time':'',
        'end_time':'','location':''}], 1_800_000_000)
    response=client.get('/preschool?q=unique+class+closure')
    assert response.status_code==200 and 'Synthetic Unique Class Closure' in response.text
    assert 'Search class information' in response.text


def test_sources_are_secondary_to_internal_class_pages():
    future={**item('Synthetic class duty'),'kind':'action','date':'2099-12-01'}
    store.save_message('personal@example.invalid',message('teacher@example.invalid'),[future])
    response=client.get('/preschool')
    assert 'Recently added from email' not in response.text
    assert 'Private subject' not in response.text
    assert 'https://mail.google.com/mail/#all/'+MID not in response.text
    topic=client.get('/preschool/guide/participation')
    assert 'https://mail.google.com/mail/#all/'+MID in topic.text
    assert 'Sources' in topic.text
    assert 'Synthetic class duty' not in response.text
    assert 'Synthetic class duty' in topic.text


class HandbookStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack=[]
        self.entries=[]
        self.entry_details=[]
        self.links=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'class-fact' in attrs.get('class','').split():
            self.entries.append((tag,tuple(self.stack)))
        if 'class-sources' in attrs.get('class','').split():
            self.entry_details.append((tag,'open' in attrs))
        if tag=='a':self.links.append(attrs.get('href',''))
        if tag not in ('input','link','meta','br','hr','img'):
            self.stack.append(tag)
    def handle_endtag(self,tag):
        if tag in self.stack:
            index=len(self.stack)-1-self.stack[::-1].index(tag)
            del self.stack[index:]


def test_topic_content_is_readable_and_sources_are_collapsed():
    entries=[item('Synthetic first note'),item('Synthetic second note')]
    store.save_message('personal@example.invalid',message('teacher@example.invalid'),entries)
    page=client.get('/preschool/guide/updates')
    parsed=HandbookStructure();parsed.feed(page.text)
    assert len(parsed.entries)==2
    assert all(tag=='li' and parents[-1]=='ul' for tag,parents in parsed.entries)
    assert parsed.entry_details==[('details',False)]
    assert '>Email source</a>' not in page.text
    assert 'Edit source record' not in page.text
    assert 'school-hero' not in page.text and 'school-inbox-card' not in page.text
    assert 'Made for the beautiful chaos' not in page.text


def test_default_search_finds_history_and_preserves_query_in_view_links():
    entry={**item('Synthetic historical tuition'),'kind':'action','date':'2020-01-01'}
    store.save_message('personal@example.invalid',message('teacher@example.invalid'),[entry])
    page=client.get('/preschool?q=historical+tuition&dismissed=1')
    assert 'Synthetic historical tuition' in page.text and 'including history' in page.text
    parsed=HandbookStructure();parsed.feed(page.text)
    controls=[parse_qs(urlsplit(url).query) for url in parsed.links if urlsplit(url).path=='/preschool/search']
    assert {'q':['historical tuition'],'past':['0'],'dismissed':['1']} in controls
    current=client.get('/preschool?q=historical+tuition&past=0')
    assert 'Synthetic historical tuition' not in current.text
    assert 'No matching class information is recorded.' in current.text
    assert 'No to-dos have been recorded yet.' not in client.get('/preschool').text
    assert 'Synthetic historical tuition' in client.get('/preschool/history').text


def test_recent_search_does_not_join_identical_ids_across_accounts():
    store.save_message('personal@example.invalid',message('teacher@example.invalid'),[item('Synthetic needle')])
    with store.account('shared'):
        msg={**message('teacher@example.invalid'),'subject':'Synthetic unrelated subject'}
        store.save_message('family@example.invalid',msg,[item('Synthetic other')])
    page=client.get('/preschool?q=needle')
    assert 'Synthetic unrelated subject' not in page.text
    assert 'Synthetic needle' in page.text


def test_failed_search_is_not_reported_as_success_and_uses_oldest_completion():
    store.set_setting('scan_status','Mail check unavailable; will retry')
    store.set_setting('scan_last_attempt','1800000000')
    store.set_setting('scan_complete','1700000000')
    with store.account('shared'):
        store.set_setting('scan_status','Up to date')
        store.set_setting('scan_last_attempt','1800000000')
        store.set_setting('scan_complete','1800000000')
    assert 'Mail check unavailable; will retry' not in client.get('/preschool').text
    page=client.get('/preschool/manage')
    from preschool.templating import templates
    assert 'Mail check unavailable; will retry' in page.text
    assert 'Last attempt:' in page.text
    assert 'Both mailboxes searched through '+templates.env.filters['when'](1700000000) in page.text


def test_unified_queue_has_no_per_message_approval_or_rejection():
    with store.account('shared'):
        store.add_candidates('family@example.invalid',[{'id':MID,'sender':'teacher@example.invalid',
            'subject':'Group note','sent':'','snippet':'A local synthetic note','classification':'review'}])
    key=_view_key('candidate','shared',MID)
    assert 'Group note' not in client.get('/preschool').text
    page=client.get('/preschool/manage')
    assert 'Group note' in page.text and 'Inclusion policy' in page.text
    assert 'Analyze selected' not in page.text and 'Not relevant' not in page.text
    assert 'Shared household' not in page.text and 'Personal mailbox' not in page.text
    response=client.post('/preschool/review/'+key+'/dismiss',headers=ORIGIN,follow_redirects=False)
    assert response.status_code==404
    with store.account('shared'):
        assert store.candidate(MID)['state']=='new'


def test_shared_bridge_map_has_only_fixed_reads(monkeypatch):
    requests = []
    monkeypatch.setattr(bridge, 'get', lambda path, **params: requests.append(path) or {})
    with store.account('shared'):
        mail.get('/gmail/search', query='fake')
        mail.get('/gmail/message', message_id=MID)
        with pytest.raises(bridge.BridgeError): mail.get('/gmail/cleanup/apply')
    assert requests == ['/gmail/shared/search', '/gmail/shared/message']
    with pytest.raises(bridge.BridgeError): bridge.post('/gmail/shared/search', {})


def test_shared_group_search_and_independent_cursor(monkeypatch):
    calls = []
    def fake_get(path, **params):
        calls.append((path, params))
        if path == '/gmail/shared/profile': return {'mailbox':'family@example.invalid'}
        if path == '/gmail/shared/search': return {'messages': []}
        raise AssertionError(path)
    monkeypatch.setattr(bridge, 'get', fake_get)
    response = client.post('/preschool/group', data={'group':'class@example.invalid'}, headers=ORIGIN)
    assert response.status_code == 200
    with store.account('shared'):
        assert scanner.discover(now=1000000)
        assert store.setting('recipient_group') == 'class@example.invalid'
    query = next(params['query'] for path,params in calls if path == '/gmail/shared/search')
    assert 'to:class@example.invalid' in query and 'cc:class@example.invalid' in query and 'list:class@example.invalid' in query
    assert store.setting('recipient_group') is None
    assert store.setting('scan_complete') == ''


def test_group_rejects_gmail_search_operators():
    response = client.post('/preschool/group', data={'group':'class} OR from:other'}, headers=ORIGIN)
    assert response.status_code == 200 and 'without spaces or search operators' in response.text
    with store.account('shared'):
        assert store.setting('recipient_group') is None


def test_shared_candidate_page_explains_criteria_without_per_message_action(monkeypatch):
    with store.account('shared'):
        store.add_candidates('family@example.invalid', [{'id':MID,'sender':'teacher@example.invalid',
            'subject':'Class','sent':'','snippet':'','classification':'review'}])
    page = client.get('/preschool/shared/candidate/' + MID)
    assert page.status_code == 200
    assert 'criteria' in page.text.lower()
    assert 'Analyze this email' not in page.text and 'Not relevant' not in page.text
    key=_view_key('candidate','shared',MID)
    assert client.post('/preschool/review/'+key+'/process',headers=ORIGIN).status_code==404
    assert client.post('/preschool/review/'+key+'/dismiss',headers=ORIGIN).status_code==404
    with store.account('shared'):
        assert store.candidate(MID)['state'] == 'new'
    assert store.candidate(MID) is None


def test_per_email_analysis_endpoint_is_not_exposed(monkeypatch):
    paths = []
    def get(path, **params):
        paths.append(path)
        if path == '/gmail/shared/profile': return {'mailbox':'family@example.invalid'}
        if path == '/gmail/shared/message':
            return {'id': MID, 'from':'teacher@example.invalid', 'subject':'Class update',
                    'date':'Sun, 01 Nov 2026 10:00:00 -0800', 'text':'The class meets Friday.',
                    'truncated':False, 'attachments':[]}
        raise AssertionError(path)
    monkeypatch.setattr(bridge, 'get', get)
    with store.account('shared'):
        store.add_candidates('family@example.invalid', [{'id':MID,'sender':'teacher@example.invalid',
            'subject':'Class update','sent':'','snippet':'','classification':'review'}])
    assert client.post('/preschool/analyze-selected',data={'acknowledge':'yes'},headers=ORIGIN).status_code==404
    assert client.post('/preschool/review/' + _view_key('candidate','shared',MID) + '/process',headers=ORIGIN).status_code==404
    assert paths == []
    with store.account('shared'):
        assert not store.processed(MID) and store.all_items()==[]
    assert not store.processed(MID)


def test_shared_automatic_luna_is_off_by_default(monkeypatch):
    monkeypatch.setattr(luna, 'configured', lambda: True)
    with store.account('shared'):
        assert not scanner.auto_process(1000000)


def test_backfill_endpoint_cannot_resume_while_automatic_ai_is_paused():
    store.set_setting('auto_enabled','0')
    with store.account('shared'):
        store.set_setting('auto_enabled','0')
    response=client.post('/preschool/shared/backfill',headers={'Origin':'http://testserver'},follow_redirects=False)
    assert response.status_code==303
    assert response.headers.get('location')=='/preschool/shared'
    with store.account('shared'):
        assert store.setting('backfill_enabled','0')=='0'
        assert store.setting('backfill_status')=='Paused while automatic AI is off'
    assert store.setting('backfill_status') is None


def test_unified_backfill_is_blocked_until_ai_is_resumed():
    store.set_setting('auto_enabled','0')
    with store.account('shared'):
        store.set_setting('auto_enabled','0')
    response=client.post('/preschool/backfill',headers={'Origin':'http://testserver'},follow_redirects=False)
    assert response.status_code==303 and response.headers.get('location')=='/preschool/manage'
    for account in ('personal','shared'):
        with store.account(account):
            assert store.setting('backfill_enabled','0')=='0'
            assert store.setting('backfill_status')=='Paused while automatic AI is off'
