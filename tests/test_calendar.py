"""Calendar: read-only week view, overlaps, tile, item and evidence. Synthetic events only."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from hub import bridge, home, today
from hub.engine import catalog
from hub.main import app
from hub.tools.calendar import read

NOW = datetime(2026, 3, 4, 9, 0, tzinfo=home.PACIFIC)  # a Wednesday


def _at(days, hour, minute=0):
    return (NOW + timedelta(days=days)).replace(hour=hour, minute=minute).isoformat()


def ev(title, start, end, **extra):
    return {'summary': title, 'start': start, 'end': end, **extra}


EVENTS = [
    ev('Synthetic standup', {'dateTime': _at(0, 10)}, {'dateTime': _at(0, 11)}),
    ev('Synthetic dentist', {'dateTime': _at(0, 10, 30)}, {'dateTime': _at(0, 11, 30)}, location='Clinic'),
    ev('Free block', {'dateTime': _at(0, 10)}, {'dateTime': _at(0, 12)}, transparency='transparent'),
    ev('Cancelled thing', {'dateTime': _at(0, 10)}, {'dateTime': _at(0, 12)}, status='cancelled'),
    ev('School closed', {'date': '2026-03-05'}, {'date': '2026-03-07'}),
    ev('Later meeting', {'dateTime': _at(3, 15)}, {'dateTime': _at(3, 16)}),
]


@pytest.fixture
def helper(monkeypatch):
    seen = {}
    def fake(path, ttl=60, **params):
        seen.update(path=path, **params)
        if seen.get('fail'):
            raise seen['fail']
        return {'events': EVENTS}
    monkeypatch.setattr(bridge, 'get', fake)
    return seen


def test_week_reads_only_the_event_list_and_skips_cancelled(helper):
    events = read.week(NOW)
    assert helper['path'] == '/calendar/events' and helper['time_zone'] == 'America/Los_Angeles'
    assert helper['start'].startswith('2026-03-04T00:00:00') and helper['end'].startswith('2026-03-11T00:00:00')
    assert [e['title'] for e in events] == ['Synthetic standup', 'Synthetic dentist', 'Free block', 'School closed', 'Later meeting']
    closed = events[3]
    assert closed['all_day'] and str(closed['day']) == '2026-03-05' and str(closed['last_day']) == '2026-03-06'
    assert '/calendar/event' not in bridge.READ_PATHS and not any(p.startswith('/calendar') for p in bridge.WRITE_PATHS)


def test_overlaps_ignore_free_and_all_day_events(helper):
    events = read.week(NOW)
    pairs = read.overlaps(events, {NOW.date()})
    assert [(a['title'], b['title']) for a, b in pairs] == [('Synthetic standup', 'Synthetic dentist')]
    assert read.overlaps(events, {NOW.date() + timedelta(days=1)}) == []


def test_tile_counts_today_flags_overlap_and_reports_a_lapsed_sign_in(helper, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(home, 'datetime', Clock)
    tile = home.calendar()
    assert tile['tone'] == 'warn' and tile['headline'] == '3 today' and tile['detail'] == 'Two events overlap'
    assert [l['label'] for l in tile['lines']] == ['Synthetic standup', 'Synthetic dentist']
    helper['fail'] = bridge.BridgeError('Google connector request failed; authorization may need renewal', 502)
    tile = home.calendar()
    assert tile['tone'] == 'bad' and 'signing in' in tile['headline']
    assert today.from_tiles([tile])[0]['title'] == 'Calendar needs signing in again'
    helper['fail'] = bridge.BridgeError('Gmail bridge is unreachable')
    with pytest.raises(bridge.BridgeError):
        home.calendar()


def test_overlap_today_or_tomorrow_becomes_an_item(helper, monkeypatch):
    events = read.week(NOW)
    monkeypatch.setattr(read, 'week', lambda now=None: events)
    monkeypatch.setattr(home, 'today', lambda: NOW.date())
    [item] = today.calendar()
    assert item['tone'] == 'warn' and item['href'] == '/calendar' and 'Synthetic standup and Synthetic dentist' in item['why']
    monkeypatch.setattr(read, 'week', lambda now=None: (_ for _ in ()).throw(read.SignInNeeded()))
    assert today.calendar() == []


def test_evidence_sends_titles_times_and_places_only(helper, monkeypatch):
    events = read.week(NOW)
    monkeypatch.setattr(read, 'week', lambda now=None: events)
    found = catalog.calendar_evidence()
    first = found['next_7_days'][1]
    assert first == {'date': '2026-03-04', 'all_day': False, 'start': '10:30', 'end': '11:30', 'title': 'Synthetic dentist',
                     'location': 'Clinic', 'busy': True}
    assert catalog.source('calendar').evidence and 'calendar' in catalog.PAGES


def test_page_lists_the_week_and_explains_a_lapsed_sign_in(helper):
    page = TestClient(app).get('/calendar')
    assert page.status_code == 200 and 'The next seven days' in page.text
    helper['fail'] = bridge.BridgeError('authorization may need renewal', 401)
    assert 'phone-setup --calendar-auth' in TestClient(app).get('/calendar').text
    helper['fail'] = bridge.BridgeError('Gmail bridge is unreachable')
    assert 'isn’t answering' in TestClient(app).get('/calendar').text
