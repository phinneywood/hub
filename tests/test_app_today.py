"""The app's Today screen (hub/app/today.py): what needs a person, then the day. A made-up day throughout
(tests/day_world.py); no real calendar, mailbox or service is read."""
import json

import pytest
from fastapi.testclient import TestClient

from hub.app import today as screen
from hub.main import app
from tests import day_world

client = TestClient(app)
SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
OWNER = 'me@example.com'        # the one allowed login (tests/conftest.py), so the owner of the main Google account


@pytest.fixture
def day(monkeypatch):
    return day_world.install(monkeypatch)


def seen(login=OWNER):
    return client.get('/app/api/today', headers={'tailscale-user-login': login} if login else {}).json()


def test_what_needs_a_person_is_a_short_list_the_most_pressing_first(day):
    needs = seen()['needs']
    assert [(n['title'], n['why'], n['open']['label'], n['tick']) for n in needs] == [
        ('Email from Sam Rivera', 'Waiting 4 days. Send the signed form back.', 'Open', 'Done'),
        ('Approve the grocery cart', 'Kitchen has a cart ready. Nothing is ordered until you approve it.', 'Review', 'Not today'),
        ('Email from Maple Street Dental', 'Confirm Tuesday at 3:00.', 'Open', 'Done'),
    ]
    assert [n['key'] for n in needs] == ['mail:m2', 'kitchen-cart', 'mail:m1']


def test_housekeeping_is_told_not_asked_and_nothing_is_listed_twice(day):
    found = seen()
    assert found['quiet'] == [{'text': 'Hub filed 12 emails.', 'open': {'href': '/gmail', 'label': 'See'}}]
    titles = [n['title'] for n in found['needs']]
    assert 'Choose flights' not in titles and 'Photo day' not in titles         # the trip and school have their own places
    assert found['trip'] == {'title': 'Lisbon', 'when': 'In 73 days', 'note': 'Flights aren’t chosen yet', 'open': {'href': '/trip', 'label': 'Compare'}}


def test_the_rest_of_today_and_tomorrow_come_from_the_calendar(day):
    found = seen()
    assert [(e['time'], e['half'], e['title'], e['next']) for e in found['today']] == [
        ('7:00', 'pm', 'Dinner at home', True), ('7:45', 'pm', 'Robin’s bedtime', False)]            # lunch is over
    assert [(e['time'], e['half'], e['title'], e['note'], e['warn']) for e in found['tomorrow']] == [
        ('All day', '', 'Alex away', '', False),
        ('9:30', 'am', 'Swim lesson', 'Community pool', False),
        ('11:00', 'am', 'Grocery pickup', 'Greenway Market', False),
        ('4:00', 'pm', 'Dentist', '', False),
        ('4:00', 'pm', 'Call with the plumber', 'Overlaps Dentist at 4:00 pm', True)]


def test_an_event_under_way_says_now(day):
    day.events.insert(0, day_world.event('Playdate', 0, '17:30', '18:30', 'The park'))
    first = seen()['today'][0]
    assert (first['time'], first['title'], first['next']) == ('Now', 'Playdate', True)


def test_the_week_at_school_is_the_reviewed_guide_for_seven_days(day):
    assert [(s['day'], s['title'], s['note'], s['struck']) for s in seen()['school']] == [
        ('Mon', 'Photo day', '', False), ('Wed', 'You bring the snack', '9 am', False),
        ('Thu', 'Garden morning', 'Cancelled', True), ('Fri', 'Family work party', '10 am, The garden', False)]


def test_each_person_sees_their_own_mail_and_calendar_and_everyone_the_households_things(day, monkeypatch):
    from hub import config
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {OWNER, 'sam@example.com'})
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'people': [
        {'name': 'Alex', 'logins': [OWNER], 'accounts': ['default']}, {'name': 'Sam', 'logins': ['sam@example.com'], 'accounts': []}]}))
    mine, theirs, nobody = seen(), seen('sam@example.com'), seen(None)
    assert [n['key'] for n in mine['needs']] == ['mail:m2', 'kitchen-cart', 'mail:m1'] and mine['today'] and mine['mine'] is True
    for other in (theirs, nobody):            # the other person, and a request without a login: the household's only
        assert [n['key'] for n in other['needs']] == ['kitchen-cart'] and other['mine'] is False
        assert other['today'] == other['tomorrow'] == [] and other['note'] is None and other['quiet'] == []
        assert other['school'] == mine['school'] and other['trip'] == mine['trip']   # school and the trip are the household's
        assert 'Sam Rivera' not in json.dumps(other) and 'Dentist' not in json.dumps(other)
    # A need from someone else's account can't be ticked by them, nor put back.
    sam = {**SIGNED_IN, 'tailscale-user-login': 'sam@example.com'}
    for body in ({'key': 'mail:m2'}, {'key': 'mail:m2', 'back': True}, {'key': 'gmail-needs'}, {'key': 'ai-1'}):
        assert client.post('/app/api/today/tick', json=body, headers=sam).status_code == 409
    assert [n['key'] for n in seen()['needs']] == ['mail:m2', 'kitchen-cart', 'mail:m1']
    assert client.post('/app/api/today/tick', json={'key': 'kitchen-cart'}, headers=sam).json()['said'] == 'Hidden for everyone until tomorrow'


def test_a_login_hub_cant_place_is_told_why_its_mail_and_calendar_are_missing(day, monkeypatch):
    from hub import config
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {OWNER, 'sam@example.com'})        # two logins, and nobody said whose is whose
    found = seen()
    assert [n['key'] for n in found['needs']] == ['kitchen-cart'] and found['today'] == []
    assert found['quiet'] == [{'text': 'Hub doesn’t know whose mail and calendar are yours yet, so they aren’t shown. The owner '
                                       'sets this in Hub’s settings file.', 'open': None}]


def test_a_tick_handles_an_email_or_hides_an_item_and_can_be_put_back(day):
    assert client.post('/app/api/today/tick', json={'key': 'mail:m2'}, headers=SIGNED_IN).json() == {'ok': True, 'said': 'Marked as handled', 'key': 'mail:m2'}
    assert [n['key'] for n in seen()['needs']] == ['kitchen-cart', 'mail:m1']
    assert client.post('/app/api/today/tick', json={'key': 'kitchen-cart'}, headers=SIGNED_IN).json()['said'] == 'Hidden for everyone until tomorrow'
    assert [n['key'] for n in seen()['needs']] == ['mail:m1']
    for key in ('mail:m2', 'kitchen-cart'):
        assert client.post('/app/api/today/tick', json={'key': key, 'back': True}, headers=SIGNED_IN).json() == {'ok': True, 'said': 'Put back'}
    assert [n['key'] for n in seen()['needs']] == ['mail:m2', 'kitchen-cart', 'mail:m1']
    assert client.post('/app/api/today/tick', json={'key': 'mail:gone'}, headers=SIGNED_IN).status_code == 409
    # Only a signed-in device may tick; a page on another site may not ask at all.
    assert client.post('/app/api/today/tick', json={'key': 'mail:m1'}, headers={'origin': 'http://testserver'}).status_code == 403
    assert client.post('/app/api/today/tick', json={'key': 'mail:m1'}, headers={**SIGNED_IN, 'origin': 'https://elsewhere.example'}).status_code == 403
    assert len(seen()['needs']) == 3


def test_more_than_three_emails_are_counted_and_the_list_stops_at_five(day):
    day.mail += [{'id': f'm{n}', 'who': f'Sender {n}', 'step': 'Reply.', 'subject': '', 'days': 0} for n in range(3, 7)]
    found = seen()
    assert [n['key'] for n in found['needs']] == ['mail:m2', 'kitchen-cart', 'mail:m1', 'mail:m3']
    assert {'text': '3 more emails need you.', 'open': {'href': '/gmail', 'label': 'Mail'}} in found['quiet']
    day.faults = [('calendar', 'Calendar needs signing in again', 'Run the sign-in step.', '/calendar'),
                  ('network', 'The network is down', '', '/network'), ('gmail', 'Mail can’t be read', '', '/status')]
    found = seen()
    assert [n['title'] for n in found['needs']][:3] == ['Calendar needs signing in again', 'The network is down', 'Mail can’t be read']
    assert len(found['needs']) == 5 and found['more'] == 2                       # faults first; the rest can wait


def test_a_source_that_cant_be_read_is_left_out_not_fatal(day, monkeypatch):
    from hub.tools.gmail import triage
    from hub.tools.trip import store as trips
    from preschool import publication
    day.calendar_down = True
    monkeypatch.setattr(publication, 'latest', lambda: 1 / 0)
    monkeypatch.setattr(trips, 'load', lambda: 1 / 0)
    monkeypatch.setattr(triage, 'picture', lambda now=None: 1 / 0)
    found = seen()
    assert found['ok'] and found['today'] == found['tomorrow'] == found['school'] == [] and found['trip'] is None
    assert [n['title'] for n in found['needs']] == ['Approve the grocery cart', '2 emails need you']      # Hub's own count stands in


def test_a_quiet_day_is_empty_everywhere(day):
    day.events, day.school, day.mail, day.filed, day.cart = [], [], [], 0, False
    day.trip['start'], day.trip['end'] = '2031-01-01', '2031-01-09'
    found = seen()
    assert (found['needs'], found['today'], found['tomorrow'], found['school'], found['trip'], found['quiet']) == ([], [], [], [], None, [])
    assert screen.picture(day_world.NOW, login=OWNER)['more'] == 0
