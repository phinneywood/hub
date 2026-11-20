"""The palette Hub picks for the day from what is going on (hub/app/mood.py): a celebration, time off, or an
ordinary day. Made-up calendars only."""
from datetime import date

from hub.app import mood

THURSDAY = date(2026, 11, 19)
SATURDAY = date(2026, 11, 21)
THANKSGIVING = date(2026, 11, 26)


def test_an_ordinary_weekday_is_dusk():
    assert mood.pick(THURSDAY, ['Dentist', 'Swim lesson'])['palette'] == 'dusk'


def test_a_birthday_on_the_calendar_is_clay_and_says_whose():
    found = mood.pick(THURSDAY, ['Swim lesson', "Robin's birthday"])
    assert found == {'palette': 'clay', 'name': 'Clay', 'why': "for Robin's birthday", 'calendar': True}


def test_guests_and_parties_are_clay_but_a_party_in_a_longer_word_is_not():
    assert mood.pick(THURSDAY, ['Guests for dinner'])['palette'] == 'clay'
    assert mood.pick(THURSDAY, ['Dinner party at ours'])['palette'] == 'clay'
    assert mood.pick(THURSDAY, ['Call the third-party vendor'])['palette'] == 'dusk'
    assert mood.pick(THURSDAY, ['Partying'])['palette'] == 'dusk'


def test_everyday_titles_that_only_mention_a_celebration_are_not_one():
    for title in ['Third party vendor call', 'Plumber: fix shower', 'Meteor shower', 'Renew web hosting', 'Guest lecture at work',
                  'Work anniversary', 'Order Thanksgiving turkey', 'Christmas shopping', 'Buy birthday present for Leo',
                  'Party of 6 at the restaurant', 'Celebration of life - Aunt May']:
        assert mood.pick(THURSDAY, [title])['palette'] == 'dusk', title


def test_class_dates_that_only_mention_a_holiday_are_school_days():
    for title in ['Holiday concert 10am', 'Holiday party in class', 'Registration closed', 'Closed-toe shoes day', 'Last day before winter break']:
        assert mood.pick(THURSDAY, school=[title])['palette'] == 'dusk', title
    assert mood.pick(THURSDAY, school=['Winter break'])['palette'] == 'evergreen'


def test_a_holiday_hub_knows_by_date_is_clay_without_a_calendar():
    assert mood.pick(THANKSGIVING) == {'palette': 'clay', 'name': 'Clay', 'why': 'for Thanksgiving'}
    assert mood.pick(date(2026, 12, 25))['why'] == 'for Christmas'


def test_time_off_is_evergreen():
    assert mood.pick(SATURDAY)['why'] == 'for the weekend'
    assert mood.pick(THURSDAY, trip=(date(2026, 11, 18), date(2026, 11, 22)))['why'] == 'while you’re away'
    assert mood.pick(THURSDAY, trip=(date(2027, 3, 1), date(2027, 3, 9)))['palette'] == 'dusk'
    assert mood.pick(THURSDAY, school=['No school: teacher work day'])['why'] == 'for a day without school'


def test_a_celebration_outranks_time_off():
    assert mood.pick(SATURDAY, ['Birthday party'])['palette'] == 'clay'


def test_a_long_title_is_cut_short_in_the_reason():
    found = mood.pick(THURSDAY, ['Birthday ' + 'x' * 80])
    assert len(found['why']) <= len('for ') + mood.LONGEST and found['why'].endswith('…')


def test_the_day_is_decided_once_and_someone_else_never_sees_the_event_name(monkeypatch):
    asked = []
    monkeypatch.setattr(mood, '_kept', {})
    monkeypatch.setattr(mood, '_titles', lambda day: asked.append(day) or ["Sam's birthday"])
    monkeypatch.setattr(mood, '_trip', lambda: None)
    monkeypatch.setattr(mood, '_school', lambda day: [])
    from datetime import datetime
    from hub import home
    noon = datetime(2026, 11, 19, 12, tzinfo=home.PACIFIC)
    assert mood.today(noon) == {'palette': 'clay', 'name': 'Clay', 'why': "for Sam's birthday"}
    assert mood.today(noon, theirs=False) == {'palette': 'clay', 'name': 'Clay', 'why': 'for a celebration today'}
    assert len(asked) == 1


def test_a_calendar_that_cannot_be_read_is_asked_again(monkeypatch):
    calls = []

    def fails(day):
        calls.append(day)
        raise RuntimeError('signed out')
    monkeypatch.setattr(mood, '_kept', {})
    monkeypatch.setattr(mood, '_titles', fails)
    monkeypatch.setattr(mood, '_trip', lambda: None)
    monkeypatch.setattr(mood, '_school', lambda day: [])
    from datetime import datetime
    from hub import home
    noon = datetime(2026, 11, 19, 12, tzinfo=home.PACIFIC)
    assert mood.today(noon)['palette'] == 'dusk'
    mood.today(noon)
    assert len(calls) == 2
