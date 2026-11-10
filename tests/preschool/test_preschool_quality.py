"""Offline contract/benchmark tests: invented evidence and injected responses only."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from preschool.support import bridge, luna
import hub.main as main
from preschool import criteria, extract, jev, processor, quality, scanner, sources, store


@pytest.fixture(autouse=True)
def no_external_access(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('Quality tests must never access accounts, providers or public sources')
    monkeypatch.setattr(bridge, 'get', denied)
    monkeypatch.setattr(luna, 'complete', denied)
    monkeypatch.setattr(jev, 'decide', denied)
    monkeypatch.setattr(sources, '_read_response', denied)


def proposal(source, **changes):
    item = {'kind':'action', 'title':'MHP class contribution', 'details':'$85 due May 5, 2027.',
            'date':'2027-05-05', 'time':None, 'location':None, 'url':None,
            'uncertainty':'', 'source_quote':source}
    item.update(changes)
    return {'items':[item]}


FEE = 'MHP class contribution of $85 is due May 5, 2027.'


@pytest.mark.parametrize('changes',[
    {'details':'$999 due May 5, 2027.'},
    {'title':'MHP class contribution of $999'},
    {'details':'€85 due May 5, 2027.'},
    {'details':'$-85 due May 5, 2027.'},
    {'details':'$85k due May 5, 2027.'},
    {'date':'2099-12-31'},
    {'date':None, 'details':'$85 due May 12, 2027.'},
    {'date':None, 'details':'$85 due February 31, 2027.'},
    {'date':None, 'details':'$85 due 12 May 2027.'},
])
def test_invented_amounts_and_dates_do_not_pass_with_a_genuine_quote(changes):
    with pytest.raises(extract.ExtractionError, match='source review'):
        extract.validate(proposal(FEE, **changes), FEE, quality.SENT)


def test_each_item_must_quote_its_own_amount_not_an_unrelated_bill():
    source = FEE + '\nAn unrelated workshop costs $999.'
    with pytest.raises(extract.ExtractionError):
        extract.validate(proposal(FEE,details='$999 due May 5, 2027.'),source,quality.SENT)


def test_numeric_currency_formatting_can_change_without_changing_amount():
    raw=proposal(FEE,details='85.00 dollars due May 5, 2027.')
    assert extract.validate(raw,FEE,quality.SENT)[0]['date']=='2027-05-05'
    assert extract.validate(proposal(FEE,details='The contribution is $85.'),FEE,quality.SENT)


@pytest.mark.parametrize('source,sent,target',[
    ('MHP meeting on May 5, 2027.',quality.SENT,'2027-05-05'),
    ('MHP meeting on 2027-05-05.',None,'2027-05-05'),
    ('MHP meeting on 5/5/2027.',None,'2027-05-05'),
    ('MHP meeting on May 5.',quality.SENT,'2027-05-05'),
    ('MHP meeting tomorrow.',quality.SENT,'2027-05-02'),
    # The timestamp is May 2 UTC but May 1 in the class's time zone.
    ('MHP meeting tomorrow.','2027-05-02T01:00:00+00:00','2027-05-02'),
])
def test_supported_dates_have_deterministic_source_evidence(source,sent,target):
    raw=proposal(source,kind='event',title='MHP meeting',details='',date=target)
    assert extract.validate(raw,source,sent)[0]['date']==target


@pytest.mark.parametrize('source,sent,target',[
    ('MHP meeting next Friday.',quality.SENT,'2027-05-07'),
    ('MHP meeting May 5.',None,'2027-05-05'),
    ('MHP meeting January 5.','2026-12-20T09:00:00-08:00','2027-01-05'),
    ('MHP meeting January 5.','2026-12-20T09:00:00-08:00','2026-01-05'),
    ('MHP meeting Friday, May 5, 2027.',quality.SENT,'2027-05-05'),
    ('MHP meetings on May 5, 2027 and May 12, 2027.',quality.SENT,'2027-05-05'),
])
def test_ambiguous_dates_and_mismatched_weekdays_require_review(source,sent,target):
    raw=proposal(source,kind='event',title='MHP meeting',details='',date=target)
    with pytest.raises(extract.ExtractionError):extract.validate(raw,source,sent)


def test_missing_amount_and_due_date_are_not_invented():
    source='MHP class contribution: check the original bill for the amount and due date.'
    raw=proposal(source,details='Check the original bill.',date=None)
    item=extract.validate(raw,source)[0]
    assert item['date'] is None and '$' not in item['details']
    with pytest.raises(extract.ExtractionError):extract.validate(proposal(source),source)


@pytest.mark.parametrize('body',[
    'MHP meeting cancelled. Previously May 5, 2027.',
    'MHP meeting rescheduled from May 5, 2027 to May 12, 2027.',
    'MHP meeting postponed; new date TBD.',
])
def test_changed_dates_are_held_before_any_model_and_not_persisted(body,monkeypatch):
    mid='abcdef1234567890';sender='teacher@example.invalid'
    message={'id':mid,'from':sender,'date':'Sat, 01 May 2027 09:00:00 -0700','text':body,'attachments':[]}
    prepared=processor.prepare_message('owner@example.invalid',mid,sender,message)
    assert 'changed_source' in prepared['evidence'].review
    with pytest.raises(extract.ExtractionError):
        processor.read_message('owner@example.invalid',mid,sender,prepared=prepared)
    assert not store.processed(mid)


def test_group_context_is_preserved_for_email_attachment_and_link_without_addresses(monkeypatch):
    with store.account('shared'):store.set_setting('recipient_group','class@example.invalid')
    mid='abcdef1234567890';sender='teacher@example.invalid'
    msg={'id':mid,'from':sender,'date':'Sat, 01 May 2027 09:00:00 -0700',
         'text':'Bring boots for our class.', 'recipients':{'to':'class@example.invalid','cc':'private@example.invalid'}}
    collected=sources.Evidence([{'kind':'plain text','text':'Class supplies: bring gloves.'}],
        [{'label':'Class routine','url':'https://maple-hill.example/kids/mhp/','text':'Class routine: bring coats.'}],[])
    monkeypatch.setattr(sources,'collect',lambda *a,**kw:collected)
    prepared=processor.prepare_message('owner@example.invalid',mid,sender,msg)
    requests=[]
    def complete(req):
        requests.append(json.loads(req['messages'][1]['content']))
        return {'items':[]}
    monkeypatch.setattr(luna,'complete',complete)
    processor.read_message('owner@example.invalid',mid,sender,prepared=prepared)
    assert len(requests)==3
    assert all(req['addressed_to_configured_class_group'] is True for req in requests)
    assert '@example.invalid' not in json.dumps(requests)


def canned_items(case, text):
    if case.supporting and text!=case.supporting:return []
    return [proposal(text,kind=expected.kind,title=' '.join(expected.terms),
                     details=expected.amount or '',date=expected.date,
                     uncertainty='Weekday-only date requires source review.' if expected.uncertain else '')['items'][0]
            for expected in case.expected]


def test_benchmark_evaluator_accepts_expected_facts_with_fake_models(monkeypatch):
    cases={criteria.current_text(case.body):case for case in quality.CASES if not case.review}
    cases.update({case.supporting:case for case in quality.CASES if case.supporting})
    def decide(message,*args,**kwargs):
        relevant=bool(cases[message['text']].expected)
        return {'preschool_relevant':.99 if relevant else .01,'useful_information':.99 if relevant else .01}
    def extraction(text,sent,**kwargs):
        raw={'items':canned_items(cases[text],text)}
        return extract.validate(raw,text,sent)
    report=quality.run(decide=decide,extraction=extraction)
    assert report['passed']==report['total']==len(quality.CASES)
    assert report['synthetic_only'] and report['criteria_version']=='1.4'
    assert report['status']=='completed' and report['executed']==len(quality.CASES)
    assert store.message_count()==0


def test_benchmark_detects_missing_extra_and_wrong_facts_not_just_valid_json():
    case=next(case for case in quality.CASES if case.name=='stated-fee')
    correct=canned_items(case,case.body)
    assert quality.compare(case,correct)==[]
    assert 'missing_expected_item' in quality.compare(case,[])
    wrong=copy.deepcopy(correct);wrong[0]['date']='2099-01-01';wrong[0]['details']='$999'
    assert set(quality.compare(case,wrong))=={'wrong_or_missing_date','wrong_or_missing_amount'}
    assert 'unexpected_extra_item' in quality.compare(case,correct*2)
    mixed=next(case for case in quality.CASES if case.name=='mixed-newsletter')
    unrelated=proposal('street market',kind='event',title='Street market',details='',date=None)['items']
    assert 'unrelated_content' in quality.compare(mixed,unrelated)


def test_benchmark_default_is_non_live(monkeypatch,capsys):
    monkeypatch.setattr('sys.argv',['quality'])
    assert quality.main()==0
    report=json.loads(capsys.readouterr().out)
    assert report['live_calls'] is False and len(report['cases'])==len(quality.CASES)


def test_benchmark_stops_on_provider_failure_without_printing_raw_errors():
    calls=[]
    def unavailable(*args,**kwargs):
        calls.append(1)
        raise jev.JevError('synthetic private provider detail')
    report=quality.run(decide=unavailable)
    assert calls==[1]
    assert report['status']=='blocked' and report['executed']==1
    assert report['results'][0]['failures']==['provider_unavailable']
    assert all(row['failures']==['not_run_after_provider_failure'] for row in report['results'][1:])
    assert 'private provider detail' not in json.dumps(report)


def test_hub_can_run_only_the_fixed_suite_without_terminal_keychain_access(monkeypatch):
    calls=[]
    report={'synthetic_only':True,'criteria_version':criteria.VERSION,'status':'completed',
            'executed':11,'passed':11,'total':11,'results':[]}
    monkeypatch.setattr(quality,'run',lambda:calls.append('fixed_suite') or report)
    client=TestClient(main.app)
    response=client.post('/status/preschool-quality-test',headers={'Origin':'http://testserver'})
    assert response.status_code==200 and response.json()==report
    assert calls==['fixed_suite']
    assert response.headers['Cache-Control']=='no-store'


def test_hub_benchmark_requires_owner_and_same_origin_and_rejects_inputs(monkeypatch):
    monkeypatch.setattr(quality,'run',lambda:pytest.fail('Rejected requests must not run models'))
    client=TestClient(main.app)
    path='/status/preschool-quality-test'
    assert client.get(path).status_code==405
    assert client.post(path).status_code==403
    assert client.post(path,headers={'Origin':'https://outside.invalid'}).status_code==403
    assert client.post(path,headers={'Origin':'http://testserver','Tailscale-User-Login':'other@example.invalid'}).status_code==403
    assert client.post(path+'?model=other',headers={'Origin':'http://testserver'}).status_code==400
    assert client.post(path,json={'prompt':'arbitrary text'},headers={'Origin':'http://testserver'}).status_code==400


def test_hub_benchmark_prevents_overlapping_runs(monkeypatch):
    import threading
    lock=threading.Lock()
    lock.acquire()
    monkeypatch.setattr(main,'_preschool_quality_lock',lock)
    monkeypatch.setattr(quality,'run',lambda:pytest.fail('No overlapping model calls'))
    try:
        response=TestClient(main.app).post('/status/preschool-quality-test',headers={'Origin':'http://testserver'})
        assert response.status_code==409 and lock.locked()
    finally:
        lock.release()


def test_hub_benchmark_sanitizes_failures_and_releases_lock(monkeypatch):
    def fail():raise RuntimeError('synthetic private provider detail')
    monkeypatch.setattr(quality,'run',fail)
    client=TestClient(main.app)
    response=client.post('/status/preschool-quality-test',headers={'Origin':'http://testserver'})
    assert response.status_code==503
    assert 'private provider detail' not in response.text
    assert not main._preschool_quality_lock.locked()
