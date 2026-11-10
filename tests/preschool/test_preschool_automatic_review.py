"""Complete invented batches, injected scores, scratch persistence only."""
import copy
from dataclasses import replace
from importlib.resources import files
import json

import pytest

from preschool import automatic_review as automatic, extract, publication, store


@pytest.fixture
def batch():
    with store.account('shared'):
        store.set_setting('family_group_addresses', json.dumps(['class@example.invalid']))
    message = {'id': 'synthetic-message', 'subject': 'Class reminder',
               'text': 'Sprouts families: bring boots.',
               'date': '2027-05-01T09:00:00-07:00',
               'recipients': {'to': 'class@example.invalid'}}
    item = {'kind': 'update', 'title': 'Bring boots', 'details': 'Bring boots.',
            'date': None, 'time': None, 'location': None, 'url': None,
            'uncertainty': '', 'source_quote': message['text']}
    return [(message, {'items': [item]})]


def judge(stage, state):
    return {key: .001 if key == 'personal_information' else .99 for key in automatic.QUESTIONS[stage]}


def assess(batch, **options):
    return automatic.assess(batch, {'addresses': ['class@example.invalid'], 'lists': []},
                            judge=options.pop('judge', judge), as_of_date='2027-05-01', enabled=True, **options)


def test_eligible_batch_freezes_without_a_human_checkbox(batch):
    result = assess(batch)
    assert result.diagnostics == []
    assert automatic.save_preview(result, 0, enabled=True) == 1
    assert publication.latest()['payload']['pages'][0]['body'] == 'Bring boots\nBring boots.'
    assert automatic.save_preview(result, 1, enabled=True) == 1
    changed = result.proposal
    changed['pages'][0]['body'] = 'Not reviewed'
    assert result.proposal['pages'][0]['body'] != 'Not reviewed'


def test_disabled_path_never_calls_judges_or_saves(batch):
    with pytest.raises(ValueError, match='not enabled'):
        automatic.assess(batch, {}, judge=lambda *args: pytest.fail('No call'), as_of_date='2027-05-01')
    result = assess(batch)
    with pytest.raises(ValueError):
        automatic.save_preview(result, 0)
    assert publication.latest() is None


@pytest.mark.parametrize('failure', ['audience', 'uncertainty', 'quote', 'privacy', 'support', 'scope', 'service'])
def test_holds_cannot_freeze_partial_editions_or_leak_source(batch, failure):
    message, raw = batch[0]
    if failure == 'audience':
        message['recipients'] = {'to': 'private@example.invalid'}
    elif failure == 'uncertainty':
        raw['items'][0]['uncertainty'] = 'Date unclear'
    elif failure == 'quote':
        raw['items'][0]['source_quote'] = 'Invented evidence'
    def failing(stage, state):
        if failure == 'service':
            raise RuntimeError('SYNTHETIC_PRIVATE_DIAGNOSTIC')
        values = judge(stage, state)
        if failure == 'privacy' and stage == 'privacy': values['safe_public_copy'] = .94
        if failure == 'support' and stage == 'support': values['supported'] = .94
        if failure == 'scope' and stage == 'final': values['scope_appropriate'] = .89
        return values
    result = assess(batch, judge=failing)
    assert result.diagnostics and result.proposal['holds']
    assert 'SYNTHETIC_PRIVATE_DIAGNOSTIC' not in result.diagnostics_json
    with pytest.raises(ValueError):
        automatic.save_preview(result, 0, enabled=True)
    assert publication.latest() is None


def test_optional_escalation_retains_original_thresholds(batch):
    calls = []
    def uncertain(stage, state):
        values = judge(stage, state)
        if stage == 'final': values['useful_to_sprouts'] = .85
        return values
    def stronger(stage, state):
        calls.append(stage)
        return judge(stage, state)
    assert assess(batch, judge=uncertain, stronger=stronger).diagnostics == []
    assert calls == ['final']
    assert assess(batch, judge=uncertain).diagnostics


def test_extreme_negative_privacy_is_not_overridden(batch):
    def negative(stage, state):
        values = judge(stage, state)
        if stage == 'privacy': values['safe_public_copy'] = .01
        return values
    assert assess(batch, judge=negative, stronger=lambda *args: pytest.fail('Do not override')).diagnostics


def test_audience_check_precedes_all_inference(batch):
    batch[0][0]['recipients'] = {}
    assert assess(batch, judge=lambda *args: pytest.fail('No inference')).diagnostics


def test_forged_or_serialized_assessment_cannot_be_used_as_review(batch):
    result = assess(batch)
    forged = automatic.Assessment(result.proposal_json, '[]', object())
    altered = result.proposal
    altered['pages'][0]['body'] = 'Unreviewed text'
    for value in (forged, result.proposal, None, replace(result, proposal_json=json.dumps(altered))):
        with pytest.raises(ValueError):
            automatic.save_preview(value, 0, enabled=True)


def test_changed_audience_binding_invalidates_completed_assessment(batch):
    result = assess(batch)
    with store.account('shared'):
        store.set_setting('family_group_addresses', json.dumps(['other@example.invalid']))
    with pytest.raises(ValueError):
        automatic.save_preview(result, 0, enabled=True)
    assert publication.latest() is None


def test_unconfigured_audience_cannot_authorize_even_the_first_model_call(batch):
    with pytest.raises(ValueError, match='configured class audience'):
        automatic.assess(batch, {'addresses': ['other@example.invalid'], 'lists': []},
            judge=lambda *args: pytest.fail('Unconfigured audience must not send content'),
            as_of_date='2027-05-01', enabled=True)


def test_service_failure_stops_the_batch_without_trying_later_sources(batch):
    calls = []
    def unavailable(stage, state):
        calls.append(stage)
        raise RuntimeError('SYNTHETIC_PRIVATE_ERROR')
    second = copy.deepcopy(batch[0])
    second[0]['id'] = 'second-synthetic-message'
    result = assess(batch + [second], judge=unavailable)
    assert result.diagnostics and calls == ['source']


def test_judge_mutation_cannot_change_frozen_copy(batch):
    original = copy.deepcopy(batch)
    def mutating(stage, state):
        if 'public_copy' in state: state['public_copy']['title'] = 'Unreviewed edit'
        batch[0][1]['items'][0]['title'] = 'Caller mutation'
        return judge(stage, state)
    result = assess(batch, judge=mutating)
    assert original[0][1]['items'][0]['title'] in result.proposal['pages'][0]['body']
    assert 'Unreviewed edit' not in result.proposal_json
    assert 'Caller mutation' not in result.proposal_json


def test_source_to_automatic_snapshot_and_exact_calendar_bundle(batch):
    message = batch[0][0]
    message['text'] = 'Sprouts class outing May 7, 2027 at 10 AM. Meet at the park.'
    selected = {'kind': 'event', 'title': 'Class outing', 'details': 'Meet at the park.',
                'date': '2027-05-07', 'time': '10 AM', 'location': 'park', 'url': None,
                'uncertainty': '', 'paragraph_id': 1}
    resolved = extract.resolve_paragraphs({'items': [selected]}, extract.paragraphs(message['text']))
    result = assess([(message, resolved)])
    assert not result.diagnostics
    revision = automatic.save_preview(result, 0, enabled=True)
    snapshot = publication.latest()['payload']
    assert len(snapshot['events']) == 1
    assert snapshot['events'][0]['start_time'] == '10:00'
    assert b'DTSTART:20270507T170000Z' in publication.calendar_feed(snapshot)
    assert 'source_quote' not in publication.serialized(snapshot)
    assert 'example.invalid' not in publication.serialized(snapshot)
    assert 'Class outing' in publication.family_html(snapshot)
    # Repeating identical reviewed content is a no-op, not a new calendar identity.
    assert automatic.save_preview(result, revision, enabled=True) == revision


def test_a_held_new_item_never_withdraws_the_existing_edition(batch):
    accepted = assess(batch)
    automatic.save_preview(accepted, 0, enabled=True)
    before = publication.latest()
    batch[0][1]['items'][0]['uncertainty'] = 'Unresolved source change'
    held = assess(batch)
    with pytest.raises(ValueError):
        automatic.save_preview(held, 1, enabled=True)
    assert publication.latest() == before


def test_empty_extraction_cannot_silently_replace_the_edition(batch):
    automatic.save_preview(assess(batch), 0, enabled=True)
    before = publication.latest()
    batch[0][1]['items'] = []
    result = assess(batch)
    assert result.diagnostics
    with pytest.raises(ValueError):
        automatic.save_preview(result, 1, enabled=True)
    assert publication.latest() == before


def test_stable_catalog_keys_survive_dismissal_without_reassigning_event_uid(batch):
    message = batch[0][0]
    passages = ['Class outing May 7, 2027.', 'Class picnic May 14, 2027.']
    message['text'] = '\n\n'.join(passages)
    items = [{'kind': 'event', 'title': title, 'details': '', 'date': day,
              'time': None, 'location': None, 'url': None, 'uncertainty': '', 'source_quote': quote}
             for title, day, quote in (('Class outing', '2027-05-07', passages[0]),
                                       ('Class picnic', '2027-05-14', passages[1]))]
    first = assess([(message, {'items': items})], item_keys={message['id']: ['personal:1', 'personal:2']})
    automatic.save_preview(first, 0, enabled=True)
    prior = {row['title']: row for row in publication.latest()['payload']['events']}
    second = assess([(message, {'items': [items[1]]})], item_keys={message['id']: ['personal:2']})
    automatic.save_preview(second, 1, enabled=True)
    current = {row['uid']: row for row in publication.latest()['payload']['events']}
    assert current[prior['Class picnic']['uid']]['title'] == 'Class picnic'
    assert current[prior['Class picnic']['uid']]['sequence'] == prior['Class picnic']['sequence']
    assert current[prior['Class outing']['uid']]['status'] == 'cancelled'
