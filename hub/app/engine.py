"""The Engine screen's data and taps: what the owner asked Hub for, what Hub may do unasked, what it spent.

The same reading and the same changes as the older Engine page (hub/main.py `/engine`, hub/engine/), in the app's
shape. Nothing new is read and no new effect is possible: every tap here was a form on that page.
"""
from .. import home, luna, people, store
from ..engine import ask, catalog, improve, notes, overnight, recipes, registry, state

TOLD = {'instruction': 'Got it. The overnight review and Ask follow that from tonight.',
        'request': 'Hub can’t do that yet. It’s saved under “Asked for, not built yet”.',
        'unsorted': 'Saved under “Asked for, not built yet”. Hub couldn’t sort it just now.',
        'full': 'That list is full. Remove something first.', 'empty': 'Nothing to save.'}
RAN = {'ok': 'Done. Anything it found is on Today.', 'already': 'Already ran today.',
       'daily_limit': 'Today’s limit of model calls is used up.', 'monthly_limit': 'This month’s spending limit is reached.',
       'not_set_up': 'The model key isn’t set up.', 'error': 'The model route didn’t answer. Nothing changed.',
       'busy': 'A review is already running.'}


class Refused(Exception):
    """A tap that can't be done as asked; the words are for the owner."""


def _speakers():
    try:
        from ..tools.house import read
        return {d['id']: d['name'] for d in read.snapshot()['speakers']}
    except Exception:  # noqa: BLE001 - speaker names are a nicety; the notes stand without them
        return {}


def _runs(kind, limit):
    return [{'at': r['ts'], 'ok': r['status'] == 'ok', 'calls': r['luna_calls'], 'checks': r.get('jev_calls') or 0,
             'cost': round(r['cost'] or 0, 4)} for r in state.runs(kind, limit=limit)]


def picture():
    """The Engine screen as data. Reads what Hub keeps; calls no model."""
    from ..tools.gmail import triage
    from ..tools.house import zones
    day = home.today().isoformat()
    names = _speakers()
    by_note = zones.by_note()

    def zone(ids):
        return [names.get(e, e.partition('.')[2]) for e in ids]
    asked = [{'id': i['id'], 'text': i['text'], 'zone': zone(by_note.get(i['id'], ()))} for i in state.instructions()]
    used, sort_used = state.used(day, overnight.KIND), state.used(day, triage.KIND)
    return {
        'sorting': luna.configured() and state.used(day, ask.KIND)['luna_calls'] < ask.CALLS_PER_DAY,
        'asked': asked, 'full': len(asked) >= state.MAX_INSTRUCTIONS,
        'notes': [{'id': p['id'], 'text': p['text'], 'said': p['said'], 'zone': zone(p['zone'])} for p in notes.waiting()],
        'recipes': [{'id': r['id'], 'name': r['name'], 'steps': [recipes.describe(s) for s in r['steps']], 'said': r.get('said')}
                    for r in recipes.waiting()],
        'kept': [{'id': r['id'], 'name': r['name'], 'steps': [recipes.describe(s) for s in r['steps']]} for r in recipes.saved()],
        'requests': [{'id': str(r['id']), 'text': r['text'], 'at': r['created_at'], 'kind': 'request'} for r in state.requests()]
        + [{'id': r['id'], 'text': r['brief'], 'at': r['at'], 'kind': 'improvement'} for r in improve.requests()],
        'actions': [{'slug': a.slug, 'name': a.name, 'does': a.does, 'undo': a.undo, 'level': state.trust(a),
                     'levels': [{'value': v, 'name': registry.TRUST_WORDS[v]} for v in a.levels()]} for a in catalog.ACTIONS],
        'spend': {'review': {'used': used['luna_calls'], 'limit': overnight.LUNA_CALLS_PER_DAY},
                  'sorting': {'used': sort_used['luna_calls'], 'limit': triage.LUNA_CALLS_PER_DAY,
                              'checks': sort_used['jev_calls'], 'check_limit': triage.JEV_CALLS_PER_DAY},
                  'month': round(used['month_cost'], 2), 'month_limit': overnight.MONTH_COST_CAP,
                  'can_run': luna.configured()},
        'runs': _runs(overnight.KIND, 7), 'sorts': _runs(triage.KIND, 7),
        'sources': [{'name': s.name, 'reads': s.reads, 'sends': s.sends} for s in catalog.SOURCES],
    }


def tell(text):
    """One line the owner typed: guidance Hub follows, or a note of something to build."""
    result = ask.submit(text)
    store.log('engine_ask', result)
    return TOLD.get(result, TOLD['unsorted']), result in ('instruction', 'request', 'unsorted')


def do(what, key, value=None, login=None):
    """One tap on the Engine screen, by `login`. Returns the sentence to show; raises Refused when it can't be done.
    How far Hub goes with an action on one person's account (filing their mail) is theirs to set, and a run of the
    overnight review, which reads the main account, is its owner's: to anyone else they aren't there."""
    key = str(key or '')[:40]
    if what == 'forget':
        state.remove_instruction(int(key))
        store.log('engine_instruction', 'removed')
        return 'Removed. Hub won’t follow that any more.'
    if what == 'request-done':
        state.close_request(int(key))
        return 'Removed.'
    if what == 'improvement-done':
        improve.remove(key)
        return 'Removed.'
    if what in ('note-save', 'note-skip'):
        try:
            notes.save(key) if what == 'note-save' else notes.skip(key)
        except notes.Refused as error:
            raise Refused(error) from None
        return 'Saved. Ask and the overnight review follow it from now on.' if what == 'note-save' else 'Not saved.'
    if what in ('recipe-save', 'recipe-skip', 'recipe-remove'):
        change = {'recipe-save': recipes.save, 'recipe-skip': recipes.skip, 'recipe-remove': lambda r: recipes.remove(int(r))}[what]
        try:
            change(key)
        except recipes.Refused as error:
            raise Refused(error) from None
        return {'recipe-save': 'Saved. Name it in Ask to run it.', 'recipe-skip': 'Not saved.', 'recipe-remove': 'Removed.'}[what]
    if what == 'trust':
        found = catalog.action(key)
        whose = catalog.action_account(found) if found else None
        if not found or (whose and not people.sees(login, whose)) or not state.set_trust(found, str(value)):
            raise Refused('Hub can’t go that far with this one.')
        return f'{found.name}: {registry.TRUST_WORDS[str(value)].lower()}.'
    if what == 'run' and people.owner(login):
        return RAN.get(overnight.prepare()['status'], RAN['error'])
    raise Refused('Hub couldn’t read that request.')
