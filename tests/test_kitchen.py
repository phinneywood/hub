"""Kitchen tab: a thin front end for the separate kitchen service. The service is faked here."""
import pytest
from fastapi.testclient import TestClient

from hub import home
from hub.main import app
from hub.tools.kitchen import client as kitchen

ORIGIN = {'origin': 'http://testserver'}
MEMBER = {**ORIGIN, 'tailscale-user-login': 'me@example.com'}


@pytest.fixture
def service(monkeypatch):
    calls = []

    def fake(method, path, body=None, timeout=10):
        calls.append((method, path, body))
        if path.startswith('/messages') and method == 'GET':
            return 200, {'messages': [], 'plans': {'3': {'status': 'pending', 'decided_by': None}}, 'busy': None, 'queued': 0}
        return 200, {'ok': True}
    monkeypatch.setattr(kitchen, 'call', fake)
    return calls


def test_page_and_nav():
    page = TestClient(app).get('/kitchen')
    assert page.status_code == 200
    assert '>Kitchen</h1>' in page.text
    for where in ('/today', '/tools'):      # Kitchen stays one tap away: its own card, and the All tools menu
        assert 'href="/kitchen"' in TestClient(app).get(where).text


def test_message_carries_the_members_login(service):
    r = TestClient(app).post('/kitchen/messages', json={'text': 'eggs'}, headers=MEMBER)
    assert r.status_code == 200
    assert service[-1] == ('POST', '/messages', {'login': 'me@example.com', 'text': 'eggs'})


def test_approve_needs_a_signed_in_device(service):
    c = TestClient(app)
    assert c.post('/kitchen/plans/3/approve', headers=ORIGIN).status_code == 403
    assert not service
    assert c.post('/kitchen/plans/3/approve', headers=MEMBER).status_code == 200
    assert service[-1] == ('POST', '/plans/3/approve', {'login': 'me@example.com'})
    assert c.post('/kitchen/plans/3/checkout', headers=MEMBER).status_code == 404


def test_cross_site_approve_is_refused(service):
    r = TestClient(app).post('/kitchen/plans/3/approve',
                             headers={'origin': 'https://evil.example', 'tailscale-user-login': 'me@example.com'})
    assert r.status_code == 403 and not service


def test_strangers_are_refused(service):
    r = TestClient(app).post('/kitchen/messages', json={'text': 'hi'},
                             headers={**ORIGIN, 'tailscale-user-login': 'stranger@example.com'})
    assert r.status_code == 403 and not service


def test_service_down_is_a_clear_502(monkeypatch):
    def down(*a, **k):
        raise kitchen.KitchenDown('ConnectionRefusedError')
    monkeypatch.setattr(kitchen, 'call', down)
    r = TestClient(app).get('/kitchen/messages')
    assert r.status_code == 502 and 'isn’t answering' in r.json()['error']
    assert home.kitchen()['headline'] == 'Kitchen is offline'


def test_tile_flags_a_plan_waiting(service):
    assert home.kitchen()['headline'] == 'Cart plan to approve'


def test_service_worker_is_served_as_javascript():
    r = TestClient(app).get('/kitchen/sw.js')
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/javascript')
    assert 'showNotification' in r.text


def test_learn_new_orders_is_relayed(service):
    assert TestClient(app).post('/kitchen/learn', headers=MEMBER).status_code == 200
    assert service[-1] == ('POST', '/learn', {'login': 'me@example.com'})


def test_placing_an_order_needs_a_signed_in_device_and_names_the_member(service):
    assert TestClient(app).post('/kitchen/orders/7/place', headers=ORIGIN).status_code == 403
    assert TestClient(app).post('/kitchen/orders/7/buy', headers=MEMBER).status_code == 404
    assert TestClient(app).post('/kitchen/orders/7/place', headers=MEMBER).status_code == 200
    assert service[-1] == ('POST', '/orders/7/place', {'login': 'me@example.com'})
    assert TestClient(app).post('/kitchen/orders/7/place', headers={'tailscale-user-login': 'me@example.com',
                                                                    'origin': 'https://elsewhere.example'}).status_code == 403
