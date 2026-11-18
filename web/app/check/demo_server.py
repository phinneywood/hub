"""The app on a made-up house, for trying it by hand, for screenshots and for the browser check. Never for real use.

Run from the repository root:  .venv/bin/python web/app/check/demo_server.py
The real app and the real change code run against a fictional house that follows what it is sent
(tests/app_world.py): tap a room and the next reading shows it. It uses a throwaway data folder, calls no
model, looks in no key store and reaches no other machine and no service on this one (web/check_guard.py).
Every room, name and song here is invented.

Today shows a made-up day (tests/day_world.py): a calendar, a class guide, two emails, a waiting cart and a trip.
House health shows a made-up night from a stand-in caretaker (tests/caretaker_world.py): Hub restarted, a lamp that
came back by itself, an integration that waits for Restart Home Assistant, a week of history and drills that play.
Mail shows the same two emails, a question nobody answered and what the last sort left in the inbox; Kitchen shows
a made-up list on its way into a practice cart (tests/kitchen_world.py): products picked from a made-up history,
one thing that needs a pick, and a cart plan waiting for a tap. Filing, approving and ordering only change the
made-up day.

Errands shows a made-up week (tests/errands_world.py): an invitation, a bill, a booking, a calendar invitation and
two class to-dos, read once by Hub's real reading and drafting on a stand-in for its model. Opening a step only
notes it here; nothing is sent anywhere, and "Add to the list" puts things on the made-up Kitchen list. The mail's
errands are the owner's; a request with the header `tailscale-user-login: casey@example.com` is the made-up partner,
whose own mail Hub doesn't read, and sees the class guide's two only.

Today is also the family brief: the made-up house's weather (rain this afternoon, sun tomorrow), its locks (one
with a flat battery that stopped responding, one low) and a note from a stand-in for Ask's model. The wall, the brief
on the kitchen iPad, is at /app/#wall. POST /demo/morning, /demo/evening or /demo/night moves the made-up clock.

Ask works here too, on a stand-in for its model (tests/ask_script.py) that picks a tool from a few plain words:
"dim the kitchen", "everything off", "some jazz in the bedroom, softly", "play it in the dining room too",
"pause", "movie night", "remember to start music softly", "what should we cook this week?", "what needs me today?",
"add sponges and milk to the list", "what's on the list?", "order the groceries for tomorrow morning",
"move the reading lamp to the bedroom", "remove the dining room", "unlock the front door",
"every evening at 6:45 turn on the hallway light", "what errands are ready?".
The stand-in is only a script; how well Ask understands is the real model's job and can't be judged here.
"""
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path

scratch = tempfile.mkdtemp(prefix='hub-app-check-')
os.environ.update(HUB_DATA_DIR=scratch, HUB_BACKGROUND_JOBS='disabled', HUB_BRIDGE_URL='http://127.0.0.1:9',
                  HUB_BRIDGE_TOKEN_FILE=str(Path(scratch) / 'absent'), HUB_KITCHEN_SOCKET=str(Path(scratch) / 'absent.sock'),
                  HUB_HA_BROKER_SOCKET=str(Path(scratch) / 'absent-broker.sock'), HUB_ALLOWED_LOGINS='me@example.com,casey@example.com',
                  PYDANTIC_AI_NO_BANNER='1', HUB_RELEASES_DIR=str(Path(scratch) / 'releases'),
                  HUB_CARETAKER_DIR=str(Path(scratch) / 'caretaker'))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import check_guard  # noqa: E402
import pytest  # noqa: E402

patch = pytest.MonkeyPatch()
check_guard.apply(patch)   # before the app exists: no key store, no other machine, no local service

import uvicorn  # noqa: E402

from hub import people, store  # noqa: E402
from hub.app import levels, routes, scenes  # noqa: E402
from hub.app.ask import mailchange, morning, private, route, turns  # noqa: E402
from hub.main import app  # noqa: E402
from hub.tools.house import admin, control, read  # noqa: E402

morning.MORNING_HOUR = 0        # Ask opens on the morning message at any hour the check runs
from hub.tools.gmail import filing, inbox, missions, triage  # noqa: E402
from tests import app_world, ask_script, day_world, errands_world, kitchen_world  # noqa: E402

# A made-up checked version waiting for Deploy. No deployer listens here, so a tap leaves it "updating".
_ready = Path(scratch) / 'releases'
(_ready / ('d' * 40) / 'venv').mkdir(parents=True)
(_ready / 'queue').mkdir()
(_ready / 'pending.json').write_text(json.dumps({'sha': 'd' * 40, 'subjects': ['Home: a made-up change, ready to deploy']}))
# ...and a made-up update Hub made by itself an hour ago, which Undo can still put back.
(_ready / ('f' * 40) / 'venv').mkdir(parents=True)
(_ready / 'current').symlink_to('e' * 40)
(_ready / 'last.json').write_text(json.dumps({'sha': 'e' * 40, 'previous': 'f' * 40, 'status': 'accepted', 'how': 'auto',
                                              'at': time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(time.time() - 3600))}))
# Two made-up people: the owner, whose Google account is the bridge's main one, and a partner Hub reads no mail of yet.
(Path(scratch) / 'household.json').write_text(json.dumps({'versions': {'repo': 'https://github.com/example/hub'}, 'people': [
    {'name': 'Alex', 'logins': ['me@example.com'], 'accounts': ['default']}, {'name': 'Casey', 'logins': ['casey@example.com'], 'accounts': []}]}))
world = app_world.install(patch)

# The Activity screen: a made-up evening in Home Assistant's logbook, and Apple Home through a stand-in HomeClaw.
from hub.app import activity  # noqa: E402
_now = time.time()
world.states['person.alex'] = {'entity_id': 'person.alex', 'state': 'home', 'attributes': {'friendly_name': 'Alex', 'user_id': 'u-alex'}}
world.logbook = [
    {'when': _now - 7200, 'entity_id': 'light.porch_light', 'state': 'on', 'context_event_type': 'automation_triggered',
     'context_domain': 'automation', 'context_name': 'Porch at sunset'},
    {'when': _now - 3600, 'entity_id': 'binary_sensor.front_door', 'state': 'on'},
    {'when': _now - 3590, 'entity_id': 'binary_sensor.front_door', 'state': 'off'},
    {'when': _now - 1800, 'entity_id': 'event.living_button', 'state': '2026-11-15T17:58:00.000-08:00', 'attributes': {'event_type': 'multi_press_1'}},
    {'when': _now - 1799, 'entity_id': 'scene.dinner', 'state': '2026-11-15T17:58:01.000-08:00', 'context_event_type': 'automation_triggered',
     'context_domain': 'automation', 'context_name': 'Living room button'},
    {'when': _now - 900, 'entity_id': 'media_player.living', 'state': 'playing', 'context_user_id': 'u-alex'},
    {'when': _now - 86400 - 600, 'entity_id': 'light.kitchen_ceiling', 'state': 'off', 'context_user_id': 'u-alex'},
]


def _claw_events(since):
    def at(ago):
        return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(_now - ago))
    return [{'type': 'characteristic_change', 'timestamp': at(2400), 'characteristic': 'lock_current_state', 'value': '1',
             'service_type': 'lock', 'home': {'id': 'demo'}, 'accessory': {'id': 'a1', 'name': 'Side door', 'room': 'Entry'}},
            {'type': 'characteristic_change', 'timestamp': at(600), 'characteristic': 'brightness', 'value': '40',
             'service_type': 'lightbulb', 'home': {'id': 'demo'}, 'accessory': {'id': 'a2', 'name': 'Garage lights', 'room': 'Garage'}}]


patch.setattr(activity.apple, 'setup', lambda: {'logging': True, 'home': 'demo'})
patch.setattr(activity.apple, 'events', _claw_events)

# House health: a made-up caretaker folder with a night in it (tests/caretaker_world.py), and a stand-in caretaker
# that answers the screen's taps and plays a drill through when one is asked for.
from tests import caretaker_world  # noqa: E402
care = caretaker_world.Caretaker(caretaker_world.night(Path(scratch) / 'caretaker', released=True)).start()

# The Engine screen: a made-up instruction, something to build and a note waiting for a tap.
from hub.engine import notes as engine_notes, state as engine_state  # noqa: E402
engine_state.add_instruction('Always tell me about school mail with a deadline.', 'me@example.com')
engine_state.add_request('A button that warms the house before we wake')
# Mail settings: one made-up sender in the plan, and Gmail changes not paused.
from hub import store as mail_plan, worker as mail_worker  # noqa: E402
from hub.tools.gmail import missions as mail_missions  # noqa: E402
mail_plan.set_decision('news@example.com', 'archive', 120, True)
patch.setattr(mail_missions, 'pause_state', lambda: 'ok')
patch.setattr(mail_worker, 'start', lambda: None)
# Ask's mail search and read: a made-up mailbox where the bridge would answer, with one sender AI is off for.
from tests import mail_world  # noqa: E402
mail_world.install(patch)
MAIL_WRITES = mail_world.install_writes(patch)      # and Ask's mail changes, after the tap: kept here, never sent
mail_plan.set_ai_blocked(mail_world.OFF, True)
engine_notes.propose('The porch light goes off at midnight', 'remember that the porch light goes off at midnight', login='me@example.com')
patch.setattr(control, 'CHECK_WAIT', 0.2)

# A made-up library, so "Play something else" and Ask have something to offer.
ask_script.library(patch, world)
routes._picks.update(at=float('-inf'), found=[])

# A made-up day for Today, seen from ten past six this evening: a calendar, a class guide, two emails, a trip.
# Kitchen's waiting cart comes from the made-up Kitchen below.
from datetime import datetime  # noqa: E402

from hub import home  # noqa: E402

EVENING = datetime.now(home.PACIFIC).replace(hour=18, minute=10, second=0, microsecond=0)
day = day_world.install(patch, kitchen_too=False, now=EVENING)
# The caretaker's Today items next to the made-up day's, so the integration that waits for a tap is on Today too.
from hub.engine import catalog  # noqa: E402
from hub.engine.registry import Source  # noqa: E402
patch.setattr(catalog, 'SOURCES', [*catalog.SOURCES, Source('caretaker', 'House caretaker', '', '', items=(catalog.caretaker_items,))])

# The family brief on the made-up house. The forecast, the sun and the brief's clock follow the demo's own clock,
# which /demo/morning, /demo/evening and /demo/night move; the locks are read as well as the made-up list; and the
# note comes from a stand-in for Ask's model, a made-up line for each part of the day.
from hub.app import today as screen  # noqa: E402
from hub.app.ask import note  # noqa: E402
from hub.engine import catalog  # noqa: E402
from hub.engine.registry import Source  # noqa: E402

CLOCK = {'now': EVENING}
HOURS = {'morning': (7, 40), 'evening': (18, 10), 'night': (22, 40)}
SKIES = {'morning': ('partlycloudy', 45), 'evening': ('cloudy', 48), 'night': ('clear-night', 44)}
NOTES = {'morning': 'The rain holds off until about 3, so lunch with Sam stays dry; it clears by 6 for dinner at home.',
         'evening': 'Alex is away tomorrow, so the swim lesson at 9:30 and the grocery pickup at 11 fall to one person.'}


def at_hour(part):
    hour, minute = HOURS[part]
    CLOCK['now'] = EVENING.replace(hour=hour, minute=minute)
    sky, degrees = SKIES[part]
    world.states['weather.home'] = {'entity_id': 'weather.home', 'state': sky, 'attributes': {
        'friendly_name': 'Home', 'temperature': degrees, 'temperature_unit': '°F', 'precipitation_unit': 'in', 'wind_speed': 6,
        'wind_speed_unit': 'mph', 'humidity': 80, 'uv_index': 0.4}}
    note.forget()


async def made_up_note(facts, transport=None):
    part = facts['when'].split(',')[0]
    return NOTES.get(part, ''), {'tokens_in': 0, 'tokens_out': 0, 'cost': 0.0}


world.clock = lambda: CLOCK['now']
patch.setattr(screen, 'clock', lambda: CLOCK['now'])
patch.setattr(home, 'today', lambda: CLOCK['now'].date())
_night = read.night
patch.setattr(read, 'night', lambda now=None: _night(now or CLOCK['now']))
patch.setattr(catalog, 'SOURCES', [Source('gmail', 'Gmail', '', '', items=(day.mail_items,), account=people.DEFAULT),     # the owner's
                                   Source('calendar', 'Calendar', '', '', account=people.DEFAULT),
                                   Source('made-up', 'Made up', '', '', items=(day.items, catalog.house_items, catalog.lock_items))])
patch.setattr(note, 'write', made_up_note)
patch.setattr(note, 'GAP_SECONDS', 0)
at_hour('evening')

# Ask, without a model: the stand-in picks the tool; the private desk and Kitchen answer with made-up words.
patch.setattr(route, 'build', lambda http_client=None: ask_script.MODEL)
patch.setattr(route, 'ready', lambda: None)
patch.setattr(private, 'answer', lambda text, topics=None: (
    'Two things need you today. The plumber comes at ten, and Robin’s class trip form is due on Friday.',
    {'tokens_in': 0, 'tokens_out': 0, 'cost': 0.0}))
# A made-up Kitchen (tests/kitchen_world.py): a list with made-up products, a practice cart that fills a few seconds
# after it is asked to, a cart plan waiting, and an assistant that takes a few seconds to answer.
shop = kitchen_world.install(patch, takes=4.0)

# Errands: the made-up week's mail and the class guide's two to-dos (added to the made-up guide), read once as the
# morning job would, on a stand-in for Luna. Free times for a booking come from the made-up calendar.
def todos():
    return [row for row in errands_world.school(EVENING) if row['kind'] == 'action']


day.school += todos()
week = errands_world.demo(patch, EVENING)

# Mail, on the made-up day's inbox: reading can be filed with a tap, and filing only empties it here.
patch.setattr(missions, 'is_paused', lambda: False)
patch.setattr(filing, 'trusted', lambda kind: 'undo' if kind == 'reading' else 'suggest')
patch.setattr(filing, 'ready', lambda kind, now=None: list(day.sorted.get(kind, [])) if kind == 'reading' else [])
patch.setattr(triage, 'dismiss_asked', lambda thread: bool([day.asked.remove(a) for a in list(day.asked) if a['thread_id'] == thread]))


def made_up_filing(kind):
    filed = day.sorted.pop(kind, [])
    inbox._filing['result'] = {'status': 'filed' if filed else 'nothing', 'count': len(filed)}


patch.setattr(inbox, '_file_soon', made_up_filing)


def a_habit():
    """Two earlier evenings on which the same thing was asked at about this time, so Ask offers it again."""
    for days in (1, 2):
        turns.save('me@example.com', 'Dim the living room to 30', 'The living room is at 30%.', [{'kind': 'rooms', 'rooms': ['living-room']}],
                   at=time.time() - days * 86400)


a_habit()

# Dashboards (hub/app/boards/): a made-up due date 176 days after the demo's day (week 15), a made-up network read
# from a stand-in for the older Network page's overview, and a month of the made-up inbox's size.
from datetime import timedelta  # noqa: E402

from hub.app.boards import network as network_board  # noqa: E402

_house = json.loads((Path(scratch) / 'household.json').read_text())
_house['pregnancy'] = {'due': (EVENING.date() + timedelta(days=176)).isoformat()}
(Path(scratch) / 'household.json').write_text(json.dumps(_house))


def _wave(points, base, swing, spike=None):
    rows = []
    for n in range(points):
        value = base + swing * ((n * 37) % 11) / 10 + (swing * 4 if spike and spike[0] <= n < spike[1] else 0)
        rows.append(f'{2 + 716 * n / (points - 1):.1f},{176 - 168 * value / (base + swing * 5):.1f}')
    return [' '.join(rows)]


MADE_UP_NETWORK = {
    'configured': True, 'connected': True, 'headline': 'Internet is up', 'tone': 'ok', 'facts': '14 ms ping · router link up',
    'summary_label': 'External reachability', 'updated': '6:10 pm', 'node_count': 3, 'client_count': 26, 'online_count': 23,
    'summary': {'reliability': 99.93, 'outages': 1, 'downtime_text': '1 min', 'incidents': []},
    'incidents': [{'label': 'Reported offline', 'when': 'Today 3:12 am', 'duration': '1 min', 'ongoing': False}],
    'device_incidents': [{'source': 'Upstairs mesh point', 'label': 'Reported offline', 'when': 'Today 3:12 am', 'duration': '1 min',
                          'url': '/network'}],
    'attention': [],
    'cards': [{'title': 'Download traffic', 'current': '38.4 Mbit/s', 'graph': {'segments': _wave(96, 20, 30, (70, 78)), 'max': 210,
                                                                           'unit': 'Mbit/s', 'start_label': 'Yesterday 6:10 pm', 'end_label': 'Today 6:10 pm'}},
              {'title': 'Upload traffic', 'current': '4.2 Mbit/s', 'graph': {}},
              {'title': 'External latency', 'current': '14.0 ms', 'graph': {'segments': _wave(96, 12, 4), 'max': 31, 'unit': 'ms',
                                                                        'start_label': 'Yesterday 6:10 pm', 'end_label': 'Today 6:10 pm'}},
              {'title': 'External packet loss', 'current': '0.0 %', 'graph': {}}],
    'top': [{'name': 'Living room TV', 'download_text': '24.1 Mbit/s', 'upload_text': '0.8 Mbit/s'},
            {'name': 'Work laptop', 'download_text': '9.6 Mbit/s', 'upload_text': '3.1 Mbit/s'},
            {'name': 'Kitchen speaker', 'download_text': '0.4 Mbit/s', 'upload_text': '0.1 Mbit/s'}],
}
patch.setattr(network_board, 'read', lambda: MADE_UP_NETWORK)
from hub.app import boards  # noqa: E402

for _done in ('vitamins', 'provider', 'insurance', 'flu', 'tell'):          # the first trimester's list, done
    boards.tick('me@example.com', 'pregnancy', _done, True)
with store.db() as _conn:
    for _n in range(31):
        _conn.execute('insert or replace into inbox_history(day, total, unread) values(?,?,?)',
                      ((EVENING.date() - timedelta(days=30 - _n)).isoformat(), 4200 - _n * 38 + (_n % 4) * 25, 120))


@app.get('/demo/mail-writes')
def mail_writes():
    """For the browser check: what Ask's mail cards sent to the stand-in for Gmail, in order."""
    return MAIL_WRITES


# For the browser check only: put the made-up house back as it started, make it stop answering, or take Ask
# away as on a Hub where it isn't set up yet.
@app.post('/demo/{what}')
def stage(what: str):
    if what == 'reset':
        world.reset()
        world.down = False
        world.setup.clear()
        world.sent.clear()
        control._taps.clear()
        control._recent.clear()
        levels.forget()
        for scene in scenes.kept():
            scenes.forget(scene['id'])
        for note in turns.remembered('me@example.com'):
            turns.forget(note['id'], 'me@example.com')
        fresh = day_world.Day(EVENING)
        day.events, day.school, day.mail, day.filed, day.cart, day.faults = fresh.events, fresh.school, fresh.mail, fresh.filed, True, []
        day.sorted, day.asked = fresh.sorted, fresh.asked
        shop.reset()
        shop.down = shop.old = False
        shop.limited = ''
        day.school += todos()
        errands_world.again(week)
        MAIL_WRITES.clear()
        with mailchange._db() as conn:      # Ask's mail cards
            conn.execute('delete from ask_mail_waiting')
        with admin._db() as conn:           # the record of setup changes, and the cards that wait
            conn.execute('delete from house_setup')
            conn.execute('delete from house_setup_waiting')
        with store.db() as conn:
            conn.execute('delete from today_snoozed')
            conn.commit()
        with turns._db() as conn:
            conn.execute('delete from app_ask')
        with morning._db() as conn:          # and the day's morning message is made again when Ask opens
            for table in ('app_ask_morning', 'app_ask_morning_day', 'app_ask_watch'):
                conn.execute(f'delete from {table}')
        a_habit()
        world.accessories = copy.deepcopy(app_world.ACCESSORIES)
        at_hour('evening')
        care.reset()
    elif what in HOURS:
        at_hour(what)
    elif what in ('down', 'up'):
        world.down = what == 'down'
    elif what == 'quiet-day':
        day.events, day.school, day.mail, day.filed, day.cart = [], [], [], 0, False
        world.accessories = []          # new batteries all round
        care.quiet()
        shop.reset(cart_waiting=False)
        shop.items = [i for i in shop.items if i['state'] != 'pick']
        errands_world.quiet()
    elif what == 'errands-replied':         # the owner sent the party reply from their mail app
        week.replied(errands_world.HOST)
    elif what in ('kitchen-away', 'kitchen-old', 'kitchen-back'):       # the kitchen service stopped, or from before it kept the list
        shop.down, shop.old = what == 'kitchen-away', what == 'kitchen-old'
    elif what == 'kitchen-empty':
        shop.items.clear()
        shop.plans['7'] = {'status': 'done'}
    elif what in ('kitchen-limit', 'kitchen-limit-before', 'kitchen-unlimited'):     # Kitchen's Claude plan at its limit
        shop.limited = {'kitchen-limit': 'notice', 'kitchen-limit-before': 'before'}.get(what, '')
    elif what == 'kitchen-unsure':          # a cart turn that stopped before it could say: one thing to check
        for item in shop.items[:1]:
            item['state'] = 'unknown'
    elif what in ('ask-away', 'ask-back'):
        patch.setattr(route, 'ready', (lambda: 'Ask isn’t set up yet: its key isn’t in 1Password.') if what == 'ask-away' else (lambda: None))
    else:
        return {'ok': False}
    read.clear_cache()
    routes.feed.poke()
    return {'ok': True, 'at': CLOCK['now'].isoformat()}


class SignedIn:
    """A request without a login counts as the owner's phone, so taps work; one may name another login (the browser
    check opens the app as the partner, casey@example.com). The demo has only a made-up house to change."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and not any(name == b'tailscale-user-login' for name, _ in scope['headers']):
            scope = dict(scope, headers=[*scope['headers'], (b'tailscale-user-login', b'me@example.com')])
        await self.inner(scope, receive, send)


demo = SignedIn(app)

if __name__ == '__main__':
    # APP_CHECK_HOST: another address to listen on, such as this machine's private tailnet address, to try the
    # demo from a phone. It has no sign-in of its own: anyone who can reach that address can tap the made-up house.
    host, port = os.environ.get('APP_CHECK_HOST', '127.0.0.1'), int(os.environ.get('APP_CHECK_PORT', '8912'))
    uvicorn.run(demo, host=host, port=port, log_level='warning')
