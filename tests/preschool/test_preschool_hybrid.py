"""Offline-only routes and injected clocks; never call real model services."""
import copy

import pytest

from preschool import evaluation, hybrid


def state():
    return {'copy': {'kind': 'update', 'title': 'Class supplies', 'details': 'Bring boots.',
                     'date': None, 'time': None, 'location': None},
            'evidence': 'Bring boots.', 'display_role': 'sprouts_instruction', 'as_of_date': '2027-05-01'}


def values(**changes):
    return {**dict.fromkeys(hybrid.AXES, .99), **changes}


def judge(route=hybrid.CHEAP, **changes):
    return evaluation.FixtureJudge(route, values(**changes))


def policy(**changes):
    return hybrid.Policy(**{'enabled': True, 'escalation': True, 'audit_every': 0, **changes})


def test_disabled_and_deterministic_holds_never_call_judges():
    def denied(*a):
        pytest.fail('A disabled or held case cannot invoke a judge')
    runner = hybrid.Runner()
    assert runner.judge(state(), denied, denied)['state'] == 'held_disabled'
    runner = hybrid.Runner(policy())
    assert runner.judge(state(), denied, denied, deterministic_hold=True)['state'] == 'held_deterministic'
    assert runner.calls == 0


@pytest.mark.parametrize('changes', [
    {'accept': .5}, {'reject': .5}, {'accept': True}, {'accept': float('nan')},
    {'max_calls': True}, {'max_cost_micros': -1}, {'max_seconds': 0}, {'max_seconds': float('inf')},
    {'enabled': 1}, {'escalation': 'yes'}, {'audit_every': -1},
])
def test_invalid_policy_is_rejected(changes):
    with pytest.raises(ValueError):
        policy(**changes)


def test_only_offline_routes_are_constructible():
    with pytest.raises(ValueError):
        hybrid.Route('anthropic/live-model', 'some-provider', 1)


def test_cheap_confident_case_does_not_escalate():
    runner = hybrid.Runner(policy())
    result = runner.judge(state(), judge())
    assert result['state'] == 'eligible_in_trial' and result['human_review_required']
    assert runner.calls == 1 and not result['escalated']
    assert runner.reserved_micros == hybrid.CHEAP.max_cost_micros


def test_uncertainty_uses_strong_judge_without_mutating_input():
    runner = hybrid.Runner(policy())
    data = state()
    before = copy.deepcopy(data)
    result = runner.judge(data, judge(supported=.6), judge(hybrid.STRONG))
    assert data == before
    assert result['state'] == 'eligible_in_trial' and result['escalated']
    assert runner.calls == 2


def test_no_escalation_without_opt_in_and_no_error_fallback():
    runner = hybrid.Runner(policy(escalation=False))
    assert runner.judge(state(), judge(supported=.6), judge(hybrid.STRONG))['state'] == 'held_escalation_disabled'
    assert runner.calls == 1
    assert runner.judge(state(), strong=judge(hybrid.STRONG), mode='strong')['state'] == 'held_escalation_disabled'


def test_high_confidence_audit_holds_disagreement_in_any_predicate():
    runner = hybrid.Runner(policy(audit_every=1))
    result = runner.judge(state(), judge(), judge(hybrid.STRONG, privacy_safe=.01))
    assert result['state'] == 'held_disagreement' and result['audited']
    assert runner.calls == 2


def test_strong_uncertainty_and_confident_rejection_remain_held_or_rejected():
    runner = hybrid.Runner(policy())
    assert runner.judge(state(), judge(supported=.6), judge(hybrid.STRONG, supported=.6))['state'] == 'held_uncertain'
    assert runner.judge(state(), judge(privacy_safe=.01))['state'] == 'rejected_in_trial'


@pytest.mark.parametrize('cap', [{'max_calls': 1}, {'max_cost_micros': 10}])
def test_budget_is_reserved_before_escalation_and_stops_run(cap):
    runner = hybrid.Runner(policy(**cap))
    result = runner.judge(state(), judge(supported=.6), judge(hybrid.STRONG))
    assert result['state'] == 'held_budget' and runner.stopped
    assert runner.calls == 1 and runner.reserved_micros == 10
    assert runner.judge(state(), judge())['state'] == 'held_run_stopped'


@pytest.mark.parametrize('bad', ['exception', 'provider', 'model', 'missing', 'nan', 'boolean', 'range', 'extra'])
def test_unusable_response_stops_without_fallback_or_raw_error_disclosure(bad):
    class Broken:
        offline = True
        route = hybrid.CHEAP
        def __call__(self, data):
            if bad == 'exception':
                raise RuntimeError('SYNTHETIC_PRIVATE_PROVIDER_DETAIL')
            result = judge()(data)
            if bad in ('provider', 'model'):
                result[bad] = 'SYNTHETIC_PRIVATE_PROVIDER_DETAIL'
            elif bad == 'missing':
                result['scores'].pop('supported')
            elif bad == 'extra':
                result['extra'] = 'SYNTHETIC_PRIVATE_PROVIDER_DETAIL'
            else:
                result['scores']['supported'] = {'nan': float('nan'), 'boolean': True, 'range': 2}[bad]
            return result
    runner = hybrid.Runner(policy())
    result = runner.judge(state(), Broken(), judge(hybrid.STRONG))
    assert result['state'] == 'held_judge_failure' and runner.stopped
    assert runner.calls == 1 and runner.reserved_micros == 10
    assert 'SYNTHETIC_PRIVATE' not in str(result)


def test_missing_or_live_adapter_is_never_called():
    runner = hybrid.Runner(policy())
    live = judge()
    live.offline = False
    assert runner.judge(state(), live)['state'] == 'held_route'
    assert runner.calls == 0


def test_elapsed_limit_rejects_late_result_without_replay():
    now = [0]
    class Slow:
        offline = True
        route = hybrid.CHEAP
        def __call__(self, data):
            now[0] = 100
            return judge()(data)
    runner = hybrid.Runner(policy(max_seconds=1), clock=lambda: now[0])
    assert runner.judge(state(), Slow())['state'] == 'held_time_limit'
    assert runner.calls == 1 and runner.stopped
    assert runner.judge(state(), judge())['state'] == 'held_run_stopped'


@pytest.mark.parametrize('change', ['raw_source', 'private_field', 'oversize', 'bad_date', 'uncertainty'])
def test_input_minimization_and_bounds_precede_calls(change):
    data = state()
    if change == 'raw_source':
        data['sender'] = 'private@example.invalid'
    elif change == 'private_field':
        data['copy']['source_quote'] = 'private evidence'
    elif change == 'oversize':
        data['evidence'] = 'x' * 501
    elif change == 'bad_date':
        data['as_of_date'] = 'May 2027'
    else:
        data['copy']['uncertainty'] = 'Unknown'
    runner = hybrid.Runner(policy())
    assert runner.judge(data, judge())['state'] == 'held_input'
    assert runner.calls == 0


def test_minimized_state_contains_no_labels_and_calls_receive_independent_copies():
    seen = []
    class Mutator:
        offline = True
        route = hybrid.CHEAP
        def __call__(self, data):
            seen.append(copy.deepcopy(data))
            data['copy']['title'] = 'MUTATED'
            return judge(supported=.6)(data)
    class Strong:
        offline = True
        route = hybrid.STRONG
        def __call__(self, data):
            seen.append(data)
            return judge(hybrid.STRONG)(data)
    runner = hybrid.Runner(policy())
    runner.judge(state(), Mutator(), Strong())
    assert seen == [state(), state()]
