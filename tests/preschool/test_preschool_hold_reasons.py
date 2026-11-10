"""Invented text and mocked scores only; hold diagnostics cannot release content."""
import copy
import json
from itertools import product

import pytest

from preschool import evaluation, evaluation_cases, family_gate as gate, hybrid
from preschool.support import luna

ITEM = {'kind': 'update', 'title': 'Optional school wreath', 'details': '$25.',
        'date': None, 'time': None, 'location': None, 'url': None, 'uncertainty': ''}


@pytest.mark.parametrize('change,reason', [
    ({'uncertainty': 'Synthetic unresolved context.'}, 'uncertainty_present'),
    ({'kind': 'unknown'}, 'invalid_public_kind'),
    ({'details': {'private': 'SYNTHETIC_PRIVATE_DETAIL'}}, 'invalid_public_field'),
    ({'details': 'x' * 1201}, 'public_text_too_long'),
    ({'details': 'Contact synthetic-parent@example.invalid'}, 'private_text'),
    ({'details': 'Bad\x00control'}, 'private_text'),
    ({'title': ''}, 'empty_public_title'),
    ({'date': '20270505'}, 'invalid_public_date'),
    ({'details': 'Student ID: 901234567'}, 'personal_identifier'),
    ({'url': 'https://outside.invalid/private?token=synthetic'}, 'non_public_resource'),
])
def test_public_shape_holds_report_fixed_reasons_without_source_values(change, reason):
    item = {**ITEM, **change}
    original = copy.deepcopy(item)
    diagnosis = gate.diagnose_public_copy(item)
    assert item == original
    assert diagnosis == {'state': 'held_public_shape', 'hold_reasons': [reason]}
    encoded = json.dumps(diagnosis)
    for value in ('SYNTHETIC_PRIVATE_DETAIL', 'synthetic-parent', '901234567', 'token=synthetic'):
        assert value not in encoded
    with pytest.raises(gate.GateError) as caught:
        gate.public_copy(item)
    assert caught.value.code == reason and caught.value.diagnostic() == {'hold_reasons': [reason]}


def test_missing_fields_and_oversized_copy_are_diagnosable(monkeypatch):
    assert gate.diagnose_public_copy(None)['hold_reasons'] == ['invalid_public_fields']
    assert gate.diagnose_public_copy({'title': 'Synthetic'})['hold_reasons'] == ['invalid_public_fields']
    monkeypatch.setattr(gate, 'MAX_COPY', 1)
    assert gate.diagnose_public_copy(ITEM)['hold_reasons'] == ['copy_too_large']


def test_error_diagnostic_does_not_serialize_even_injected_private_message():
    error = gate.GateError('SYNTHETIC_PRIVATE_ERROR', code='personal_identifier')
    assert error.diagnostic() == {'hold_reasons': ['personal_identifier']}
    assert gate.GateError('SYNTHETIC_PRIVATE_ERROR', code='SYNTHETIC_PRIVATE_CODE').diagnostic() == {'hold_reasons': ['unclassified_hold']}
    assert gate.GateError('SYNTHETIC_PRIVATE_ERROR', code={}).code == 'unclassified_hold'


def test_optional_prices_and_missing_dates_times_are_not_structural_privacy_holds():
    for price in ('$25.', '$40.', '$15.'):
        item = {**ITEM, 'details': price}
        assert gate.diagnose_public_copy(item) == {'state': 'public_shape_ready', 'hold_reasons': []}
        assert gate.public_copy(item)['date'] is None
    assert gate.diagnose_public_copy({**ITEM, 'title': 'Bring boots', 'details': 'Bring boots.'})['state'] == 'public_shape_ready'
    # An absent optional field differs from an explicit uncertainty statement.
    assert gate.diagnose_public_copy({**ITEM, 'uncertainty': 'Date unknown.'})['hold_reasons'] == ['uncertainty_present']


def test_diagnostics_cannot_strip_uncertainty_or_mutate_private_records():
    item = {**ITEM, 'uncertainty': 'Needs review.', 'source_quote': 'PRIVATE_QUOTE', 'sender': 'PRIVATE_SENDER'}
    before = copy.deepcopy(item)
    assert gate.diagnose_public_copy(item)['hold_reasons'] == ['uncertainty_present']
    assert item == before


def test_each_screen_threshold_failure_is_reported_and_exact_thresholds_still_pass():
    result = gate.screen_result({'preschool_relevant': .87, 'useful_information': .70})
    assert result['state'] == 'held_relevance'
    assert result['hold_reasons'] == ['relevance_below_min', 'source_usefulness_below_min']
    assert gate.screen_result({'preschool_relevant': .90, 'useful_information': .75})['hold_reasons'] == []


def test_all_final_threshold_failures_reported_without_relaxing_any_threshold():
    values = {'personal_information': .03, 'useful_to_sprouts': .83, 'scope_appropriate': .86}
    result = gate.final_result(values)
    assert result['state'] == 'held_final_check'
    assert result['hold_reasons'] == ['personal_information_above_max', 'copy_usefulness_below_min', 'scope_below_min']
    exact = {'personal_information': .01, 'useful_to_sprouts': .90, 'scope_appropriate': .90}
    assert gate.final_result(exact)['state'] == 'eligible_in_trial'
    assert gate.privacy_result({'safe_public_copy': .949})['hold_reasons'] == ['privacy_safety_below_min']
    assert gate.privacy_result({'safe_public_copy': .95})['hold_reasons'] == []


def test_diagnostic_refactor_preserves_legacy_eligibility_over_boundary_grid():
    grid = (0, .01, .010001, .05, .749999, .75, .899999, .90, .949999, .95, 1)
    for relevance, useful in product(grid, repeat=2):
        actual = gate.screen_result({'preschool_relevant': relevance, 'useful_information': useful})
        assert (actual['state'] == 'ready') == (relevance >= .90 and useful >= .75)
    for personal, useful, scope in product(grid, repeat=3):
        actual = gate.final_result({'personal_information': personal, 'useful_to_sprouts': useful, 'scope_appropriate': scope})
        assert (actual['state'] == 'eligible_in_trial') == (personal <= .01 and useful >= .90 and scope >= .90)
    for safe in grid:
        assert (gate.privacy_result({'safe_public_copy': safe})['state'] == 'eligible_in_trial') == (safe >= .95)


@pytest.mark.parametrize('values', [None, {}, {'safe_public_copy': True}, {'safe_public_copy': float('nan')},
                                  {'safe_public_copy': 1.01}, {'safe_public_copy': .99, 'private': 'RAW'}])
def test_invalid_numeric_inputs_cannot_be_replayed_as_valid_decisions(values):
    with pytest.raises(gate.GateError) as caught:
        gate.privacy_result(values)
    assert caught.value.code == 'decision_scores_invalid'


@pytest.mark.parametrize('mutation,reason', [
    (lambda data: data.update(provider='SYNTHETIC_PRIVATE_PROVIDER'), 'decision_provider_mismatch'),
    (lambda data: data.update(model='SYNTHETIC_PRIVATE_MODEL'), 'decision_model_mismatch'),
    (lambda data: data.update(answers={}), 'decision_answers_invalid'),
    (lambda data: data['answers']['safe_public_copy'].update(noul=True), 'decision_scores_invalid'),
])
def test_response_holds_distinguish_route_schema_and_scores(mutation, reason):
    data = {'provider': 'TypeSafe', 'model': 'typesafe/jev-1.14',
            'answers': {'safe_public_copy': {'type': 'noul', 'noul': .99}}}
    mutation(data)
    with pytest.raises(gate.GateError) as caught:
        gate._validate(data, gate.PRIVACY_QUESTIONS)
    assert caught.value.diagnostic() == {'hold_reasons': [reason]}
    assert 'SYNTHETIC_PRIVATE' not in json.dumps(caught.value.diagnostic())


def test_audience_and_shape_holds_stop_before_key_or_provider_access(monkeypatch):
    monkeypatch.setattr(luna, 'key', lambda: pytest.fail('A deterministic hold cannot request credentials'))
    approved = {'addresses': ['class@example.invalid'], 'lists': []}
    message = {'text': 'Bring boots.', 'recipients': {'to': 'private@example.invalid'}}
    for screen in (gate.screen, gate.context_screen):
        result = screen(message, approved)
        assert result['state'] == 'held_audience' and result['hold_reasons'] == ['audience_not_class']
    with pytest.raises(gate.GateError) as caught:
        gate.final_check({**ITEM, 'source_quote': 'PRIVATE_SOURCE'}, display_role='school_context')
    assert caught.value.code == 'private_source_fields'


def test_benchmark_carries_public_shape_reason_without_logging_copy():
    case = next(case for case in evaluation_cases.cases() if case.name == 'ambiguous-weekday')
    report = evaluation.run_cases([case])
    row = report['cases'][0]['facts'][0]
    assert row['state'] == 'held_public_shape' and row['hold_reasons'] == ['uncertainty_present']
    assert report['hold_reason_counts'] == {'uncertainty_present': 1}
    assert report['calls'] == 0
    assert 'Date requires review.' not in json.dumps(report)


def test_hybrid_reports_precise_gate_and_predicate_holds():
    runner = hybrid.Runner(hybrid.Policy(enabled=True, escalation=False, audit_every=0))
    data = {'copy': {k: v for k, v in ITEM.items() if k not in ('url', 'uncertainty')},
            'evidence': 'Optional wreath $25.', 'display_role': 'school_context', 'as_of_date': '2027-05-01'}
    data['copy']['uncertainty'] = 'Needs source review.'
    assert runner.judge(data)['hold_reasons'] == ['uncertainty_present']
    data['copy'].pop('uncertainty')
    judge = evaluation.FixtureJudge(hybrid.CHEAP, {name: .60 for name in hybrid.AXES})
    result = runner.judge(data, judge)
    assert result['state'] == 'held_escalation_disabled'
    assert result['hold_reasons'] == ['escalation_disabled'] + [axis + '_uncertain' for axis in hybrid.AXES]
