"""Fake-only family publication: closed fields, frozen previews and durable ICS IDs."""
import json
import re
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from preschool.support import bridge, config, luna
from hub.main import app
from preschool import audience, class_context, ical, jev, publication, site_store, sources, store, website

client = TestClient(app)
ORIGIN = {'Origin': 'http://testserver'}
KEY = 'a' * 64


@pytest.fixture(autouse=True)
def no_external_services(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('Family previews must not access mail, sources, models or calendars')
    for module, name in ((bridge, 'get'), (bridge, 'post'), (luna, 'complete'), (jev, 'decide'),
                         (sources, '_read_response')):
        monkeypatch.setattr(module, name, denied)


def source(key=KEY, **changes):
    proof=audience.classify({'recipients':{'to':'class@example.invalid'}})
    return {'view_key': key, 'kind': 'event', 'title': 'Class outing', 'details': 'Class-wide information.',
            'date': '2026-11-09', 'time': '7:00 PM Pacific', 'location': 'Classroom', 'url': None,
            'uncertainty': '', 'dismissed': False, 'saved_at': 1, 'sender': 'PRIVATE_SENDER',
            'subject': 'PRIVATE_SUBJECT', 'gmail_url': 'https://mail.google.com/mail/#all/PRIVATE_MESSAGE',
            'source_quote': 'PRIVATE_QUOTE', 'account': 'PRIVATE_ACCOUNT',
            'sharing_audience':proof['scope'],'audience_policy':proof['policy'],
            'audience_binding_hash':proof['binding_hash'], **changes}


@pytest.fixture(autouse=True)
def explicit_synthetic_audience(isolated):
    with store.account('shared'):
        store.set_setting('family_group_addresses',json.dumps(['class@example.invalid']))


def timetable():
    return {'slug': 'our-class', 'revision': 1, 'updated': 1, 'payload': {
        'title': 'Friday class', 'weekdays': [4], 'start_date': '2026-09-11', 'end_date': '2027-06-17',
        'start_time': '09:30', 'end_time': '11:30', 'location': 'Classroom', 'details': 'Reviewed regular class.',
        'source_keys': [KEY], 'public_sources': [], 'exceptions': []}}


def exception(day, kind='cancelled'):
    return {'date': day, 'kind': kind, 'title': 'No class' if kind == 'cancelled' else 'Class field trip',
            'details': 'Reviewed change.', 'location': '' if kind == 'cancelled' else 'Destination',
            'start_time': None, 'end_time': None, 'source_keys': [KEY]}


def site(rows=None, tables=(), articles=None, today='2026-11-05'):
    rows = [source()] if rows is None else rows
    articles = {} if articles is None else {slug: {'source_keys': [], **row} for slug, row in articles.items()}
    data = website.project(rows, [], articles, today, tables)
    return {**data, 'all_sources': rows, 'articles': articles}


def proposal(rows=None, tables=(), articles=None):
    return publication.candidate(site(rows, tables, articles), tables)


def freeze(value=None, revision=0):
    publication.save_preview(value if value is not None else proposal(), revision, reviewed=True)
    return publication.latest()['payload']


def unfolded(feed):
    return re.sub(r'\r\n[ \t]', '', feed.decode())


def test_export_allowlists_drop_all_source_metadata_and_never_use_hub_layout():
    article = {'slug': 'updates', 'title': 'Class announcements', 'body': 'Reviewed family information.',
               'source_keys': [KEY], 'revision': 99, 'updated': 1, 'private_extra': 'PRIVATE_PAGE_METADATA'}
    value = freeze(proposal(articles={'updates': article}))
    encoded = json.dumps(value) + publication.family_html(value) + unfolded(publication.calendar_feed(value))
    for private in ('PRIVATE_', 'mail.google.com', 'source_quote', 'source_keys', 'gmail_url', '_keys',
                    '/gmail', '/network', '/mobility', 'teacher@example'):
        assert private not in encoded
    assert set(value) == {'schema', 'title', 'revision', 'updated', 'timezone', 'pages', 'events', 'resources'}
    assert set(value['pages'][0]) == {'slug', 'title', 'body'}
    assert set(value['events'][0]) == publication.EVENT_FIELDS | {'uid', 'sequence', 'created', 'updated'}


@pytest.mark.parametrize('private', ['Contact parent@example.invalid', 'Password is hidden',
    'Gallery access code', 'Call 206-555-0123', 'Open https://mail.google.com/', 'Roster attached',
    'Text with\rmalformed control', 'Name\x00hidden'])
def test_obvious_private_prose_is_held_not_automatically_redacted_or_published(private):
    article = {'slug': 'updates', 'title': 'Class news', 'body': private}
    value = proposal([source(details=private)], articles={'updates': article})
    assert value['pages'] == [] and value['events'] == []
    assert len(value['holds']) >= 2
    assert private not in json.dumps(freeze(value))


def test_maintained_pages_only_no_fallback_or_unincorporated_source_copy():
    value = proposal([source(kind='update', title='Unreviewed new paragraph', date=None)])
    assert value['pages'] == []
    article = {'slug': 'updates', 'title': 'Class news', 'body': 'Maintained edition.'}
    value = freeze(proposal([source(kind='update', title='Unreviewed new paragraph')], articles={'updates': article}))
    assert 'Unreviewed' not in json.dumps(value)
    assert 'Maintained edition.' in json.dumps(value)


def test_full_term_closures_and_field_trip_do_not_duplicate_or_inherit_regular_times():
    table = timetable()
    table['payload']['exceptions'] = [exception('2026-11-27'), exception('2026-12-04', 'changed')]
    rows = [source(kind='update', date=None)]
    value = freeze(proposal(rows, [table]))
    assert value['events'][0]['date'] == '2026-09-11'
    assert value['events'][-1]['date'] == '2027-06-11'
    closed = [row for row in value['events'] if row['date'] == '2026-11-27']
    trip = [row for row in value['events'] if row['date'] == '2026-12-04']
    assert len(closed) == len(trip) == 1
    assert closed[0]['status'] == 'cancelled'
    assert trip[0]['location'] == 'Destination' and trip[0]['start_time'] is None
    assert 'Time not confirmed' in trip[0]['time_note']
    parsed = ical.parse(publication.calendar_feed(value).decode())
    assert next(row for row in parsed if row['date'] == '2026-12-04')['time'] is None


def test_missing_evidence_holds_occurrences_and_school_reference_is_never_promoted():
    table = timetable()
    data = site([], [table])
    data['school_calendar'] = [{'event': 'Not a class event', 'date_iso': '2026-11-06'}]
    assert publication.candidate(data, [table])['events'] == []


def test_unknown_unparsed_single_and_compact_times_never_invent_an_end_or_noon():
    assert publication.event_time('1:00–6:00 PM Pacific') == ('13:00', '18:00', '')
    assert publication.event_time('9 AM–1 PM') == ('09:00', '13:00', '')
    assert publication.event_time('4-6pm') == ('16:00', '18:00', '')
    assert publication.event_time('7:00 PM Pacific') == ('19:00', None, 'End time not confirmed.')
    for value in (None, '9–1 PM', 'after lunch', '7 PM Eastern', '23:00–01:00'):
        start, end, note = publication.event_time(value)
        assert start is end is None and note
    value = freeze()
    feed = unfolded(publication.calendar_feed(value))
    assert 'DTSTART:20261110T030000Z' in feed and 'DTEND:' not in feed


def test_pacific_clock_is_serialized_as_correct_utc_on_both_sides_of_dst():
    value = freeze(proposal([source(date='2026-11-06', time='9:30 AM–11:30 AM'),
                             source('b'*64, date='2027-04-02', time='9:30 AM–11:30 AM')]))
    feed = unfolded(publication.calendar_feed(value))
    assert 'DTSTART:20261106T173000Z' in feed
    assert 'DTEND:20261106T193000Z' in feed
    assert 'DTSTART:20270402T163000Z' in feed
    assert 'DTEND:20270402T183000Z' in feed


@pytest.mark.parametrize('day,time', [('2026-11-01', '1:30 AM'), ('2027-03-14', '2:30 AM')])
def test_ambiguous_or_nonexistent_dst_times_fail_closed(day, time):
    with pytest.raises(ValueError, match='daylight-saving'):
        publication.candidate(site([source(date=day, time=time)], today='2026-10-01'), [])


def test_date_markers_have_exclusive_next_day_end_and_transparent_status():
    value = freeze(proposal([source(time=None)]))
    feed = unfolded(publication.calendar_feed(value))
    assert 'DTSTART;VALUE=DATE:20261109\r\nDTEND;VALUE=DATE:20261110' in feed
    assert 'TRANSP:TRANSPARENT' in feed and 'Time not confirmed' in feed


def test_title_date_location_and_time_edits_keep_uid_increment_sequence_and_no_duplicates():
    first = freeze()
    changed = proposal([source(title='Corrected class outing', date='2026-11-10', location='New room', time='6 PM–8 PM')])
    second = freeze(changed, 1)
    assert len(second['events']) == 1
    assert second['events'][0]['uid'] == first['events'][0]['uid']
    assert second['events'][0]['sequence'] == 1
    assert first['events'][0]['sequence'] == 0 and first['events'][0]['title'] == 'Class outing'
    assert second['events'][0]['created'] == first['events'][0]['created']
    with closing(site_store.db()) as conn:
        assert conn.execute('select count(*) from family_previews').fetchone()[0] == 2


def test_unchanged_preview_is_idempotent_and_unmodified_events_keep_sequence_and_dtstamp():
    value = proposal()
    first = freeze(value)
    assert publication.save_preview(value, 1, reviewed=True) == 1
    value['pages'] = [{'slug': 'updates', 'title': 'Class news', 'body': 'New reviewed guide edition.'}]
    second = freeze(value, 1)
    assert second['events'] == first['events'] and second['revision'] == 2


def test_removal_retains_generic_cancellation_and_can_be_reinstated_with_same_uid():
    first = freeze()
    second = freeze(proposal([]), 1)
    cancelled = second['events'][0]
    assert cancelled['uid'] == first['events'][0]['uid'] and cancelled['sequence'] == 1
    assert cancelled['status'] == 'cancelled' and cancelled['details'] == cancelled['location'] == ''
    assert 'Class outing' not in json.dumps(second)
    third = freeze(proposal(), 2)
    assert third['events'][0]['uid'] == cancelled['uid'] and third['events'][0]['sequence'] == 2
    assert third['events'][0]['status'] == 'confirmed'


def test_regular_slot_changed_to_closure_keeps_same_uid():
    table = timetable()
    rows = [source(kind='update', date=None)]
    first = freeze(proposal(rows, [table]))
    table['payload']['exceptions'] = [exception('2026-11-27')]
    second = freeze(proposal(rows, [table]), 1)
    before = next(row for row in first['events'] if row['date'] == '2026-11-27')
    after = next(row for row in second['events'] if row['date'] == '2026-11-27')
    assert before['uid'] == after['uid'] and after['sequence'] == 1 and after['status'] == 'cancelled'


def test_added_exact_duplicate_evidence_retains_identity_even_with_earlier_sorting_key():
    first = freeze(proposal([source('b'*64)]))
    second = freeze(proposal([source('b'*64), source(KEY)]), 1)
    assert len(second['events']) == 1 and first['events'] == second['events']


def test_conflicting_merge_or_split_stops_atomically_instead_of_guessing_event_identity():
    freeze(proposal([source(), source('b'*64, title='Another class outing')]))
    with pytest.raises(ValueError, match='separate events'):
        freeze(proposal([source(), source('b'*64)]), 1)
    assert publication.latest()['revision'] == 1
    assert len(publication.latest()['payload']['events']) == 2


def test_splitting_combined_evidence_fails_without_mutating_the_calendar_ledger():
    first = freeze(proposal([source(), source('b'*64)]))
    with pytest.raises(ValueError, match='combined event was split'):
        freeze(proposal([source(), source('b'*64, title='A conflicting separate event')]), 1)
    assert publication.latest()['payload'] == first
    with closing(site_store.db()) as conn:
        assert conn.execute('select count(*) from family_calendar').fetchone()[0] == 1
        assert conn.execute('select sequence from family_calendar').fetchone()[0] == 0


def test_retained_cancellations_count_toward_limit_and_overflow_rolls_back(monkeypatch):
    monkeypatch.setattr(publication, 'MAX_EVENTS', 1)
    first = freeze()
    with pytest.raises(ValueError, match='retained calendar'):
        freeze(proposal([source('b'*64, title='New separate event')]), 1)
    assert publication.latest()['payload'] == first
    with closing(site_store.db()) as conn:
        assert conn.execute('select count(*) from family_calendar').fetchone()[0] == 1
        assert conn.execute('select count(*) from family_event_keys').fetchone()[0] == 1


def test_existing_past_events_stay_in_proposals_but_new_expired_events_are_not_imported():
    assert proposal([source(date='2026-08-01')])['events'] == []
    first = freeze()
    value = publication.candidate(site(today='2027-01-01'), [])
    second = freeze(value, 1)
    assert first['events'] == second['events']


@pytest.mark.parametrize('url', ['https://mail.google.com/mail/#all/private',
    'https://docs.google.com/document/d/privateIdentity123/view', 'https://outside.invalid/form',
    'https://maple-hill.example/gallery/', 'https://maple-hill.example/form?token=secret',
    'https://maple-hill.example/unsubscribe', 'javascript:alert(1)'])
def test_private_or_unapproved_resource_destinations_are_omitted_without_fetching(url):
    value = proposal([source(kind='resource', date=None, url=url)])
    assert value['resources'] == [] and value['holds']


def test_public_official_resource_is_exported_without_its_source_dictionary():
    value = freeze(proposal([source(kind='resource', date=None, url='https://maple-hill.example/kids/mhp/')]))
    assert value['resources'] == [{'title': 'Class outing', 'details': 'Class-wide information.',
                                  'url': 'https://maple-hill.example/kids/mhp/'}]


def test_untrusted_calendar_text_is_escaped_and_folded_by_utf8_octets():
    details = 'Café 🌱; bring boots, coats.\nBEGIN:VEVENT\nSUMMARY:Not a second event\\'
    value = freeze(proposal([source(title='<script>bad()</script>', details=details * 5)]))
    feed = publication.calendar_feed(value)
    assert all(len(line) <= 75 for line in feed.split(b'\r\n'))
    assert b'\n' not in feed.replace(b'\r\n', b'')
    text = unfolded(feed)
    assert text.count('\r\nBEGIN:VEVENT\r\n') == 1
    assert '\\; bring boots\\, coats.' in text and '\\nBEGIN:VEVENT\\nSUMMARY:' in text
    assert '\\\\' in text
    parsed = ical.parse(feed.decode())
    assert len(parsed) == 1 and parsed[0]['title'] == '<script>bad()</script>'
    html = publication.family_html(value)
    assert '<script>bad()' not in html and '&lt;script&gt;bad()' in html
    for forbidden in ('BEGIN:VALARM', 'ATTENDEE:', 'ORGANIZER:', 'URL:'):
        assert forbidden not in text


def test_review_and_stale_revision_are_mandatory_even_for_direct_operator_calls():
    with pytest.raises(ValueError, match='Review every'):
        publication.save_preview(proposal(), 0)
    freeze()
    with pytest.raises(site_store.RevisionConflict):
        publication.save_preview(proposal(), 0, reviewed=True)


def test_closed_schema_rejects_accidental_private_fields():
    value = proposal()
    value['events'][0]['gmail_url'] = 'private'
    with pytest.raises(ValueError, match='fields'):
        freeze(value)
    assert publication.latest() is None


def test_empty_preview_endpoints_fail_closed_and_all_routes_remain_owner_only():
    for path in ('/preschool/family-preview/', '/preschool/calendar.ics', '/preschool/family-preview/snapshot.json'):
        assert client.get(path).status_code == 404
        assert client.get(path, headers={'Tailscale-User-Login': 'outsider@example.invalid'}).status_code == 403


def test_same_origin_review_and_fingerprint_protect_prepare_and_reader_never_reads_catalogs(monkeypatch):
    site_store.save('updates', 'Class news', 'Reviewed family paragraph.', [], 0)
    page = client.get('/preschool/manage/publication')
    assert 'Private relevance is not permission to share.' in page.text
    digest = re.search(r'name="fingerprint" value="([a-f0-9]+)"', page.text)[1]
    data = {'fingerprint': digest, 'revision': 0, 'reviewed': 'yes'}
    assert client.post('/preschool/manage/publication', data=data).status_code == 403
    assert client.post('/preschool/manage/publication', headers=ORIGIN, data={**data, 'reviewed': ''}).status_code == 400
    assert client.post('/preschool/manage/publication', headers=ORIGIN, data={**data, 'fingerprint': '0'*64}).status_code == 409
    response = client.post('/preschool/manage/publication', headers=ORIGIN, data=data, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/preschool/'
    def denied(*args, **kwargs):
        pytest.fail('The frozen family reader must not query private catalogs')
    monkeypatch.setattr(store, 'all_items', denied)
    monkeypatch.setattr(store, 'setting', denied)
    monkeypatch.setattr(site_store, 'pages', denied)
    assert 'Reviewed family paragraph.' in client.get('/preschool/family-preview/').text
    assert client.get('/preschool/family-preview/snapshot.json').json()['revision'] == 1
    feed = client.get('/preschool/calendar.ics')
    assert feed.status_code == 200 and feed.headers['content-type'].startswith('text/calendar')
    assert feed.content == client.get('/preschool/family-preview/calendar.ics').content
    assert client.get('/preschool/family-preview/style.css').status_code == 200


def test_page_edit_during_review_requires_a_fresh_proposal():
    site_store.save('updates', 'Class news', 'Initial reviewed paragraph.', [], 0)
    page = client.get('/preschool/manage/publication')
    digest = re.search(r'name="fingerprint" value="([a-f0-9]+)"', page.text)[1]
    site_store.save('updates', 'Class news', 'Changed paragraph requiring review.', [], 1)
    response = client.post('/preschool/manage/publication', headers=ORIGIN,
                           data={'fingerprint': digest, 'revision': 0, 'reviewed': 'yes'})
    assert response.status_code == 409 and 'Class information changed' in response.text
    assert publication.latest() is None


def test_frozen_preview_does_not_change_with_new_source_or_page_edits_and_preserves_pauses():
    for account in ('personal', 'shared'):
        with store.account(account):
            store.set_setting('auto_enabled', '0')
            store.set_setting('backfill_enabled', '0')
    site_store.save('updates', 'Class news', 'First reviewed paragraph.', [], 0)
    freeze(publication.candidate(class_context(), []))
    before = client.get('/preschool/family-preview/snapshot.json').content
    site_store.save('updates', 'Class news', 'Later private draft edition.', [], 1)
    assert client.get('/preschool/family-preview/snapshot.json').content == before
    assert 'Later private draft' not in client.get('/preschool/family-preview/').text
    for account in ('personal', 'shared'):
        with store.account(account):
            assert store.setting('auto_enabled') == store.setting('backfill_enabled') == '0'
            assert store.all_items() == []
    assert (config.DATA_DIR / 'preschool-site.db').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('changes',[
    {'sharing_audience':'private'}, {'sharing_audience':None},
    {'audience_policy':'0'}, {'audience_binding_hash':'0'*64},
])
def test_private_missing_or_stale_audience_blocks_all_new_email_derived_material(changes):
    article={'slug':'updates','title':'Class news','body':'Ordinary-looking but private information.',
             'source_keys':[KEY]}
    value=proposal([source(**changes),source('b'*64,kind='resource',date=None,
        url='https://maple-hill.example/kids/mhp/',**changes)],articles={'updates':article})
    assert value['pages']==value['events']==value['resources']==[]
    assert sum('audience' in hold for hold in value['holds'])==3


def test_prior_approval_preserves_only_exact_public_copy_not_future_source_edits():
    article={'slug':'updates','title':'Class news','body':'Reviewed paragraph.','source_keys':[KEY]}
    rows=[source(),source('b'*64,kind='resource',date=None,url='https://maple-hill.example/kids/mhp/')]
    first=freeze(proposal(rows,articles={'updates':article}))
    private=[{**row,'sharing_audience':'private'} for row in rows]
    unchanged=proposal(private,articles={'updates':article})
    assert unchanged['pages']==first['pages'] and unchanged['resources']==first['resources']
    assert len(unchanged['events'])==1
    changed=proposal([{**row,'details':'New private detail.'} for row in private],
                     articles={'updates':{**article,'body':'Changed private paragraph.'}})
    assert changed['pages']==changed['events']==changed['resources']==[]
    assert publication.latest()['payload']==first


def test_changed_page_requires_every_source_but_exact_duplicate_fact_can_use_independent_class_evidence():
    private=source(sharing_audience='private')
    class_wide=source('b'*64)
    article={'slug':'updates','title':'Class news','body':'Mixed source paragraph.','source_keys':[KEY,'b'*64]}
    value=proposal([private,class_wide],articles={'updates':article})
    assert value['pages']==[] and len(value['events'])==1
    # A different private fact cannot borrow class evidence merely by sharing a title.
    value=proposal([private,source('b'*64,details='Different public details.')])
    assert len(value['events'])==1 and value['events'][0]['details']=='Different public details.'


def test_schedule_exception_cannot_borrow_regular_class_audience():
    table=timetable()
    table['payload']['exceptions']=[{**exception('2026-11-27'),'source_keys':['b'*64]}]
    rows=[source(kind='update'),source('b'*64,kind='update',sharing_audience='private')]
    value=proposal(rows,[table])
    assert value['events'] and all(row['date']!='2026-11-27' for row in value['events'])


def test_unknown_source_key_is_not_treated_as_owner_authored_text():
    article={'slug':'updates','title':'Class news','body':'Needs source proof.','source_keys':['c'*64]}
    assert proposal(articles={'updates':article})['pages']==[]


def test_url_allowlist_is_not_itself_a_public_audience_grant_for_google_documents():
    row={'reference_only':True,'view_key':KEY,'url':'https://docs.google.com/document/d/privateIdentity123/view',
         'source_quote':'Ordinary-looking text.'}
    assert not audience.source_allowed(row)
    row['url']='https://maple-hill.example/kids/mhp/'
    assert not audience.source_allowed(row)  # unbound reference identity


def test_identical_private_new_event_cannot_borrow_an_unrelated_approved_event_identity():
    freeze()
    value=proposal([source('b'*64,sharing_audience='private')])
    assert value['events']==[]
