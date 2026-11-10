"""Pure OFFLINE escalation advice for existing gate predicates, never live routing.

Do not map source relevance to privacy/support or invert a score without its
question's semantics. Original gate thresholds are always used for eligibility.
Extreme-score cutoffs below are illustrative routing rules, not calibration or
proof that a model knows when it is wrong. No copy, quote, key or transport enters
these functions, and a replay result grants no export/publication authority.
"""
import argparse
import json

from . import family_gate as gate, school

CLASSIFIERS = {'source': gate.screen_result, 'final': gate.final_result, 'privacy': gate.privacy_result}


def classify(stage, values):
    if not isinstance(stage, str) or stage not in CLASSIFIERS:
        raise ValueError('Unknown offline review stage.')
    return CLASSIFIERS[stage](values)


def advice(stage, values, *, deterministic_hold=False, audit=False):
    if not isinstance(stage, str) or stage not in CLASSIFIERS or type(audit) is not bool:
        raise ValueError('Invalid offline review options.')
    result = {'stage': stage, 'offline_only': True, 'human_review_required': True,
              'recommendation': 'keep_hold', 'hold_reasons': [], 'extreme_negative_predicates': []}
    if deterministic_hold is not False:
        return {**result, 'state': 'held_deterministic', 'hold_reasons': ['deterministic_hold']}
    assessed = classify(stage, values)
    negative = [name for name, value in assessed['scores'].items()
                if (value >= .95 if name == 'personal_information' else value <= .05)]
    ready = not assessed['hold_reasons']
    recommendation = ('keep_hold' if negative else 'escalation_candidate' if audit or not ready else 'no_escalation_needed')
    return {**result, 'state': assessed['state'], 'hold_reasons': assessed['hold_reasons'],
            'recommendation': recommendation, 'extreme_negative_predicates': negative}


def replay(stage, values, stronger_values=None, *, deterministic_hold=False, audit=False):
    """Compare supplied test-double judgments. This function never calls a model.

    A structural hold or extreme negative cannot be overruled. A stronger result
    must pass every original stage threshold; uncertainty is never erased. A
    conflicting audit holds rather than treating the earlier pass as authority.
    """
    result = advice(stage, values, deterministic_hold=deterministic_hold, audit=audit)
    if result['recommendation'] != 'escalation_candidate':
        return {**result, 'stronger_examined': False}
    if stronger_values is None:
        return {**result, 'stronger_examined': False, 'state': 'held_no_stronger_result',
                'hold_reasons': [*result['hold_reasons'], 'stronger_result_unavailable']}
    checked = classify(stage, stronger_values)
    return {**result, 'state': checked['state'], 'hold_reasons': checked['hold_reasons'],
            'stronger_examined': True}


# Invented numeric responses, NOT copied private live diagnostics. These isolate
# decisions useful reminders/optional school context can encounter without
# changing class-audience, privacy, uncertainty or human-review requirements.
# The class score key is filled from the private school settings (see scenarios()).
_SCENARIOS = (
    ('short-routine-reminder', 'source',
     {'preschool_relevant': .87, 'useful_information': .70},
     {'preschool_relevant': .99, 'useful_information': .99}, False, False),
    ('optional-school-price', 'final',
     {'personal_information': .03, 'useful_to_{CLASS_KEY}': .83, 'scope_appropriate': .86},
     {'personal_information': .001, 'useful_to_{CLASS_KEY}': .99, 'scope_appropriate': .99}, False, False),
    ('other-class-extreme-negative', 'source',
     {'preschool_relevant': .03, 'useful_information': .02},
     {'preschool_relevant': .99, 'useful_information': .99}, False, False),
    ('person-risk-extreme-negative', 'final',
     {'personal_information': .98, 'useful_to_{CLASS_KEY}': .99, 'scope_appropriate': .99},
     {'personal_information': .001, 'useful_to_{CLASS_KEY}': .99, 'scope_appropriate': .99}, False, False),
    ('uncertainty-metadata-hard-hold', 'final',
     {'personal_information': .001, 'useful_to_{CLASS_KEY}': .99, 'scope_appropriate': .99},
     {'personal_information': .001, 'useful_to_{CLASS_KEY}': .99, 'scope_appropriate': .99}, True, False),
    ('privacy-still-uncertain', 'privacy', {'safe_public_copy': .92}, {'safe_public_copy': .94}, False, False),
    ('audit-finds-scope-concern', 'final',
     {'personal_information': .001, 'useful_to_{CLASS_KEY}': .99, 'scope_appropriate': .99},
     {'personal_information': .001, 'useful_to_{CLASS_KEY}': .99, 'scope_appropriate': .30}, False, True),
)


def scenarios():
    return school.fill(_SCENARIOS)


def __getattr__(name):
    if name == 'SCENARIOS':
        return scenarios()
    raise AttributeError(name)


def report():
    rows = []
    for name, stage, values, stronger, held, audit in scenarios():
        before = advice(stage, values, deterministic_hold=held, audit=audit)
        after = replay(stage, values, stronger, deterministic_hold=held, audit=audit)
        rows.append({'case': name, 'initial': before, 'replay': after})
    return {'schema': 1, 'synthetic_only': True, 'live_calls': False,
            'model_accuracy_measured': False, 'thresholds_changed': False,
            'automatic_routing_enabled': False,
            'note': 'Invented stronger judgments test routing logic, not recoverability or model safety.',
            'results': rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(json.dumps(report(), sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
