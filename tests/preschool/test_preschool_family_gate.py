"""Invented mail and fake model transport only; no live calls or publication."""
import pytest

from preschool.support import luna
from preschool import family_gate as gate, jev

APPROVED={'addresses':['sprouts@example.invalid'],'lists':['sprouts.example.invalid']}
ITEM={'kind':'event','title':'Parent meeting','details':'General class meeting.',
      'date':'2026-11-09','time':'7 PM','location':'Red Room'}


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setattr(luna,'key',lambda:'SYNTHETIC_ONLY_KEY')


def transport(scores, capture=None):
    def respond(url,request,key):
        assert url==jev.URL and key=='SYNTHETIC_ONLY_KEY'
        assert request['model']=='typesafe/jev-1.14'
        assert request['provider']=={'only':['TypeSafe'],'allow_fallbacks':False,'zdr':True,'data_collection':'deny'}
        if capture is not None:capture.append(request)
        return {'provider':'TypeSafe','model':jev.MODEL,
                'answers':{key:{'type':'noul','noul':value} for key,value in scores.items()}}
    return respond


@pytest.mark.parametrize('headers',[{'to':'owner@example.invalid'}, {'bcc':'sprouts@example.invalid'},
    {'to':'"sprouts@example.invalid" <owner@example.invalid>'},{'list-id':'otherclass.example.invalid'}])
def test_private_unknown_bcc_and_display_names_never_reach_model_or_key(headers,monkeypatch):
    monkeypatch.setattr(luna,'key',lambda:pytest.fail('Must stop before key access'))
    result=gate.screen({'recipients':headers,'text':'Synthetic class news.'},APPROVED,
                       lambda *a:pytest.fail('Private mail must never reach the model'))
    assert result['state']=='held_audience'


def test_scope_context_is_explicit_and_source_identities_are_not_sent():
    calls=[]
    raw={'recipients':{'to':'sprouts@example.invalid'},'from':'staff@example.invalid',
         'id':'synthetic-private-id','text':'School-wide closure Wednesday November 11.','subject':'General school context'}
    result=gate.screen(raw,APPROVED,transport({'preschool_relevant':.99,'useful_information':.99},calls))
    assert result['state']=='ready'
    state=calls[0]['state']
    assert 'recipients' not in state and 'from' not in state and 'id' not in state
    assert state['target_class']['class']=='MHP Sprouts' and 'other-class-only' in state['scope_rule']


def test_only_exact_public_fields_reach_final_decision():
    calls=[]
    item={**ITEM,'source_quote':'PRIVATE QUOTE','sender':'PRIVATE SENDER','account':'PRIVATE ACCOUNT'}
    public=gate.public_copy(item)
    result=gate.final_check(public,display_role='sprouts_instruction',transport=transport(
        {'personal_information':0,'useful_to_sprouts':1,'scope_appropriate':1},calls))
    assert result['state']=='eligible_in_trial' and set(public)==set(ITEM)
    assert calls[0]['state']['public_copy']==ITEM
    with pytest.raises(gate.GateError,match='Private source'):
        gate.final_check(item,display_role='sprouts_instruction')


@pytest.mark.parametrize('details',['Contact family@example.invalid','Password: synthetic',
    'Call 206-555-0123','The roster is attached','Open https://mail.google.com/'])
def test_obvious_private_values_stop_without_a_model_call(details):
    with pytest.raises(gate.GateError):gate.public_copy({**ITEM,'details':details})


@pytest.mark.parametrize('url',['https://docs.google.com/document/d/privateSynthetic123/view',
    'https://outside.invalid/','https://maple-hill.example/path?token=synthetic'])
def test_private_resource_destinations_do_not_reach_final_gate(url):
    with pytest.raises(gate.GateError):gate.public_copy({**ITEM,'url':url})


@pytest.mark.parametrize('changes',[{'personal_information':.02},{'useful_to_sprouts':.89},{'scope_appropriate':.89}])
def test_any_uncertain_or_unsafe_decision_holds_copy(changes):
    scores={'personal_information':0,'useful_to_sprouts':1,'scope_appropriate':1,**changes}
    assert gate.final_check(ITEM,display_role='school_context',transport=transport(scores))['state']=='held_final_check'


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(provider='Other'),lambda r:r.update(model='typesafe/jev-1.140'),
    lambda r:r['answers'].update(extra={'type':'noul','noul':0}),
    lambda r:r['answers']['personal_information'].update(noul=True),
    lambda r:r['answers']['personal_information'].update(noul=float('nan')),
])
def test_wrong_provider_model_or_malformed_answers_fail_closed(mutation):
    def respond(url,request,key):
        result={'provider':'TypeSafe','model':jev.MODEL,'answers':{
            name:{'type':'noul','noul':0 if name=='personal_information' else 1} for name in gate.FINAL_QUESTIONS}}
        mutation(result);return result
    with pytest.raises(gate.GateError):gate.final_check(ITEM,display_role='school_context',transport=respond)


def test_generic_paperwork_requirement_is_not_a_hard_pattern_block():
    assert gate.public_copy({**ITEM,'details':'Submit the required vaccination paperwork.'})


def test_uncertainty_and_personal_identifiers_cannot_be_dropped_from_view():
    with pytest.raises(gate.GateError,match='Uncertainty'):
        gate.public_copy({**ITEM,'uncertainty':'Year not confirmed.'})
    with pytest.raises(gate.GateError,match='identifier'):
        gate.public_copy({**ITEM,'details':'Student ID: 901234567'})


@pytest.mark.parametrize('change',[
    {'subject':'Fwd: School news'}, {'subject':'Re: FW: School news'},
    {'text':'My note\n---------- Forwarded message ---------\nSynthetic class news.'},
])
def test_forwarded_group_delivery_still_stops_before_models(change,monkeypatch):
    monkeypatch.setattr(luna,'key',lambda:pytest.fail('Forwarded content must stop before key access'))
    raw={'recipients':{'to':'sprouts@example.invalid'},'text':'Synthetic class news.',**change}
    assert gate.screen(raw,APPROVED)['state']=='held_audience'


def test_matching_class_delivery_does_not_override_low_relevance():
    raw={'recipients':{'to':'sprouts@example.invalid'},'text':'Only the Threes have a special Monday class.'}
    result=gate.screen(raw,APPROVED,transport({'preschool_relevant':.03,'useful_information':.1}))
    assert result['state']=='held_relevance'


@pytest.mark.parametrize('changes',[
    {'date':'20261109'}, {'details':{'source_quote':'SYNTHETIC_PRIVATE_DATA'}},
    {'location':['SYNTHETIC_PRIVATE_DATA']}, {'title':''},
])
def test_malformed_public_values_cannot_enter_the_decision_request(changes):
    with pytest.raises(gate.GateError):gate.public_copy({**ITEM,**changes})


def test_provider_redirect_is_not_followed_or_retried(monkeypatch):
    calls=[]
    class Response:
        status_code=302
    class Client:
        def __init__(self,**kwargs):
            assert kwargs=={'trust_env':False,'follow_redirects':False,'timeout':jev.TIMEOUT}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            calls.append(url)
            assert url==jev.URL and kwargs['json']['provider']==jev.PROVIDER
            return Response()
    monkeypatch.setattr(gate.httpx,'Client',Client)
    with pytest.raises(gate.GateError,match='unavailable'):
        gate.final_check(ITEM,display_role='school_context')
    assert calls==[jev.URL]


def test_pinned_alias_accepts_only_its_known_canonical_response_revision():
    def respond(url,request,key):
        assert request['model']=='typesafe/jev-1.14'
        return {'provider':'TypeSafe','model':'typesafe/jev-1.14-20261014','answers':{
            name:{'type':'noul','noul':0 if name=='personal_information' else 1} for name in gate.FINAL_QUESTIONS}}
    assert gate.final_check(ITEM,display_role='school_context',transport=respond)['state']=='eligible_in_trial'


def test_privacy_only_gate_does_not_rejudge_relevance_or_missing_time():
    calls=[]
    result=gate.privacy_check({**ITEM,'time':None},transport({'safe_public_copy':.98},calls))
    assert result['state']=='eligible_in_trial'
    assert set(calls[0]['questions'])=={'safe_public_copy'}
    assert 'public_copy' in calls[0]['state'] and 'source_quote' not in calls[0]['state']
    assert gate.privacy_check(ITEM,transport({'safe_public_copy':.94}))['state']=='held_privacy'


def test_school_context_screen_keeps_delivery_boundary_and_high_confidence_threshold(monkeypatch):
    raw={'recipients':{'to':'sprouts@example.invalid'},'text':'MHP wreath pickup is December 3.'}
    calls=[]
    assert gate.context_screen(raw,APPROVED,transport({'preschool_relevant':.99,'useful_information':.98},calls))['state']=='ready'
    assert 'fundraiser' in calls[0]['questions']['preschool_relevant']['criteria']['true']
    assert gate.context_screen(raw,APPROVED,transport({'preschool_relevant':.89,'useful_information':.98}))['state']=='held_relevance'
    monkeypatch.setattr(luna,'key',lambda:pytest.fail('Direct mail must not reach models'))
    assert gate.context_screen({**raw,'recipients':{'to':'owner@example.invalid'}},APPROVED)['state']=='held_audience'


def test_unexpected_transport_failure_never_echoes_private_diagnostics():
    def failed(*args):raise RuntimeError('SYNTHETIC_PRIVATE_REPLY')
    with pytest.raises(gate.GateError) as caught:
        gate.privacy_check(ITEM,transport=failed)
    assert 'SYNTHETIC_PRIVATE_REPLY' not in str(caught.value)
