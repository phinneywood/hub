"""Class website behavior, independent of Gmail and model connections."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from preschool.support import bridge, config, luna
from hub.main import app
from preschool import _view_key, jev, site_store, store, website

client = TestClient(app)
ORIGIN = {'Origin':'http://testserver'}


@pytest.fixture(autouse=True)
def no_external_services(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('The class website must not read Gmail, call a model or change mail')
    monkeypatch.setattr(bridge,'get',denied)
    monkeypatch.setattr(bridge,'post',denied)
    monkeypatch.setattr(luna,'complete',denied)
    monkeypatch.setattr(jev,'decide',denied)


def item(title, **changes):
    return {'kind':'update','title':title,'details':'Synthetic class information.',
            'date':None,'time':None,'location':None,'url':None,'uncertainty':'',
            'source_quote':'Synthetic supporting evidence.',**changes}


def save(number, entries, account='personal'):
    with store.account(account):
        store.save_message(account+'@example.invalid',{
            'id':f'{number:016x}','sender':'teacher@example.invalid',
            'subject':'PRIVATE_EMAIL_SUBJECT','date':'Wed, 04 Nov 2026 09:00:00 -0800',
            'has_attachments':False},entries)


def source(number, **changes):
    return {**item('Synthetic class fact',**changes),'view_key':f'{number:064x}',
            'gmail_url':'https://mail.google.com/mail/#all/'+f'{number:016x}',
            'dismissed':False,'saved_at':1}


@pytest.mark.parametrize('record,expected',[
    (item('Drop-off and pickup routine'),'routines'),
    (item('Bring boots'),'preparation'),
    (item('Parent volunteer duty',kind='action'),'participation'),
    (item('Tuition contribution',kind='action'),'fees'),
    (item('Classroom news'),'updates'),
    (item('Parent meeting',kind='event'),'dates'),
    (item('Class guide PDF',kind='resource'),'resources'),
])
def test_class_information_is_organized_by_topic_not_message(record,expected):
    assert website.topic_for(record)==expected


def test_exact_duplicate_facts_combine_sources_but_conflicts_do_not_disappear():
    same=[source(1),source(2)]
    data=website.project(same,[],{},'2026-11-04')
    assert len(data['facts'])==1 and len(data['facts'][0]['sources'])==2
    conflict=source(3,details='A different classroom instruction.')
    assert len(website.project(same+[conflict],[],{},'2026-11-04')['facts'])==2


def test_this_week_is_bounded_and_school_calendar_is_not_promoted_to_class_facts():
    rows=[source(1,kind='event',date='2026-11-04'),source(2,kind='event',date='2026-11-10'),
          source(3,kind='event',date='2026-11-11'),source(4,kind='action',date='2026-11-03')]
    school=[{'id':'calendar-1','date_iso':'2026-11-05','event':'School-wide reference'}]
    data=website.project(rows,school,{},'2026-11-04')
    assert [row['date'] for row in data['this_week']]==['2026-11-04','2026-11-10']
    assert [row['date'] for row in data['coming_up']]==['2026-11-11']
    assert len(data['past_due'])==1
    assert len(data['school_calendar'])==1
    assert all(row['title']!='School-wide reference' for row in data['this_week'])


def test_real_uncertainty_is_held_but_provenance_notes_do_not_hide_valid_information():
    rows=[source(1,uncertainty='Date is ambiguous.'),
          source(2,uncertainty='From an attachment.'),
          source(3,uncertainty='From an allowlisted linked source.')]
    data=website.project(rows,[],{},'2026-11-04')
    assert data['held_source_count']==1
    assert len(data['facts'])==1 and len(data['facts'][0]['sources'])==2


def test_home_links_to_topics_and_current_information_not_email_subjects_or_queue():
    today=datetime.now(ZoneInfo('America/Los_Angeles')).date()
    save(1,[item('Bring class boots',kind='action',date=(today+timedelta(days=1)).isoformat()),
            item('Class outing',kind='event',date=(today+timedelta(days=14)).isoformat())])
    store.add_candidates('personal@example.invalid',[{'id':'ffffffffffffffff','sender':'teacher@example.invalid',
        'subject':'PRIVATE_PENDING_SUBJECT','sent':'','snippet':'','classification':'review'}])
    page=client.get('/preschool')
    assert page.status_code == 404
    assert 'Bring class boots' not in page.text and 'Class outing' not in page.text
    assert 'PRIVATE_EMAIL_SUBJECT' not in page.text and 'PRIVATE_PENDING_SUBJECT' not in page.text
    assert 'mail.google.com' not in page.text
    assert 'No class guide edition is ready' in page.text
    assert 'Possible class-message matches' not in page.text
    assert 'PRIVATE_PENDING_SUBJECT' in client.get('/preschool/manage').text


def test_topic_page_combines_multiple_emails_with_sources_secondary():
    save(1,[item('First classroom fact')])
    save(2,[item('Second classroom fact')],account='shared')
    page=client.get('/preschool/guide/updates')
    assert 'First classroom fact' in page.text and 'Second classroom fact' in page.text
    assert 'PRIVATE_EMAIL_SUBJECT' not in page.text
    start=page.text.index('<details class="class-sources">')
    assert page.text.index('mail.google.com')>start
    assert '<details class="class-sources" open' not in page.text


def test_class_page_updates_are_independent_of_emails_and_keep_editions_and_sources():
    save(1,[item('Class routine: first instruction',details='Original source instruction.')])
    first=client.post('/preschool/manage/topics/routines',headers=ORIGIN,
        data={'body':'Our current class routine.','revision':'0'},follow_redirects=False)
    assert first.status_code==303 and first.headers['location']=='/preschool/guide/routines'
    second=client.post('/preschool/manage/topics/routines',headers=ORIGIN,
        data={'body':'Our corrected class routine.','revision':'1'})
    assert second.status_code==200
    assert 'Our corrected class routine.' in second.text
    assert 'Original source instruction.' not in second.text
    assert 'Our current class routine.' not in second.text
    assert 'Our current class routine.' in client.get('/preschool/history/routines/1').text
    assert 'Original source instruction.' in client.get('/preschool/history').text
    assert 'Original source instruction.' in store.all_items()[0]['details']
    assert store.message_count()==1
    current=site_store.pages()['routines']
    assert current['revision']==2
    assert len(current['source_keys'])==1 and len(site_store.history('routines'))==2
    assert (config.DATA_DIR/'preschool-site.db').stat().st_mode & 0o777==0o600


def test_new_source_does_not_overwrite_or_append_competing_text_to_maintained_page():
    save(1,[item('Class routine: earlier source')])
    client.post('/preschool/manage/topics/routines',headers=ORIGIN,data={'body':'The current class routine.','revision':0})
    save(2,[item('Class routine: new proposed change')])
    page=client.get('/preschool/guide/routines')
    assert 'The current class routine.' in page.text
    assert 'Class routine: new proposed change' not in page.text
    assert '1 new source fact not yet incorporated' in client.get('/preschool/manage').text
    assert site_store.pages()['routines']['revision']==1


def test_stale_edit_cannot_overwrite_newer_class_page():
    client.post('/preschool/manage/topics/routines',headers=ORIGIN,data={'body':'First edition.','revision':0})
    response=client.post('/preschool/manage/topics/routines',headers=ORIGIN,data={'body':'Unsaved competing edit.','revision':0})
    assert response.status_code==409
    assert 'changed while you were editing' in response.text
    assert 'Unsaved competing edit.' in response.text
    assert site_store.pages()['routines']['body']=='First edition.'


def test_publishing_requires_same_origin_and_saves_only_escaped_text():
    assert client.post('/preschool/manage/topics/updates',data={'body':'Not authorized.','revision':0}).status_code==403
    response=client.post('/preschool/manage/topics/updates',headers=ORIGIN,
        data={'body':'<script>untrusted()</script>','revision':0})
    assert '&lt;script&gt;untrusted()&lt;/script&gt;' in response.text
    assert '<script>untrusted()</script>' not in response.text
    assert client.post('/preschool/manage/topics/updates',headers=ORIGIN,data={'body':'','revision':1}).status_code==400
    assert len(site_store.history('updates'))==1


def test_site_editing_never_resumes_collection_or_changes_source_catalog():
    for account in ('personal','shared'):
        with store.account(account):
            store.set_setting('auto_enabled','0');store.set_setting('backfill_enabled','0')
    save(1,[item('Classroom fact')])
    before=store.all_items()
    client.post('/preschool/manage/topics/updates',headers=ORIGIN,data={'body':'Maintained class announcement.','revision':0})
    assert store.all_items()==before
    for account in ('personal','shared'):
        with store.account(account):
            assert store.setting('auto_enabled')=='0' and store.setting('backfill_enabled')=='0'


def test_uncertain_source_details_are_maintenance_only():
    save(1,[item('Ambiguous source fact',uncertainty='Unclear date and audience.')])
    assert 'Ambiguous source fact' not in client.get('/preschool/guide/updates').text
    assert 'Ambiguous source fact' in client.get('/preschool/manage').text
    assert '1 saved source item needs clarification' in client.get('/preschool/manage').text


def test_maintained_fee_page_removes_superseded_reminders_from_current_site_but_not_history():
    save(1,[item('Past class contribution',kind='action',date='2020-01-01')])
    assert 'Past class contribution' in client.get('/preschool/history').text
    client.post('/preschool/manage/topics/fees',headers=ORIGIN,
        data={'body':'Refer to the current class invoice for the contribution schedule.','revision':0})
    assert 'past-due responsibility' not in client.get('/preschool').text
    assert 'Past class contribution' in client.get('/preschool/history').text


def test_search_uses_published_topic_text_and_labels_earlier_facts_as_history():
    save(1,[item('Class routine: earlier fact')])
    client.post('/preschool/manage/topics/routines',headers=ORIGIN,data={'body':'Updated pickup instructions.','revision':0})
    current=client.get('/preschool/search?q=pickup&past=0')
    assert 'Updated pickup instructions.' in current.text
    old=client.get('/preschool/search?q=earlier')
    assert 'Previous source information' in old.text
    assert 'Class routine: earlier fact' not in client.get('/preschool/search?q=earlier&past=0').text


def test_initial_publication_is_idempotent_and_does_not_overwrite_maintained_topics():
    from preschool import class_context
    save(1,[item('First classroom fact')])
    data=class_context()
    assert site_store.seed_topics(data['topics'])==1
    assert site_store.seed_topics(data['topics'])==0
    assert 'First classroom fact' in site_store.pages()['updates']['body']
    assert len(site_store.history('updates'))==1
    client.post('/preschool/manage/topics/updates',headers=ORIGIN,data={'body':'Maintained classroom page.','revision':1})
    assert site_store.seed_topics(class_context()['topics'])==0
    assert site_store.pages()['updates']['body']=='Maintained classroom page.'
    assert store.message_count()==1


def test_resource_destinations_and_source_quotes_cannot_inject_scripts():
    save(1,[item('<script>bad title</script>',kind='resource',url='javascript:alert(1)',
                 source_quote='<script>bad source</script>')])
    page=client.get('/preschool/resources')
    assert 'href="javascript:' not in page.text
    assert '<script>bad' not in page.text
    assert '&lt;script&gt;bad title' in page.text and '&lt;script&gt;bad source' in page.text


def test_new_source_during_edit_requires_reopening_instead_of_silently_incorporating_it():
    import re
    save(1,[item('First classroom fact')])
    editor=client.get('/preschool/manage/topics/updates')
    fingerprint=re.search(r'name="source_version" value="([a-f0-9]+)"',editor.text)[1]
    save(2,[item('A new classroom fact after editing began')])
    response=client.post('/preschool/manage/topics/updates',headers=ORIGIN,
        data={'body':'My unsaved page text.','revision':0,'source_version':fingerprint})
    assert response.status_code==409
    assert 'Supporting class information changed' in response.text
    assert 'My unsaved page text.' in response.text
    assert 'updates' not in site_store.pages()
