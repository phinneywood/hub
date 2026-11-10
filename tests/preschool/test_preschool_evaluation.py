"""Labelled synthetic benchmark correctness, including deliberately bad fixtures."""
from dataclasses import replace
import json

import pytest

from preschool import evaluation, hybrid, store
from preschool.evaluation_cases import cases, validate_cases


def case(name):
    return next(case for case in cases() if case.name == name)


def test_splits_are_disjoint_by_family_and_tuning_refuses_final_test():
    assert len(validate_cases()) == 30
    assert all(sum(c.split == split for c in cases()) == 10 for split in ('development', 'calibration', 'test'))
    with pytest.raises(ValueError, match='calibration'):
        evaluation.tune([case('future-pickup')])
    with pytest.raises(ValueError, match='cross'):
        validate_cases([case('boots'), replace(case('boots'), name='variant', split='test')])


def test_source_selection_retains_separate_prices_with_no_real_services():
    report = evaluation.run_cases([case('separate-prices')], mode='strong')
    assert report['metrics']['retained_expected'] == 4
    assert report['metrics']['missing_extraction'] == 0
    assert report['metrics']['unsupported_accepted'] == 0
    assert store.message_count() == 0


def test_missing_output_and_extra_wrong_content_are_counted_not_just_valid_json():
    report = evaluation.run_cases([case('empty-output'), case('staff-name')], mode='cheap')
    assert report['metrics']['missing_extraction'] == 1
    assert report['metrics']['missing_after_judging'] == 1
    assert report['metrics']['private_accepted'] == 1
    assert report['metrics']['unexpected_eligible'] == 1


def test_changed_sources_audience_and_numeric_guards_precede_models():
    report = evaluation.run_cases([case(name) for name in
        ('missing-audience', 'cancelled', 'wrong-price', 'wrong-date', 'forwarded-private')])
    assert report['calls'] == 0
    assert report['metrics']['held'] == 5


def test_scope_conflicts_and_privacy_are_separate_from_factual_support():
    report = evaluation.run_cases([case('wrong-venue'), case('another-class-time'), case('named-child')], mode='strong')
    assert report['metrics']['unexpected_eligible'] == 0
    assert all(row['facts'][0]['state'] == 'rejected_in_trial' for row in report['cases'])


def test_calibration_detects_high_confidence_error_and_empty_samples():
    rows = [{'scores': dict.fromkeys(hybrid.AXES, .99), 'truth': dict.fromkeys(hybrid.AXES, False)}]
    result = evaluation.calibration_metrics(rows)
    assert result['privacy_safe']['brier'] > .9 and result['privacy_safe']['ece'] == .99
    assert evaluation.calibration_metrics([])['supported']['brier'] is None
    assert evaluation.wilson_upper(0, 0) is None
    assert evaluation.wilson_upper(0, 10) > .2


def test_hybrid_can_resolve_uncertainty_but_is_not_claimed_to_catch_confident_errors():
    cases = [case('school-paperwork'), case('individual-health')]
    report = evaluation.run_cases(cases)
    assert report['metrics']['retained_expected'] == 1
    assert report['metrics']['escalated'] >= 1
    # At least cheap-only exposes the intentionally overconfident failure.
    assert evaluation.run_cases(cases, mode='cheap')['metrics']['private_accepted'] == 1


def test_report_is_reproducible_text_free_and_explicitly_not_live_accuracy():
    first = evaluation.report()
    assert first == evaluation.report()
    assert first['mocked_judges'] and first['synthetic_only'] and not first['live_calls']
    assert not first['model_accuracy_measured'] and first['human_review_required']
    encoded = json.dumps(first)
    for text in ('Bring boots.', 'Mira', 'Niko', 'Rivera', 'Elin', 'class@example.invalid'):
        assert text not in encoded
    assert len(first['fixture_digest']) == 64


def test_cli_cannot_enable_live_calls(capsys):
    assert evaluation.main([]) == 0
    assert not json.loads(capsys.readouterr().out)['live_calls']
    with pytest.raises(SystemExit):
        evaluation.main(['--live'])
