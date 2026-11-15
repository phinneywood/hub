"""The app on a made-up house, for trying it by hand, for screenshots and for the browser check. Never for real use.

Run from the repository root:  .venv/bin/python web/app/check/demo_server.py
The real app and the real change code run against a fictional house that follows what it is sent
(tests/app_world.py): tap a room and the next reading shows it. It uses a throwaway data folder, calls no
model, looks in no key store and reaches no other machine and no service on this one (web/check_guard.py).
Every room, name and song here is invented.

Today shows a made-up day (tests/day_world.py): a calendar, a class guide, two emails, a waiting cart and a trip.
Mail shows the same two emails, a question nobody answered and what the last sort left in the inbox; Kitchen shows
a short conversation with a cart plan and an order waiting for a tap. Filing, approving and ordering only change
the made-up day.

Ask works here too, on a stand-in for its model (tests/ask_script.py) that picks a tool from a few plain words:
"dim the kitchen", "everything off", "some jazz in the bedroom, softly", "play it in the dining room too",
"pause", "movie night", "remember to start music softly", "what should we cook this week?", "what needs me today?".
The stand-in is only a script; how well Ask understands is the real model's job and can't be judged here.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

scratch = tempfile.mkdtemp(prefix='hub-app-check-')
os.environ.update(HUB_DATA_DIR=scratch, HUB_BACKGROUND_JOBS='disabled', HUB_BRIDGE_URL='http://127.0.0.1:9',
                  HUB_BRIDGE_TOKEN_FILE=str(Path(scratch) / 'absent'), HUB_KITCHEN_SOCKET=str(Path(scratch) / 'absent.sock'),
                  HUB_HA_BROKER_SOCKET=str(Path(scratch) / 'absent-broker.sock'), HUB_ALLOWED_LOGINS='me@example.com',
                  PYDANTIC_AI_NO_BANNER='1', HUB_RELEASES_DIR=str(Path(scratch) / 'releases'))
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import check_guard  # noqa: E402
import pytest  # noqa: E402

patch = pytest.MonkeyPatch()
check_guard.apply(patch)   # before the app exists: no key store, no other machine, no local service

import uvicorn  # noqa: E402

from hub import store  # noqa: E402
from hub.app import levels, routes, scenes  # noqa: E402
from hub.app.ask import private, route, turns  # noqa: E402
from hub.main import app  # noqa: E402
from hub.tools.house import control, read  # noqa: E402
from hub.tools.gmail import filing, inbox, missions, triage  # noqa: E402
from hub.tools.kitchen import client as kitchen  # noqa: E402
from tests import app_world, ask_script, day_world  # noqa: E402

# A made-up checked version waiting for Deploy. No deployer listens here, so a tap leaves it "updating".
_ready = Path(scratch) / 'releases'
(_ready / ('d' * 40) / 'venv').mkdir(parents=True)
(_ready / 'queue').mkdir()
(_ready / 'pending.json').write_text(json.dumps({'sha': 'd' * 40, 'subjects': ['Home: a made-up change, ready to deploy']}))
(Path(scratch) / 'household.json').write_text(json.dumps({'versions': {'repo': 'https://github.com/example/hub'}}))
world = app_world.install(patch)

# The Engine screen: a made-up instruction, something to build and a note waiting for a tap.
from hub.engine import notes as engine_notes, state as engine_state  # noqa: E402
engine_state.add_instruction('Always tell me about school mail with a deadline.')
engine_state.add_request('A button that warms the house before we wake')
# Mail settings: one made-up sender in the plan, and Gmail changes not paused.
from hub import store as mail_plan, worker as mail_worker  # noqa: E402
from hub.tools.gmail import missions as mail_missions  # noqa: E402
mail_plan.set_decision('news@example.com', 'archive', 120, True)
patch.setattr(mail_missions, 'pause_state', lambda: 'ok')
patch.setattr(mail_worker, 'start', lambda: None)
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

# Ask, without a model: the stand-in picks the tool; the private desk and Kitchen answer with made-up words.
patch.setattr(route, 'build', lambda http_client=None: ask_script.MODEL)
patch.setattr(route, 'ready', lambda: None)
patch.setattr(private, 'answer', lambda text, topics=None: (
    'Two things need you today. The plumber comes at ten, and Robin’s class trip form is due on Friday.',
    {'tokens_in': 0, 'tokens_out': 0, 'cost': 0.0}))
KITCHEN_TAKES = 4.0     # seconds the made-up Kitchen takes to answer
KITCHEN_SAYS = {'role': 'assistant', 'text': 'Three dinners you don’t usually make. Tell me which and I’ll add what you’re missing to the cart.',
                'recipes': [{'title': 'Miso-glazed eggplant with sesame rice', 'total': '40 minutes'},
                            {'title': 'Chicken with preserved lemon and olives', 'total': '55 minutes'},
                            {'title': 'Black bean and squash tacos', 'total': '30 minutes'}]}
FIRST = 100             # the made-up conversation so far has ids below this; what is asked here comes after
SO_FAR = [
    {'id': 1, 'role': 'member', 'author': 'Alex', 'text': 'Something easy for dinner tonight?'},
    {'id': 2, 'role': 'assistant', 'text': 'Two that use what you bought this week.',
     'recipes': [{'title': 'Sheet-pan gnocchi with peppers', 'active': '10 min', 'total': '30 min', 'have': 'gnocchi, peppers, onion',
                  'need': 'feta'},
                 {'title': 'Black bean and squash tacos', 'active': '15 min', 'total': '30 min', 'diet': 'Vegetarian',
                  'have': 'black beans, tortillas', 'need': 'squash, lime'}]},
    {'id': 3, 'role': 'member', 'author': 'Alex', 'text': 'Add what we need for the tacos'},
    {'id': 4, 'role': 'assistant', 'text': 'Here is the plan. Nothing goes in the cart until you tap.',
     'plan': {'id': 7, 'status': 'pending', 'items': [{'product': 'Butternut squash', 'size': '1 each', 'qty': 1, 'price': '2.49'},
                                                      {'product': 'Limes', 'size': '1 each', 'qty': 3, 'price': '0.50'}]}},
    {'id': 5, 'role': 'assistant', 'text': 'Here is the order, as checkout shows it. Nothing is ordered until you tap Place order.',
     'order': {'id': 3, 'status': 'pending', 'review': {'items': 14, 'subtotal': 52.10, 'fees': 4.95, 'savings': 3.20, 'tax': 0.0,
                                                        'total': 53.85, 'when': 'Tomorrow, 9–10 am', 'how': 'Pickup'}}},
]
SHOPPING = '# Shopping list\n- [ ] Bananas\n- [ ] Eggs\n- [ ] Coffee beans\n- [ ] Dish soap\n'
asked_kitchen = []      # when each message was passed on; message n is FIRST + its 1-based place, doubled
kitchen_now = {'plans': {}, 'orders': {}, 'notices': []}


def kitchen_reset():
    asked_kitchen.clear()
    kitchen_now.update(plans={'7': {'status': 'pending' if day.cart else 'done'}}, orders={'3': {'status': 'pending'}}, notices=[])


def made_up_kitchen(method, path, body=None, timeout=10):
    """Stands where the kitchen service would: a conversation so far, a list, and a few seconds later an answer to
    each new message. Approving, dismissing and placing only change what it says next."""
    if (method, path) == ('POST', '/messages'):
        asked_kitchen.append(time.monotonic())
        return 200, {'id': FIRST + len(asked_kitchen) * 2 - 1}
    if (method, path) == ('GET', '/list'):
        return 200, {'text': SHOPPING}
    if method == 'POST' and path.startswith(('/plans/', '/orders/')):
        _, what, number, choice = path.split('/')
        kitchen_now[what][number] = {'status': {'approve': 'done', 'place': 'placed'}.get(choice, 'dismissed'), 'decided_by': 'me'}
        if what == 'plans':
            day.cart = False
        kitchen_now['notices'].append(f'me {"approved" if choice == "approve" else "placed" if choice == "place" else "dismissed"} the '
                                      f'{"cart plan" if what == "plans" else "order"}.')
        return 200, {'ok': True}
    if method == 'GET' and path.startswith('/messages?after='):
        after, now = int(path.rsplit('=', 1)[1]), time.monotonic()
        messages = [dict(m, at=time.time() - 3600 + m['id'] * 60) for m in SO_FAR if m['id'] > after]
        for n, at in enumerate(asked_kitchen, 1):
            if FIRST + n * 2 - 1 > after:
                messages.append({'id': FIRST + n * 2 - 1, 'role': 'member', 'text': '', 'author': 'me', 'at': time.time()})
            if FIRST + n * 2 > after and now - at >= KITCHEN_TAKES:
                messages.append(dict(KITCHEN_SAYS, id=FIRST + n * 2, at=time.time()))
        for n, words in enumerate(kitchen_now['notices']):
            if 50 + n > after:
                messages.append({'id': 50 + n, 'role': 'notice', 'text': words, 'at': time.time()})
        messages.sort(key=lambda m: m['id'])
        return 200, {'messages': messages, 'busy': any(now - at < KITCHEN_TAKES for at in asked_kitchen),
                     'plans': dict(kitchen_now['plans']), 'orders': dict(kitchen_now['orders'])}
    raise kitchen.KitchenDown('made up')


patch.setattr(kitchen, 'call', made_up_kitchen)
kitchen_reset()

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


# For the browser check only: put the made-up house back as it started, make it stop answering, or take Ask
# away as on a Hub where it isn't set up yet.
@app.post('/demo/{what}')
def stage(what: str):
    if what == 'reset':
        world.states, world.down = app_world._states(), False
        world.sent.clear()
        control._taps.clear()
        control._recent.clear()
        levels.forget()
        for scene in scenes.kept():
            scenes.forget(scene['id'])
        for note in turns.remembered():
            turns.forget(note['id'])
        fresh = day_world.Day(EVENING)
        day.events, day.school, day.mail, day.filed, day.cart, day.faults = fresh.events, fresh.school, fresh.mail, fresh.filed, True, []
        day.sorted, day.asked = fresh.sorted, fresh.asked
        kitchen_reset()
        with store.db() as conn:
            conn.execute('delete from today_snoozed')
            conn.commit()
        with turns._db() as conn:
            conn.execute('delete from app_ask')
        a_habit()
    elif what in ('down', 'up'):
        world.down = what == 'down'
    elif what == 'quiet-day':
        day.events, day.school, day.mail, day.filed, day.cart = [], [], [], 0, False
        kitchen_now.update(plans={}, orders={})
    elif what in ('ask-away', 'ask-back'):
        patch.setattr(route, 'ready', (lambda: 'Ask isn’t set up yet: its key isn’t in 1Password.') if what == 'ask-away' else (lambda: None))
    else:
        return {'ok': False}
    read.clear_cache()
    routes.feed.poke()
    return {'ok': True}


class SignedIn:
    """Every request counts as the owner's phone, so taps work. The demo has only a made-up house to change."""

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
