"""The Mail screen's picture, as plain data for the app (web/app), and its taps.

Drawn from Hub's own sorting tables (hub/tools/gmail/triage.py), never a Gmail call, so it opens at once and
still opens when Gmail can't be reached. Mail is "only what needs me" (the owner's words): the screen leads with
the emails that need the owner and the questions they sent that nobody answered, then one line per kind Hub
sorted, with filing at the level the owner chose and Undo. Rules, levels and the backlog stay on the older page.

The taps are the older page's own: marking an email handled, a question as no longer waiting, a record as fine,
filing a kind and putting a filed batch back. Filing and Undo move mail in Gmail through the bridge, exactly as
the older page's buttons do; nothing else here changes Gmail.
"""
import time
from urllib.parse import urlencode

from .. import home
from ..engine import state

ROWS = 8                        # rows per list before "and N more"
NOTICES = {'filed': 'Filed {n} email{s}.', 'nothing': 'Nothing was ready to file.', 'busy': 'Hub was still working on the inbox.',
           'paused': 'Changes are paused, so nothing was filed.', 'review': 'Hub couldn’t confirm that filing. Check the older Mail page.',
           'refused': 'The Gmail helper refused that change. Nothing moved.', 'error': 'Hub couldn’t reach Gmail. Nothing moved.',
           'part': 'Filed {n} email{s}, then the Gmail helper refused the rest.'}


def _search(query):
    return '/gmail/search?' + urlencode({'q': query})


def _email(row):
    from ..tools.gmail import inbox
    return {'id': row['id'], 'who': row['who'], 'subject': row['subject'] or '', 'step': row.get('step') or '',
            'days': row['days'], 'find': inbox.find(row)}


def _shelf(kind, seen, now, batches):
    from ..tools.gmail import filing, triage
    entry = seen['kinds'][kind]
    level = filing.trusted(kind)
    return {'kind': kind, 'name': entry['name'], 'count': len(entry['open']), 'new': entry['new'],
            'ready': len(filing.ready(kind, now)) if level != 'suggest' else 0, 'level': level,
            'wait': filing.WAIT_DAYS.get(kind, 0), 'rows': [_email(r) for r in entry['open'][:3]],
            'undo': [{'change': b['change_id'], 'count': b['count'], 'at': b['at']} for b in batches if b['kind'] == kind][:2],
            'label': filing.LABELS[kind], 'names': triage.COUNT_WORDS[kind]}


def picture(now=None):
    """The whole Mail screen."""
    from ..tools.gmail import common, filing, inbox, missions, triage
    now = time.time() if now is None else now
    seen = triage.picture(now)
    batches = filing.batches(now)
    last = state.last_run(home.today().isoformat(), triage.KIND)
    working = 'sorting' if triage.sorting() else 'filing' if inbox.filing_now() else ''
    filed = None if working else inbox._filing['result']
    if filed:
        inbox._filing['result'] = None          # told once, as on the older page
    return {
        'ok': True, 'sorted': bool(seen['synced_at']),
        'synced': common.age_label(seen['synced_at'], now) if seen['synced_at'] else '',
        'working': working, 'failed': bool(last and last['status'] == 'error' and not working),
        'paused': missions.is_paused(), 'review': seen['review'],
        'needs': [_email(r) for r in seen['needs'][:ROWS]], 'needs_more': max(0, len(seen['needs']) - ROWS),
        'waiting_days': triage.WAITING_DAYS,
        'asked': [{'id': r['thread_id'], 'subject': r.get('subject') or '', 'to': r.get('who') or '',
                   'days': r['days'], 'find': inbox.find_asked(r)} for r in seen['asked'][:ROWS]],
        'odd': [_email(r) for r in seen['odd'][:ROWS]],
        'shelves': [_shelf(kind, seen, now, batches) for kind in triage.FILED],
        'sorted_today': triage.counts_text({kind: entry['new'] for kind, entry in seen['kinds'].items()}),
        'unsorted': len(seen['kinds'][triage.UNSORTED]['open']),
        'notice': NOTICES.get(filed['status'], '').format(n=filed['count'], s='s' * (filed['count'] != 1)) if filed else '',
    }


def tap(do, key=''):
    """One tap. Returns (ok, what to tell the owner)."""
    from ..tools.gmail import filing, inbox, triage
    key = str(key or '')[:200]
    if do == 'done':
        return (True, 'Marked as handled') if triage.done(key) else (False, 'That one is already handled.')
    if do == 'back':
        return (True, 'Put back') if triage.reopen(key) else (False, 'There was nothing to put back.')
    if do == 'answered':
        return (True, 'Stopped watching') if triage.dismiss_asked(key) else (False, 'That one is already gone.')
    if do == 'fine':
        return (True, 'Marked as fine') if triage.looks_fine(key) else (False, 'That one is already gone.')
    if do == 'sort':
        triage.sort_soon()
        return True, 'Sorting the inbox'
    if do == 'file':
        if key not in filing.LABELS or filing.trusted(key) == 'suggest':
            return False, 'That kind is set to “Leave in the inbox”. Change it on the older Mail page first.'
        if triage.sorting() or inbox.filing_now():
            return False, 'Hub is still working on the inbox. Try again in a minute.'
        inbox._file_soon(key)
        return True, 'Filing'
    if do == 'undo':
        said = filing.undo(key)
        words = {'undone': 'Back in the inbox', 'nothing': 'There was nothing to put back.',
                 'refused': 'The Gmail helper refused that change. Nothing moved.',
                 'review': 'Hub couldn’t confirm that. The older Mail page shows what to check.'}
        return said == 'undone', words.get(said, 'Hub couldn’t reach Gmail. Nothing moved.')
    return False, 'Hub couldn’t read that request.'
