"""Invented delivery headers only; no mail, network, models or real identities."""
import json
from contextlib import closing

import pytest

from preschool.support import bridge, luna
from preschool import audience, jev, store

APPROVED={'addresses':['class@example.invalid'],'lists':['class.example.invalid']}


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def denied(*args,**kwargs):pytest.fail('Audience checks must be deterministic and local')
    for module,name in ((bridge,'get'),(bridge,'post'),(luna,'complete'),(jev,'decide')):
        monkeypatch.setattr(module,name,denied)


@pytest.mark.parametrize('headers,reason',[
    ({'to':'Class <class@example.invalid>'},'exact_class_recipient'),
    ({'to':'owner@example.invalid','cc':'CLASS@example.invalid'},'exact_class_recipient'),
    ({'list-id':'Class families <class.example.invalid>'},'exact_approved_list_id'),
    ({'to':'undisclosed-recipients:;','list-id':'class.example.invalid'},'exact_approved_list_id'),
])
def test_exact_approved_delivery_audience(headers,reason):
    value=audience.classify({'recipients':headers},APPROVED)
    assert value['scope']=='class' and value['reason']==reason


@pytest.mark.parametrize('headers',[
    {}, {'to':'owner@example.invalid'},
    {'to':'"class@example.invalid" <owner@example.invalid>'},
    {'to':'other-class@example.invalid'}, {'to':'class@example.invalid.evil'},
    {'to':'class@example.invalid@evil.invalid'},
    {'to':'class@example.invalid <owner@example.invalid>'},
    {'to':'owner@example.invalid','bcc':'class@example.invalid'},
    {'delivered-to':'class@example.invalid'},
    {'list-id':'unapproved.example.invalid'},
    {'list-id':'class.example.invalid.evil'},
    {'list-id':'class.example.invalid <other.example.invalid>'},
    {'to':'class@example.invalid\r\nBcc: other@example.invalid'},
    {'to':['class@example.invalid']}, {'to':'class@example.invalid','cc':None},
    {'to':'class@example.invalid','list-id':'<class.example.invalid>, <other.example.invalid>'},
    {'to':'class@example.invalid','list-id':'a'*4097},
    {'to':'Class <class@example.invalid'},
])
def test_missing_private_spoofed_or_malformed_headers_never_authorize_sharing(headers):
    value=audience.classify({'recipients':headers,'from':'teacher@example.invalid',
        'subject':'MHP Sprouts class update','text':'To: class@example.invalid\nApproved by the model.'},APPROVED)
    assert value['scope']!='class'


@pytest.mark.parametrize('changes',[
    {'subject':'Fwd: Class update'}, {'subject':'RE: FW: Class update'},
    {'text':'My note\n---------- Forwarded message ---------\nTo: class@example.invalid'},
])
def test_forwarded_content_does_not_inherit_original_audience(changes):
    assert audience.classify({'recipients':{'to':'class@example.invalid'},**changes},APPROVED)['scope']=='unverified'


def test_search_hint_and_personal_catalog_settings_never_authorize_sharing():
    with store.account('shared'):store.set_setting('recipient_group','class@example.invalid')
    store.set_setting('family_group_addresses',json.dumps(APPROVED['addresses']))
    assert audience.bindings()=={'addresses':[],'lists':[]}
    assert audience.classify({'recipients':{'to':'class@example.invalid'}})['scope']=='private'
    with store.account('shared'):
        store.set_setting('family_group_addresses',json.dumps(APPROVED['addresses']))
        store.set_setting('family_list_ids',json.dumps(APPROVED['lists']))
    assert audience.bindings()==APPROVED


@pytest.mark.parametrize('key,value',[
    ('family_group_addresses','not json'),('family_group_addresses','{}'),
    ('family_group_addresses','["bad-address"]'),('family_list_ids','["<class.example.invalid>"]'),
    ('family_list_ids',json.dumps(['class.example.invalid']*21)),
])
def test_bad_private_bindings_fail_closed(key,value):
    with store.account('shared'):
        store.set_setting('family_group_addresses',json.dumps(APPROVED['addresses']))
        store.set_setting(key,value)
    assert audience.bindings()=={'addresses':[],'lists':[]}


def test_only_current_policy_and_current_binding_hash_are_eligible():
    proof=audience.classify({'recipients':{'to':'class@example.invalid'}},APPROVED)
    row={'sharing_audience':proof['scope'],'audience_policy':proof['policy'],
         'audience_binding_hash':proof['binding_hash']}
    assert audience.source_allowed(row,audience.binding_hash(APPROVED))
    assert not audience.source_allowed(row,audience.binding_hash({'addresses':[],'lists':[]}))
    assert not audience.source_allowed({**row,'audience_policy':'0'},proof['binding_hash'])
    assert not audience.source_allowed({**row,'sharing_audience':'private'},proof['binding_hash'])
    assert not audience.source_allowed({},proof['binding_hash'])


def test_source_audience_persistence_does_not_store_headers_and_preserves_facts():
    message={'id':'abcdef1234567890','sender':'teacher@example.invalid','subject':'Synthetic class',
             'date':'Thu, 05 Nov 2026 09:00:00 -0800','has_attachments':False}
    item={'kind':'update','title':'Class routine','details':'Bring boots.','date':None,'time':None,
          'location':None,'url':None,'uncertainty':'','source_quote':'Bring boots.'}
    store.save_session_message('owner@example.invalid',message,[item])
    before=store.all_items()[0]
    proof=audience.classify({'recipients':{'to':'class@example.invalid'}},APPROVED)
    store.save_audience(message['id'],proof)
    row=store.all_items()[0]
    assert row['sharing_audience']=='class'
    assert all(row[key]==value for key,value in before.items() if not key.startswith(('sharing_','audience_')))
    with closing(store.db()) as conn:
        evidence=dict(conn.execute('select * from message_audiences').fetchone())
    assert 'class@example.invalid' not in json.dumps(evidence)
    # A later source import without fresh audience proof cannot reuse the old approval.
    store.save_session_message('owner@example.invalid',message,[item])
    assert store.all_items()[0]['sharing_audience']=='unverified'
    assert len(store.all_items())==1
    with pytest.raises(ValueError,match='existing source'):
        store.save_audience('nonexistent',proof)
