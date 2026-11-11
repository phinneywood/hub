"""Home dashboard: live tiles are bounded, read-only and fail soft. Synthetic data only."""
import time
from datetime import date, datetime

from fastapi.testclient import TestClient

from hub import home
from hub.main import app

TOOLS = [{'slug': 'gmail', 'name': 'Gmail'}, {'slug': 'preschool', 'name': 'Preschool'},
         {'slug': 'network', 'name': 'Network'}, {'slug': 'mobility', 'name': 'Bike + bus'},
         {'slug': 'trip', 'name': 'Trip'}]


def test_home_renders_one_tile_per_tool_even_when_sources_fail(monkeypatch):
    def broken():
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr(home, 'SUMMARIES', {**home.SUMMARIES, 'gmail': broken, 'network': broken})
    page = TestClient(app).get('/')
    assert page.status_code == 200
    assert page.text.count('class="tile ') == 8
    assert home.UNAVAILABLE in page.text
    assert 'aria-current="page"' in page.text  # brand marks home as current


def test_slow_source_does_not_block_the_page(monkeypatch):
    def slow():
        time.sleep(1.0)
        return home.tile('gmail', 'ok', 'late')
    monkeypatch.setattr(home, 'DEADLINE_SECONDS', 0.05)
    monkeypatch.setattr(home, 'SUMMARIES', {'gmail': slow})
    started = time.monotonic()
    tiles = home.tiles(TOOLS[:1])
    assert time.monotonic() - started < 0.5
    assert tiles[0]['headline'] == 'Still loading…'


def test_tool_without_summary_falls_back_to_description(monkeypatch):
    monkeypatch.setattr(home, 'SUMMARIES', {})
    [only] = home.tiles([{'slug': 'other', 'name': 'Other', 'description': 'Synthetic tool'}])
    assert only['headline'] == 'Other' and only['detail'] == 'Synthetic tool' and only['tone'] == 'idle'


def test_preschool_tile_shows_next_reviewed_dates(monkeypatch):
    from preschool import publication
    monkeypatch.setattr(home, 'today', lambda: date(2026, 1, 5))
    events = [
        {'date': '2026-01-02', 'title': 'Past event', 'start_time': None, 'location': '', 'status': 'confirmed'},
        {'date': '2026-01-05', 'title': 'Parent meeting', 'start_time': '19:00', 'location': 'Room A', 'status': 'confirmed'},
        {'date': '2026-01-06', 'title': 'Class', 'start_time': '09:30', 'location': '', 'status': 'confirmed'},
        {'date': '2026-01-20', 'title': 'Holiday class', 'start_time': None, 'location': '', 'status': 'cancelled'},
    ]
    monkeypatch.setattr(publication, 'latest', lambda: {'payload': {'events': events}})
    result = home.preschool()
    assert result['headline'] == 'Parent meeting'
    assert result['detail'] == 'Today · 7 PM · Room A'
    assert result['tone'] == 'ok'
    assert [line['note'] for line in result['lines']] == ['Tomorrow · 9:30 AM', 'Tue, Jan 20 · cancelled']
    assert result['lines'][1]['struck']


def test_preschool_tile_without_edition(monkeypatch):
    from preschool import publication
    monkeypatch.setattr(publication, 'latest', lambda: None)
    assert home.preschool()['href'] == '/preschool/manage'


def test_trip_tile_counts_down_and_flags_unbooked_flights(monkeypatch):
    from hub.tools.trip import store
    monkeypatch.setattr(store, 'load', lambda: {'title': 'Synthetic City · Wedding', 'start': '2027-05-22',
                                                'end': '2027-05-29', 'selected_flight': None})
    monkeypatch.setattr(home, 'today', lambda: date(2027, 5, 1))
    result = home.trip()
    assert result['headline'] == 'Synthetic City'
    assert result['detail'] == 'In 21 days · May 22–29'
    assert result['lines'][0]['label'] == 'Choose flights'
    monkeypatch.setattr(home, 'today', lambda: date(2027, 5, 24))
    assert home.trip()['detail'] == 'Day 3 of 8'


def test_day_and_clock_labels():
    base = date(2026, 3, 2)  # Monday
    assert home.day_label('2026-03-02', base) == 'Today'
    assert home.day_label('2026-03-03', base) == 'Tomorrow'
    assert home.day_label('2026-03-06', base) == 'Friday'
    assert home.day_label('2026-03-09', base) == 'Mon, Mar 9'
    assert home.clock_label('09:30') == '9:30 AM' and home.clock_label('19:00') == '7 PM'
    assert home.clock_label('bad') == '' and home.clock_label(None) == ''


def test_greeting_by_time_of_day():
    assert home.greeting(datetime(2026, 3, 2, 8))[0] == 'Good morning'
    assert home.greeting(datetime(2026, 3, 2, 13))[0] == 'Good afternoon'
    assert home.greeting(datetime(2026, 3, 2, 20)) == ('Good evening', 'Monday, March 2')


def test_shell_shows_refresh_only_where_gmail_data_is_used():
    client = TestClient(app)
    assert 'refresh=1' not in client.get('/trip').text
    assert 'refresh=1' in client.get('/').text


def _network_fixture(monkeypatch, reach_age, latency_age, reach_state='on'):
    from datetime import timedelta, timezone
    from hub.tools.network import analytics as a, ha, pages, store
    now = datetime.now(timezone.utc)
    def stamp(age):
        return (now - timedelta(seconds=age)).isoformat()
    sources = {'reachability': {'kind': 'reachability', 'state': reach_state, 'last_reported': stamp(reach_age)},
               'latency': {'kind': 'latency', 'state': '11.2', 'unit': 'ms', 'last_reported': stamp(latency_age)}}
    monkeypatch.setattr(ha, 'configured', lambda: True)
    monkeypatch.setattr(ha, 'states', lambda: [])
    monkeypatch.setattr(pages, '_device_rows', lambda states: ([], []))
    monkeypatch.setattr(a, 'select_sources', lambda states, metrics, choices: (sources, []))
    monkeypatch.setattr(store, 'get', lambda key, default=None: default)


def test_network_fresh_ping_proves_reachability_when_steady_sensor_ages(monkeypatch):
    _network_fixture(monkeypatch, reach_age=300_000, latency_age=20)
    result = home.network()
    assert result['headline'] == 'Internet is up' and result['tone'] == 'ok'
    assert result['detail'] == '11 ms ping'


def test_network_stale_without_fresh_ping(monkeypatch):
    _network_fixture(monkeypatch, reach_age=300_000, latency_age=300_000)
    assert home.network()['headline'] == 'Readings are stale'


def test_network_reported_down_wins_over_ping(monkeypatch):
    _network_fixture(monkeypatch, reach_age=10, latency_age=10, reach_state='off')
    assert home.network()['tone'] == 'bad'


def test_gmail_tile_says_how_old_its_numbers_are(monkeypatch):
    import time
    from hub import bridge
    from hub.tools.gmail import common
    monkeypatch.setattr(bridge, '_request', lambda *a, **k: {
        'label_counts': [{'id': 'INBOX', 'messagesTotal': 10, 'messagesUnread': 2}], 'senders': []})
    monkeypatch.setattr(common, 'overview_fetched_at', lambda: time.time() - 600)
    from hub.tools.gmail import missions
    monkeypatch.setattr(missions, 'is_paused', lambda: False)
    result = home.gmail()
    assert result['as_of'] == 'Updated 10 min ago'
