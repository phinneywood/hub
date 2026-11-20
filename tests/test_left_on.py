"""Left on (hub/engine/left_on.py): a fan, a light, a plug or a speaker on far longer than it usually is, said on Today
the same day. The house and its week are made up; nothing real is reached."""
from datetime import datetime, timedelta, timezone

from hub import home, store, today
from hub.app import activity
from hub.app.ask import morning
from hub.engine import catalog, left_on
from hub.tools.house import broker, read

NOW = datetime(2026, 11, 19, 14, 0, tzinfo=home.PACIFIC).timestamp()
HOUR = 3600
REGISTRY = {'entities': {'fan.bedroom': {'entity_id': 'fan.bedroom', 'area_id': 'bedroom'},
                         'light.closet': {'entity_id': 'light.closet', 'area_id': 'hall', 'name': 'Closet light'},
                         'switch.fridge': {'entity_id': 'switch.fridge', 'area_id': 'kitchen'},
                         'switch.motion_enabled': {'entity_id': 'switch.motion_enabled', 'entity_category': 'config'},
                         'media_player.study': {'entity_id': 'media_player.study', 'area_id': 'study'}},
            'rooms': {'bedroom': 'Bedroom', 'hall': 'Hall', 'kitchen': 'Kitchen', 'study': 'Study'},
            'device_room': {}, 'device_name': {}, 'room_labels': {}, 'room_floor': {}}


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def state(entity, value, on_for, name, **attrs):
    return {'entity_id': entity, 'state': value, 'last_changed': iso(NOW - on_for), 'attributes': {'friendly_name': name, **attrs}}


def logbook(entity, rows, upto=None):
    with activity._db() as conn:
        conn.executemany("insert into house_activity(key, ts, source, entity, name, room, kind, what, cause) "
                         "values (?, ?, 'ha', ?, '', '', 'other', ?, '')", [(f'{entity}|{ts}|{what}', ts, entity, what)
                                                                            for ts, what in rows])
        if upto is not None:        # how far Hub's copy of the logbook reaches
            conn.execute("insert or replace into house_activity_read(source, ts, at) values ('ha', ?, ?)", (upto, upto))


def history(entity, on, off, times, lasting, every=24 * HOUR, since=NOW - 3 * HOUR):
    """`times` past spells of `on`, each `lasting` long, one a day, the latest starting a day before `since`."""
    rows = []
    for n in range(times):
        start = since - (n + 1) * every
        rows += [(start, on), (start + lasting, off)]
    logbook(entity, rows)


def test_the_limit_comes_from_the_things_own_record():
    nights = [9 * HOUR] * 5 + [10 * HOUR] * 2
    assert left_on.usual(nights) == 10 * HOUR                       # three nights in four it's off within ten hours
    assert left_on.limit(nights) == 15 * HOUR                       # and is said at 1.5 times that
    # One forgotten night doesn't raise the bar for the next.
    assert left_on.limit([9 * HOUR] * 7 + [18 * HOUR]) == 13.5 * HOUR
    assert left_on.limit([10 * 60] * 6) == 6 * HOUR                 # a closet light: never less than six hours
    assert left_on.limit([2 * HOUR] * 6) == 6 * HOUR
    assert left_on.limit([4 * HOUR] * 6) == 7 * HOUR                # or three hours past its usual
    assert left_on.limit([9 * HOUR] * 4) is None                    # too few days on record to know


def test_what_is_on_far_longer_than_usual_is_said_and_nothing_else():
    history('fan.bedroom', 'on', 'off', 7, 9 * HOUR)
    history('light.closet', 'on', 'off', 8, 10 * 60)
    history('media_player.study', 'playing', 'paused', 6, HOUR)
    history('light.hall_group', 'on', 'off', 6, HOUR)
    history('switch.motion_enabled', 'on', 'off', 6, HOUR)
    states = [state('fan.bedroom', 'on', 15 * HOUR, 'Fan'),
              state('light.closet', 'on', 7 * HOUR, 'Closet'),
              state('media_player.study', 'playing', 14 * HOUR, 'Study speaker'),
              state('switch.fridge', 'on', 40 * 24 * HOUR, 'Fridge plug'),           # always on: nothing on record
              state('light.hall_group', 'on', 20 * HOUR, 'Hall', is_hue_group=True),  # a Hue room
              state('switch.motion_enabled', 'on', 20 * HOUR, 'Motion enabled'),       # a setting
              state('light.porch', 'on', 3 * HOUR, 'Porch'),
              state('light.closet_two', 'off', 30 * HOUR, 'Closet two')]
    found = left_on.found(REGISTRY, states, NOW)
    assert [(f['entity'], f['name'], round(f['on_for'] / HOUR), round(f['usual'] / 60)) for f in found] == [
        ('media_player.study', 'Study speaker', 14, 60), ('light.closet', 'Closet light (Hall)', 7, 10),
        ('fan.bedroom', 'Fan (Bedroom)', 15, 540)]
    # Twelve hours is a long night for the fan, not yet a forgotten one.
    assert [f['entity'] for f in left_on.found(REGISTRY, [state('fan.bedroom', 'on', 12 * HOUR, 'Fan')], NOW)] == []


def test_a_porch_lights_motion_blinks_dont_make_its_nights_look_long():
    history('light.porch', 'on', 'off', 7, 14 * HOUR)
    for hours in (2, 4, 6):
        history('light.porch', 'on', 'off', 7, 120, since=NOW - (3 + hours) * HOUR)
    porch = lambda hours: left_on.found(REGISTRY, [state('light.porch', 'on', hours * HOUR, 'Porch')], NOW)
    assert porch(15) == []                                          # a night like its others
    assert [round(f['usual'] / HOUR) for f in porch(22)] == [14]


def test_how_long_comes_from_the_logbook_when_a_restart_or_a_drop_reset_home_assistants_clock():
    history('fan.bedroom', 'on', 'off', 7, 9 * HOUR, since=NOW - 17 * HOUR)
    logbook('fan.bedroom', [(NOW - 16 * HOUR, 'on'), (NOW - 5 * HOUR, 'went offline'), (NOW - 5 * HOUR + 60, 'on')])
    fan = [state('fan.bedroom', 'on', 5 * HOUR - 60, 'Fan')]        # Home Assistant: on since it came back
    assert left_on.found(REGISTRY, fan, NOW) == []                  # Hub's copy doesn't reach that far yet
    logbook('fan.bedroom', [], upto=NOW - 60)
    assert [(f['entity'], round(f['on_for'] / HOUR)) for f in left_on.found(REGISTRY, fan, NOW)] == [('fan.bedroom', 16)]
    # Off the network for an hour: what came back is a new time on.
    logbook('fan.bedroom', [(NOW - 5 * HOUR + 120, 'went offline'), (NOW - 4 * HOUR, 'on')])
    assert left_on.found(REGISTRY, [state('fan.bedroom', 'on', 4 * HOUR, 'Fan')], NOW) == []


def test_hidden_things_followers_in_a_speaker_group_and_odd_states_are_left_out():
    registry = {**REGISTRY, 'entities': {**REGISTRY['entities'],
                                         'switch.heater': {'entity_id': 'switch.heater', 'hidden_by': 'user'},
                                         'media_player.kitchen': {'entity_id': 'media_player.kitchen', 'area_id': 'kitchen'}}}
    for entity in ('switch.heater', 'media_player.study', 'media_player.kitchen'):
        history(entity, 'playing' if entity.startswith('media') else 'on', 'paused', 6, HOUR)
    group = ['media_player.study', 'media_player.kitchen']
    states = [state('switch.heater', 'on', 20 * HOUR, 'Heater'),
              state('media_player.study', 'playing', 14 * HOUR, 'Study speaker', group_members=group),
              state('media_player.kitchen', 'playing', 14 * HOUR, 'Kitchen speaker', group_members=group),
              {**state('fan.bedroom', 'on', 20 * HOUR, 'Fan'), 'attributes': 'odd'}, 'odd']
    assert [f['entity'] for f in left_on.found(registry, states, NOW)] == ['media_player.study']


def test_today_says_it_the_same_day_and_not_today_puts_it_away(monkeypatch):
    history('fan.bedroom', 'on', 'off', 7, 9 * HOUR)
    monkeypatch.setattr(read, '_fetch', lambda: (REGISTRY, [state('fan.bedroom', 'on', 15 * HOUR, 'Fan')]))
    monkeypatch.setattr(left_on.time, 'time', lambda: NOW)
    (item,) = catalog.left_on_items()
    assert item['key'] == 'house-left-on-fan.bedroom' and item['tone'] == 'warn'
    assert item['title'] == 'Fan (Bedroom) has been on for 15 hours'
    assert item['why'] == ('It’s usually on for up to 9 hours. Switch it off from Home or Ask, or tap Not today if '
                           'it’s meant to be on.')
    assert (item['href'], item['cta']) == ('/app/#house', 'Open Home')
    assert morning._kind(item['key']) == 'house'                   # the morning message carries it like the house's own
    assert today.account_of(item['key']) is None                   # the household's: anyone at home can see to it
    monkeypatch.setattr(today, '_checks', lambda: [(catalog.left_on_items, None)])
    assert today.rule_items() == []                                 # names stay out of the overnight review
    today.hide(item['key'])
    day = home.today()
    assert item['key'] in store.snoozed(day.isoformat()) and item['key'] not in store.snoozed((day + timedelta(days=1)).isoformat())


def test_nothing_is_said_when_the_house_cant_be_read(monkeypatch):
    def away():
        raise broker.HomeDown('The Home Assistant broker on this Mac isn’t answering.')
    monkeypatch.setattr(read, '_fetch', away)
    assert catalog.left_on_items() == []


def test_a_reading_is_kept_a_few_minutes_or_until_hub_changes_the_house(monkeypatch):
    history('fan.bedroom', 'on', 'off', 7, 9 * HOUR)
    reads = []
    monkeypatch.setattr(read, '_fetch', lambda: reads.append(1) or (REGISTRY, [state('fan.bedroom', 'on', 15 * HOUR, 'Fan')]))
    monkeypatch.setattr(left_on.time, 'time', lambda: NOW)
    assert len(left_on.items()) == 1 and len(left_on.items()) == 1 and reads == [1]
    read.forget_states()                                            # someone switched something from Hub
    assert len(left_on.items()) == 1 and reads == [1, 1]
