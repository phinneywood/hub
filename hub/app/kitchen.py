"""Kitchen for the app and for Ask: the household's shopping list, each thing's product and where it stands on
its way into the cart, what waits for a tap, the cart as last read, and the few settings.

All of it comes from the separate kitchen service over its owner-only socket (hub/tools/kitchen/client.py), which
keeps the list, picks each product from the household's own purchase history in code, and does the clicking in
the shop. Hub adds nothing and keeps nothing: this file asks, shapes the answer for the screen, and passes taps
on with the signed-in member's login. No model is called here.

What is here is private household data: products, prices, and notes about who eats what. It is drawn on the
Kitchen screen and on Ask's cards for signed-in members. None of it is ever given to Ask's model: Ask's own
sentences about the list say how many things and the member's own words, nothing more (hub/app/ask/tools.py).
"""
import re

from ..tools.kitchen import client
from .words import are

TEXT = 200
MOST_ADDED = 20             # things added by one request
MOST_OF_ONE = 24            # how many of one thing
STATES = ('pick', 'ready', 'adding', 'cart', 'unavailable', 'unknown', 'removing')
SHOP = 'https://www.fredmeyer.com/'
OLD = 'Kitchen on this Mac is an older version without the list. Restart the kitchen service to update it.'
AWAY = 'Kitchen isn’t answering right now.'


class Refused(Exception):
    """A tap or request that can't be done as asked; the words are for the member."""


class Old(Refused):
    """The kitchen service is a version from before it kept the list itself."""


def _clip(text, most=TEXT):
    return ' '.join(str(text or '').split())[:most]


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _option(o):
    return {'product': _clip(o.get('product')), 'size': _clip(o.get('size'), 60), 'why': _clip(o.get('why'), 12),
            'times': o.get('times') if isinstance(o.get('times'), int) else 0, 'last': _clip(o.get('last'), 10),
            'price': _clip(o.get('price'), 12), 'url': o['url'] if str(o.get('url') or '').startswith(SHOP) else '',
            'note': _clip(o.get('note'), 160)}


def _item(i):
    return {'id': int(i['id']), 'name': _clip(i.get('name'), 100), 'qty': i.get('qty') if isinstance(i.get('qty'), int) else 1,
            'asked': i.get('asked') if isinstance(i.get('asked'), int) and not isinstance(i.get('asked'), bool) else None,
            'source': _clip(i.get('source'), 12), 'by': _clip(i.get('by'), 40), 'note': _clip(i.get('note')),
            'product': _clip(i.get('product')), 'size': _clip(i.get('size'), 60), 'why': _clip(i.get('why'), 12),
            'times': i.get('times') if isinstance(i.get('times'), int) else 0, 'last': _clip(i.get('last'), 10),
            'price': _clip(i.get('price'), 12), 'about': _clip(i.get('about'), 160),
            'state': i.get('state') if i.get('state') in STATES else 'unknown', 'left': bool(i.get('left')),
            'options': [_option(o) for o in i.get('options') or () if isinstance(o, dict) and o.get('product')][:8]}


def _ask(method, path, body=None, timeout=8):
    """One request to the kitchen service; returns its answer. Raises Refused in the member's words."""
    try:
        status, data = client.call(method, path, body, timeout=timeout)
    except client.KitchenDown:
        raise Refused(AWAY) from None
    if status == 404:
        raise Old(OLD)
    if status == 403:
        raise Refused('Kitchen doesn’t know this login. Ask the owner to add it as a member.')
    if status >= 400:
        raise Refused(_clip(data.get('error')) or 'Kitchen couldn’t do that.')
    return data


def state():
    """The kitchen service's own picture, as it sends it. Raises Refused."""
    return _ask('GET', '/kitchen')


def picture():
    """The Kitchen screen as data. Never raises: a Kitchen that is away, or too old, says so."""
    try:
        found = state()
    except Old:
        return {'ok': False, 'why': 'old', 'said': OLD}
    except Refused as error:
        return {'ok': False, 'why': 'away', 'said': str(error)}
    seen = (found.get('cart') or {}).get('seen') or None
    settings, reminders = found.get('settings') or {}, (found.get('settings') or {}).get('reminders') or {}
    ideas = found.get('ideas') or None
    return {
        'ok': True, 'items': [_item(i) for i in found.get('items') or () if isinstance(i, dict) and 'id' in i],
        'plan': found.get('plan'), 'order': found.get('order'),
        'cart': {'lines': [{'product': _clip(line.get('product')), 'size': _clip(line.get('size'), 60),
                            'qty': line.get('qty') if isinstance(line.get('qty'), int) else 1, 'price': _number(line.get('price'))}
                           for line in seen.get('lines') or () if isinstance(line, dict)][:80],
                 'subtotal': _number(seen.get('subtotal')), 'when': _clip(seen.get('when'), 120), 'at': _number(seen.get('at'))} if seen else None,
        'practice': bool((found.get('cart') or {}).get('practice')),
        'due': [{'name': _clip(d.get('name'), 100), 'product': _clip(d.get('product')), 'every': d.get('every'), 'days': d.get('days')}
                for d in found.get('due') or () if isinstance(d, dict)][:6],
        'ideas': {'at': ideas.get('at'), 'recipes': [r for r in ideas.get('recipes') or () if isinstance(r, dict)][:3]} if ideas else None,
        'auto': bool(settings.get('auto_cart')), 'wait': settings.get('cart_wait') if isinstance(settings.get('cart_wait'), int) else 60,
        'reminders': {'state': reminders.get('state') if reminders.get('state') in ('on', 'away', 'off') else 'off',
                      'count': reminders.get('count') if isinstance(reminders.get('count'), int) else None,
                      'at': _number(reminders.get('at')), 'why': _clip(reminders.get('why'), 80), 'wanted': bool(reminders.get('wanted'))},
        'history': {'orders': (found.get('history') or {}).get('orders') or 0, 'newest': _clip((found.get('history') or {}).get('newest'), 10),
                    'stale': bool((found.get('history') or {}).get('stale'))},
        'working': _clip(found.get('busy'), 12) or ('queued' if found.get('queued') else '')}


def written(text):
    """What was typed into the list's own field, as separate things: commas and new lines part them. "Mac and
    cheese" stays one thing."""
    return [part for part in (_clip(p, 100) for p in re.split(r'[,\n;]+', str(text or ''))) if part][:MOST_ADDED]


def add(items, login, source='app'):
    """Put things on the list, each as it was said. Returns {'added': [...], 'already': [...], 'changed': [...]} in
    the app's shape: `changed` is what was on the list already and was said again with another number."""
    wanted = [w for w in (_clip(i, 100) for i in items or ()) if w][:MOST_ADDED]
    if not wanted:
        raise Refused('Say what to add.')
    found = _ask('POST', '/items', {'login': login, 'items': wanted, 'source': source})
    return {key: [_item(i) for i in found.get(key) or ()] for key in ('added', 'already', 'changed')}


def _plain(word):
    return word[:-1] if len(word) > 3 and word.endswith('s') and not word.endswith('ss') else word


def _words(text):
    return {_plain(w) for w in re.sub(r'[^a-z0-9]+', ' ', str(text).lower()).split()} - {'the', 'a', 'an', 'some', 'of'}


def named(names, items):
    """The list's items that the member's words mean: the same words, else the one item that holds them all."""
    found = []
    for name in names or ():
        asked = _words(name)
        same = [i for i in items if _words(i['name']) == asked] or [i for i in items if asked and asked <= _words(i['name'])]
        if len(same) == 1 and same[0] not in found:
            found.append(same[0])
    return found


def remove(item_id, login):
    """Take one thing off the list; out of the cart too, if it was there. Returns the item as it now stands."""
    return _item(_ask('POST', f'/items/{int(item_id)}/remove', {'login': login})['item'])


def order(when, login):
    """Ask for checkout to be read, never placed. Returns the message the review will follow."""
    return _ask('POST', '/order', {'login': login, 'when': _clip(when)}).get('id')


def tap(do, body, login):
    """A tap on the Kitchen screen or on one of Ask's Kitchen cards. Returns ({what to say}, anything the app
    needs back). Raises Refused."""
    body = body if isinstance(body, dict) else {}
    if do == 'add':
        found = add(written(body.get('text')), login)
        names = [i['name'] for i in found['added']]
        if not names and found['changed']:
            return f'{found["changed"][0]["name"]}: now {found["changed"][0]["qty"]}', found
        if not names:
            return ('That is already on the list.' if found['already'] else 'Nothing to add.'), found
        return (f'{names[0]} {are(names)} on the list' if len(names) == 1 else f'{len(names)} things are on the list'), found
    if do == 'remove':
        gone = remove(body.get('id'), login)
        return f'{gone["name"]} {are([gone["name"]])} off the list' + (', and coming out of the cart' if gone['state'] == 'removing' else ''), {'item': gone}
    if do == 'pick':
        product = _clip(body.get('product'))
        if not product:
            raise Refused('Pick a product.')
        found = _ask('POST', f'/items/{int(body.get("id"))}/pick', {
            'login': login, 'product': product, 'size': _clip(body.get('size'), 60), 'price': _clip(body.get('price'), 12),
            'url': body['url'] if str(body.get('url') or '').startswith(SHOP) else ''})
        return 'Picked. Hub will choose it next time too', {'item': _item(found['item'])}
    if do == 'qty':
        number = body.get('qty')
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= MOST_OF_ONE:
            raise Refused(f'A number from 1 to {MOST_OF_ONE}.')
        item = _item(_ask('POST', f'/items/{int(body.get("id"))}/qty', {'login': login, 'qty': number})['item'])
        back = item['state'] == 'ready' and body.get('carted') is True          # it was in the cart: the cart is set to the new number next
        return f'{item["name"]}: {item["qty"]}' + (', waiting for the cart again' if back else ''), {'item': item}
    if do == 'keep':
        return 'Kept in the cart', {'item': _item(_ask('POST', f'/items/{int(body.get("id"))}/keep', {'login': login})['item'])}
    if do == 'fill':
        changes = _ask('POST', '/cart/fill', {'login': login}).get('changes') or 0
        return ('Adding to the cart' if changes else 'Nothing is waiting for the cart'), {'changes': changes}
    if do == 'read':
        _ask('POST', '/cart/read', {'login': login})
        return 'Reading the cart', {}
    if do == 'order':
        return 'Getting checkout ready. Nothing is ordered until you tap Place order', {'after': order(body.get('when'), login)}
    if do in ('auto', 'reminders') and isinstance(body.get('on'), bool):
        _ask('POST', '/settings', {'login': login, 'auto_cart' if do == 'auto' else 'reminders': body['on']})
        if do == 'auto':
            return ('From now on the list fills the cart by itself' if body['on'] else 'The list waits for your tap from now on'), {}
        return ('Reading the Groceries list' if body['on'] else 'The Groceries list is no longer read'), {}
    raise Refused('Hub couldn’t read that request.')
