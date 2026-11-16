"""The Archive in Hub (hub/app/archive.py and its routes): one search across photos, documents, ChatGPT history,
mail and calendar history. A stand-in for the archive program on a real socket (tests/archive_standin.py), and a
stand-in bridge; every name, email and event is made up."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from hub import bridge, config, store
from hub.app import archive
from hub.main import app
from tests import archive_standin, archive_world

client = TestClient(app)
OWNER = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
OTHER = {'origin': 'http://testserver', 'tailscale-user-login': 'sam@example.com'}
TODAY = dt.date(2026, 11, 16)


@pytest.fixture
def served(monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {'me@example.com', 'sam@example.com'})
    server, path = archive_standin.start()
    archive_world.allow(path, monkeypatch)
    monkeypatch.setenv('HUB_ARCHIVE_SOCKET', str(path))
    monkeypatch.setattr(archive, '_tailscale_login', lambda: 'me@example.com')
    archive._owner.update(at=float('-inf'), login=None)
    yield server
    server.shutdown()
    server.server_close()


class Bridge:
    """Stands in for the mail and calendar bridge: made-up emails and events, and what Hub asked of it."""

    def __init__(self, events=None):
        self.asked = []
        self.events = events or {}

    def get(self, path, ttl=60, timeout=None, keep=True, **params):
        self.asked.append((path, params))
        if path == '/gmail/search':
            q = params['query'].lower()
            found = [m for m in MAIL if all(w in (m['subject'] + ' ' + m['snippet'] + ' ' + m['from']).lower()
                                            for w in q.split() if ':' not in w)]
            return {'messages': found[:params['limit']]}
        if path == '/gmail/message':
            m = next(m for m in MAIL if m['id'] == params['message_id'])
            return {**m, 'text': 'Thanks for choosing us. Card 4111 1111 1111 4242 was charged $189.00.',
                    'attachments': [{'part_id': '2', 'filename': 'invoice.pdf', 'mime_type': 'application/pdf', 'bytes': 2048,
                                     'readable': True, 'inline': False}]}
        if path == '/gmail/document-attachment':
            return {'filename': 'invoice.pdf', 'text': 'Furnace service invoice 4411', 'truncated': False}
        if path == '/calendar/events':
            start = dt.date.fromisoformat(params['start'][:10])
            return {'events': [e for e in self.events.get(start.strftime('%Y-%m'), [])], 'next_page_token': None}
        raise AssertionError(path)


MAIL = [
    {'id': '18c0000000000001', 'from': 'Example Heating <office@heating.example>', 'subject': 'Your furnace service invoice',
     'date': 'Fri, 03 Oct 2025 09:12:00 -0700', 'snippet': 'Annual furnace tune-up and filter replacement'},
    {'id': '18c0000000000002', 'from': '"Example Heating" <office@heating.example>', 'subject': 'Furnace service reminder',
     'date': 'Mon, 01 Sep 2025 08:00:00 -0700', 'snippet': 'Time to book your furnace service'},
    {'id': '18c0000000000003', 'from': 'Field Trips <trips@school.example>', 'subject': 'Aquarium field trip',
     'date': 'Mon, 20 Apr 2026 08:00:00 -0700', 'snippet': 'Bring a packed lunch'}]


@pytest.fixture
def mail(monkeypatch):
    fake = Bridge()
    monkeypatch.setattr(bridge, 'get', fake.get)
    return fake


# ---- reading a search, with no model ----

def meaning(text, today=TODAY):
    return archive.understand(text, ['Alex', 'Sam'], ['Sam'], today)


def test_kids_at_the_beach_in_2024():
    m = meaning('show me the kids at the beach in 2024')
    assert (m['kids'], m['terms'], m['after'], m['before'], m['about']) == (True, 'beach', '2024-01-01', '2025-01-01',
                                                                           'Kids at the beach in 2024')


def test_when_did_we_last_service_the_furnace():
    m = meaning('When did we last service the furnace?')
    assert (m['terms'], m['sort'], m['after']) == ('service furnace', 'newest', None)


@pytest.mark.parametrize('said, after, before', [
    ('photos from last summer', '2026-06-01', '2026-09-01'),
    ('pictures from summer 2024', '2024-06-01', '2024-09-01'),
    ('last winter', '2025-12-01', '2026-03-01'),
    ('the beach in july 2025', '2025-07-01', '2025-08-01'),
    ('last year at the park', '2025-01-01', '2026-01-01'),
    ('two years ago', '2024-01-01', '2025-01-01'),
    ('between 2019 and 2021', '2019-01-01', '2022-01-01'),
])
def test_dates_years_and_seasons(said, after, before):
    m = meaning(said)
    assert (m['after'], m['before']) == (after, before)


def test_last_summer_in_july_is_last_years_and_a_month_alone_is_any_year():
    assert meaning('last summer', dt.date(2026, 7, 4))['after'] == '2025-06-01'
    m = meaning('snow in december')
    assert m['months'] == [12] and m['after'] is None and m['terms'] == 'snow'
    assert meaning('summer at the lake')['months'] == [6, 7, 8]
    assert meaning('things we may need')['months'] == []          # "may" alone is a word


def test_people_by_name_and_possessive_and_sources_by_word():
    m = meaning("Sam's birthday photos")
    assert m['people'] == ['Sam'] and m['sources'] == ['photos'] and m['terms'] == 'birthday'
    m = meaning('furnace pdf and the email about it')
    assert m['sources'] == ['documents', 'mail'] and m['kind'] == ['pdf'] and m['terms'] == 'furnace'
    assert meaning('videos of Alex')['kind'] == ['video']
    assert meaning('my chats about filters')['sources'] == ['chats']


def test_the_sentence_hub_writes():
    assert archive.photo_sentence('Kids at the beach in 2024', {'count': 48, 'days': 6}) == \
        'Kids at the beach in 2024: 48 photos from 6 days.'
    assert archive.photo_sentence('The park', {'count': 1, 'days': 1}) == 'The park: 1 photo from one day.'
    assert archive.photo_sentence('Snow', {'count': 0}) == 'Snow: no photos.'


# ---- the screen's search ----

def test_signed_in_devices_only(served):
    for path in ('/app/api/archive', '/app/api/archive/search?q=beach', '/app/api/archive/thumb/p:00000001-1111-4000-8000-000000000000'):
        assert client.get(path, headers={'origin': 'http://testserver'}).status_code == 403


def test_kids_at_the_beach_from_the_screen(served):
    found = client.get('/app/api/archive/search', params={'q': 'the kids at the beach in 2024'}, headers=OWNER).json()
    assert found['said'] == 'Kids at the beach in 2024: 9 photos from 2 days.'
    assert found['photos']['count'] == 9 and found['understood']['kids'] is True
    asked = dict(served.asked[-1][1])
    assert asked['anyof'] == ['Sam'] and asked['young'] == ['1'] and asked['after'] == ['2024-01-01']


def test_people_alone_are_found_in_photos_and_words_everywhere(served):
    alone = client.get('/app/api/archive/search', params={'q': 'Alex and Sam'}, headers=OWNER).json()
    assert alone['sources'] == ['photos'] and 'documents' not in alone
    words = client.get('/app/api/archive/search', params={'q': 'furnace'}, headers=OWNER).json()
    assert words['documents']['count'] == 1 and words['chats']['count'] == 1 and words['photos']['count'] == 1
    assert words['mail'] is True                                   # the screen asks for mail next


def test_chatgpt_history_is_the_owners_alone(served):
    theirs = client.get('/app/api/archive/search', params={'q': 'furnace'}, headers=OTHER).json()
    assert 'chats' not in theirs and 'chats' not in theirs['sources']
    assert client.get('/app/api/archive/item/c:c0ffee00-0000-4000-8000-000000000001', headers=OTHER).status_code == 404
    assert client.get('/app/api/archive/item/c:c0ffee00-0000-4000-8000-000000000001', headers=OWNER).json()['title'] == \
        'Furnace filter sizes'
    landing = client.get('/app/api/archive', headers=OTHER).json()
    assert 'chats' not in landing['counts'] and landing['owner'] is False


def test_the_landing(served):
    landing = client.get('/app/api/archive', headers=OWNER).json()
    assert landing['ready'] and landing['owner'] and landing['counts']['photos']['items'] == 12
    assert landing['onthisday'][0]['year'] == 2023
    assert landing['switches'] == {'documents': False, 'chats': False}
    assert {p['name']: p['kid'] for p in landing['people']} == {'Sam': True, 'Alex': False}


def test_without_the_archive_the_screen_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv('HUB_ARCHIVE_SOCKET', str(tmp_path / 'none.sock'))
    landing = client.get('/app/api/archive', headers=OWNER).json()
    assert landing['ready'] is False and landing['away'] == 'The archive isn’t set up on this Mac yet.'
    found = client.get('/app/api/archive/search', params={'q': 'beach'}, headers=OWNER).json()
    assert found['ready'] is False and 'photos' not in found


def test_pictures_pages_and_files_by_id_only(served):
    photo = 'p:00000001-1111-4000-8000-000000000000'
    invoice = archive_standin.DOCUMENTS[0]['id']
    assert client.get(f'/app/api/archive/thumb/{photo}', headers=OWNER).headers['content-type'] == 'image/png'
    shown = client.get(f'/app/api/archive/photo/{photo}', headers=OWNER)
    assert shown.status_code == 200 and shown.headers['cache-control'] == 'no-store'
    assert client.get(f'/app/api/archive/page/{invoice}/1', headers=OWNER).status_code == 200
    got = client.get(f'/app/api/archive/file/{invoice}', headers=OWNER)
    assert got.content.startswith(b'%PDF') and 'attachment' in got.headers['content-disposition']
    assert got.headers['x-content-type-options'] == 'nosniff'
    assert client.get(f'/app/api/archive/file/{photo}', headers=OWNER).status_code == 404     # never a photo's original
    assert client.get(f'/app/api/archive/photo/{invoice}', headers=OWNER).status_code == 404
    for odd in ('..%2F..%2Fetc', 'p:short', 'c:c0ffee00-0000-4000-8000-000000000001'):
        assert client.get(f'/app/api/archive/thumb/{odd}', headers=OWNER).status_code == 404
    assert all(path.split('/')[1] in ('thumb', 'photo', 'page', 'file', 'people', 'search', 'item', 'status', 'onthisday')
               for path, _ in served.asked)


def test_the_kids_can_be_changed_here(served):
    said = client.post('/app/api/archive/kid', json={'name': 'alex', 'kid': True}, headers=OWNER).json()['said']
    assert said == 'Alex is one of the kids now.'
    assert {p['name']: p['kid'] for p in archive.people()} == {'Sam': True, 'Alex': True}
    client.get('/app/api/archive/search', params={'q': 'the kids'}, headers=OWNER)
    assert sorted(dict(served.asked[-1][1])['anyof']) == ['Alex', 'Sam']
    assert client.post('/app/api/archive/kid', json={'name': 'Nobody'}, headers=OWNER).status_code == 409


def test_what_ask_may_read_is_the_owners_switch(served):
    assert client.post('/app/api/archive/switch', json={'name': 'documents', 'on': True}, headers=OTHER).status_code == 409
    on = client.post('/app/api/archive/switch', json={'name': 'documents', 'on': True}, headers=OWNER).json()
    assert on['said'] == 'Ask may read documents now.' and on['switches'] == {'documents': True, 'chats': False}
    assert client.post('/app/api/archive/switch', json={'name': 'photos', 'on': True}, headers=OWNER).status_code == 409


# ---- mail, live through the bridge ----

def test_mail_each_phrase_its_own_query_newest_first(served, mail):
    found = client.get('/app/api/archive/mail', params={'q': 'furnace service in 2025'}, headers=OWNER).json()['mail']
    assert [m['subject'] for m in found] == ['Your furnace service invoice', 'Furnace service reminder']
    assert found[0]['from'] == 'Example Heating' and found[0]['day'] == '2025-10-03'
    assert mail.asked[-1] == ('/gmail/search', {'query': 'furnace service after:2025/01/01 before:2026/01/01', 'limit': 10})
    queries = archive.gmail_queries(archive.understand('x'), ['furnace service', 'hvac maintenance', 'heating tune-up'])
    assert queries == ['furnace service', 'hvac maintenance', 'heating tune-up']
    assert archive.gmail_queries(archive.understand('in 2025')) == []                 # a date alone lists nothing


def test_an_email_and_its_pdf_on_a_tap(served, mail):
    opened = client.get('/app/api/archive/item/m:18c0000000000001', headers=OWNER).json()
    assert opened['subject'] == 'Your furnace service invoice' and opened['attachments'][0]['readable']
    text = client.get('/app/api/archive/attachment/m:18c0000000000001/2', headers=OWNER).json()
    assert text['text'] == 'Furnace service invoice 4411'
    assert client.get('/app/api/archive/attachment/m:18c0000000000001/..', headers=OWNER).status_code == 404


# ---- calendar history ----

def event(key, day, title, where=''):
    return {'id': key, 'status': 'confirmed', 'summary': title, 'location': where, 'start': {'date': day}, 'end': {'date': day}}


def test_calendar_history_goes_back_until_the_calendar_runs_out(monkeypatch):
    events = {'2026-10': [event('a1', '2026-10-03', 'Furnace service', 'Home')],
              '2025-10': [event('b1', '2025-10-03', 'Furnace service')], '2024-06': [event('c1', '2024-06-20', 'Dentist')]}
    fake = Bridge(events)
    monkeypatch.setattr(bridge, 'get', fake.get)
    now = dt.datetime(2026, 11, 16, 9, tzinfo=archive.home.PACIFIC)
    for _ in range(4):
        archive.calendar_collect(now, pause=0)
    status = archive.calendar_status()
    assert status['complete'] and status['events'] == 3 and status['first'] == '2024-06-20'
    found = archive.calendar(archive.understand('when did we last have the furnace serviced'))
    assert [e['day'] for e in found] == ['2026-10-03', '2025-10-03']
    assert archive.calendar(archive.understand('dentist in 2024'))[0]['title'] == 'Dentist'
    asked = [p for path, p in fake.asked if path == '/calendar/events']
    assert all(dt.date.fromisoformat(p['end'][:10]) - dt.date.fromisoformat(p['start'][:10]) <= dt.timedelta(days=31)
               for p in asked if p['start'][:10] < '2026-10')                     # a month at a time, as the bridge allows
    fake.events['2026-10'] = []                                                   # an event moved away is let go
    archive.calendar_collect(now + dt.timedelta(days=1), pause=0)                 # at the next night's reading
    assert [e['day'] for e in archive.calendar(archive.understand('furnace'))] == ['2025-10-03']


def test_calendar_searches_by_date_alone_and_never_lists_everything(monkeypatch):
    fake = Bridge({'2026-10': [event('a1', '2026-10-03', 'Furnace service')]})
    monkeypatch.setattr(bridge, 'get', fake.get)
    archive.calendar_collect(dt.datetime(2026, 11, 16, 9, tzinfo=archive.home.PACIFIC), pause=0)
    assert len(archive.calendar(archive.understand('october 2026'))) == 1
    assert archive.calendar(archive.understand('')) == []
    with store.db() as conn:
        assert conn.execute('select count(*) from archive_events').fetchone()[0] >= 1
