"""Home “Needs you” list: read-only, fail-soft recommendations. Synthetic data only."""
from datetime import date

from fastapi.testclient import TestClient

from hub import delayed, home, store, today
from hub.main import app


def _only(monkeypatch, *sources):
    from hub.engine import catalog
    from hub.engine.registry import Source
    monkeypatch.setattr(catalog, 'SOURCES', [Source('synthetic', 'Synthetic', '', '', items=tuple(sources))])
    monkeypatch.setattr(home, 'SUMMARIES', {})


def test_stopped_rules_offer_one_tap_only_when_every_rule_is_safe(monkeypatch):
    monkeypatch.setattr(delayed, 'health', lambda: {'on': 0, 'stopped': 5, 'safe_to_resume': 5})
    [item] = today.gmail_rules()
    assert item['title'] == '5 archive-after-30-days rules stopped' and item['action'] == 'gmail.resume_rules'
    monkeypatch.setattr(delayed, 'health', lambda: {'on': 0, 'stopped': 2, 'safe_to_resume': 1})
    [item] = today.gmail_rules()
    assert item['action'] is None and item['href'] == '/gmail/delayed'
    monkeypatch.setattr(delayed, 'health', lambda: {'on': 3, 'stopped': 0, 'safe_to_resume': 0})
    assert today.gmail_rules() == []


def test_planned_changes_point_at_the_plan_or_at_status_when_paused(monkeypatch):
    from hub.tools.gmail import missions
    monkeypatch.setattr(store, 'plan_count', lambda: 2)
    monkeypatch.setattr(missions, 'is_paused', lambda: False)
    [item] = today.gmail_plan()
    assert item['href'] == '/gmail/plan' and item['post'] is None and item['title'] == '2 Gmail changes ready to apply'
    monkeypatch.setattr(missions, 'is_paused', lambda: True)
    assert today.gmail_plan()[0]['href'] == '/status'
    monkeypatch.setattr(store, 'plan_count', lambda: 0)
    assert today.gmail_plan() == []


def test_preschool_lists_today_and_tomorrow_only(monkeypatch):
    from preschool import publication
    monkeypatch.setattr(home, 'today', lambda: date(2026, 1, 5))
    events = [
        {'date': '2026-01-05', 'title': 'Parent meeting', 'start_time': '19:00', 'location': 'Room A', 'status': 'confirmed'},
        {'date': '2026-01-06', 'title': 'Class', 'start_time': '09:30', 'location': '', 'status': 'cancelled'},
        {'date': '2026-01-20', 'title': 'Later', 'start_time': None, 'location': '', 'status': 'confirmed'},
    ]
    monkeypatch.setattr(publication, 'latest', lambda: {'payload': {'events': events}})
    first, second = today.preschool()
    assert (first['title'], first['why']) == ('Parent meeting', 'Today · 7 PM · Room A')
    assert (second['title'], second['tone']) == ('Cancelled: Class', 'warn')
    monkeypatch.setattr(publication, 'latest', lambda: None)
    assert today.preschool() == []


def test_trip_flights_only_inside_the_horizon(monkeypatch):
    from hub.tools.trip import store as trips
    monkeypatch.setattr(trips, 'load', lambda: {'start': '2031-10-04', 'end': '2031-10-11', 'selected_flight': None})
    monkeypatch.setattr(home, 'today', lambda: date(2031, 5, 1))
    assert today.trip() == []
    monkeypatch.setattr(home, 'today', lambda: date(2031, 9, 13))
    assert today.trip()[0]['why'] == 'The trip is in 21 days and nothing is selected.'


def test_list_is_ranked_capped_and_survives_a_broken_source(monkeypatch):
    def broken():
        raise RuntimeError('synthetic failure')
    _only(monkeypatch, broken, lambda: [today.rec(f'k{i}', 'info', f'Item {i}', '', href='/x', cta='Open') for i in range(6)])
    tiles = [{'slug': 'network', 'tone': 'bad', 'headline': 'Internet looks down', 'detail': '', 'href': '/network', 'cta': 'Details'}]
    items = today.needs_you(tiles)
    assert len(items) == today.MAX_ITEMS and items[0]['title'] == 'Internet looks down'
    assert today.brief(items).splitlines()[0] == '5 things for today:'
    assert today.brief([]) == 'Nothing needs you today.'


def test_home_shows_actions_and_not_today_hides_until_tomorrow(monkeypatch):
    _only(monkeypatch, lambda: [today.rec('a', 'warn', 'Synthetic rule stopped', 'Safe.', href='/gmail/delayed',
                                          cta='Turn back on', action='gmail.resume_rules'),
                                today.rec('b', 'info', 'Synthetic plan', '', href='/gmail/plan', cta='Review and apply')])
    client = TestClient(app)
    page = client.get('/today').text
    assert 'action="/gmail/delayed/resume-safe"' in page and 'href="/gmail/plan"' in page
    assert client.get('/today/brief').text == '2 things for today:\n• Synthetic rule stopped\n• Synthetic plan'
    after = client.post('/today/snooze', data={'key': 'a'}, headers={'origin': 'http://testserver'}).text
    assert 'Synthetic rule stopped' not in after and 'Synthetic plan' in after
    monkeypatch.setattr(home, 'today', lambda: date(2099, 1, 1))
    assert 'Synthetic rule stopped' in client.get('/today').text


def test_home_says_so_when_nothing_needs_you(monkeypatch):
    _only(monkeypatch)
    assert 'Nothing needs you right now.' in TestClient(app).get('/today').text
