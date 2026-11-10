"""Session catch-up uses fake mail and local publication, never a model API."""
import copy

import pytest

from preschool.support import bridge,config,luna
from preschool import _view_key,jev,schedule,session_import,site_store,store,website


def source(key):
    return {'view_key':key,'gmail_url':'https://mail.google.com/mail/#all/abcdef1234567890',
            'source_quote':'Synthetic source evidence.','saved_at':1,'kind':'update','title':'Synthetic evidence',
            'details':'','date':None,'time':None,'location':None,'url':None,'uncertainty':'','dismissed':False}


KEY='a'*64
PUBLIC={'url':'https://maple-hill.example/kids/mhp/','quote':'Synthetic term dates.','title':'Public term reference'}


def rule():
    return {'title':'Friday class','weekdays':[4],'start_date':'2026-09-11','end_date':'2027-06-17',
            'start_time':'09:30','end_time':'11:30','location':'Classroom','details':'Source-confirmed regular class.',
            'source_keys':[KEY],'public_sources':[PUBLIC],'exceptions':[]}


def timetable(payload=None):
    return {'slug':'our-class','payload':payload or rule(),'revision':1,'updated':1}


def exception(day,kind='cancelled'):
    return {'date':day,'kind':kind,'title':'No class — school break','details':'Source-confirmed school closure.',
            'location':'','start_time':None,'end_time':None,'source_keys':[KEY]}


def test_verified_weekly_class_appears_without_a_new_email_for_every_occurrence():
    rows=schedule.occurrences([timetable()],'2026-11-05',[source(KEY)])
    assert rows[0]['date']=='2026-11-06' and rows[0]['time']=='9:30 AM – 11:30 AM Pacific'
    assert rows[0]['schedule_status']=='regular'
    assert rows[0]['sources'][0]['view_key']==KEY
    assert all(row['date']<='2027-02-02' for row in rows)


def test_closed_class_is_replaced_not_shown_as_a_regular_session():
    payload=rule();payload['exceptions']=[exception('2026-11-27')]
    rows=schedule.occurrences([timetable(payload)],'2026-11-26',[source(KEY)])
    assert rows[0]['date']=='2026-11-27' and rows[0]['schedule_status']=='cancelled'
    assert rows[0]['title'].startswith('No class') and rows[0]['time'] is None
    assert len([row for row in rows if row['date']=='2026-11-27'])==1


def test_field_trip_does_not_inherit_an_unconfirmed_regular_time_or_location():
    payload=rule();changed=exception('2026-12-04','changed')
    changed.update(title='Class field trip',location='Source-confirmed destination',details='Time not yet confirmed.')
    payload['exceptions']=[changed]
    rows=schedule.occurrences([timetable(payload)],'2026-12-04',[source(KEY)])
    assert rows[0]['time'] is None and rows[0]['location']=='Source-confirmed destination'


def test_timetable_stops_at_verified_term_end_and_fails_closed_without_sources():
    assert schedule.occurrences([timetable()],'2027-06-18',[source(KEY)])==[]
    assert schedule.occurrences([timetable()],'2026-11-05',[])==[]
    payload=rule();change=exception('2026-11-06');change['source_keys']=['b'*64];payload['exceptions']=[change]
    assert all(row['date']!='2026-11-06' for row in schedule.occurrences([timetable(payload)],'2026-11-05',[source(KEY)]))


def test_schedule_uses_local_civil_times_across_daylight_saving_changes():
    rows=schedule.occurrences([timetable()],'2026-10-29',[source(KEY)],horizon=15)
    assert rows[0]['date']=='2026-10-30' and rows[1]['date']=='2026-11-06'
    assert rows[0]['time']==rows[1]['time']=='9:30 AM – 11:30 AM Pacific'


@pytest.mark.parametrize('change',[
    {'weekdays':[7]}, {'weekdays':[True]}, {'end_date':'2028-06-17'},
    {'start_time':'11:30','end_time':'09:30'}, {'source_keys':[]},
    {'exceptions':[exception('2026-11-05')]},
    {'public_sources':[{'url':'https://outside.invalid/path','quote':'Synthetic','title':'Unapproved'}]},
])
def test_unsupported_or_unbounded_schedule_data_is_rejected(change):
    payload=rule();payload.update(change)
    with pytest.raises(ValueError):schedule.validate(payload)


def test_schedule_publication_keeps_revisions_and_checks_for_conflicting_updates():
    assert site_store.save_timetable('our-class',rule(),0)==1
    with pytest.raises(site_store.RevisionConflict):site_store.save_timetable('our-class',rule(),0)
    payload=copy.deepcopy(rule());payload['exceptions']=[exception('2026-11-27')]
    assert site_store.save_timetable('our-class',payload,1)==2
    assert site_store.timetables()[0]['revision']==2


def test_timetable_enters_the_same_week_and_date_views_as_explicit_class_events():
    data=website.project([source(KEY)],[],{},'2026-11-05',[timetable()])
    assert data['this_week'][0]['title']=='Friday class'
    assert data['dated'][0]['date']=='2026-11-06'


@pytest.fixture
def fake_session(monkeypatch,tmp_path):
    secret=tmp_path/'private'/'bridge-token';secret.parent.mkdir();(secret.parent/'organization-paused').touch()
    monkeypatch.setattr(config,'BRIDGE_TOKEN_FILE',secret)
    store.set_setting('mailbox','owner@example.invalid')
    for account in ('personal','shared'):
        with store.account(account):store.set_setting('auto_enabled','0');store.set_setting('backfill_enabled','0')
    def denied(*args,**kwargs):pytest.fail('Session import must not call a separate model or mutate Gmail')
    monkeypatch.setattr(luna,'complete',denied);monkeypatch.setattr(jev,'decide',denied);monkeypatch.setattr(bridge,'post',denied)
    raw={'id':'abcdef1234567890','from':'teacher@example.invalid','subject':'Synthetic class facts',
         'date':'Thu, 05 Nov 2026 09:00:00 -0800','text':'Our class meets Fridays from 9:30 to 11:30.',
         'truncated':False,'attachments':[],'links':[]}
    def get(path,**params):
        if path=='/gmail/profile':return {'mailbox':'owner@example.invalid'}
        if path=='/gmail/shared/profile':return {'mailbox':'household@example.invalid'}
        if path in ('/gmail/message','/gmail/shared/message'):return raw
        raise AssertionError(path)
    monkeypatch.setattr(bridge,'get',get)
    draft={'source_index':0,'item':{'kind':'update','title':'Class routine','details':'Fridays, 9:30–11:30.',
        'date':None,'time':None,'location':None,'url':None,'uncertainty':'','source_quote':'Our class meets Fridays from 9:30 to 11:30.'}}
    return raw,draft


def test_session_import_verifies_sources_and_is_idempotent_without_external_model_calls(fake_session):
    raw,draft=fake_session
    first=session_import.import_facts('personal',raw['id'],raw['from'],[draft])
    second=session_import.import_facts('personal',raw['id'],raw['from'],[draft])
    assert first['item_ids']==second['item_ids'] and len(store.all_items())==1
    assert store.setting('auto_enabled')=='0' and store.setting('backfill_enabled')=='0'


def test_session_import_never_overwrites_a_preexisting_owner_edit(fake_session):
    raw,draft=fake_session
    original=session_import.import_facts('personal',raw['id'],raw['from'],[draft])
    item=store.get_item(original['item_ids'][0]);item['details']='Owner-maintained correction.'
    store.update_item(item['id'],item)
    session_import.import_facts('personal',raw['id'],raw['from'],[draft])
    assert store.get_item(item['id'])['details']=='Owner-maintained correction.'


def test_session_import_rejects_nonmatching_quote_and_keeps_existing_data(fake_session):
    raw,draft=fake_session;draft['item']['source_quote']='Invented source quote'
    with pytest.raises(ValueError,match='quote does not match'):
        session_import.import_facts('personal',raw['id'],raw['from'],[draft])
    assert store.message_count()==0


def test_session_import_refuses_if_automatic_processing_is_active(fake_session):
    raw,draft=fake_session;store.set_setting('auto_enabled','1')
    with pytest.raises(ValueError,match='must stay paused'):
        session_import.import_facts('personal',raw['id'],raw['from'],[draft])


def test_session_import_requires_the_bridge_pause(fake_session):
    raw,draft=fake_session
    (config.BRIDGE_TOKEN_FILE.parent/'organization-paused').unlink()
    with pytest.raises(ValueError,match='maintenance pause'):
        session_import.import_facts('personal',raw['id'],raw['from'],[draft])
    assert store.message_count()==0


def test_session_import_rejects_changed_mailbox_identity(fake_session):
    raw,draft=fake_session
    store.set_setting('mailbox','different@example.invalid')
    with pytest.raises(ValueError,match='identity changed'):
        session_import.import_facts('personal',raw['id'],raw['from'],[draft])
    assert store.message_count()==0


def test_session_import_rejects_changed_sender_and_truncated_body(fake_session):
    raw,draft=fake_session
    with pytest.raises(ValueError,match='sender did not match'):
        session_import.import_facts('personal',raw['id'],'different@example.invalid',[draft])
    raw['truncated']=True
    with pytest.raises(ValueError,match='truncated email'):
        session_import.import_facts('personal',raw['id'],raw['from'],[draft])
    assert store.message_count()==0


def test_session_import_shared_catalog_stays_separate(fake_session):
    raw,draft=fake_session
    with store.account('shared'):
        store.set_setting('mailbox','household@example.invalid')
    result=session_import.import_facts('shared',raw['id'],raw['from'],[draft])
    assert result['item_ids'] and store.message_count()==0
    with store.account('shared'):
        assert store.message_count()==1
        assert store.setting('auto_enabled')=='0' and store.setting('backfill_enabled')=='0'


def test_session_import_holds_unavailable_sources_without_claiming_full_review(fake_session):
    raw,draft=fake_session
    raw['attachments']=[{'mime_type':'application/pdf','readable':False}]
    result=session_import.import_facts('personal',raw['id'],raw['from'],[draft])
    assert result['partial_sources'] and store.message_count()==1
    assert store.setting('auto_enabled')=='0' and store.setting('backfill_enabled')=='0'
