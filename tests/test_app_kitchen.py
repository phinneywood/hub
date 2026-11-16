"""Kitchen in the app and in Ask (hub/app/kitchen.py, the groceries tool, the Kitchen routes), on a made-up
Kitchen that follows what Hub sends it (tests/kitchen_world.py). No kitchen service, no shop, no model."""
import json

import pytest
from fastapi.testclient import TestClient

from hub.app import kitchen as shop
from hub.app import levels, today
from hub.app.ask import route
from hub.main import app
from hub.tools.house import control
from tests import app_world, ask_script, day_world, kitchen_world
from tests.ask_script import SEEN

client = TestClient(app)
SIGNED_IN = {'origin': 'http://testserver', 'tailscale-user-login': 'me@example.com'}
ON_THIS_MAC = {'origin': 'http://testserver'}


@pytest.fixture
def kitchen(monkeypatch):
    return kitchen_world.install(monkeypatch)


@pytest.fixture
def asking(kitchen, monkeypatch):
    """Ask on the stand-in for its model, with the made-up house and the made-up Kitchen."""
    control._taps.clear()
    control._recent.clear()
    levels.forget()
    SEEN.clear()
    app_world.install(monkeypatch)
    monkeypatch.setattr(route, 'build', lambda http_client=None: ask_script.MODEL)
    monkeypatch.setattr(route, 'ready', lambda: None)
    return kitchen


def ask(text):
    with client.stream('POST', '/app/api/ask', json={'text': text}, headers={**SIGNED_IN, 'accept': 'text/event-stream'}) as stream:
        events = [json.loads(line.removeprefix('data: ')) for line in stream.iter_lines() if line.startswith('data: ')]
    return ''.join(e['text'] for e in events if e['t'] == 'say').strip(), [e['card'] for e in events if e['t'] == 'card'], events[-1]


def picture():
    return client.get('/app/api/kitchen').json()


def tap(do, **body):
    return client.post(f'/app/api/kitchen/{do}', json=body, headers=SIGNED_IN)


def by_name():
    return {i['name']: i for i in picture()['items']}


# ---- the screen ----

def test_the_screen_is_the_list_with_each_things_product_and_where_it_stands(kitchen):
    seen = picture()
    assert seen['ok'] and [(i['name'], i['state']) for i in seen['items']] == [
        ('bananas', 'cart'), ('eggs', 'cart'), ('coffee beans', 'ready'), ('dish soap', 'pick')]
    eggs, soap = seen['items'][1], seen['items'][3]
    assert (eggs['product'], eggs['size'], eggs['why'], eggs['times'], eggs['last']) == ('Meadow Farm Large Brown Eggs', '12 ct', 'usual', 21, '2026-11-02')
    assert soap['product'] == '' and [(o['product'], o['why']) for o in soap['options']] == [('Clear Springs Lemon Dish Soap', 'once')]
    assert seen['plan']['id'] == 7 and seen['order'] is None and seen['practice'] and not seen['auto']
    assert seen['reminders'] == {'state': 'off', 'count': None, 'at': None, 'why': '', 'wanted': False}
    assert seen['history'] == {'orders': 64, 'newest': '2026-11-07', 'stale': True}
    assert [d['name'] for d in seen['due']] == [] and len(seen['ideas']['recipes']) == 2      # eggs and coffee are on the list already


def test_a_thing_typed_on_the_screen_is_picked_and_waits_for_the_cart_tap(kitchen):
    answer = tap('add', text='Sponges, 2 avocados\nmac and cheese').json()
    assert answer['ok'] and answer['said'] == '3 things are on the list'
    assert [(i['name'], i['qty'], i['state'], i['product']) for i in answer['added']] == [
        ('Sponges', 1, 'ready', 'Brightside Non-Scratch Scrub Sponges'), ('avocados', 2, 'ready', 'Green Valley Large Ripe Avocado'),
        ('mac and cheese', 1, 'pick', '')]
    assert kitchen.calls[-1] == ('POST', '/items', {'login': 'me@example.com', 'items': ['Sponges', '2 avocados', 'mac and cheese'], 'source': 'app'})
    assert tap('add', text='sponges').json()['said'] == 'That is already on the list.'
    assert tap('fill').json() == {'ok': True, 'said': 'Adding to the cart', 'changes': 3}
    assert {n: i['state'] for n, i in by_name().items()} == {
        'bananas': 'cart', 'eggs': 'cart', 'coffee beans': 'cart', 'dish soap': 'pick', 'Sponges': 'cart', 'avocados': 'cart', 'mac and cheese': 'pick'}


def test_a_pick_a_removal_and_the_switch(kitchen):
    soap = by_name()['dish soap']
    picked = tap('pick', id=soap['id'], product='Clear Springs Lemon Dish Soap', size='25 fl oz', url='https://evil.example/x').json()
    assert picked['said'] == 'Picked. Hub will choose it next time too' and picked['item']['state'] == 'ready' and picked['item']['why'] == 'chosen'
    assert kitchen.calls[-1][2]['url'] == ''                                           # only the shop's own pages are passed on
    gone = tap('remove', id=by_name()['eggs']['id']).json()
    assert gone['said'] == 'eggs are off the list, and coming out of the cart' and 'eggs' not in by_name()
    assert tap('remove', id=by_name()['coffee beans']['id']).json()['said'] == 'coffee beans are off the list'
    assert tap('auto', on=True).json()['said'] == 'From now on the list fills the cart by itself'
    assert picture()['auto'] and by_name()['dish soap']['state'] == 'cart'
    tap('add', text='limes')
    assert by_name()['limes']['state'] == 'cart'                                         # no tap needed now
    assert tap('reminders', on=True).json()['said'] == 'Reading the Groceries list' and picture()['reminders']['state'] == 'on'
    assert tap('read').json()['said'] == 'Reading the cart'
    assert [line['product'] for line in picture()['cart']['lines']][-1] == 'Sunny Grove Rotini Pasta'
    assert tap('nonsense').json() == {'error': 'Hub couldn’t read that request.'}
    assert tap('remove', id=999).json() == {'error': 'That item isn’t waiting on the list any more.'}


def test_how_many_is_a_tap_and_a_number_written_counts_single_things(kitchen):
    eggs = tap('add', text='6 eggs, 3 avocados').json()
    assert eggs['ok'] and [(i['name'], i['qty'], i['asked']) for i in eggs['changed']] == [('eggs', 1, 6)]       # a dozen covers six
    assert [(i['name'], i['qty'], i['asked']) for i in eggs['added']] == [('avocados', 3, 3)]
    assert tap('add', text='6 eggs').json()['said'] == 'That is already on the list.'
    avocados = by_name()['avocados']
    more = tap('qty', id=avocados['id'], qty=5).json()
    assert (more['ok'], more['said'], more['item']['qty'], more['item']['asked']) == (True, 'avocados: 5', 5, None)
    tap('fill')
    assert by_name()['avocados']['state'] == 'cart'
    fewer = tap('qty', id=avocados['id'], qty=2, carted=True).json()
    assert (fewer['said'], fewer['item']['state']) == ('avocados: 2, waiting for the cart again', 'ready')
    for odd in (0, 25, '3', None, True):
        assert tap('qty', id=avocados['id'], qty=odd).json() == {'error': 'A number from 1 to 24.'}
    assert tap('qty', id=999, qty=2).json() == {'error': 'That item isn’t waiting on the list any more.'}
    assert client.post('/app/api/kitchen/qty', json={'id': avocados['id'], 'qty': 2}, headers=ON_THIS_MAC).status_code in (401, 403)


def test_every_tap_needs_a_signed_in_device(kitchen):
    for do, body in (('add', {'text': 'eggs'}), ('fill', {}), ('auto', {'on': True}), ('remove', {'id': 1}), ('order', {})):
        assert client.post(f'/app/api/kitchen/{do}', json=body, headers=ON_THIS_MAC).status_code == 403
    assert client.post('/app/api/kitchen/fill', json={}, headers={'origin': 'https://evil.example', 'tailscale-user-login': 'me@example.com'}).status_code == 403
    assert not [c for c in kitchen.calls if c[0] == 'POST']
    assert picture()['ok']                                                              # looking needs no login


def test_a_kitchen_that_is_away_or_too_old_says_so(kitchen):
    kitchen.down = True
    assert picture() == {'ok': False, 'why': 'away', 'said': 'Kitchen isn’t answering right now.'}
    assert tap('add', text='eggs').json() == {'error': 'Kitchen isn’t answering right now.'}
    kitchen.down, kitchen.old = False, True
    assert picture()['why'] == 'old' and 'Restart the kitchen service' in picture()['said']
    assert tap('add', text='eggs').json()['error'] == picture()['said']


# ---- in Ask ----

def test_ask_adds_to_the_list_in_one_trip_and_says_only_the_owners_words(asking):
    answer, cards, end = ask('Add sponges and 2 avocados to the list')
    assert answer == 'Sponges and avocados are on the list.' and end['status'] == 'ok' and len(SEEN) == 1
    assert cards == [{'kind': 'groceries', 'items': [5, 6]}]
    assert asking.calls[-1] == ('POST', '/items', {'login': 'me@example.com', 'items': ['sponges', '2 avocados'], 'source': 'ask'})
    assert by_name()['avocados']['qty'] == 2
    assert ask('we’re out of dragon fruit')[0] == 'Dragon fruit is on the list. Hub hasn’t bought it before: pick one below.'
    assert ask('add eggs to the cart')[0] == 'Eggs are already on the list.'
    assert ask('add milk and eggs to the list')[0] == 'Milk is on the list. Eggs were already there.'


def test_ask_changes_a_number_when_the_same_thing_is_said_again(asking):
    assert ask('add avocados to the list')[0] == 'Avocados are on the list.'
    answer, cards, _ = ask('add 5 avocados to the list')
    assert answer == 'Avocados changed to five.' and by_name()['avocados']['qty'] == 5
    assert cards == [{'kind': 'groceries', 'items': [by_name()['avocados']['id']]}]
    assert ask('add 5 avocados and sponges to the list')[0] == 'Sponges are on the list. Avocados were already there.'


def test_ask_reads_the_cart_from_its_own_page_and_says_only_how_many(asking):
    answer, cards, _ = ask('What’s in the cart?')
    assert answer == 'Reading the cart. This takes a minute.' and cards[0]['kind'] == 'groceries' and cards[0]['cart'] is True and cards[0]['since'] > 0
    assert ('POST', '/cart/read', {'login': 'me@example.com'}) in asking.calls
    answer, cards, _ = ask('what is in the cart')                        # read a moment ago: shown as it is, not read again
    assert answer == 'Five things are in the cart.' and cards == [{'kind': 'groceries', 'cart': True}]
    assert [c for c in asking.calls if c[1] == '/cart/read'] == [('POST', '/cart/read', {'login': 'me@example.com'})]
    assert not any(word in json.dumps([str(m) for m in SEEN]) for word in ('Rotini', '3.19', 'Sunny Acres'))     # no product, no price to the model


def test_nothing_of_kitchens_reaches_asks_model(asking):
    ask('Add sponges and milk to the list')
    ask('what’s on the list?')
    ask('dim the kitchen')
    everything = json.dumps([[str(p.content) for m in turn for p in m.parts] for turn in SEEN], ensure_ascii=False)
    for private in ('Brightside', 'Meadow Farm', 'two a week', '5.79', 'Harbor Roasters', 'coffee beans', 'dish soap'):
        assert private not in everything, private
    assert 'Asked: what’s on the list?\\nHub: Six things are on the list: two in the cart, one needs a pick.' in everything


def test_ask_shows_the_list_takes_things_off_and_readies_checkout(asking):
    answer, cards, _ = ask('What’s on the list?')
    assert answer == 'Four things are on the list: two in the cart, one needs a pick.' and cards == [{'kind': 'groceries', 'all': True}]
    assert ask('take the eggs off the list')[0] == 'Eggs are off the list, and coming out of the cart.'
    assert ask('remove coffee beans from the list')[0] == 'Coffee beans are off the list.'
    assert ask('take the caviar off the list')[:2] == ('Hub didn’t find that on the list.', [{'kind': 'groceries', 'all': True}])
    answer, cards, _ = ask('order the groceries for tomorrow morning')
    assert answer == 'Reading checkout. This takes a minute.' and cards == [{'kind': 'kitchen', 'after': 400}]
    assert asking.calls[-1] == ('POST', '/order', {'login': 'me@example.com', 'when': 'tomorrow morning'})
    assert picture()['order']['status'] == 'pending'
    for item in list(asking.items):
        asking.items.remove(item)
    assert ask('show the list')[0] == 'The list is empty.'


def test_recipes_and_questions_still_go_to_the_kitchen_assistant_word_for_word(asking):
    answer, cards, _ = ask('What should we cook this week?')
    assert answer == 'Kitchen is on it.' and cards == [{'kind': 'kitchen', 'after': 101}]
    assert asking.calls[-1] == ('POST', '/messages', {'login': 'me@example.com', 'text': 'What should we cook this week?'})


def test_with_an_older_kitchen_the_words_go_to_its_assistant_instead(asking):
    asking.old = True
    answer, cards, _ = ask('add sponges to the list')
    assert answer == 'Kitchen is on it.' and cards == [{'kind': 'kitchen', 'after': 101}]
    asking.old, asking.down = False, True
    assert ask('add sponges to the list')[0] == 'Kitchen isn’t answering right now.'


# ---- Today ----

def test_today_asks_for_the_one_thing_kitchen_most_needs(kitchen, monkeypatch):
    day_world.install(monkeypatch, kitchen_too=False)
    need = lambda: [(n['title'], n['tone']) for n in today._cart()]      # noqa: E731
    assert need() == [('Approve the grocery cart', 'warn')]
    kitchen.plans['7'] = {'status': 'done'}
    assert need() == [('Pick a product for the list', 'info')]
    tap('add', text='mac and cheese')
    assert need() == [('Pick products for 2 things on the list', 'info')]
    tap('order')
    assert need() == [('Place the grocery order', 'warn')]
    kitchen.old = True
    kitchen.plans['7'] = {'status': 'pending'}
    assert need() == [('Approve the grocery cart', 'warn')]                 # an older Kitchen is still read the older way
    kitchen.down = True
    assert need() == []


def test_what_was_typed_is_parted_by_commas_and_lines_only():
    assert shop.written('eggs, 2 avocados\n mac and cheese ; ') == ['eggs', '2 avocados', 'mac and cheese']
    assert shop.written('  ') == [] and len(shop.written(','.join(['x'] * 50))) == 20
