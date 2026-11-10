import math
from email.utils import formatdate

import pytest
from fastapi.testclient import TestClient

from preschool.support import bridge, luna, store as hub_store
from hub.main import app
from preschool import criteria, extract, jev, scanner, store

MID='b123456789abcdef'
SENDER='parent@example.invalid'
client=TestClient(app)
ORIGIN={'Origin':'http://testserver'}


def valid_response(relevant=.97, useful=.91):
    return {'provider':'TypeSafe','model':'typesafe/jev-1.14-20261014','answers':{
        'preschool_relevant':{'type':'noul','noul':relevant},
        'useful_information':{'type':'noul','noul':useful}}}


def test_request_uses_fixed_decision_model_privacy_and_minimizes_identity(monkeypatch):
    monkeypatch.setattr(luna,'key',lambda:'fake-key')
    captured={}
    def transport(url,body,token):
        captured.update(url=url,body=body,token=token)
        return valid_response()
    message={'subject':'Class meeting','text':'MHP parent meeting on Monday.',
             'recipients':{'to':'mhp-2030', 'cc':'other@example.invalid'},
             'from':'Erin <person@example.invalid>','attachments':[{'mime_type':'text/calendar'}]}
    scores=jev.decide(message,'mhp-2030',transport)
    assert captured['url']==jev.URL and captured['token']=='fake-key'
    assert captured['body']['model']=='typesafe/jev-1.14'
    assert captured['body']['provider']==jev.PROVIDER
    assert captured['body']['state']['addressed_to_configured_class_group'] is True
    assert captured['body']['state']['criteria_version']=='1.4'
    assert captured['body']['state']['message_has_attachments'] is True
    assert 'person@example.invalid' not in str(captured['body'])
    assert 'other@example.invalid' not in str(captured['body'])
    assert scores['preschool_relevant']==.97


@pytest.mark.parametrize('response',[{}, {'provider':'Wrong','model':'typesafe/jev-1.14','answers':{}},
    {'provider':'TypeSafe','model':'typesafe/jev-1.13','answers':{}},
    {'provider':'TypeSafe','model':'typesafe/jev-1.14','answers':{'preschool_relevant':{'type':'noul','noul':math.nan},'useful_information':{'type':'noul','noul':.9}}}])
def test_invalid_decision_output_fails_closed(response,monkeypatch):
    monkeypatch.setattr(luna,'key',lambda:'synthetic')
    with pytest.raises(jev.JevError):jev.decide({'subject':'S','text':'T'},transport=lambda *args:response)


def test_decision_rejects_empty_and_overlong_messages():
    with pytest.raises(jev.JevError):jev.request_state({'text':''},None)
    with pytest.raises(jev.JevError):jev.request_state({'text':'x'*(jev.MAX_TEXT+1)},None)


def test_criteria_separates_current_email_from_quoted_history():
    text='Class begins Friday.\nOn Tuesday, the teacher wrote:\n> old quoted school note'
    assert criteria.current_text(text)=='Class begins Friday.\n'
    assert 'practical reminder' in jev.QUESTIONS['useful_information']['criteria']['true']
    assert 'tuition' in jev.QUESTIONS['preschool_relevant']['criteria']['true']
    assert 'MapleNews' in jev.QUESTIONS['preschool_relevant']['criteria']['true']
    assert 'class-group recipient plus current content clearly about this class qualifies' in extract.SYSTEM


def setup_candidate(monkeypatch,when):
    with store.account('shared'):
        store.add_candidates('family@example.invalid',[{'id':MID,'sender':SENDER,'subject':'Class update',
            'sent':formatdate(when,usegmt=True),'snippet':'MHP class','classification':'review'}])
        store.set_setting('auto_started',when-60)
    monkeypatch.setattr(luna,'configured',lambda:True)
    monkeypatch.setattr(hub_store,'ai_blocked',lambda sender:False)
    monkeypatch.setattr(bridge,'get',lambda path,**params:
        {'mailbox':'family@example.invalid'} if path=='/gmail/shared/profile' else
        {'id':MID,'from':'Parent <parent@example.invalid>','subject':'Class update',
         'date':formatdate(when,usegmt=True),'text':'Class meets on Friday.','truncated':False,'attachments':[]} if path=='/gmail/shared/message' else
        (_ for _ in ()).throw(AssertionError(path)))


def test_high_confidence_relevant_mail_calls_luna_and_saves_decision(monkeypatch):
    when=1773000000; setup_candidate(monkeypatch,when)
    monkeypatch.setattr(jev,'decide',lambda *a,**kw:{'preschool_relevant':.97,'useful_information':.9})
    luna_calls=[]
    monkeypatch.setattr('preschool.extract.extract',lambda *a,**kw:luna_calls.append(1) or [])
    with store.account('shared'):
        store.set_setting('auto_enabled','1')
        assert scanner.auto_process(when)
        row=store.candidate(MID)
        assert row['state']=='processed' and row['relevance_score']==.97 and row['decision_model']==jev.MODEL
        assert len(luna_calls)==1


def test_uncertain_or_nonuseful_decision_waits_for_owner(monkeypatch):
    when=1773000001; setup_candidate(monkeypatch,when)
    monkeypatch.setattr(jev,'decide',lambda *a,**kw:{'preschool_relevant':.65,'useful_information':.6})
    luna_calls=[]
    monkeypatch.setattr('preschool.extract.extract',lambda *a,**kw:luna_calls.append(1) or [])
    with store.account('shared'):
        store.set_setting('auto_enabled','1')
        assert not scanner.auto_process(when)
        row=store.candidate(MID)
        assert row['state']=='needs_review' and row['relevance_score']==.65
        assert not luna_calls


def test_low_relevance_is_filtered_but_restorable(monkeypatch):
    when=1773000002; setup_candidate(monkeypatch,when)
    monkeypatch.setattr(jev,'decide',lambda *a,**kw:{'preschool_relevant':.01,'useful_information':.02})
    with store.account('shared'):
        store.set_setting('auto_enabled','1')
        assert not scanner.auto_process(when)
        assert store.candidate(MID)['state']=='filtered'
        store.mark_candidate(MID,'new')
        assert len(store.candidates())==1


def test_shared_automatic_screen_is_disabled_by_default(monkeypatch):
    when=1773000003; setup_candidate(monkeypatch,when)
    monkeypatch.setattr(jev,'decide',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('should stay off')))
    with store.account('shared'):
        assert not scanner.auto_process(when)
        assert store.candidate(MID)['state']=='new'


def test_explicit_90_day_backfill_can_screen_old_messages_but_not_older_ones(monkeypatch):
    when=1773000004; setup_candidate(monkeypatch,when)
    monkeypatch.setattr(jev,'decide',lambda *a,**kw:{'preschool_relevant':.97,'useful_information':.9})
    monkeypatch.setattr('preschool.extract.extract',lambda *a,**kw:[])
    with store.account('shared'):
        store.set_setting('auto_enabled','1')
        store.set_setting('auto_started',when+60)
        assert not scanner.auto_process(when)
        assert store.candidate(MID)['state']=='new'
        store.set_setting('backfill_enabled','1')
        store.set_setting('backfill_from',when-90*86400)
        assert scanner.auto_process(when)
        assert store.processed(MID)


def test_backfill_query_is_bounded_and_group_scoped():
    query=scanner.search_query('class@example.invalid','after:1790000000')
    assert 'to:class@example.invalid' in query and 'cc:class@example.invalid' in query and 'list:class@example.invalid' in query
    assert 'tuition' in query
    assert 'after:1790000000' in query
    with pytest.raises(ValueError):scanner.search_query('x} OR from:other','after:1')


def test_daily_ai_caps_are_shared_across_mailboxes_without_resetting_history():
    day='2026-11-01'
    with store.account('personal'):store.set_setting('auto_count_'+day,'2')
    with store.account('shared'):store.set_setting('auto_count_'+day,'1')
    assert scanner.global_daily_count('auto_count_',day)==3
    scanner.set_global_daily_count('auto_count_',day,4)
    assert scanner.global_daily_count('auto_count_',day)==4


def test_status_jev_check_uses_synthetic_state_only(monkeypatch):
    seen=[]
    def fake_decide(message, class_group=None, supporting_sources=()):
        seen.append(message)
        return {'preschool_relevant':.99,'useful_information':.98}
    monkeypatch.setattr(jev,'decide',fake_decide)
    monkeypatch.setattr(luna,'configured',lambda:False)
    response=client.post('/status/jev-test',headers=ORIGIN)
    assert response.status_code==200 and 'Synthetic relevance' in response.text
    assert len(seen)==1 and 'Synthetic preschool family work party' in seen[0]['text']


@pytest.mark.parametrize('account',['personal','shared'])
def test_both_accounts_require_explicit_automatic_activation(monkeypatch,account):
    monkeypatch.setattr(luna,'configured',lambda:pytest.fail('No provider access without activation'))
    monkeypatch.setattr(bridge,'get',lambda *a,**kw:pytest.fail('No mailbox reads without activation'))
    with store.account(account):
        assert not scanner.auto_process(1773000000)
        assert store.setting('auto_enabled') is None


def test_quota_exhaustion_defers_then_resumes_without_a_second_jev_judgment(monkeypatch):
    when=1773000000;setup_candidate(monkeypatch,when)
    judgments=[];extractions=[]
    monkeypatch.setattr(jev,'decide',lambda *a,**kw:judgments.append(1) or {'preschool_relevant':.99,'useful_information':.99})
    monkeypatch.setattr(extract,'extract',lambda *a,**kw:extractions.append(1) or [])
    day=scanner.datetime.fromtimestamp(when,scanner.ZoneInfo('America/Los_Angeles')).date().isoformat()
    scanner.set_global_daily_count('auto_count_',day,scanner.DAILY_EMAIL_LIMIT)
    with store.account('shared'):
        store.set_setting('auto_enabled','1')
        assert not scanner.auto_process(when)
        row=store.candidate(MID)
        assert row['state']=='ready' and row['criteria_version']==criteria.VERSION
        assert not extractions and judgments==[1]
        # A saved ready decision needs no remaining Jev quota to extract.
        scanner.set_global_daily_count('auto_count_',day,0)
        scanner.set_global_daily_count('jev_count_',day,scanner.DAILY_JEV_LIMIT)
        assert scanner.auto_process(when+1)
        assert judgments==[1] and extractions==[1]
        assert store.candidate(MID)['state']=='processed'


def test_class_group_match_requires_complete_token():
    assert jev.class_group_match({'to':'Class <class@example.invalid>'},'class@example.invalid')
    assert not jev.class_group_match({'to':'other-class@example.invalid'},'class@example.invalid')
    assert not jev.class_group_match({'to':'class@example.invalid.evil'},'class@example.invalid')


def test_startup_initializes_both_accounts_off_without_resetting_existing_activation(monkeypatch):
    monkeypatch.setattr(scanner,'_started',False)
    class NoThread:
        def __init__(self,*args,**kwargs):pass
        def start(self):pass
    monkeypatch.setattr(scanner.threading,'Thread',NoThread)
    scanner.start()
    for account in ('personal','shared'):
        with store.account(account):assert store.setting('auto_enabled')=='0'
    store.set_setting('auto_enabled','1')
    monkeypatch.setattr(scanner,'_started',False)
    scanner.start()
    assert store.setting('auto_enabled')=='1'


def test_policy_upgrade_reopens_only_unprocessed_decisions_and_preserves_saved_edits():
    store.add_candidates('owner@example.invalid',[
        {'id':MID,'sender':SENDER,'subject':'Synthetic','sent':'','snippet':'','classification':'review'}])
    store.save_decision(MID,{'preschool_relevant':.99,'useful_information':.99},'ready')
    from contextlib import closing
    with closing(store.db()) as db,db:
        db.execute("update candidates set criteria_version='1.3' where id=?",(MID,))
    assert store.reopen_stale_decisions(criteria.VERSION)==1
    assert store.candidate(MID)['state']=='new'
    entry={'kind':'update','title':'Synthetic edited note','details':'Owner edit','date':None,'time':None,
           'location':None,'url':None,'uncertainty':'','source_quote':'Synthetic evidence'}
    store.save_message('owner@example.invalid',{'id':MID,'sender':SENDER,'subject':'Synthetic','date':'',
                                               'has_attachments':False},[entry])
    with closing(store.db()) as db,db:
        db.execute("update messages set criteria_version='1.3' where id=?",(MID,))
    assert store.reopen_stale_decisions(criteria.VERSION)==0
    assert store.all_items()[0]['details']=='Owner edit'
    assert store.all_items()[0]['criteria_version']=='1.3'
