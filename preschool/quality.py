"""Synthetic-only quality benchmark. No catalog, Gmail or public-link access.

An injected/offline transport tests the evaluator, not the quality of live models.
The operator must explicitly select --live or the fixed Hub status action to measure
the existing pinned models. No generic inference or credential-export endpoint exists.
Reports contain case names and failures only, never provider text.
"""
import argparse
import json
from dataclasses import dataclass

from .support import luna
from . import criteria, evidence, extract, jev, scanner, school

SENT = '2027-05-01T09:00:00-07:00'


@dataclass(frozen=True)
class Expected:
    kind: str
    terms: tuple
    date: str | None = None
    amount: str | None = None
    uncertain: bool = False


@dataclass(frozen=True)
class Case:
    name: str
    body: str
    expected: tuple = ()
    group: bool = False
    supporting: str = ''
    forbidden: tuple = ()
    review: bool = False


# School names are `{SHORT}`-style tokens filled from the private settings by cases().
_CASES = (
    Case('class-group-without-acronym', 'For our class, please bring rain boots every day for outdoor play.',
         (Expected('update', ('boots',)),), group=True),
    Case('mixed-newsletter', 'Neighborhood street market May 8. {SHORT} families: bring gloves for classroom cleanup.',
         (Expected('action', ('gloves',)),), forbidden=('street market',)),
    Case('stated-fee', '{SHORT} class contribution of $85 is due May 5, 2027.',
         (Expected('action', ('contribution',), '2027-05-05', '$85'),)),
    Case('missing-amount', '{SHORT} tuition is due May 5, 2027. Check the original bill for the amount.',
         (Expected('action', ('tuition',), '2027-05-05'),)),
    Case('relative-date', '{SHORT} parent meeting is tomorrow.',
         (Expected('event', ('meeting',), '2027-05-02'),)),
    Case('attachment-only-fact', 'Please see the {SHORT} meeting attachment.',
         (Expected('event', ('meeting',), '2027-05-05'),),
         supporting='{SHORT} parent meeting on May 5, 2027.'),
    Case('neighborhood-only', 'Neighborhood street market May 8. Local restaurant reviews.'),
    Case('group-only-not-class-content', 'Do you have a favorite restaurant for my birthday dinner?', group=True),
    Case('quoted-history-only', 'Thanks!\nOn Friday, the teacher wrote:\n> {SHORT} parent meeting on May 5, 2027.'),
    Case('changed-date', '{SHORT} parent meeting rescheduled from May 5, 2027 to May 12, 2027.', review=True),
    Case('ambiguous-weekday', '{SHORT} parent meeting is next Friday.',
         (Expected('event', ('meeting',), uncertain=True),)),
)


def cases():
    return school.fill(_CASES)


def __getattr__(name):
    if name == 'CASES':
        return cases()
    raise AttributeError(name)


def compare(case, items):
    """Score concrete required facts, omissions and unwanted additions without an LLM judge."""
    failures = []
    remaining = list(items)
    for expected in case.expected:
        match = next((item for item in remaining if item['kind'] == expected.kind and
                      all(term in (item['title'] + ' ' + item['details']).casefold() for term in expected.terms)), None)
        if match is None:
            failures.append('missing_expected_item')
            continue
        remaining.remove(match)
        if match['date'] != expected.date:
            failures.append('wrong_or_missing_date')
        wanted = evidence.amounts(expected.amount or '')
        if evidence.amounts(match['title'] + ' ' + match['details']) != wanted:
            failures.append('wrong_or_missing_amount')
        if expected.uncertain and not match['uncertainty'].strip():
            failures.append('missing_uncertainty')
    if remaining:
        failures.append('unexpected_extra_item')
    visible = ' '.join(item['title'] + ' ' + item['details'] for item in items).casefold()
    if any(term in visible for term in case.forbidden):
        failures.append('unrelated_content')
    return sorted(set(failures))


def run(*, decide=None, extraction=None):
    """At most 11 judgments and bounded extraction of invented text; no persistence."""
    decide = decide or jev.decide
    extraction = extraction or extract.extract
    results = []
    provider_failed = False
    for case in cases():
        if provider_failed:
            results.append({'case':case.name, 'passed':False, 'failures':['not_run_after_provider_failure']})
            continue
        current = criteria.current_text(case.body)
        if evidence.CHANGED.search(current):
            failures = [] if case.review else ['unexpected_review']
        else:
            supporting = [{'kind':'plain text', 'label':'Synthetic attachment', 'text':case.supporting}] if case.supporting else []
            message = {'subject':'Synthetic class quality check', 'text':current,
                       'recipients':{'to':'class@example.invalid'} if case.group else {}}
            try:
                scores = decide(message, 'class@example.invalid', supporting_sources=supporting)
                if scores['preschool_relevant'] < scanner.FILTER_IRRELEVANT:
                    failures = ['missed_relevant_source'] if case.expected else []
                elif scores['preschool_relevant'] < scanner.AUTO_RELEVANT or scores['useful_information'] < scanner.AUTO_USEFUL:
                    failures = ['unexpected_review'] if not case.review else []
                else:
                    context = {'addressed_to_configured_class_group':case.group}
                    items = extraction(current, SENT, context=context)
                    if case.supporting:
                        items += extraction(case.supporting, SENT, context=context)
                    failures = compare(case, items)
            except (jev.JevError, luna.LunaError):
                failures = ['provider_unavailable']
                provider_failed = True
            except extract.ExtractionError:
                failures = ['validation_failure']
        results.append({'case':case.name, 'passed':not failures, 'failures':failures})
    return {'synthetic_only':True, 'criteria_version':criteria.VERSION,
            'status':'blocked' if provider_failed else 'completed',
            'executed':sum(row['failures'] != ['not_run_after_provider_failure'] for row in results),
            'passed':sum(row['passed'] for row in results), 'total':len(results), 'results':results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Call the existing pinned models with invented text only (billable).')
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({'live_calls':False, 'cases':[case.name for case in cases()],
                          'note':'Use --live explicitly to measure model quality. Offline tests do not establish live accuracy.'}, indent=2))
        return 0
    report = run()
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
