"""The Mail settings screen: how far Hub files each kind, the charge alert, senders the owner sorted, the plan of
inbox changes, the archive-after-30-days rules and the backlog. For the app (web/app/src/mailrules.tsx).

It reads Hub's own tables, and asks the Gmail helper only whether changes are paused. Every tap calls the older
Mail pages' own handler (hub/tools/gmail/), so the same checks, pause and Undo apply; their redirect says how it went.
"""
from urllib.parse import parse_qs, urlsplit

from .. import delayed, store, worker
from ..engine import catalog, registry, state

PLAN_ACTIONS = store.ACTIONS
FINISHED = 12                   # applied changes shown, newest first; each keeps its Undo
NOT_DONE = {'paused': 'Changes are paused, so nothing was applied. Your plan is saved.',
            'no': 'Hub couldn’t do that.', 'refused': 'That rule had a change whose result is unknown. Check Gmail first.',
            'error': 'That rule is stopped. Resume it first.'}


class Refused(Exception):
    """A tap that can't be done as asked; the words are for the owner."""


def _who(item):
    from ..tools.gmail import missions
    return missions.item_title(item)


def _item(item):
    from .. import senders
    return {'id': item['id'], 'who': _who(item), 'sender': item['sender'] if item['decision'] != 'sweep' else '',
            'does': senders.describe_item(item), 'status': item['status'],
            'step': senders.step_text(item) if item['status'] in ('applying', 'undoing') else '',
            'done': item.get('archived') or 0, 'of': item.get('count') or 0, 'error': item.get('error') or ''}


def _plan():
    from ..tools.gmail import missions
    items = store.items()
    planned = sorted((i for i in items if i['decision'] in PLAN_ACTIONS and i['status'] == 'planned'), key=lambda i: -(i['count'] or 0))
    running = [i for i in items if i['status'] in ('applying', 'undoing')]
    problems = [i for i in items if i['status'] in ('failed', 'needs_review', 'undo_partial')]
    finished = sorted((i for i in items if i['status'] in ('done', 'undone')), key=lambda i: -(i['updated_at'] or 0))[:FINISHED]
    return {'planned': [_item(i) for i in planned], 'running': [_item(i) for i in running],
            'problems': [_item(i) for i in problems], 'finished': [_item(i) for i in finished],
            'leaving': sum(i['count'] or 0 for i in planned if missions.leaves_inbox(i)), 'working': worker.running(),
            'later': sum(1 for i in items if i['decision'] == 'later' and i['status'] == 'planned')}


def _rules():
    return [{'id': r['id'], 'sender': r['sender'], 'on': bool(r['enabled']) and not r['error'], 'stopped': bool(r['error']),
             'check_first': bool(r['error']) and delayed.needs_check(r['id']), 'last': r.get('last_checked')}
            for r in delayed.rules() if r['enabled'] or r['error']]


def picture():
    """The Mail settings screen as data."""
    from ..tools.gmail import filing, inbox, missions, nightly, triage
    levels = []
    for kind in triage.KINDS:
        if kind not in filing.ACTIONS:
            continue
        action = catalog.action(filing.ACTIONS[kind])
        levels.append({'kind': kind, 'name': triage.NAMES[kind], 'level': filing.trusted(kind),
                       'levels': [{'value': v, 'name': registry.TRUST_WORDS[v]} for v in action.levels()]})
    backlog = nightly.saved_backlog()
    return {
        'paused': missions.pause_state() != 'ok', 'levels': levels, 'charge': triage.charge_limit(),
        'senders': [{'sender': s, 'kind': triage.NAMES.get(k, k)} for s, k in sorted(triage.sender_kinds().items())],
        'plan': _plan(), 'rules': _rules(), 'checking': missions.delayed_checking(),
        'backlog': {'text': nightly.backlog_text(backlog), 'planned': bool(inbox.backlog_item())}
        if backlog and backlog['count'] >= inbox.BACKLOG_WORTH else None,
    }


def quiet():
    """The next loud sender to decide about, as the older Quiet page's card showed it: how many of theirs are in the
    inbox, how much is unread, the last three subjects, and whether a filter already skips the inbox for them. Asks
    the Gmail helper (counts, three subjects, the filters); subjects are shown to the owner only. {'done': True} when
    every loud sender in the sample is decided."""
    from email.utils import parseaddr

    from .. import bridge, senders
    from ..tools.gmail import common, missions
    decided = missions.decided_senders()
    todo, sample = common.sender_queue(decided, senders.ranked(common.overview()))
    later = sum(1 for i in store.items() if i['decision'] == 'later')
    progress = {'reviewed': len(decided), 'left': len(todo), 'later': later, 'sample': sample.get('sample_size')}
    if not todo:
        return {'done': True, **progress}
    sender, sampled = todo[0]
    query = senders.inbox_query(sender)
    count = bridge.get('/gmail/count', ttl=300, query=query, max=senders.COUNT_CAP)
    recent = bridge.get('/gmail/search', ttl=300, query=query, limit=3).get('messages', [])
    rules = bridge.get('/gmail/filters', limit=100).get('filters', [])
    signals = missions.card_signals(sender, count['count'], count['exact'])
    return {'done': False, 'sender': sender, 'name': parseaddr(recent[0]['from'])[0] if recent else '', 'count': count['count'],
            'exact': bool(count['exact']), 'unread_share': signals['unread_share'], 'sampled': sampled,
            'covered': bool(senders.covered(sender, rules)),
            'recent': [{'subject': m.get('subject') or '(no subject)', 'unread': 'UNREAD' in (m.get('labels') or [])} for m in recent],
            **progress}


QUIET_SAID = {'filter_archive': 'In your plan: skip the inbox from now on, and archive what’s there.',
              'archive': 'In your plan: archive what’s there.', 'filter': 'In your plan: skip the inbox for new mail.',
              'keep': 'Kept in the inbox.', 'later': 'Left for later.'}


def _went(reply):
    """Read an older handler's redirect: its query says when something wasn't done."""
    query = parse_qs(urlsplit(reply.headers.get('location', '')).query)
    for key in ('paused', 'refused', 'error'):
        if query.get(key) == ['1']:
            raise Refused(NOT_DONE[key])
    if query.get('said') == ['no']:
        raise Refused(NOT_DONE['no'])


def tap(do, key='', value=None):
    """One tap. Returns the sentence to show; raises Refused when it wasn't done."""
    from ..tools.gmail import filing, inbox, missions, triage
    key = str(key or '')[:320]
    if do == 'level':
        if key not in filing.ACTIONS or not state.set_trust(catalog.action(filing.ACTIONS[key]), str(value)):
            raise Refused(NOT_DONE['no'])
        return f'{triage.NAMES[key]}: {registry.TRUST_WORDS[str(value)].lower()}.'
    if do == 'charge':
        if not triage.set_charge_limit(int(value)):
            raise Refused('Choose an amount from $1 to $100,000.')
        return f'Hub shows you a charge of ${int(value):,} or more.'
    if do == 'sender':
        _went(inbox.always(key, 'ask'))
        return 'Hub decides for that sender again.'
    if do == 'apply':
        _went(missions.plan_apply())
        return 'Applying your plan. It keeps going on the Mac.'
    if do == 'remove':
        _went(missions.plan_remove(int(key)))
        return 'Taken out of the plan.'
    if do == 'undo':
        _went(missions.plan_undo(int(key)))
        return 'Putting it back.'
    if do == 'rule-stop':
        _went(missions.delayed_stop(int(key)))
        return 'Stopped. Mail from that sender stays in the inbox.'
    if do == 'rule-resume':
        _went(missions.delayed_resume(int(key), checked=int(bool(value))))
        return 'Turned back on. The next check picks up where it left off.'
    if do == 'rules-check':
        if missions.is_paused():
            raise Refused('Changes are paused, so nothing was checked.')
        _went(missions.delayed_run_now())
        return 'Checking the rules now.'
    if do == 'quiet':
        from .. import senders
        value = value if isinstance(value, dict) else {}
        decision = str(value.get('decision') or '')
        if not senders.valid_sender(key) or decision not in senders.DECISIONS:
            raise Refused(NOT_DONE['no'])
        missions.decide(sender=key, decision=decision, count=max(int(value.get('count') or 0), 0), exact=int(bool(value.get('exact', True))),
                        display_name=str(value.get('name') or '')[:200])
        return QUIET_SAID[decision]
    if do == 'backlog':
        _went(inbox.backlog_start())
        return 'The backlog clear is in your plan.'
    raise Refused('Hub couldn’t read that request.')
