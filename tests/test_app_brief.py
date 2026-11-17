"""The family brief (hub/app/brief.py and its sources): the weather from Home Assistant's forecast, the locks and
batteries from Home Assistant and Apple Home, the wall's version of Today, the note Ask's model writes and the
brief said aloud. A made-up house and day throughout (tests/app_world.py, tests/day_world.py); no real device,
calendar, mailbox or model is reached."""
import json
import subprocess
import sys
import types
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from hub import config, home, people
from hub.app import brief, today as screen
from hub.app.ask import brain, note, route
from hub.engine import catalog, state
from hub.engine.registry import Source
from hub.main import app
from hub.tools.house import apple, batteries, broker, read, weather
from tests import app_world, day_world

client = TestClient(app)
OWNER = 'me@example.com'        # the one allowed login (tests/conftest.py), so the owner of the main Google account
ME = {'tailscale-user-login': OWNER}
ROUTE_CLIENT = route.client             # Ask's real guarded client; tests give it a stand-in network
MORNING = datetime(2026, 11, 16, 7, 40, tzinfo=home.PACIFIC)        # a Monday
EVENING = datetime(2026, 11, 16, 18, 10, tzinfo=home.PACIFIC)
LATE = datetime(2026, 11, 16, 23, 30, tzinfo=home.PACIFIC)


@pytest.fixture
def world(monkeypatch):
    note.forget()
    made = app_world.install(monkeypatch)
    made.clock = lambda: MORNING
    made.states['weather.home'] = {'entity_id': 'weather.home', 'state': 'partlycloudy', 'attributes': {
        'friendly_name': 'Home', 'temperature': 45, 'temperature_unit': '°F', 'precipitation_unit': 'in', 'wind_speed': 6.2,
        'wind_speed_unit': 'mph', 'humidity': 80, 'uv_index': 0.4}}
    yield made
    note.forget()


def at(moment, monkeypatch, world):
    """See the made-up day and house from `moment`."""
    world.clock = lambda: moment
    read.clear_cache()
    monkeypatch.setattr(read, 'night', lambda now=None: not 6 <= (now or moment).hour < 21)
    day = day_world.install(monkeypatch, now=moment)
    monkeypatch.setattr(catalog, 'SOURCES', [Source('gmail', 'Gmail', '', '', items=(day.mail_items,), account=people.DEFAULT),
                                             Source('calendar', 'Calendar', '', '', account=people.DEFAULT),
                                             Source('made-up', 'Made up', '', '', items=(day.items, catalog.house_items, catalog.lock_items))])
    return day


# ---- the weather ----

def test_the_forecast_is_the_one_service_call_the_everyday_door_makes_and_it_carries_nothing_else(world):
    sent = []
    real = broker._exchange
    broker_exchange = lambda message, timeout: sent.append(message) or real(message, timeout)  # noqa: E731
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(broker, '_exchange', broker_exchange)
        hourly, daily = broker.forecast('weather.home', 'hourly', 'daily')
    assert sent == [{'op': 'ws', 'commands': [
        {'type': 'call_service', 'domain': 'weather', 'service': 'get_forecasts', 'service_data': {'type': kind},
         'target': {'entity_id': 'weather.home'}, 'return_response': True} for kind in ('hourly', 'daily')]}]
    assert len(hourly) == 48 and len(daily) == 6
    for wrong in (('light.kitchen', 'daily'), ('weather.home', 'minutely'), ('weather.home',), ('weather.home', 'daily', 'daily'),
                  ('weather.home; rm', 'daily'), (None, 'daily')):
        with pytest.raises(ValueError):
            broker.forecast(*wrong)


def test_the_morning_weather_says_now_the_high_when_it_rains_and_what_to_wear(world):
    found = weather.picture(MORNING)
    assert found['now'] == {'temp': '45°', 'words': 'Partly cloudy', 'icon': 'partly', 'said': 'partly cloudy'}
    assert found['today'] == {'high': '53°', 'low': '42°', 'words': 'Rain', 'icon': 'rain', 'rain': 'Rain from 3 pm to 6 pm',
                              'wet': True, 'wear': 'Jackets and umbrellas'}
    assert weather.says(found, 'morning') == '45° and partly cloudy, up to 53°. Rain from 3 pm to 6 pm.'
    hours = found['hours']
    assert len(hours) == 12 and hours[0]['at'] == 'Now' and [h['at'] for h in hours[1:6]] == ['8 am', '9 am', '10 am', '11 am', 'Noon']
    assert [h['wet'] for h in hours][7:11] == [False, True, True, True] and hours[8]['icon'] == 'rain' and hours[9]['icon'] == 'pour'
    assert [(d['day'], d['high'], d['low'], d['icon']) for d in found['days']] == [
        ('Tue', '56°', '41°', 'sun'), ('Wed', '54°', '43°', 'partly'), ('Thu', '50°', '44°', 'rain'), ('Fri', '51°', '42°', 'cloud')]
    assert found['sun'] == {'rise': '7:12 am', 'set': '4:31 pm', 'up': True}


def test_in_the_evening_the_weather_is_about_tomorrow_and_the_night_looks_dark(world):
    world.clock = lambda: EVENING
    found = weather.picture(EVENING)
    assert found['now']['icon'] == 'partly-night' and found['hours'][2]['icon'] == 'moon'     # after dark; 8 pm is clear
    assert found['today']['rain'] == 'Dry tonight' and found['tomorrow']['rain'] == 'Dry all day'
    assert found['tomorrow']['wear'] == 'Jackets'
    assert weather.says(found, 'evening') == 'Tomorrow: sunny, high 56°, low 41°. Dry all day.'
    assert found['sun'] == {'rise': '7:12 am', 'set': '4:31 pm', 'up': False}


def test_rain_is_said_the_way_people_say_it():
    def hours(*wet, start=7):
        base = MORNING.replace(hour=start, minute=0)
        return [{'at': base.replace(hour=start + n), 'condition': 'rainy' if w else 'cloudy', 'precipitation': 0.05 if w else 0}
                for n, w in enumerate(wet)]
    end = MORNING.replace(hour=21, minute=0)
    assert weather.rain(hours(1, 1, 1, 0, 0, 0), MORNING, 'in', end) == ('Rain until 10 am', True)
    assert weather.rain(hours(0, 0, 1, 1, 0, 0), MORNING, 'in', end) == ('Rain from 9 am to 11 am', True)
    assert weather.rain(hours(0, 0, 0, 0, 1, 1), MORNING, 'in', end) == ('Rain from 11 am', True)
    assert weather.rain(hours(1, 1, 1, 1, 1, 1), MORNING, 'in', end) == ('Rain all day', True)
    assert weather.rain(hours(0, 1, 0, 1, 0, 1, 0), MORNING, 'in', end) == ('Showers on and off from 8 am', True)
    assert weather.rain(hours(1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0), MORNING, 'in', end) == ('Rain until 8 am, then again from 3 pm', True)
    assert weather.rain(hours(*[0] * 15), MORNING, 'in', end) == ('Dry all day', False)
    assert weather.rain(hours(0, 0, 0, 0), MORNING, 'in', end) == ('', False)        # the forecast stops at 10 am: not said
    snowy = [dict(h, condition='snowy') if h['condition'] == 'rainy' else h for h in hours(0, 1, 1, 0)]
    assert weather.rain(snowy, MORNING, 'in', end) == ('Snow from 8 am to 10 am', True)
    drizzle = [dict(h, condition='cloudy', precipitation=0.004) for h in hours(*[1] * 15)]    # under a hundredth of an inch
    assert weather.rain(drizzle, MORNING, 'in', end) == ('Dry all day', False)


def test_a_house_without_weather_or_a_forecast_shows_none_and_a_failing_one_is_asked_again_later(world, monkeypatch):
    del world.states['weather.home']
    read.clear_cache()
    assert weather.picture(MORNING) is None
    world.states['weather.home'] = {'entity_id': 'weather.home', 'state': 'sunny', 'attributes': {'temperature': 60}}
    read.clear_cache()
    calls = []
    monkeypatch.setattr(broker, 'forecast', lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(broker.HomeDown('no')))
    for _ in range(2):
        with pytest.raises(broker.HomeDown):
            weather.picture(MORNING)
    assert len(calls) == 1                                     # not asked again for a minute


# ---- locks and batteries ----

def test_apple_home_is_read_only_through_homeclaws_own_list(monkeypatch):
    ran = []
    monkeypatch.setattr(apple.CLI.__class__, 'is_file', lambda self: True)
    monkeypatch.setattr(subprocess, 'run', lambda args, **k: ran.append(args) or subprocess.CompletedProcess(
        args, 0, json.dumps(app_world.ACCESSORIES).encode(), b''))
    apple.forget()
    found = apple.accessories()
    assert ran == [[str(apple.CLI), 'list', '--json']]
    assert [(a['name'], a['kind'], a['battery'], a['reachable'], a['lock'], a['label']) for a in found] == [
        ('Back door lock', 'lock', 18, True, 'locked', 'Locked'), ('Doorbell', 'video_doorbell', 76, True, None, ''),
        ('Side door lock', 'lock', 1, False, 'locked', 'Not responding')]          # the light Home Assistant shares is left out
    assert apple.accessories() == found and len(ran) == 1                         # kept for a minute
    for wrong in (('list', 'Side door lock'), ('set', 'Side door lock', 'lock_target_state', '0'), ('get', 'Side door lock')):
        with pytest.raises(ValueError):
            apple._run(*wrong)


def test_locks_from_both_apps_are_one_list_and_the_flattest_battery_leads(world):
    found = batteries.picture()
    assert [(r['name'], r['state'], r['battery'], r['tone']) for r in found['locks']] == [
        ('Side door lock', 'offline', 1, 'bad'), ('Back door lock', 'locked', 18, 'warn'), ('Front door lock', 'locked', None, '')]
    assert [(r['name'], r['level'], r['flat'], r['takes']) for r in found['low']] == [
        ('Side door lock', 1, True, 'AA batteries'), ('Back door lock', 18, False, 'AA batteries')]
    assert batteries.need(found['low']) == (
        'warn', 'Side door lock’s battery is flat',
        'It’s at 1% and has stopped responding, so it can’t be locked from a phone. Also low: Back door lock (18%). '
        'They all take AA batteries.', 'AA batteries')


def test_home_assistants_own_batteries_count_but_phones_do_not(world):
    world.states['sensor.porch_motion_battery'] = {'entity_id': 'sensor.porch_motion_battery', 'state': '9',
                                                   'attributes': {'friendly_name': 'Porch motion Battery', 'device_class': 'battery'}}
    world.states['binary_sensor.hall_button_battery'] = {'entity_id': 'binary_sensor.hall_button_battery', 'state': 'on',
                                                         'attributes': {'friendly_name': 'Hall button battery', 'device_class': 'battery'}}
    world.states['binary_sensor.back_window'] = {'entity_id': 'binary_sensor.back_window', 'state': 'off',
                                                 'attributes': {'friendly_name': 'Back window', 'device_class': 'window', 'battery_low': True}}
    world.states['sensor.phone_battery_level'] = {'entity_id': 'sensor.phone_battery_level', 'state': '4',
                                                  'attributes': {'friendly_name': 'Phone Battery level', 'device_class': 'battery'}}
    world.entries['sensor.phone_battery_level'] = {'entity_id': 'sensor.phone_battery_level', 'platform': 'mobile_app', 'area_id': None,
                                                   'device_id': None, 'name': None, 'labels': [], 'hidden_by': None, 'disabled_by': None}
    world.accessories = [a for a in world.accessories if a['category'] != 'lock']
    read.clear_cache()
    low = batteries.picture()['low']
    assert [(r['name'], r['level'], r['flat'], r['takes']) for r in low] == [
        ('Porch motion', 9, True, ''), ('Back window', None, False, ''), ('Hall button', None, False, '')]
    tone, title, why, buy = batteries.need(low)
    assert (tone, title, buy) == ('warn', 'Porch motion’s battery is nearly flat', '')
    assert why == 'It’s at 9%. Also low: Back window and Hall button.'


def test_what_a_lock_takes_can_be_set_in_the_households_settings(world):
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'brief': {'batteries': {'lock': 'CR123A batteries'}}}))
    assert batteries.need(batteries.picture()['low'])[3] == 'CR123A batteries'


def test_the_list_offers_the_batteries_in_one_line_and_a_quiet_lock_at_night(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    found = {n['key']: n for n in screen.picture(MORNING, login=OWNER)['needs']}
    assert found['house-batteries']['add'] == 'AA batteries' and found['house-batteries']['tone'] == 'warn'
    world.accessories[0]['state']['battery_level'] = '80'                     # new batteries, and still not answering
    world.accessories[1]['state'].update(battery_level='90', lock_current_state='unlocked')
    read.clear_cache()
    keys = [n['key'] for n in screen.picture(MORNING, login=OWNER)['needs']]
    assert 'house-batteries' not in keys and 'house-lock-apple-A-SIDE' in keys and 'house-lock-apple-A-BACK' not in keys
    at(LATE, monkeypatch, world)
    late = {n['key']: n for n in screen.picture(LATE, login=OWNER)['needs']}
    assert late['house-lock-apple-A-BACK']['title'] == 'Back door lock is unlocked'


def test_homeclaw_away_leaves_home_assistants_locks_and_home_assistant_away_leaves_apples(world):
    world.accessories = None                    # HomeClaw isn't installed here, so it can't be read
    read.clear_cache()
    found = batteries.picture()
    assert [r['name'] for r in found['locks']] == ['Front door lock'] and found['apple'] is False
    world.accessories = list(app_world.ACCESSORIES)
    world.down = True
    read.clear_cache()
    found = batteries.picture()
    assert [r['name'] for r in found['locks']] == ['Side door lock', 'Back door lock'] and found['ha'] is False


# ---- the brief on Today, and on the wall ----

def test_today_carries_the_brief_the_weather_the_house_and_the_part_of_the_day(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    found = client.get('/app/api/today', headers=ME).json()
    assert found['part'] == 'morning' and found['weather_says'] == '45° and partly cloudy, up to 53°. Rain from 3 pm to 6 pm.'
    assert [(r['kind'], r['name'], r['state'], r['note'], r['tone'], r['low']) for r in found['house']['rows']] == [
        ('lock', 'Side door lock', 'Not responding', 'Battery 1%', 'bad', True), ('lock', 'Back door lock', 'Locked', 'Battery 18%', '', True),
        ('lock', 'Front door lock', 'Locked', '', '', False), ('alarm', 'Alarm', 'Disarmed', '', '', False),
        ('garage', 'Garage door', 'Closed', '', '', False)]
    assert found['house']['says'] == 'Side door lock isn’t responding.'
    assert found['overnight'] is None and found['note'] is None and found['wall'] is False
    assert [(s['day'], s['title'], s['date'], s['todo']) for s in found['school']][:2] == [
        ('Wed', 'Photo day', '2026-11-18', False), ('Fri', 'You bring the snack', '2026-11-20', False)]


def test_the_wall_shows_the_households_needs_and_only_counts_the_rest(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    phone = client.get('/app/api/today', headers=ME).json()
    wall = client.get('/app/api/today?wall=1').json()
    assert [n['key'] for n in phone['needs']] == ['house-batteries', 'mail:m2', 'kitchen-cart', 'mail:m1']
    assert [n['key'] for n in wall['needs']] == ['house-batteries', 'kitchen-cart']
    assert wall['elsewhere'] == 0 and wall['quiet'] == [] and wall['wall'] is True      # the owner's mail isn't even counted
    assert 'Sam Rivera' not in json.dumps(wall) and 'Maple Street' not in json.dumps(wall)
    assert wall['today'] == wall['tomorrow'] == wall['school'] == [] and wall['note'] is None   # calendars and school: phones
    assert client.get('/app/api/today?wall=1', headers=ME).json() == wall                # the same whoever asks
    assert wall['trip'] is None and wall['weather'] == phone['weather'] and wall['house'] == phone['house']
    assert brief.is_family('house-batteries') and not brief.is_family('mail:m1') and not brief.is_family('hub-release')


def test_a_part_added_to_today_later_stays_off_the_wall_until_it_is_added_there(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    found = client.get('/app/api/today', headers=ME).json()
    found['ready'] = {'cards': [{'key': 'errand-7', 'title': 'Reply to Sam Rivera', 'family': False}], 'more': 0}
    wall = brief.family(found)
    assert 'ready' not in wall and 'Sam Rivera' not in json.dumps(wall)
    assert set(wall) == set(brief.WALL) | {'needs', 'more', 'elsewhere', 'quiet', 'trip', 'wall', 'today', 'tomorrow', 'school', 'note', 'mine'}
    assert wall['today'] == wall['tomorrow'] == wall['school'] == [] and wall['note'] is None and wall['mine'] is False


def test_the_brief_said_aloud(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    said = client.get('/app/api/brief', headers=ME).text
    assert said == ('Good morning. 45° and partly cloudy, up to 53°. Rain from 3 pm to 6 pm. Jackets and umbrellas. '
                    'First up: Lunch with Sam at noon. Side door lock isn’t responding. 4 things need you.')
    at(EVENING, monkeypatch, world)
    said = client.get('/app/api/brief?wall=1', headers=ME).text
    assert said == ('Good evening. Tomorrow: sunny, high 56°, low 41°. Dry all day. Jackets. Side door lock isn’t responding. '
                    '2 things need you.')                                      # the household's: nobody's calendar


def test_a_slow_or_broken_part_is_left_out_on_time(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    monkeypatch.setattr(brief, 'DEADLINE_SECONDS', 0.2)
    monkeypatch.setattr(weather, 'picture', lambda now, found=None: __import__('time').sleep(1))
    monkeypatch.setattr(brief, '_house', lambda now: 1 / 0)
    found = screen.picture(MORNING, login=OWNER)
    assert found['weather'] is None and found['house'] is None and found['weather_says'] == '' and found['needs']


# ---- the overnight note from the caretaker ----

def test_the_caretakers_overnight_note_is_shown_when_one_is_installed(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    assert screen.picture(MORNING, login=OWNER)['overnight'] is None
    made_up = types.ModuleType('hub.caretaker')
    made_up.note = lambda: {'headline': 'A quiet night: two things fixed themselves.', 'needs': 0, 'lines': [
        {'text': 'The upstairs lights came back after Hub reloaded their bridge at 2:14 am.', 'tone': 'fixed'},
        {'text': 'The internet dropped for four minutes at 3 am and came back by itself.', 'tone': 'by_itself'},
        {'text': '<script>', 'tone': 'something else'}, 'not a line']}
    monkeypatch.setitem(sys.modules, 'hub.caretaker', made_up)
    found = screen.picture(MORNING, login=OWNER)['overnight']
    assert found == {'head': 'A quiet night: two things fixed themselves.', 'needs': 0, 'lines': [
        {'text': 'The upstairs lights came back after Hub reloaded their bridge at 2:14 am.', 'tone': 'fixed'},
        {'text': 'The internet dropped for four minutes at 3 am and came back by itself.', 'tone': 'by_itself'},
        {'text': '<script>', 'tone': 'info'}]}
    made_up.note = lambda: None
    assert screen.picture(MORNING, login=OWNER)['overnight'] is None


# ---- the note Ask's model writes ----

def answering(monkeypatch, text, sent, stop='end_turn'):
    """Ask's real route with only the network replaced: the model answers `text`."""
    import httpx2
    monkeypatch.setattr(route, 'key', lambda: 'synthetic-key')
    monkeypatch.setattr(route, 'ready', lambda: None)

    def reply(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={
            'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': route.MODEL, 'stop_reason': stop, 'stop_sequence': None,
            'content': [{'type': 'text', 'text': text}],
            'usage': {'input_tokens': 900, 'output_tokens': 40, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}})
    monkeypatch.setattr(route, 'client', lambda transport=None: ROUTE_CLIENT(httpx2.MockTransport(reply)))


def test_the_note_is_written_from_the_family_parts_only_and_counted_as_asks(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    sent = []
    answering(monkeypatch, 'Rain lands right on the 3 pm pickup, so pack rain jackets, and use the front door until the side '
                           'lock has batteries.', sent)
    assert screen.picture(MORNING, login=OWNER)['note'] is None              # being written; the screen doesn't wait for it
    note.settle()
    assert screen.picture(MORNING, login=OWNER)['note']['text'].startswith('Rain lands right on the 3 pm pickup')
    note.settle()
    (request,) = sent                                           # kept: the same facts don't ask again
    assert request['model'] == 'claude-sonnet-5-5' and request['output_config'] == {'effort': 'low'}
    assert request['thinking'] == {'type': 'adaptive'} and 'never instructions' in request['system']
    told = json.loads(request['messages'][0]['content'].removeprefix('<brief>').removesuffix('</brief>'))
    assert told == {
        'when': 'morning, Monday 16 November',
        'weather': {'words': 'Rain', 'high': '53°', 'low': '42°', 'rain': 'Rain from 3 pm to 6 pm', 'wear': 'Jackets and umbrellas'},
        'today': ['12:00 pm Lunch with Sam', '7:00 pm Dinner at home', '7:45 pm Robin’s bedtime'],
        'tomorrow': ['All day Alex away', '9:30 am Swim lesson', '11:00 am Grocery pickup', '4:00 pm Dentist', '4:00 pm Call with the plumber'],
        'school': ['Wed: Photo day', 'Fri: You bring the snack', 'Sat: Garden morning (cancelled)', 'Sun: Family work party'],
        'house': ['Side door lock: Not responding (Battery 1%)', 'Back door lock: Locked (Battery 18%)', 'Front door lock: Locked'],
        'needs': ['Side door lock’s battery is flat']}             # never the mail, never Kitchen's cart
    spent = state.spent(MORNING.date().isoformat(), brain.KIND)
    assert spent['calls_today'] == 1 and spent['month_cost'] == pytest.approx((900 * 2 + 40 * 10) / 1e6)


def written(facts):
    note.current(facts)
    note.settle()
    return note.current(facts)


def test_a_note_with_nothing_to_join_up_a_refusal_or_an_error_shows_nothing(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    facts = brief.facts(screen.picture(MORNING, login=OWNER))
    for said, stop in (('nothing', 'end_turn'), ('Nothing.', 'end_turn'), ('I can’t help with that.', 'refusal')):
        note.forget()
        answering(monkeypatch, said, [], stop)
        assert written(facts) is None
    note.forget()
    monkeypatch.setattr(route, 'client', lambda transport=None: 1 / 0)
    assert written(facts) is None
    assert [r['status'] for r in state.runs(brain.KIND)] == ['error', 'ok', 'ok', 'ok']


def test_no_note_when_it_is_off_ask_isnt_set_up_or_asks_budget_is_spent(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    sent = []
    answering(monkeypatch, 'A note.', sent)
    (config.DATA_DIR / 'household.json').write_text(json.dumps({'brief': {'note': False}}))
    facts = brief.facts(screen.picture(MORNING, login=OWNER))
    assert written(facts) is None and sent == []
    (config.DATA_DIR / 'household.json').write_text('{}')
    state.record_run(MORNING.date().isoformat(), brain.KIND, 'ok', cost=brain.MONTH_COST_CAP)
    assert written(facts) is None and sent == []
    note.forget()
    monkeypatch.setattr(brain, 'limits', lambda: None)
    monkeypatch.setattr(route, 'ready', lambda: 'Ask isn’t set up yet.')
    assert written(facts) is None and sent == []


def test_a_new_note_waits_half_an_hour_however_much_changes(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    sent = []
    answering(monkeypatch, 'First note.', sent)
    facts = brief.facts(screen.picture(MORNING, login=OWNER))
    note.settle()
    assert written(facts)['text'] == 'First note.'
    changed = dict(facts, needs=['Something new'])
    assert written(changed)['text'] == 'First note.' and len(sent) == 1
    monkeypatch.setattr(note, 'GAP_SECONDS', 0)
    answering(monkeypatch, 'Second note.', sent)
    assert written(changed)['text'] == 'Second note.' and len(sent) == 2
    assert written(changed)['text'] == 'Second note.' and len(sent) == 2       # the same facts: kept


def test_a_note_from_the_morning_is_not_shown_in_the_evening(world, monkeypatch):
    at(MORNING, monkeypatch, world)
    sent = []
    answering(monkeypatch, 'Rain at pickup.', sent)
    facts = brief.facts(screen.picture(MORNING, login=OWNER))
    note.settle()
    assert written(facts)['text'] == 'Rain at pickup.'
    evening = dict(facts, when=facts['when'].replace('morning', 'evening'))
    assert note.current(evening) is None                # the morning's note isn't the evening's, even while a new one is written
    note.settle()
    answering(monkeypatch, 'Alex is away tomorrow.', sent)
    monkeypatch.setattr(note, 'GAP_SECONDS', 0)
    assert written(evening)['text'] == 'Alex is away tomorrow.'


# ---- what a real house may hand over: found in review ----

def test_a_home_assistant_lock_with_its_own_battery_sensor_gets_that_battery(world):
    world.states['sensor.front_door_lock_battery'] = {'entity_id': 'sensor.front_door_lock_battery', 'state': '12',
                                                      'attributes': {'friendly_name': 'Front door lock Battery', 'device_class': 'battery'}}
    read.clear_cache()
    found = batteries.picture()
    front = next(r for r in found['locks'] if r['name'] == 'Front door lock')
    assert (front['battery'], front['tone']) == (12, 'warn') and 'Front door lock' in [r['name'] for r in found['low']]
    assert batteries.stopped(found['locks']) == [r for r in found['locks'] if r['state'] == 'offline' and not r['short']]


def test_a_lock_that_is_open_or_moving_is_never_said_to_be_locked():
    rows = [{'kind': 'lock', 'name': 'Front door lock', 'state': state, 'note': '', 'tone': '', 'low': False}
            for state in ('Locked', 'Open')]
    assert brief.house_says(rows) == ''
    assert brief.house_says(rows[:1]) == 'Front door lock is locked.'


def test_a_forecast_with_only_hours_or_only_dates_is_still_read(monkeypatch):
    from datetime import date
    def only_hours(entity_id, *kinds):
        if 'daily' in kinds:
            raise broker.Rejected('no daily forecast')
        return [[{'datetime': '2026-11-16T16:00:00+00:00', 'condition': 'cloudy', 'temperature': 50}]]
    monkeypatch.setattr(broker, 'forecast', only_hours)
    hourly, daily = weather.forecasts('weather.elsewhere', fresh=True)
    assert len(hourly) == 1 and daily == []
    # A daily row stamped at midnight UTC is a date; any other moment is read in the house's own time zone.
    assert weather._date_of('2026-11-17T00:00:00+00:00') == date(2026, 11, 17)
    assert weather._date_of('2026-11-17T06:00:00+00:00') == date(2026, 11, 16)


# ---- the brief in Ask ----

def test_ask_gets_the_households_brief_beside_a_weather_question_and_never_beside_a_setup_request(world, monkeypatch):
    from hub.app.ask import private
    at(MORNING, monkeypatch, world)
    for asked in ('Will it rain this afternoon?', 'Do I need a jacket?', 'Which lock needs batteries?', 'brief me'):
        assert 'family_brief' in private.data(asked, private.about(asked)), asked
    for doing in ('delete the weather automation', 'raise the temperature to 70', 'close the garage if it rains',
                  'disable the rain delay on the sprinklers', 'play rain sounds in the kitchen', 'add batteries to the list'):
        assert 'weather' not in private.about(doing), doing
    found = private.data('Will it rain this afternoon?', {'weather'})
    assert set(found) == {'today', 'weekday', 'family_brief'}                   # nothing of anyone's mail or calendar
    told = found['family_brief']
    assert told['weather']['said'].startswith('45° and partly cloudy') and told['weather']['today']['rain'] == 'Rain from 3 pm to 6 pm'
    side = next(t for t in told['house']['things'] if t['name'] == 'Side door lock')
    assert (side['state'], side['battery'], side['battery_low']) == ('Not responding', 'Battery 1%', True)
    assert 'icon' not in json.dumps(told)
