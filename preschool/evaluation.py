"""Reproducible OFFLINE benchmark. No live flag, keys, catalogs or default inference.

The included model doubles use evaluator labels to exercise the harness. All
scores, costs and routing results are invented, not model-performance evidence.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import math

from . import audience, criteria, evidence, extract, family_gate, hybrid
from .evaluation_cases import AS_OF, SENT, VERSION, cases as fixture_cases, validate_cases

APPROVED = {'addresses': ['class@example.invalid'], 'lists': []}


class FixtureJudge:
    offline = True

    def __init__(self, route, values):
        self.route = route
        self.values = values

    def __call__(self, state):
        return {'model': self.route.model, 'provider': self.route.provider, 'scores': dict(self.values)}


def calibration_metrics(rows):
    """Brier and 10-bin ECE per predicate; rows contain no source/public text."""
    output = {}
    for axis in hybrid.AXES:
        pairs = [(hybrid.scores(row['scores'])[axis], row['truth'][axis]) for row in rows]
        bins = []
        for number in range(10):
            group = [(p, y) for p, y in pairs if min(int(p * 10), 9) == number]
            if group:
                bins.append({'count': len(group), 'mean_probability': sum(p for p, _ in group) / len(group),
                             'positive_rate': sum(y for _, y in group) / len(group)})
        count = len(pairs)
        output[axis] = {'count': count,
                        'brier': sum((p - y) ** 2 for p, y in pairs) / count if count else None,
                        'ece': sum(b['count'] * abs(b['mean_probability'] - b['positive_rate']) for b in bins) / count if count else None,
                        'bins': bins}
    return output


def wilson_upper(errors, count):
    """Approximate 95% binomial upper bound, not a deployment acceptance claim."""
    if not count:
        return None
    z = 1.96
    p = errors / count
    return (p + z*z/(2*count) + z*math.sqrt(p*(1-p)/count + z*z/(4*count*count))) / (1 + z*z/count)


def expected_matches(expected, item):
    title, day, amount = expected
    return (item['title'].casefold() == title.casefold() and item['date'] == day and
            evidence.amounts(item['title'] + ' ' + item['details']) == evidence.amounts(amount or ''))


def run_cases(cases, *, mode='hybrid', policy=None):
    cases = tuple(cases)
    validate_cases(cases)
    policy = policy or hybrid.Policy(enabled=True, escalation=True)
    runner = hybrid.Runner(policy)
    results, calibration, hold_counts = [], [], {}
    totals = dict.fromkeys(('expected_facts', 'extracted_facts', 'retained_expected', 'missing_extraction',
                            'missing_after_judging', 'unexpected_eligible', 'unsupported_accepted',
                            'private_accepted', 'scope_wrong_accepted', 'useless_accepted',
                            'safe_blocked', 'held', 'escalated', 'audited', 'unsafe_privacy_cases'), 0)
    for case in cases:
        totals['expected_facts'] += len(case.expected)
        totals['unsafe_privacy_cases'] += sum(not fact.truth[1] for fact in case.facts)
        message = {'subject': 'Synthetic class notice', 'text': case.body,
                   'recipients': {'to': 'class@example.invalid'} if case.delivery == 'class' else {}}
        if case.delivery == 'forwarded':
            message.update(subject='Fwd: Synthetic notice', recipients={'to': 'class@example.invalid'})
        rows, extracted, accepted = [], [], []
        reason = None
        current = criteria.current_text(case.body)
        source = case.attachment or current
        if audience.classify(message, APPROVED)['scope'] != 'class':
            reason = 'held_audience'
        elif evidence.CHANGED.search(current) or evidence.CHANGED.search(source):
            reason = 'held_changed_source'
        else:
            # The canned response tests extraction contracts, NOT model ability.
            raw = {'items': [fact.proposal() for fact in case.facts]}
            try:
                extracted = extract.extract(source, SENT, lambda request: raw,
                    context={'addressed_to_configured_class_group': True})
            except extract.ExtractionError:
                reason = 'held_extraction'
        for item in extracted:
            # Identify fixtures by full output values, not evaluator labels in state.
            fact = next(fact for fact in case.facts if all(item[key] == fact.proposal()[key]
                        for key in extract.FIELDS if key != 'source_quote'))
            truth = dict(zip(hybrid.AXES, fact.truth))
            cheap_values = dict(zip(hybrid.AXES, fact.cheap_scores))
            strong_values = {axis: .99 if truth[axis] else .01 for axis in hybrid.AXES}
            try:
                public = family_gate.public_copy(item)
            except family_gate.GateError as error:
                decision = {'state': 'held_public_shape', 'escalated': False, 'audited': False,
                            'cheap_scores': None, 'strong_scores': None, **error.diagnostic()}
            else:
                state = {'copy': public, 'evidence': item['source_quote'], 'display_role': fact.role, 'as_of_date': AS_OF}
                decision = runner.judge(state, FixtureJudge(hybrid.CHEAP, cheap_values),
                                        FixtureJudge(hybrid.STRONG, strong_values), mode=mode)
            eligible = decision['state'] == 'eligible_in_trial'
            if eligible:
                accepted.append(item)
            else:
                totals['held'] += 1
                totals['safe_blocked'] += int(all(fact.truth))
            for axis, metric in zip(hybrid.AXES, ('unsupported_accepted', 'private_accepted', 'scope_wrong_accepted', 'useless_accepted')):
                totals[metric] += int(eligible and not truth[axis])
            totals['escalated'] += int(decision['escalated'])
            totals['audited'] += int(decision['audited'])
            if decision['cheap_scores'] is not None:
                calibration.append({'scores': decision['cheap_scores'], 'truth': truth})
            rows.append({'state': decision['state'], 'truth': truth, 'scores': decision['cheap_scores'],
                         'strong_scores': decision['strong_scores'], 'escalated': decision['escalated'],
                         'audited': decision['audited'], 'hold_reasons': decision.get('hold_reasons', [])})
            for hold in decision.get('hold_reasons', []):
                hold_counts[hold] = hold_counts.get(hold, 0) + 1
        totals['extracted_facts'] += len(extracted)
        remaining = list(accepted)
        for expected in case.expected:
            if not any(expected_matches(expected, item) for item in extracted):
                totals['missing_extraction'] += 1
            found = next((item for item in remaining if expected_matches(expected, item)), None)
            if found is None:
                totals['missing_after_judging'] += 1
            else:
                remaining.remove(found)
                totals['retained_expected'] += 1
        totals['unexpected_eligible'] += len(remaining)
        if reason:
            totals['held'] += 1
            hold_counts[reason] = hold_counts.get(reason, 0) + 1
        results.append({'case': case.name, 'split': case.split, 'state': reason or 'evaluated', 'facts': rows})
    return {'mode': mode, 'metrics': totals, 'cases': results, 'hold_reason_counts': dict(sorted(hold_counts.items())),
            'calibration': calibration_metrics(calibration),
            'privacy_error_upper_95': wilson_upper(totals['private_accepted'], totals['unsafe_privacy_cases']),
            'calls': runner.calls, 'estimated_reserved_cost_micros': runner.reserved_micros,
            'run_stopped': runner.stopped}


def tune(cases):
    """Only calibration families may select thresholds. Never tune on final tests."""
    cases = tuple(cases)
    if not cases or any(case.split != 'calibration' for case in cases):
        raise ValueError('Threshold selection requires calibration cases only.')
    candidates = []
    for accept in (.90, .95, .99):
        for reject in (.01, .05, .10):
            policy = hybrid.Policy(enabled=True, escalation=True, accept=accept, reject=reject)
            report = run_cases(cases, policy=policy)
            m = report['metrics']
            rank = tuple(m[key] for key in ('private_accepted', 'unsupported_accepted', 'scope_wrong_accepted',
                                           'unexpected_eligible', 'missing_after_judging')) + (report['estimated_reserved_cost_micros'],)
            candidates.append((rank, policy))
    return min(candidates, key=lambda value: value[0])[1]


def report():
    fixtures = validate_cases(fixture_cases())
    policy = tune(case for case in fixtures if case.split == 'calibration')
    return {'schema': 1, 'suite': VERSION, 'fixture_digest': hashlib.sha256(
                json.dumps([asdict(case) for case in fixtures], sort_keys=True).encode()).hexdigest(),
            'synthetic_only': True, 'mocked_judges': True, 'live_calls': False,
            'model_accuracy_measured': False, 'costs_are_estimates': True,
            'human_review_required': True, 'selected_offline_policy': asdict(policy),
            'note': 'Label-driven test doubles exercise the harness; results and thresholds are NOT live model evidence.',
            'splits': {split: {mode: run_cases((case for case in fixtures if case.split == split),
                        mode=mode, policy=policy) for mode in ('cheap', 'strong', 'hybrid')}
                       for split in ('development', 'calibration', 'test')}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', action='store_true', help='Omit per-case fixture results.')
    args = parser.parse_args(argv)
    result = report()
    if args.summary:
        for modes in result['splits'].values():
            for value in modes.values():
                value.pop('cases')
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
