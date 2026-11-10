"""Pure synthetic stage replay, no model/provider access or eligibility bypass."""
import copy
import json

import pytest

from preschool import family_gate as gate, stage_review


def test_source_uncertainty_can_be_proposed_for_escalation_without_inventing_privacy_scores():
    values = {'preschool_relevant': .87, 'useful_information': .70}
    before = copy.deepcopy(values)
    result = stage_review.advice('source', values)
    assert result['recommendation'] == 'escalation_candidate'
    assert result['state'] == 'held_relevance'
    assert result['hold_reasons'] == ['relevance_below_min', 'source_usefulness_below_min']
    assert result['offline_only'] and result['human_review_required'] and values == before
    assert 'privacy_safe' not in json.dumps(result) and 'supported' not in json.dumps(result)


def test_original_final_privacy_scope_and_usefulness_thresholds_survive_replay():
    values = {'personal_information': .03, 'useful_to_sprouts': .83, 'scope_appropriate': .86}
    assert stage_review.advice('final', values)['recommendation'] == 'escalation_candidate'
    for key, value in (('personal_information', .02), ('useful_to_sprouts', .89), ('scope_appropriate', .89)):
        strong = {'personal_information': .001, 'useful_to_sprouts': .99, 'scope_appropriate': .99, key: value}
        result = stage_review.replay('final', values, strong)
        assert result['state'] == 'held_final_check' and result['hold_reasons']
    assert stage_review.replay('final', values,
        {'personal_information': .001, 'useful_to_sprouts': .99, 'scope_appropriate': .99})['state'] == 'eligible_in_trial'


def test_confident_negative_and_deterministic_holds_cannot_be_overridden():
    negative = {'preschool_relevant': .03, 'useful_information': .02}
    positive = {'preschool_relevant': .99, 'useful_information': .99}
    result = stage_review.replay('source', negative, positive)
    assert result['state'] == 'held_relevance' and not result['stronger_examined']
    assert result['extreme_negative_predicates'] == ['preschool_relevant', 'useful_information']
    assert stage_review.replay('source', None, positive, deterministic_hold=True)['state'] == 'held_deterministic'


def test_personal_information_polarity_is_the_opposite_of_privacy_safety():
    risky = {'personal_information': .98, 'useful_to_sprouts': .99, 'scope_appropriate': .99}
    result = stage_review.advice('final', risky)
    assert result['recommendation'] == 'keep_hold' and result['extreme_negative_predicates'] == ['personal_information']
    assert stage_review.advice('privacy', {'safe_public_copy': .02})['recommendation'] == 'keep_hold'
    assert stage_review.advice('privacy', {'safe_public_copy': .92})['recommendation'] == 'escalation_candidate'
    assert stage_review.advice('privacy', {'safe_public_copy': .99})['recommendation'] == 'no_escalation_needed'


def test_missing_or_unusable_stronger_result_still_holds_without_fallback():
    values = {'safe_public_copy': .92}
    result = stage_review.replay('privacy', values)
    assert result['state'] == 'held_no_stronger_result' and 'stronger_result_unavailable' in result['hold_reasons']
    for stronger in ({}, {'safe_public_copy': True}, {'safe_public_copy': float('nan')}):
        with pytest.raises(gate.GateError):
            stage_review.replay('privacy', values, stronger)
    assert stage_review.replay('privacy', values, {'safe_public_copy': .94})['state'] == 'held_privacy'


def test_audit_can_hold_previously_eligible_copy():
    safe = {'personal_information': .001, 'useful_to_sprouts': .99, 'scope_appropriate': .99}
    assert stage_review.advice('final', safe)['recommendation'] == 'no_escalation_needed'
    risky = {**safe, 'personal_information': .70}
    result = stage_review.replay('final', safe, risky, audit=True)
    assert result['state'] == 'held_final_check' and result['stronger_examined']


def test_shape_uncertainty_must_remain_a_hard_hold_not_be_relabelled_model_uncertainty():
    item = {'kind': 'update', 'title': 'Optional wreath', 'details': '$25.', 'date': None,
            'time': None, 'location': None, 'uncertainty': 'Requires source review.'}
    diagnosis = gate.diagnose_public_copy(item)
    assert diagnosis['hold_reasons'] == ['uncertainty_present']
    values = {'personal_information': .001, 'useful_to_sprouts': .99, 'scope_appropriate': .99}
    replay = stage_review.replay('final', values, values, deterministic_hold=bool(diagnosis['hold_reasons']))
    assert replay['state'] == 'held_deterministic' and not replay['stronger_examined']
    assert item['uncertainty'] == 'Requires source review.'


@pytest.mark.parametrize('stage', ['unknown', None, [], 'SYNTHETIC_PRIVATE_STAGE'])
def test_unknown_stage_is_rejected_without_echoing_input(stage):
    with pytest.raises(ValueError) as caught:
        stage_review.advice(stage, {})
    assert 'SYNTHETIC_PRIVATE_STAGE' not in str(caught.value)


def test_report_is_repeatable_and_only_describes_invented_offline_replays():
    report = stage_review.report()
    assert report == stage_review.report()
    assert len(report['results']) == 7
    assert report['synthetic_only'] and not report['live_calls'] and not report['thresholds_changed']
    assert not report['model_accuracy_measured'] and not report['automatic_routing_enabled']
    assert report['results'][0]['replay']['state'] == 'ready'
    assert report['results'][1]['replay']['state'] == 'eligible_in_trial'
    assert all(result['replay']['human_review_required'] for result in report['results'])


def test_cli_has_no_live_mode(capsys):
    assert stage_review.main([]) == 0
    assert not json.loads(capsys.readouterr().out)['live_calls']
    with pytest.raises(SystemExit):
        stage_review.main(['--live'])
