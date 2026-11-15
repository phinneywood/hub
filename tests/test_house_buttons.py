"""Buttons on walls and remotes: read from Home Assistant, shown in their room. A fake broker and made-up devices only."""
import pytest

from hub.app import model
from hub.tools.house import broker, read

AREAS = [{'area_id': 'den', 'name': 'Den'}]
DEVICES = [{'id': 'dev-button', 'area_id': 'den', 'name': 'Mini switch', 'name_by_user': 'Den button'},
           {'id': 'dev-remote', 'area_id': 'den', 'name': 'Remote'}]
ENTITIES = [
    {'entity_id': 'light.den_lamp', 'area_id': 'den'},
    {'entity_id': 'event.den_button', 'device_id': 'dev-button', 'original_device_class': 'button'},
    {'entity_id': 'sensor.den_button_battery', 'device_id': 'dev-button', 'original_device_class': 'battery'},
    {'entity_id': 'event.remote_button', 'device_id': 'dev-remote', 'original_device_class': 'button'},
    {'entity_id': 'event.backup', 'original_device_class': None},
]
STATES = [
    {'entity_id': 'light.den_lamp', 'state': 'on', 'attributes': {'friendly_name': 'Den lamp', 'brightness': 255}},
    {'entity_id': 'event.den_button', 'state': '2026-11-15T20:17:22.000+00:00',
     'attributes': {'event_type': 'multi_press_2', 'device_class': 'button'}},
    {'entity_id': 'sensor.den_button_battery', 'state': '87', 'attributes': {'device_class': 'battery'}},
    {'entity_id': 'event.remote_button', 'state': 'unknown', 'attributes': {'device_class': 'button'}},
    {'entity_id': 'event.backup', 'state': '2026-11-15T09:58:46+00:00', 'attributes': {'event_type': 'completed'}},
]
DESCRIPTION = 'Den button. Single press: next of Bright, Dim, Off. Double press: Dim. Long press: Off.'


@pytest.fixture
def fake(monkeypatch):
    asked = []
    reads = {'config/area_registry/list': AREAS, 'config/device_registry/list': DEVICES,
             'config/entity_registry/list': ENTITIES, 'config/label_registry/list': [], 'get_states': STATES}

    def answer(c):
        if c['type'] == 'search/related':
            return {'automation': ['automation.den_button']} if c['item_id'] == 'event.den_button' else {}
        if c['type'] == 'automation/config':
            return {'config': {'alias': 'Den button', 'description': DESCRIPTION}}
        return reads[c['type']]

    def exchange(message, timeout):
        asked.append(message)
        return [answer(c) for c in message['commands']]
    monkeypatch.setattr(broker, '_exchange', exchange)
    read.clear_cache()
    yield asked
    read.clear_cache()


def test_buttons_are_read_with_room_battery_last_press_and_what_presses_do(fake):
    found = read.snapshot(fresh=True)
    den, remote = sorted(found['buttons'], key=lambda b: b['id'])
    assert den['name'] == 'Den button' and den['room'] == 'Den' and den['battery'] == 87
    assert den['last'] == 'Double press' and den['at'].startswith('2026-11-15T20:17')
    assert den['does'] == [{'press': 'Single press', 'does': 'next of Bright, Dim, Off'},
                           {'press': 'Double press', 'does': 'Dim'}, {'press': 'Long press', 'does': 'Off'}]
    assert remote['last'] is None and remote['at'] is None and remote['does'] == []
    assert 'event.backup' not in {b['id'] for b in found['buttons']}       # not a button


def test_presses_are_read_rarely(fake):
    read.snapshot(fresh=True)
    read.snapshot(fresh=True)
    asks = [m for m in fake if any(c['type'] == 'search/related' for c in m['commands'])]
    assert len(asks) == 1


def test_a_button_shows_in_its_room_on_the_home_screen(fake):
    house = model.home(read.snapshot(fresh=True), {})
    (den,) = house['rooms'].values()
    assert [b['name'] for b in den['buttons']] == ['Den button', 'Remote']


def test_an_unreadable_automation_leaves_the_presses_unsaid(fake, monkeypatch):
    def refuse(*commands, timeout=0):
        raise broker.HomeDown('no')
    monkeypatch.setattr(broker, 'ask', refuse)
    found = read.snapshot(fresh=True)
    assert all(b['does'] == [] for b in found['buttons'])


def test_any_other_description_is_one_row():
    assert read._says('Turns the den lamp on.') == [{'press': '', 'does': 'Turns the den lamp on.'}]
    assert read._says(None) == []


def test_only_fenced_reads_with_arguments_leave_hub():
    with pytest.raises(ValueError):
        broker.ask({'type': 'automation/config', 'entity_id': 'automation.x', 'config': {}})
    with pytest.raises(ValueError):
        broker.ask({'type': 'call_service', 'domain': 'light'})
