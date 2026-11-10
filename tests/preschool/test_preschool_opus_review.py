"""Pinned requests and real scratch journals; all inference is mocked."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import httpx

from preschool import family_gate, model_journal, opus_review


def state():
    return {'public_copy': {'kind': 'update', 'title': 'Bring boots', 'details': 'Bring boots.',
                           'date': None, 'time': None, 'location': None},
            'target_class': dict(family_gate.CLASS_CONTEXT), 'display_role': 'school_context',
            'as_of_date': '2027-05-01'}


@pytest.fixture
def setup(tmp_path):
    journal = model_journal.Journal(tmp_path / 'campaign')
    journal.initialize(max_calls=3, max_cost_micros=450_000)
    metadata = {'checked_at': 100, 'endpoints': [{
        'model_id': opus_review.MODEL, 'tag': opus_review.PROVIDER,
        'provider_name': 'Amazon Bedrock', 'safety_retention': False, 'status': 0,
        'supported_parameters': ['max_tokens', 'response_format'],
        'pricing': {'prompt': '0.0000044', 'completion': '0.000022'}}]}
    response = {'model': opus_review.MODEL, 'provider': 'Amazon Bedrock',
                'usage': {'cost': .01}, 'choices': [{'finish_reason': 'stop',
                'message': {'content': json.dumps({'safe_public_copy': .99})}}]}
    calls = []
    def transport(url, body, timeout):
        calls.append((url, copy.deepcopy(body), timeout))
        return copy.deepcopy(response)
    judge = opus_review.Judge(transport=transport, preflight=lambda: metadata,
                             journal=journal, enabled=True, clock=lambda: 150)
    return judge, metadata, response, calls, journal


def test_request_is_pinned_private_bounded_and_tool_free(setup):
    judge, _, _, calls, journal = setup
    assert judge('privacy', state()) == {'safe_public_copy': .99}
    url, body, timeout = calls[0]
    assert url == opus_review.URL and timeout == 40
    assert body['model'] == opus_review.MODEL
    assert body['provider']['only'] == [opus_review.PROVIDER]
    assert body['provider']['allow_fallbacks'] is False
    assert body['provider']['zdr'] is True
    assert body['provider']['data_collection'] == 'deny'
    assert not {'tools', 'plugins', 'models'} & body.keys()
    with journal._db() as conn:
        assert conn.execute('select phase,actual from calls').fetchone() == ('confirmed', 10_000)
    # Identical confirmed work is reused, not charged or sent twice.
    assert judge('privacy', state()) == {'safe_public_copy': .99}
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['stale', 'retention', 'unknown-retention', 'other-route', 'price', 'missing-parameters'])
def test_preflight_failure_precedes_reservation_and_transport(setup, failure):
    judge, metadata, _, calls, journal = setup
    row = metadata['endpoints'][0]
    if failure == 'stale': metadata['checked_at'] = -200
    elif failure == 'retention': row['safety_retention'] = True
    elif failure == 'unknown-retention': row.pop('safety_retention')
    elif failure == 'other-route': row['tag'] = 'anthropic'
    elif failure == 'price': row['pricing']['completion'] = '.000023'
    elif failure == 'missing-parameters': row['supported_parameters'] = []
    with pytest.raises(ValueError, match='stopped'):
        judge('privacy', state())
    assert calls == []
    with journal._db() as conn:
        assert conn.execute('select count(*) from calls').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['model', 'provider', 'truncated', 'tools', 'score', 'cost', 'over-budget', 'duplicate-key'])
def test_unusable_response_preserves_reservation_and_prevents_retry(setup, failure):
    judge, _, response, calls, journal = setup
    choice = response['choices'][0]
    if failure == 'model': response['model'] = 'other/model'
    elif failure == 'provider': response['provider'] = 'Other provider'
    elif failure == 'truncated': choice['finish_reason'] = 'length'
    elif failure == 'tools': choice['message']['tool_calls'] = [{'name': 'do_something'}]
    elif failure == 'score': choice['message']['content'] = '{"safe_public_copy":true}'
    elif failure == 'cost': response['usage'] = {}
    elif failure == 'over-budget': response['usage']['cost'] = 1
    elif failure == 'duplicate-key': choice['message']['content'] = '{"safe_public_copy":0,"safe_public_copy":1}'
    for _ in range(2):
        with pytest.raises(ValueError): judge('privacy', state())
    assert len(calls) == 1
    with journal._db() as conn:
        assert conn.execute('select phase from calls').fetchone()[0] == 'in_flight'
    # Even another process/caller cannot evade an uncertain campaign by changing input.
    with pytest.raises(ValueError): journal.reserve('b' * 64, 1)


def test_transport_error_is_redacted_and_never_refunded(setup):
    judge, _, _, _, journal = setup
    def fail(*args, **kwargs): raise RuntimeError('SYNTHETIC_SECRET')
    judge.transport = fail
    with pytest.raises(ValueError) as error: judge('privacy', state())
    assert 'SYNTHETIC_SECRET' not in str(error.value)
    with pytest.raises(ValueError): journal.reserve('c' * 64, 1)


def test_disabled_judge_has_no_dependency_effects():
    def fail(*args, **kwargs): pytest.fail('Disabled review must have no effects')
    judge = opus_review.Judge(transport=fail, preflight=fail, journal=None)
    with pytest.raises(ValueError): judge('privacy', state())


def test_worst_case_reservations_cannot_be_refunded_or_reset(tmp_path):
    journal = model_journal.Journal(tmp_path / 'campaign')
    journal.initialize(max_calls=2, max_cost_micros=20)
    journal.reserve('a' * 64, 10)
    journal.confirm('a' * 64, 1)
    journal.reserve('b' * 64, 10)
    journal.confirm('b' * 64, 1)
    with pytest.raises(ValueError): journal.reserve('c' * 64, 1)
    with pytest.raises(FileExistsError): journal.initialize(max_calls=200, max_cost_micros=20_000)
    with pytest.raises(ValueError): journal.reserve('a' * 64, 1)


def test_concurrent_reservation_has_one_winner(tmp_path):
    journal = model_journal.Journal(tmp_path / 'campaign')
    journal.initialize(max_calls=2, max_cost_micros=20)
    def reserve(digest):
        try:
            journal.reserve(digest, 10)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, ['a' * 64, 'b' * 64])) == [False, True]


def test_oversized_input_does_not_reserve_or_send(setup):
    judge, _, _, calls, journal = setup
    with pytest.raises(ValueError): judge('privacy', {'copy': 'x' * 24_000})
    assert calls == []
    with journal._db() as conn:
        assert conn.execute('select count(*) from calls').fetchone()[0] == 0


def test_http_transport_has_fixed_routes_and_never_authenticates_public_preflight(setup):
    _, metadata, response, _, _ = setup
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == 'GET':
            assert str(request.url) == opus_review.ZDR_URL
            assert 'authorization' not in request.headers
            return httpx.Response(200, json={'data': metadata['endpoints']})
        assert request.headers['authorization'] == 'Bearer SYNTHETIC_KEY'
        return httpx.Response(200, json=response)
    transport = opus_review.Transport(lambda: 'SYNTHETIC_KEY', transport=httpx.MockTransport(handler), clock=lambda: 100)
    assert transport.preflight() == metadata
    body = opus_review.request('privacy', state())
    assert transport(opus_review.URL, body, timeout=40) == response
    body['provider']['allow_fallbacks'] = True
    with pytest.raises(ValueError): transport(opus_review.URL, body, timeout=40)
    assert len(calls) == 2


@pytest.mark.parametrize('failure', ['redirect', 'error', 'oversized'])
def test_http_transport_never_redirects_retries_or_exposes_provider_errors(failure):
    calls = []
    def handler(request):
        calls.append(True)
        if failure == 'redirect': return httpx.Response(302, headers={'Location': 'https://example.invalid'})
        if failure == 'oversized': return httpx.Response(200, content=b'x' * 65_537)
        return httpx.Response(500, text='SYNTHETIC_PRIVATE_ERROR')
    transport = opus_review.Transport(lambda: 'SYNTHETIC_KEY', transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError) as error:
        transport(opus_review.URL, opus_review.request('privacy', state()), timeout=40)
    assert 'SYNTHETIC_PRIVATE_ERROR' not in str(error.value)
    assert calls == [True]
