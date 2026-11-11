"""Hub's three main views on made-up data and a scripted model, for screenshots and browser checks. Never for real use.

Run from the repository root:  .venv/bin/python web/views/check/demo_server.py
It uses a throwaway data folder, points every connector at nothing, calls no model and needs no key.
Every name, number and event here is invented.
"""
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

scratch = tempfile.mkdtemp(prefix='hub-views-check-')
os.environ.update(HUB_DATA_DIR=scratch, HUB_BACKGROUND_JOBS='disabled', HUB_BRIDGE_URL='http://127.0.0.1:9',
                  HUB_BRIDGE_TOKEN_FILE=str(Path(scratch) / 'absent'), HUB_KITCHEN_SOCKET=str(Path(scratch) / 'absent.sock'),
                  HUB_ALLOWED_LOGINS='', PYDANTIC_AI_NO_BANNER='1')
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest  # noqa: E402
import uvicorn  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402

from hub import bridge, home, luna, model_key, store, today  # noqa: E402
from hub.agent import model  # noqa: E402
from hub.engine import state  # noqa: E402
from hub.main import app  # noqa: E402
from hub.tools import TOOLS  # noqa: E402
from hub.tools.gmail import triage  # noqa: E402
from hub.tools.network import ha  # noqa: E402
from tests import test_ask  # noqa: E402
from tests.test_network import state as reading  # noqa: E402

patch = pytest.MonkeyPatch()
test_ask.fictional(patch)
patch.setattr(luna, 'key', lambda: 'synthetic-key')
for name in ('_keychain', '_exists'):      # the Status page must not look in the real Keychain or 1Password
    patch.setattr(model_key, name, lambda service: None)
patch.setattr(model_key, '_onepassword', lambda: None)
patch.setattr(bridge, 'health', lambda: False)
patch.setattr(model, 'build', lambda http_client=None: FunctionModel(stream_function=test_ask.scripted, model_name='scripted'))
next(t for t in TOOLS if t['slug'] == 'trip')['description'] = 'Dates, plans and decisions for the next trip'

# ---- Today: the list and a card per tool ----

NEEDS = [
    today.rec('gmail-rules', 'warn', '2 archive-after-30-days rules stopped',
              'They stopped before changing anything, so turning them back on is safe.',
              href='/gmail/delayed', cta='Turn back on', action='gmail.resume_rules'),
    today.rec('gmail-needs', 'warn', '3 emails need you', 'From Maple Street Dental, Alex Rivera and Sam Quiet.', href='/gmail', cta='Open'),
    today.rec('calendar-overlap', 'info', 'Two events overlap tomorrow', 'Dentist and Call with the plumber, both at 3:00 PM.',
              href='/calendar', cta='See the day'),
    today.rec('ai-trip', 'info', 'Choose flights', 'The trip is in 73 days and nothing is selected.', href='/trip',
              cta='Compare flights', suggested=True),
]


def needs(tiles=()):
    return [today._settle(dict(item)) for item in NEEDS]


patch.setattr(today, 'needs_you', needs)
patch.setattr(today, 'start', lambda: [])
patch.setattr(today, 'collect', lambda jobs, tiles=(): needs())
patch.setattr(home, 'SUMMARIES', {
    'kitchen': lambda: home.tile('kitchen', 'warn', 'Cart plan to approve', 'Open Kitchen to check it and tap Approve.', cta='Review'),
    'gmail': lambda: home.tile('gmail', 'warn', '3 emails need you', '1 waiting 2 days or more · 4 sorted today · 1,204 in inbox',
                               items=[{'label': '2 changes planned', 'note': 'Ready to apply'}], cta='Open', as_of='Updated 12 min ago'),
    'calendar': lambda: home.tile('calendar', 'ok', '2 today', '', items=[{'label': 'Swim lesson', 'note': '5:30 PM'},
                                                                           {'label': 'Dinner with Alex and Sam', 'note': '7 PM'}]),
    'preschool': lambda: home.tile('preschool', 'info', 'Family work party', 'Saturday · 10 AM · Garden',
                                   items=[{'label': 'Class photo day', 'note': 'Tue, Mar 16'}], cta='Coming up', href='/preschool/'),
    'network': lambda: home.tile('network', 'ok', 'Internet is up', '18 ms ping · router link up'),
    'trip': lambda: home.tile('trip', 'info', 'Spring trip', 'In 73 days · May 22–29',
                              items=[{'label': 'Choose flights', 'note': 'Nothing booked yet'}], cta='Compare flights'),
    'mobility': home.mobility,
})

# ---- Insights: inbox size, sorted mail, model spend ----

picture = triage.picture()
picture['asked'] = [{'who': 'Alex Rivera', 'days': 4, 'subject': 'Saturday plans', 'thread': 't1', 'id': 'a1'}]
for kind, count in (('calendar', 4), ('expires', 3), ('reading', 9), ('noise', 22)):
    picture['kinds'][kind]['open'] = [test_ask._mail(100 + n, kind, f'sender{n}@list.example', f'Sender {n}', f'Message {n}', n % 6)
                                      for n in range(count)]

day = home.today()
for back in range(45, -1, -1):
    if back % 9 == 4:
        continue  # a day Hub wasn't opened
    total = 1204 + round(back * 9.5 + 60 * math.sin(back / 5)) + (150 if back > 20 else 0)
    store.record_inbox((day - timedelta(days=back)).isoformat(), total, 310 + back * 3)
for back in range(29, -1, -1):
    when = (day - timedelta(days=back)).isoformat()
    state.record_run(when, 'sort', 'ok', luna_calls=3 + back % 3, jev_calls=8 + back % 7, cost=0.018 + 0.004 * (back % 5))
    state.record_run(when, 'overnight', 'error' if back == 11 else 'ok', luna_calls=1, cost=0 if back == 11 else 0.031 + 0.003 * (back % 4))
    for _ in range(back % 4):
        state.record_run(when, 'chat', 'ok', luna_calls=2, cost=0.022 + 0.006 * (back % 3))

# ---- Network: invented Home Assistant readings ----

now = datetime.now(timezone.utc)
STATES = [
    reading('binary_sensor.router_wan', 'on', 'WAN connectivity', device_class='connectivity'),
    reading('sensor.router_download_speed', '86', 'Download speed', unit_of_measurement='Mbit/s'),
    reading('sensor.router_upload_speed', '12', 'Upload speed', unit_of_measurement='Mbit/s'),
    reading('sensor.internet_ping', '18', 'Internet ping', unit_of_measurement='ms'),
    reading('device_tracker.family_laptop', 'home', 'Family laptop', device_type='client', ip_address='192.0.2.21',
            mac='02:00:00:00:00:21', connection_type='band5', deco_device='Hall node', down_kilobytes_per_s=820, up_kilobytes_per_s=90),
    reading('device_tracker.living_room_tv', 'home', 'Living room TV', device_type='client', ip_address='192.0.2.34',
            mac='02:00:00:00:00:34', connection_type='band5', deco_device='Hall node', down_kilobytes_per_s=2400, up_kilobytes_per_s=40),
    reading('device_tracker.hall_node', 'home', 'Hall node', device_type='deco', master=True, internet_online=True,
            signal_band5=4, device_model='Mesh node'),
]


def history(ids, start, end=None, *, include_attributes=False):
    hours = max(2, min(96, round(((end or now) - start).total_seconds() / 3600)))
    groups = []
    for ident in ids:
        if ident == 'binary_sensor.router_wan':
            rows = [{'state': 'on', 'last_changed': start.isoformat()},
                    {'state': 'off', 'last_changed': (now - timedelta(hours=6)).isoformat()},
                    {'state': 'on', 'last_changed': (now - timedelta(hours=5, minutes=48)).isoformat()}]
        elif ident.startswith('sensor.'):
            base, swing = {'download': (70, 45), 'upload': (11, 6), 'ping': (19, 7)}[next(k for k in ('download', 'upload', 'ping') if k in ident)]
            rows = [{'state': str(round(base + swing * abs(math.sin(n * 0.9 + len(ident))), 1)),
                     'last_changed': (now - timedelta(hours=hours - n)).isoformat()} for n in range(hours + 1)]
        else:
            rows = [{'state': 'home', 'last_changed': start.isoformat()}]
        rows[0]['entity_id'] = ident
        groups.append(rows)
    return groups


patch.setattr(ha, 'configured', lambda: True)
patch.setattr(ha, 'states', lambda: STATES)
patch.setattr(ha, 'history', history)

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('VIEWS_CHECK_PORT', '8793')), log_level='warning')
