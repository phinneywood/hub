import pytest
import httpx

from preschool.support import bridge
from preschool import jev, sources


def test_fixed_public_source_allowlist_and_safe_normalization():
    normalized,kind=sources.normalize_url('https://www.maple-hill.example/kids/mhp/?utm_source=email')
    assert normalized=='https://www.maple-hill.example/kids/mhp/' and kind=='public_page'
    normalized,kind=sources.normalize_url('https://docs.google.com/spreadsheets/d/abcdefghijk1234567890/edit?gid=42&usp=sharing')
    assert normalized=='https://docs.google.com/spreadsheets/d/abcdefghijk1234567890/export?format=csv&gid=42'
    assert kind=='google_sheet'
    assert sources.normalize_url('http://maple-hill.example/kids/mhp/')[0]=='https://maple-hill.example/kids/mhp/'
    for url in ('http://evil.example/x',
                'https://maple-hill.example/rsvp/','https://maple-hill.example/x?token=secret',
                'https://127.0.0.1/admin','https://docs.google.com/document/d/abcdefghijk1234567890/edit?resourcekey=secret'):
        assert sources.normalize_url(url)[0] is None


def test_attachment_and_link_evidence_are_bounded_and_labelled(monkeypatch):
    ics=('BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:MHP Family Night\n'
         'DTSTART;VALUE=DATE:20261120\nEND:VEVENT\nEND:VCALENDAR')
    monkeypatch.setattr(sources,'_read_response',lambda client,url:((b'MHP public calendar classroom setup','text/plain'),None))
    message={'text':'MHP calendar','attachments':[{'mime_type':'text/calendar','readable':True,'attachment_id':'fake','part_id':'1'}],
             'links':[{'url':'https://www.maple-hill.example/kids/mhp/','label':'MHP calendar'}]}
    evidence=sources.collect(message,lambda attachment:ics)
    assert not evidence.review
    assert evidence.attachments[0]['calendar_events'][0]['title']=='MHP Family Night'
    assert evidence.links[0]['text']=='MHP public calendar classroom setup'
    state=jev.request_state({'subject':'Class update','text':'Friday class update'},'mhp-list',
                            [{'kind':'calendar invitation','label':'MHP calendar','text':evidence.text}])
    assert 'MHP Family Night' in state['supporting_sources'][0]['text']


def test_relevant_off_allowlist_link_holds_for_review_without_fetching(monkeypatch):
    called=[]
    monkeypatch.setattr(sources,'_read_response',lambda *a:called.append(1))
    evidence=sources.collect({'text':'See MHP registration form.','attachments':[],
        'links':[{'url':'https://outside.example/form','label':'MHP registration form'}]},lambda a:'')
    assert evidence.links==[] and evidence.review==['off_allowlist']
    assert not called


def test_links_only_in_quoted_history_are_not_fetched():
    evidence=sources.collect({'text':'Current class note.\nOld quote and a photo album link.',
        'attachments':[], 'links':[{'url':'https://docs.google.com/document/d/abcdefghijk1234567890/edit','label':'Photo album'}]},
        lambda a:'', current_text='Current class note.')
    assert evidence.links==[] and evidence.review==[]


def test_generic_link_label_uses_nearby_current_context():
    evidence=sources.collect({'text':'MHP parent education event registration: click here.', 'attachments':[],
        'links':[{'url':'https://outside.example/register','label':'click here'}]},lambda a:'')
    assert evidence.review==['off_allowlist']


def test_relative_relevant_link_is_reviewed_not_resolved_or_fetched():
    evidence=sources.collect({'text':'MHP registration is here.','attachments':[],
        'links':[{'url':'/registration','label':'MHP registration'}]},lambda a:'')
    assert evidence.review==['non_public_link'] and evidence.links==[]


def test_allowlisted_fetch_and_safe_redirects():
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        if request.url.path=='/start':
            return httpx.Response(302,headers={'Location':'https://www.maple-hill.example/final'})
        return httpx.Response(200,headers={'Content-Type':'text/plain'},content=b'MHP classroom event')
    with httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=False) as client:
        result,error=sources._read_response(client,'https://maple-hill.example/start')
    assert error is None and result==(b'MHP classroom event','text/plain')
    assert calls==['https://maple-hill.example/start','https://www.maple-hill.example/final']


def test_off_allowlist_redirect_is_refused_before_second_request():
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302,headers={'Location':'https://evil.example/collect'})
    with httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=False) as client:
        result,error=sources._read_response(client,'https://maple-hill.example/start')
    assert result is None and error=='off_allowlist'
    assert calls==['https://maple-hill.example/start']


def test_unavailable_allowlisted_source_is_reviewed(monkeypatch):
    monkeypatch.setattr(sources,'_read_response',lambda client,url:(None,'not_public_or_unavailable'))
    evidence=sources.collect({'text':'See the MHP calendar.','attachments':[],
        'links':[{'url':'https://maple-hill.example/calendar/','label':'MHP calendar'}]},lambda a:'')
    assert evidence.links==[] and evidence.review==['not_public_or_unavailable']


def test_inline_signature_images_do_not_block_message_but_real_image_files_do():
    ignored=sources.collect({'attachments':[{'mime_type':'image/png','filename':'logo.png','inline':True}], 'links':[]},lambda a:'')
    held=sources.collect({'attachments':[{'mime_type':'image/png','filename':'class-flyer.png','inline':False}], 'links':[]},lambda a:'')
    assert ignored.review==[]
    assert held.review==['unsupported_attachment']


def test_uninspected_attachments_or_truncated_link_metadata_force_review():
    attachments=sources.collect({'attachments':[{'mime_type':'text/plain','readable':True,
        'attachment_id':'fake'} for _ in range(4)],'links':[]},lambda a:'safe test text')
    links=sources.collect({'text':'MHP calendar','attachments':[],'links_truncated':True,'links':[]},lambda a:'')
    assert 'too_many_attachments' in attachments.review
    assert 'too_many_resource_links' in links.review


def test_pdf_link_uses_isolated_bridge_parser(monkeypatch):
    monkeypatch.setattr(sources,'_read_response',lambda client,url:((b'%PDF-fake','application/pdf'),None))
    calls=[]
    monkeypatch.setattr(bridge,'extract_public_document',lambda mime,data:calls.append((mime,data)) or {'text':'MHP class schedule','truncated':False})
    evidence=sources.collect({'text':'MHP class calendar','attachments':[],
        'links':[{'url':'https://drive.google.com/file/d/abcdefghijk1234567890/view','label':'MHP class calendar'}]},lambda a:'')
    assert calls and calls[0][0]=='application/pdf'
    assert evidence.links[0]['text']=='MHP class schedule'


def test_unknown_google_link_query_is_not_fetched():
    assert sources.normalize_url('https://docs.google.com/document/d/abcdefghijk1234567890/edit?authuser=private%40example.com')[0] is None


# The allowlist lives in the private household file. Every misconfiguration must fail closed.
from preschool import publication, school, scanner, school_calendar
from preschool.support import config
from tests.preschool.fictional_school import SCHOOL, write_school

ALLOWED = ('https://www.maple-hill.example/kids/mhp/',
           'https://docs.google.com/document/d/abcdefghijk1234567890/edit',
           'https://drive.google.com/file/d/abcdefghijk1234567890/view')


def assert_no_fetching():
    assert sources.allow_hosts() == frozenset()
    for url in ALLOWED:
        assert sources.normalize_url(url) == (None, 'off_allowlist')
    assert not sources._allowed_redirect('https://x.googleusercontent.com/file')
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(str(request.url)) or httpx.Response(200, content=b'x'))
    with httpx.Client(transport=transport) as client:
        assert sources._read_response(client, 'https://www.maple-hill.example/start') == (None, 'off_allowlist')
        evidence = sources.collect({'text': 'MHP calendar', 'attachments': [],
                                    'links': [{'url': ALLOWED[0], 'label': 'MHP calendar'}]}, lambda a: '', client)
    assert calls == [] and evidence.links == [] and 'off_allowlist' in evidence.review


def test_configured_hosts_allow_fetching():
    assert sources.allow_hosts() == frozenset(SCHOOL['source_hosts']) | sources.GOOGLE_HOSTS
    for url in ALLOWED:
        assert sources.normalize_url(url)[0]


def test_missing_household_file_means_no_fetching():
    (config.DATA_DIR / 'household.json').unlink()
    assert_no_fetching()
    assert publication.public_resource_url('https://www.maple-hill.example/kids/mhp/') is None
    assert school_calendar.source() == ''
    with pytest.raises(ValueError, match='not configured'):
        school_calendar.download()


@pytest.mark.parametrize('hosts', [None, [], 'maple-hill.example', ['maple-hill.example', 'https://evil.example'],
    ['maple-hill.example', 'evil.example/path'], ['maple-hill.example', 'evil.example:8443'], ['*.example'],
    ['127.0.0.1'], ['maple-hill.example', 'Evil.Example'], ['maple-hill.example', ''], ['maple-hill.example', 7],
    ['user@evil.example'], ['evil.example.'], ['docs.google.com'], ['evil.googleusercontent.com'], ['localhost']])
def test_invalid_or_missing_hosts_mean_no_fetching(hosts):
    write_school(config.DATA_DIR, {**SCHOOL, 'source_hosts': hosts})
    assert_no_fetching()


def test_malformed_household_file_means_no_fetching():
    (config.DATA_DIR / 'household.json').write_text('{"school": ["not", "an", "object"]}')
    assert_no_fetching()


def test_publishable_hosts_must_be_a_subset_of_source_hosts():
    write_school(config.DATA_DIR, {**SCHOOL, 'publishable_hosts': ['maple-hill.example', 'other.example']})
    assert publication.public_resource_url('https://maple-hill.example/kids/mhp/') is None


def test_unconfigured_school_neither_searches_nor_matches_by_name():
    (config.DATA_DIR / 'household.json').unlink()
    assert scanner.search_query('', 'newer_than:2d') == '{tuition} newer_than:2d'
    assert scanner.match_pattern().search('MHP class news') is None
    assert 'the school' in jev.questions()['useful_information']['instructions']


def test_configured_school_fills_prompts_and_discovery():
    assert scanner.search_query('', 'newer_than:2d') == '{MHP "Maple Hill Preschool" tuition} newer_than:2d'
    assert scanner.match_pattern().search('maple  hill preschool notes')
    assert 'Maple Hill Preschool (MHP)' in jev.questions()['preschool_relevant']['instructions']
    assert school.class_role() == 'sprouts_instruction' and school.calendar_filename() == 'mhp-sprouts.ics'
    assert not any('{' + token in str(jev.questions()) for token in ('SHORT', 'CLASS', 'SCHOOL', 'ORG', 'NEWS'))


def test_household_resource_words_are_private_and_fail_closed():
    assert sources.resource_words().search('Sproutly sign-in')
    write_school(config.DATA_DIR, {k: v for k, v in SCHOOL.items() if k != 'resource_words'})
    assert not sources.resource_words().search('Sproutly sign-in')
    assert sources.resource_words().search('Class calendar')
    for bad in ('Sproutly', 'a|b', '', 5):
        write_school(config.DATA_DIR, {**SCHOOL, 'resource_words': ['sproutly', bad]})
        assert school.resource_words() == ()
