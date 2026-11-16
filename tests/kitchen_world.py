"""A made-up Kitchen for the app's tests and its demo server: it stands where the kitchen service would and
follows what Hub sends it. A list whose items are given made-up products from a made-up history, a practice
cart that fills a few moments after it is asked to, a conversation so far, a cart plan and an order that wait for
a tap. Every product, price and person here is invented, and nothing reaches a shop.

It answers the service's own requests in the service's own shapes (see the kitchen service's repository), so
hub/app/kitchen.py, the routes and Ask's tools above it are all the real ones.
"""
import copy
import re
import time

from hub.tools.kitchen import client

# What this made-up household buys: the word on the list, then (product, size, why, orders, last bought, price,
# how many they usually take, a note of their own).
BUYS = {
    'banana': ('Sunny Acres Organic Bananas', '1 bunch', 'usual', 31, '2026-11-07', '1.89', 1, ''),
    'egg': ('Meadow Farm Large Brown Eggs', '12 ct', 'usual', 21, '2026-11-02', '4.29', 1, ''),
    'milk': ('Meadow Farm Organic Whole Milk', '1/2 gal', 'usual', 18, '2026-11-07', '4.99', 2, 'two a week'),
    'coffee bean': ('Harbor Roasters Breakfast Blend Whole Bean Coffee', '12 oz', 'usual', 9, '2026-10-26', '13.49', 1, ''),
    'sponge': ('Brightside Non-Scratch Scrub Sponges', '6 ct', 'history', 3, '2026-10-21', '5.79', 1, ''),
    'avocado': ('Green Valley Large Ripe Avocado', '1 each', 'usual', 12, '2026-11-07', '1.50', 3, ''),
    'butternut squash': ('Green Valley Butternut Squash', '1 each', 'history', 4, '2026-10-12', '2.49', 1, ''),
    'lime': ('Fresh Limes', '1 each', 'history', 6, '2026-10-26', '0.50', 3, ''),
}
# Other things they have bought that the same word could mean, offered beside the pick.
ALSO = {
    'sponge': [('Tidy Home Heavy Duty Sponges', '3 ct', 'history', 2, '2026-11-02', '4.29')],
    'milk': [('Oatfield Original Oat Milk', '64 fl oz', 'usual', 14, '2026-11-02', '4.79')],
    'dish soap': [('Clear Springs Lemon Dish Soap', '25 fl oz', 'once', 1, '2026-09-02', '4.79')],
}
DUE = [{'name': 'eggs', 'product': 'Meadow Farm Large Brown Eggs', 'size': '12 ct', 'every': 9, 'days': 13, 'last': '2026-11-02'},
       {'name': 'coffee beans', 'product': 'Harbor Roasters Breakfast Blend Whole Bean Coffee', 'size': '12 oz', 'every': 14, 'days': 20,
        'last': '2026-10-26'}]
SAYS = {'role': 'assistant', 'text': 'Three dinners you don’t usually make. Tell me which and I’ll add what you’re missing to the cart.',
        'recipes': [{'title': 'Miso-glazed eggplant with sesame rice', 'total': '40 minutes', 'active': '20 min', 'dishes': '1 sheet pan, 1 pot',
                     'diet': 'Safe as written', 'recent': 'rice, sesame oil', 'check': 'miso', 'need': 'eggplant, scallions'},
                    {'title': 'Chicken with preserved lemon and olives', 'total': '55 minutes', 'active': '15 min', 'dishes': '1 pan',
                     'diet': 'Safe as written', 'need': 'preserved lemon, green olives'},
                    {'title': 'Black bean and squash tacos', 'total': '30 minutes', 'active': '15 min', 'diet': 'Vegetarian',
                     'recent': 'black beans, tortillas', 'need': 'squash, lime'}]}
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
     'plan': {'id': 7, 'status': 'pending', 'items': [{'product': 'Green Valley Butternut Squash', 'size': '1 each', 'qty': 1, 'price': '2.49'},
                                                      {'product': 'Fresh Limes', 'size': '1 each', 'qty': 3, 'price': '0.50'}]}},
]
REVIEW = {'items': 14, 'subtotal': 52.10, 'fees': 4.95, 'savings': 3.20, 'tax': 0.0, 'total': 53.85, 'when': 'Tomorrow, 9–10 am', 'how': 'Pickup'}
ON_THE_LIST = ['bananas', 'eggs', 'coffee beans', 'dish soap']        # how the made-up list starts; the first two are in the cart


def _one(word):
    return word[:-1] if len(word) > 3 and word.endswith('s') and not word.endswith('ss') else word


def _key(name):
    return ' '.join(_one(w) for w in re.sub(r'[^a-z0-9]+', ' ', str(name).lower()).split())


def _wanted(written):
    """(the thing, how many) from what was written: "2 avocados" is two."""
    found = re.match(r'^\s*(\d{1,2})\s*x?\s+(.+)$', str(written))
    return (found.group(2).strip(), int(found.group(1))) if found else (' '.join(str(written).split()), 0)


def _take(asked, size, usually):
    """How many to take: a number written counts single things, and a pack of several ("12 ct") covers them."""
    pack = re.search(r'(\d+) ct', size or '')
    return usually if not asked else -(-asked // int(pack.group(1))) if pack and int(pack.group(1)) > 1 else asked


class Kitchen:
    def __init__(self, takes=0.0, cart_waiting=True):
        self.takes = takes              # seconds the made-up Kitchen takes to answer, to fill the cart and to read checkout
        self.calls, self.down, self.old = [], False, False
        self.reset(cart_waiting)

    def reset(self, cart_waiting=True):
        """As it started: a short list, part of it in the cart, a cart plan waiting, nothing asked yet."""
        self.items, self.asked, self.notices, self.next = [], [], [], 1
        self.plans = {'7': {'status': 'pending' if cart_waiting else 'done'}}
        self.orders, self.review_at = {}, None
        self.auto, self.reminders = False, {'state': 'off'}
        self.cart_seen, self.moving = None, {}          # {item id: when it lands in the cart or leaves it}
        for name in ON_THE_LIST:
            self._add(name, 'app', 'Alex')
        for item in self.items[:2]:
            item['state'] = 'cart'

    # ---- the list ----

    def _add(self, written, source, by):
        name, qty = _wanted(written)
        key = _key(name)
        if any(_key(i['name']) == key for i in self.items):
            twin = next(i for i in self.items if _key(i['name']) == key)
            if qty and qty != twin['asked']:                # said again with another number
                take = _take(qty, twin['size'], 1)         # back to waiting only when the cart must be set to a new number
                twin.update(asked=qty, state='ready' if twin['state'] == 'cart' and take != twin['qty'] else twin['state'], qty=take)
                return None, dict(twin, changed=True)
            return None, twin
        buys = BUYS.get(key)
        options = [{'product': p, 'size': s, 'why': why, 'times': n, 'last': last, 'price': price, 'qty': 1, 'note': ''}
                   for p, s, why, n, last, price in ALSO.get(key, [])]
        item = {'id': self.next, 'at': time.time(), 'name': name, 'qty': _take(qty, buys[1] if buys else '', buys[6] if buys else 1),
                'asked': qty or None, 'source': source, 'by': by, 'note': '',
                'product': buys[0] if buys else None, 'size': buys[1] if buys else None, 'why': buys[2] if buys else None,
                'times': buys[3] if buys else 0, 'last': buys[4] if buys else None, 'price': buys[5] if buys else None,
                'about': buys[7] if buys else None, 'url': '', 'state': 'ready' if buys else 'pick', 'how': None, 'options': options, 'left': False}
        if not buys and not options:        # never bought: what the shop offers, as the service's search would bring back
            title = name.capitalize()
            item['options'] = [{'product': f'Store Brand {title}', 'size': '', 'why': 'shop', 'times': 0, 'last': '', 'price': '3.49', 'qty': 1, 'note': ''},
                               {'product': f'Organic {title}', 'size': '', 'why': 'shop', 'times': 0, 'last': '', 'price': '4.99', 'qty': 1, 'note': ''}]
        self.next += 1
        self.items.append(item)
        if self.auto and item['state'] == 'ready':
            self._fill()
        return item, None

    def _fill(self, adding=True):
        """Start putting what is ready into the cart (`adding`), and taking out what is leaving; it lands `takes`
        seconds later."""
        changed = 0
        for item in self.items:
            if item['state'] == 'ready' and adding:
                item['state'] = 'adding'
            if item['state'] in ('adding', 'removing') and item['id'] not in self.moving:
                self.moving[item['id']] = time.monotonic() + self.takes
                changed += 1
        return changed

    def _settle(self):
        """What has had its time: in the cart now, or gone from it."""
        now = time.monotonic()
        for item in list(self.items):
            if item['id'] in self.moving and now >= self.moving[item['id']]:
                del self.moving[item['id']]
                if item['state'] == 'removing':
                    self.items.remove(item)
                else:
                    item['state'] = 'unavailable' if 'out of stock' in (item['product'] or '') else 'cart'
        if self.review_at is not None and now >= self.review_at and not self.orders:
            self.orders['3'] = {'status': 'pending'}

    def _state(self):
        self._settle()
        pending = self.plans.get('7', {}).get('status') == 'pending'
        order = self.orders.get('3')
        return {'items': copy.deepcopy(self.items), 'due': [d for d in DUE if not any(_key(i['name']) == _key(d['name']) for i in self.items)],
                'plan': {'id': 7, 'status': 'pending', 'items': SO_FAR[3]['plan']['items']} if pending else None,
                'order': {'id': 3, 'review': REVIEW, 'status': order['status'], 'decided_by': order.get('decided_by'), 'placed': order.get('placed')}
                if order else None,
                'cart': {'seen': copy.deepcopy(self.cart_seen), 'practice': True},
                'ideas': {'at': time.time() - 3 * 3600, 'after': 2, 'recipes': SO_FAR[1]['recipes']},
                'settings': {'auto_cart': self.auto, 'cart_wait': 60, 'reminders': dict(self.reminders, wanted=self.reminders['state'] != 'off')},
                'history': {'orders': 64, 'newest': '2026-11-07', 'stale': True},
                'busy': 'apply' if self.moving else 'review' if self.review_at and not self.orders else None, 'queued': 0}

    def _messages(self, after):
        self._settle()
        now, out = time.monotonic(), [dict(m, at=time.time() - 3600 + m['id'] * 60) for m in SO_FAR if m['id'] > after]
        for n, (at, text) in enumerate(self.asked, 1):
            if FIRST + n * 2 - 1 > after:
                out.append({'id': FIRST + n * 2 - 1, 'role': 'member', 'text': text, 'author': 'me', 'at': time.time()})
            if FIRST + n * 2 > after and now - at >= self.takes:
                out.append(dict(SAYS, id=FIRST + n * 2, at=time.time()))
        for number, words in self.notices:
            if number > after:
                out.append({'id': number, 'role': 'notice', 'text': words, 'at': time.time()})
        if self.orders and 500 > after:
            out.append({'id': 500, 'role': 'assistant', 'at': time.time(), 'text': 'Here is the order, as checkout shows it. Nothing is ordered '
                        'until you tap Place order.', 'order': {'id': 3, 'status': self.orders['3']['status'], 'review': REVIEW}})
        out.sort(key=lambda m: m['id'])
        return {'messages': out, 'busy': 'chat' if any(now - at < self.takes for at, _ in self.asked) else self._state()['busy'], 'queued': 0,
                'plans': copy.deepcopy(self.plans), 'orders': copy.deepcopy(self.orders)}

    # ---- the service's requests ----

    def call(self, method, path, body=None, timeout=10):
        self.calls.append((method, path, body))
        if self.down:
            raise client.KitchenDown('made up')
        body = body or {}
        if (method, path) == ('GET', '/kitchen'):
            return (404, {'error': 'not found'}) if self.old else (200, self._state())
        if method == 'GET' and path.startswith('/messages?after='):
            return 200, self._messages(int(path.rsplit('=', 1)[1]))
        if (method, path) == ('GET', '/list'):
            return 200, {'text': '# Shopping list\n' + ''.join(f'- {i["name"]}\n' for i in self.items)}
        if (method, path) == ('GET', '/status'):
            return 200, {'busy': None, 'queued': 0, 'push_public_key': None}
        if method != 'POST':
            return 404, {'error': 'not found'}
        if (path.startswith(('/items', '/cart/', '/settings', '/order')) and path != '/orders') and not path.startswith('/orders/'):
            if self.old:
                return 404, {'error': 'not found'}
            if not body.get('login'):
                return 403, {'error': 'the list is changed from a signed-in member’s device'}
        if path == '/messages':
            self.asked.append((time.monotonic(), body.get('text', '')))
            return 200, {'id': FIRST + len(self.asked) * 2 - 1}
        if path.startswith(('/plans/', '/orders/')):
            _, what, number, choice = path.split('/')
            getattr(self, what)[number] = {'status': {'approve': 'done', 'place': 'placed'}.get(choice, 'dismissed'), 'decided_by': 'me',
                                           **({'placed': {'status': 'placed', 'order_number': 'PRACTICE', 'total': REVIEW['total']}}
                                              if choice == 'place' else {})}
            if choice == 'place':
                self.items = [i for i in self.items if i['state'] != 'cart']
                self.cart_seen = None
            self.notices.append((50 + len(self.notices), f'me {"approved" if choice == "approve" else "placed" if choice == "place" else "dismissed"} '
                                 f'the {"cart plan" if what == "plans" else "order"}.'))
            return 200, {'ok': True}
        if path == '/learn':
            return 200, {'ok': True}
        if path == '/items':
            added, already, changed = [], [], []
            for written in body.get('items') or []:
                new, twin = self._add(written, body.get('source') or 'app', 'me')
                (added if new else changed if twin.pop('changed', False) else already).append(copy.deepcopy(new or twin))
            return 200, {'added': added, 'already': already, 'changed': changed}
        found = re.fullmatch(r'/items/(\d+)/(remove|pick|keep|qty)', path)
        if found:
            item = next((i for i in self.items if i['id'] == int(found.group(1))), None)
            if item is None or (found.group(2) == 'pick' and item['state'] not in ('pick', 'ready', 'unavailable', 'unknown')):
                return 409, {'error': 'That item isn’t waiting on the list any more.'}
            if found.group(2) == 'qty':
                if item['qty'] != body['qty']:
                    item.update(qty=body['qty'], asked=None, state='ready' if item['state'] == 'cart' else item['state'])
                    if self.auto:
                        self._fill()
            elif found.group(2) == 'pick':
                item.update(product=body.get('product'), size=body.get('size') or '', price=body.get('price') or '', why='chosen', state='ready',
                            times=0, last=None, about=None)
                if self.auto:
                    self._fill()
            elif found.group(2) == 'keep':
                item['left'] = False
            elif item['state'] in ('cart', 'unknown'):
                item.update(state='removing', left=False)
                self._fill(adding=self.auto)
            else:
                self.items.remove(item)
                item = dict(item, state='gone', how='removed')
            return 200, {'item': copy.deepcopy(item)}
        if path == '/cart/fill':
            return 200, {'ok': True, 'changes': self._fill()}
        if path == '/cart/read':
            self._settle()
            for item in self.items:             # what nobody could confirm is settled by the cart's own page
                if item['state'] == 'unknown':
                    item['state'] = 'cart'
            lines = [{'product': i['product'], 'size': i['size'] or '', 'qty': i['qty'], 'price': float(i['price']) if i['price'] else None}
                     for i in self.items if i['state'] == 'cart']
            lines += [{'product': 'Sunny Grove Rotini Pasta', 'size': '12 oz', 'qty': 3, 'price': 3.19}]     # something put in by hand
            self.cart_seen = {'lines': lines, 'subtotal': round(sum((line['price'] or 0) * line['qty'] for line in lines), 2),
                              'when': 'Pickup tomorrow, 9–10 am', 'at': time.time()}
            return 200, {'ok': True}
        if path == '/settings':
            if isinstance(body.get('auto_cart'), bool):
                self.auto = body['auto_cart']
                if self.auto:
                    self._fill()
            if isinstance(body.get('reminders'), bool):
                self.reminders = {'state': 'on', 'count': 4, 'at': time.time()} if body['reminders'] else {'state': 'off'}
            return 200, {'ok': True, 'auto_cart': self.auto, 'reminders': self.reminders['state'] != 'off'}
        if path == '/order':
            self.review_at, self.orders = time.monotonic() + self.takes, {}
            self.notices.append((400, 'me asked to check out.'))
            return 200, {'ok': True, 'id': 400}
        return 404, {'error': 'not found'}


def install(patch, takes=0.0, cart_waiting=True):
    """Put the made-up Kitchen where the kitchen service would be. `patch` is pytest's monkeypatch or a MonkeyPatch."""
    kitchen = Kitchen(takes, cart_waiting)
    patch.setattr(client, 'call', kitchen.call)
    return kitchen
