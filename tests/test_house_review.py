"""The weekly house review (hub/engine/house_review.py): rules over the house activity Hub keeps, at most three ideas
a week, suggestions only. The house, its people and its week are made up; nothing real is reached."""
import json
import time
from datetime import datetime, timedelta

import pytest

from hub import config, home, people, store, today
from hub.app import activity
from hub.app.ask import brain, morning
from hub.engine import catalog, house_review as review
from hub.tools.house import broker

from . import app_world

ME, PARTNER = 'me@example.com', 'casey@example.com'
SUNDAY = datetime(2026, 11, 22, 8, 0, tzinfo=home.PACIFIC)      # the review's morning
NOW = SUNDAY.timestamp()
REGISTRY = {'entities': {'light.floor_lamp': {'entity_id': 'light.floor_lamp', 'device_id': 'dev-lamp'},
                         'sensor.floor_lamp_signal': {'entity_id': 'sensor.floor_lamp_signal', 'device_id': 'dev-lamp'},
                         'media_player.den': {'entity_id': 'media_player.den', 'device_id': 'dev-den'},
                         'automation.old_reminder': {'entity_id': 'automation.old_reminder', 'created_at': NOW - 90 * 86400},
                         'automation.new_rule': {'entity_id': 'automation.new_rule', 'created_at': NOW - 2 * 86400}},
            'device_name': {'dev-lamp': 'Floor lamp', 'dev-den': 'Den speaker'}, 'device_room': {'dev-lamp': 'living'},
            'rooms': {'living': 'Living room'}}


def at(days_ago, hour, minute=0, second=0):
    """A moment in the week before SUNDAY, Pacific time."""
    return (SUNDAY - timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=second).timestamp()


def row(ts, entity, what, cause='', name='', room='', kind=None):
    domain = entity.partition('.')[0]
    kind = kind or {'light': 'lights', 'switch': 'lights', 'event': 'buttons', 'automation': 'routines'}.get(domain, 'other')
    return {'key': f'ha|{ts:.3f}|{entity}|{what}', 'ts': ts, 'source': 'ha', 'entity': entity,
            'name': name or entity.partition('.')[2].replace('_', ' ').capitalize(), 'room': room, 'kind': kind,
            'what': what, 'cause': cause}


def read_logbook(ts, error=None):
    """Activity's reading of the logbook, as it records it: up to `ts`, with an error or none."""
    with activity._db() as conn:
        activity._keep(conn, 'ha', [], ts, error)


def keep(rows):
    with activity._db() as conn:
        conn.executemany('insert or ignore into house_activity(key, ts, source, entity, name, room, kind, what, cause) '
                         'values (:key, :ts, :source, :entity, :name, :room, :kind, :what, :cause)', rows)


def automation(entity, name, state='on', **attrs):
    return {'entity_id': entity, 'state': state, 'attributes': {'friendly_name': name, **attrs}}


STATES = [automation('automation.evening', 'Evening lights')]


def undone_week():
    """“Evening lights” switches the kitchen ceiling off at 9 PM; on three days someone switches it back on soon after:
    once by the wall switch, once from Hub, once with a button. A fourth day it is put back only after half an hour."""
    rows = []
    for days, back, cause, button in ((1, 4, '', False), (2, 6, 'Alex', False), (3, 3, 'Living room button', True),
                                      (4, 31, '', False)):
        rows.append(row(at(days, 21), 'light.kitchen_ceiling', 'off', 'Evening lights', 'Ceiling', 'Kitchen'))
        if button:
            rows.append(row(at(days, 21, back) - 2, 'event.living_button', 'pressed'))
        rows.append(row(at(days, 21, back), 'light.kitchen_ceiling', 'on', cause, 'Ceiling', 'Kitchen'))
    return rows


def test_lights_an_automation_switched_and_people_switched_back_are_one_idea():
    keep(undone_week())
    found = review.review(NOW, REGISTRY, STATES)
    assert [f['kind'] for f in found] == ['undone']
    assert found[0]['title'] == '“Evening lights” keeps getting undone'
    assert found[0]['why'] == ('On 3 of the last 7 days, Ceiling (Kitchen) was switched back on within 15 minutes of '
                               '“Evening lights” switching it off, by hand or from outside Home Assistant.')
    assert found[0]['idea'] == 'Maybe it runs at the wrong time, or shouldn’t touch that light.'
    assert found[0]['keys'] == ['undone:evening lights'] and found[0]['href'] == '/app/#activity'
    assert review.review(NOW, REGISTRY, STATES, never={'undone:evening lights'}) == []


def test_one_day_of_being_undone_is_not_a_pattern():
    keep([r for r in undone_week() if r['ts'] > at(1, 0)])          # only the last day
    assert review.review(NOW, REGISTRY, STATES) == []


def test_a_motion_light_switched_off_now_and_then_isnt_undone():
    rows = []
    for days in range(1, 8):
        for hour in range(8, 22):           # 14 runs a day of the motion rule
            rows.append(row(at(days, hour), 'light.hall', 'on', 'Hall motion', 'Hall light', 'Hall'))
            rows.append(row(at(days, hour, 5), 'light.hall', 'off', 'Hall motion', 'Hall light', 'Hall'))
        rows.append(row(at(days, 21, 6), 'light.hall', 'on', '', 'Hall light', 'Hall'))      # someone wants it on
    keep(rows)
    assert [f for f in review.review(NOW, REGISTRY, STATES) if f['kind'] == 'undone'] == []


def test_hubs_own_checks_are_not_the_household():
    rows, hub = [], []
    for days in range(1, 8):
        rows += [row(at(days, 6), 'light.laundry_wall', 'on', 'Alex', 'Wall lights', 'Laundry'),
                 row(at(days, 6, 0, 8), 'light.laundry_wall', 'off', 'Alex', 'Wall lights', 'Laundry')]
        hub += [('light.laundry_wall', at(days, 6) - 1, 'check'), ('light.laundry_wall', at(days, 6, 0, 8) - 1, 'check')]
    keep(rows)
    assert review.review(NOW, REGISTRY, STATES, hub=hub) == []
    assert [f['kind'] for f in review.review(NOW, REGISTRY, STATES, hub=[])] == ['by_hand', 'by_hand']     # on, and off


def test_lights_switched_by_hand_at_the_same_time_most_days_but_not_with_a_button():
    rows = []
    for days, minute in ((1, 5), (2, 20), (3, 12), (4, 31), (6, 15)):
        rows.append(row(at(days, 7, minute), 'light.porch_light', 'on', name='Porch light'))
        rows.append(row(at(days, 7, minute + 1), 'switch.coffee_maker', 'on', 'Alex', 'Coffee maker', 'Kitchen'))
        rows.append(row(at(days, 22, minute), 'event.living_button', 'pressed'))      # the button's own scene: by design
        rows.append(row(at(days, 22, minute, 1), 'light.living_floor_lamp', 'off', 'Living room button', 'Floor lamp'))
    rows.append(row(at(5, 18), 'light.porch_light', 'on', name='Porch light'))         # an evening isn't the morning habit
    keep(rows)
    found = review.review(NOW, REGISTRY, STATES)
    assert [f['kind'] for f in found] == ['by_hand']
    assert found[0]['title'] == 'Porch light and 1 more switched on by hand most days'
    assert found[0]['why'] == ('On 5 of the last 7 days, Porch light and Coffee maker (Kitchen) were switched on around '
                               '7:15 AM, by hand or from outside Home Assistant, never by an automation.')
    assert found[0]['idea'] == 'If that’s a routine, an automation could do it for you.'
    assert found[0]['keys'] == ['by_hand:on:light.porch_light', 'by_hand:on:switch.coffee_maker']
    # Waved away for good one light at a time: the other still makes an idea of its own.
    (left,) = review.review(NOW, REGISTRY, STATES, never={'by_hand:on:light.porch_light'})
    assert left['title'] == 'Coffee maker (Kitchen) switched on by hand most days'


def test_a_light_switched_all_day_after_motion_or_also_by_an_automation_is_no_habit():
    rows = []
    for days in range(1, 8):
        for hour in (7, 10, 13, 16, 19):            # all day, at no one hour: a busy bathroom
            rows.append(row(at(days, hour, days * 3), 'light.bath', 'on', name='Bath light'))
        rows.append(row(at(days, 6, 30), 'binary_sensor.stairs_motion', 'motion', kind='motion', room='Stairs'))
        rows.append(row(at(days, 6, 30, 20), 'light.stairs', 'on', name='Stairs light', room='Stairs'))     # a Hue motion rule
        cause = 'Sunset' if days in (2, 4) else ''                  # some evenings the automation does it
        rows.append(row(at(days, 17, 10), 'light.porch_light', 'on', cause, 'Porch light'))
    keep(rows)
    assert review.review(NOW, REGISTRY, STATES + [automation('automation.sunset', 'Sunset')]) == []


def test_a_light_switched_just_before_midnight_or_just_after_is_one_habit():
    keep([row(at(days, 0, 5) if days % 2 else at(days, 23, 50), 'light.porch_light', 'off', name='Porch light')
          for days in range(1, 7)])
    (found,) = review.review(NOW, REGISTRY, STATES)
    assert found['why'].startswith('On 6 of the last 7 days, Porch light was switched off around 12 AM')


def test_a_device_that_keeps_dropping_off_counts_once_however_many_parts_it_has():
    rows = []
    for days in (1, 3, 5):
        for entity in ('light.floor_lamp', 'sensor.floor_lamp_signal'):
            rows.append(row(at(days, 12), entity, 'went offline'))
            rows.append(row(at(days, 12, 40), entity, 'off' if entity.startswith('light') else '-60'))
        rows.append(row(at(days, 1), 'media_player.den', 'went offline'))             # a speaker asleep isn't dropping off
    rows.append(row(at(0, 6), 'light.floor_lamp', 'went offline'))                    # this morning, and still off
    keep(rows)
    states = STATES + [{'entity_id': 'light.floor_lamp', 'state': 'unavailable', 'attributes': {}}]
    found = review.review(NOW, REGISTRY, states)
    assert [f['kind'] for f in found] == ['offline']
    assert found[0]['title'] == 'Floor lamp (Living room) keeps dropping off'
    assert found[0]['why'] == 'It went offline 3 times in the last 7 days, about 2 hours in all, and it’s offline now.'
    assert found[0]['idea'].startswith('If it’s switched off at the wall')
    assert found[0]['keys'] == ['offline:dev-lamp']


def test_many_devices_dropping_at_once_is_a_restart_not_a_device():
    rows = []
    for days in (1, 3, 5):
        for n in range(5):
            rows.append(row(at(days, 4, 0, n * 5), f'light.lamp_{n}', 'went offline'))
            rows.append(row(at(days, 4, 2), f'light.lamp_{n}', 'on'))
    keep(rows)
    registry = {**REGISTRY, 'entities': {**REGISTRY['entities'], **{
        f'light.lamp_{n}': {'entity_id': f'light.lamp_{n}', 'device_id': f'dev-{n}'} for n in range(5)}}}
    assert review.review(NOW, registry, STATES) == []


def test_broken_quiet_and_runaway_automations():
    rows = [row(at(days, 18), 'automation.hall_motion', 'ran', name='Hall motion') for days in range(8, 30)]
    rows += [row(at(3, 19, minute // 2, minute % 2 * 30), 'automation.hall_motion', 'ran', name='Hall motion')
             for minute in range(40)]
    keep(rows)
    states = STATES + [automation('automation.garage_check', 'Garage check', 'unavailable'),
                       automation('automation.old_reminder', 'Old reminder'),
                       automation('automation.leak_alert', 'Leak alert', last_triggered=(SUNDAY - timedelta(days=45)).isoformat()),
                       automation('automation.new_rule', 'New rule'),
                       automation('automation.switched_off', 'Switched off', 'off'),
                       automation('automation.daily', 'Daily', last_triggered=(SUNDAY - timedelta(days=1)).isoformat())]
    found = review.review(NOW, REGISTRY, states)
    assert [f['title'] for f in found] == ['Home Assistant can’t run “Garage check”', '“Hall motion” ran 40 times in one hour',
                                           '2 automations haven’t run in 30 days']
    assert found[1]['why'] == 'That was on Thursday from 7 PM; on other days its busiest hour has about 1.'
    assert found[2]['why'] == 'They are switched on but haven’t run: “Leak alert” and “Old reminder”.'
    assert found[2]['keys'] == ['idle:automation.leak_alert', 'idle:automation.old_reminder']
    # Waved away for good: the rest of the list stays, one automation fewer.
    found = review.review(NOW, REGISTRY, states, never={'broken:automation.garage_check', 'idle:automation.leak_alert'})
    assert [f['title'] for f in found] == ['“Hall motion” ran 40 times in one hour', '“Old reminder” hasn’t run in 30 days']
    assert found[1]['why'] == 'It’s switched on, but Home Assistant has no run of it.'
    # Mentioned lately: not again for four weeks.
    found = review.review(NOW, REGISTRY, states, recent={'idle:automation.old_reminder'})
    assert found[-1]['title'] == '“Leak alert” hasn’t run in 30 days'
    assert found[-1]['why'] == 'It’s switched on, but Home Assistant has no run of it since October 8.'


def test_at_most_three_ideas_one_of_each_kind_first():
    rows = undone_week()
    for days in (1, 2, 3, 4):
        rows.append(row(at(days, 7, 10), 'light.porch_light', 'on', name='Porch light'))
    for days in (1, 3, 5):
        rows.append(row(at(days, 12), 'light.floor_lamp', 'went offline'))
        rows.append(row(at(days, 13), 'light.floor_lamp', 'on'))
    keep(rows)
    states = STATES + [automation('automation.garage_check', 'Garage check', 'unavailable'),
                       automation('automation.attic_fan', 'Attic fan', 'unavailable')]
    found = review.review(NOW, REGISTRY, states)
    assert [f['kind'] for f in found] == ['broken', 'undone', 'offline']


@pytest.fixture
def house(monkeypatch):
    world = app_world.install(monkeypatch)
    monkeypatch.setattr(config, 'ALLOWED_LOGINS', {ME, PARTNER})
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'people': [
        {'name': 'Alex', 'logins': [ME], 'accounts': [people.DEFAULT]}, {'name': 'Casey', 'logins': [PARTNER], 'accounts': []}]}))
    world.states['automation.evening'] = automation('automation.evening', 'Evening lights')
    monkeypatch.setattr(activity.apple, 'setup', lambda: {'logging': False, 'home': 'home-1'})
    monkeypatch.setattr(activity.apple, 'events', lambda since: [])
    return world


def test_the_morning_job_makes_the_weeks_review_once_and_it_reaches_today_and_ask(house):
    now = time.time()
    house.states['automation.old_reminder'] = automation(
        'automation.old_reminder', 'Old reminder', last_triggered=datetime.fromtimestamp(now - 40 * 86400).isoformat())
    for days in (1, 2, 3):
        moment = now - days * 86400 - 3600
        house.logbook += [{'when': moment, 'entity_id': 'light.kitchen_ceiling', 'state': 'off',
                           'context_event_type': 'automation_triggered', 'context_domain': 'automation',
                           'context_name': 'Evening lights', 'context_entity_id': 'automation.evening'},
                          {'when': moment + 240, 'entity_id': 'light.kitchen_ceiling', 'state': 'on'}]
    assert 'house_review' in [s.slug for s in catalog.SOURCES if s.prepare]
    catalog.house_review_prepare()
    made = review.ideas()
    assert [i['kind'] for i in made] == ['undone', 'idle']
    assert review.prepare() is None                                  # once a week
    items = catalog.house_review_items()
    assert [(i['key'], i['href'], i['cta']) for i in items] == [('idea-' + made[0]['id'], '/app/#activity', 'See activity'),
                                                                 ('idea-' + made[1]['id'], '/app/', 'Ask Hub')]
    assert items[1]['title'] == '“Old reminder” hasn’t run in 30 days'
    # The owner's: Today shows them only to the owner, and "Not this week" hides one for the week.
    assert today.account_of(items[0]['key']) == people.DEFAULT
    today.hide(items[0]['key'])
    assert items[0]['key'] in store.snoozed((home.today() + timedelta(days=6)).isoformat())
    assert 'gmail-next' not in store.snoozed(home.today().isoformat())
    # Ask's model is given them for the owner, never for anyone else.
    said = brain.ideas(ME)
    assert len(said) == 1 and said[0].startswith('This week’s house review (Hub read the house’s record;')
    assert '“Evening lights” keeps getting undone. On 3 of the last 7 days' in said[0] and '“Old reminder”' in said[0]
    assert brain.ideas(PARTNER) == []
    assert any(line == said[0] for line in brain.house(login=ME).split('\n'))
    assert 'house review' not in brain.house(login=PARTNER)


def test_ask_is_told_an_ideas_number_in_the_mornings_message(house, monkeypatch):
    keep(undone_week())
    monkeypatch.setattr(activity, 'collect', lambda: None)       # the week is made up: nothing more to read
    read_logbook(NOW)
    review.prepare(NOW)
    (idea,) = review.ideas()
    monkeypatch.setattr(morning, 'numbers', lambda login, now=None: {'idea-' + idea['id']: 2})
    assert brain.ideas(ME)[0].startswith('This week’s house review (Hub read the house’s record; these are only ideas, and '
                                         'nothing was changed): number 2 in this morning’s message: “Evening lights”')
    assert review.decide('idea-' + idea['id'], 'never') and not review.decide(idea['id'], 'done')
    assert review.ideas() == [] and brain.ideas(ME) == []


def test_no_review_while_home_assistant_is_away_and_the_next_morning_tries_again(house, monkeypatch):
    monkeypatch.setattr(activity, 'collect', lambda: None)
    house.down = True
    assert review.prepare(NOW) is None and review.ideas() == []
    house.down = False
    keep(undone_week())
    read_logbook(NOW + 86400, error='Home Assistant can’t be read.')
    assert review.prepare(NOW + 86400) is None                     # the logbook's last reading failed: not on part of a week
    read_logbook(NOW + 86400 - 2 * 3600)
    assert review.prepare(NOW + 86400) is None                     # nor on a reading two hours old
    read_logbook(NOW + 86400)
    assert [f['kind'] for f in review.prepare(NOW + 86400)] == ['undone']


def test_the_week_is_the_seven_whole_days_before_the_morning():
    start, end = review.window(NOW)
    assert (datetime.fromtimestamp(start, home.PACIFIC).isoformat(), datetime.fromtimestamp(end, home.PACIFIC).isoformat()) \
        == ('2026-11-15T00:00:00-08:00', '2026-11-22T00:00:00-08:00')
    spring = datetime(2027, 3, 21, 8, 0, tzinfo=home.PACIFIC).timestamp()       # a week with the clocks going forward
    start, end = review.window(spring)
    assert end - start == 7 * 86400 - 3600


def test_the_week_starts_on_sunday():
    for day, sunday in (('2026-11-22', '2026-11-22'), ('2026-11-23', '2026-11-22'), ('2026-11-28', '2026-11-22'),
                        ('2026-11-29', '2026-11-29')):
        assert review.week_of(datetime.fromisoformat(day).date()).isoformat() == sunday


def test_the_overnight_review_is_never_given_the_ideas(monkeypatch):
    found = [today.rec('idea-abc', 'info', 'An idea', 'Why.', href='/app/', cta='Ask Hub'),
             today.rec('house-lock', 'warn', 'A lock is low', 'Change it.', href='/house', cta='Open')]
    monkeypatch.setattr(today, '_checks', lambda: [(lambda: found, None)])
    assert [i['key'] for i in today.rule_items()] == ['house-lock']
