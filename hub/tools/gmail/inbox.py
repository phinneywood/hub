"""The Gmail page: what needs the owner, what Hub sorted, and what it may file.

The page is drawn from Hub's own tables, so it opens at once and still opens when Gmail can't be
reached. Sorting and filing run behind the page. Every notice shown after a tap comes from the
fixed list below; nothing from the address bar is echoed.
"""
import threading
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ... import actions, bridge, home, luna, store, worker
from ...engine import catalog, state
from . import common, filing, nightly, triage
from .common import page
from .missions import go, is_paused

router = APIRouter()

SHOWN = 6                        # rows per kind before “Show all”
BACKLOG_WORTH = 500              # a smaller backlog isn't worth an offer
FILE_WORDS = {'suggest': 'Leave in the inbox', 'undo': 'File when I tap', 'auto': 'File each morning and tell me'}
WAITS = {kind: f'Hub waits {days} days before filing these, in case you still need them.' for kind, days in filing.WAIT_DAYS.items()}
SAID = {
    'filed': ('ok', 'Filed {n}. Undo is under that kind below.'),
    'nothing': ('', 'Nothing was ready to file.'),
    'busy': ('', 'Hub is still working on the inbox. Try again in a minute.'),
    'paused': ('warn', 'Changes are paused, so nothing was filed.'),
    'review': ('warn', 'Hub couldn’t confirm that change. See the note above before filing more.'),
    'refused': ('warn', 'The Gmail helper refused that change. Nothing moved.'),
    'part': ('warn', 'Filed {n}, then the Gmail helper refused the rest. Undo is under that kind below.'),
    'error': ('warn', 'Hub couldn’t reach Gmail. Nothing moved.'),
    'off': ('', 'That kind is set to “Leave in the inbox”. Change it below first.'),
    'undone': ('ok', 'Back in the inbox. Hub will leave those messages alone.'),
    'restored': ('ok', 'Back in the inbox. Hub will leave that message alone.'),
    'gone': ('', 'There was nothing to put back.'),
    'saved': ('ok', 'Saved.'),
    'moved': ('ok', 'Moved. Hub will remember that for this sender if you asked it to.'),
    'no': ('warn', 'That couldn’t be changed. It may already have been handled.'),
    'checked': ('ok', 'Thanks. The next sort looks at what is really in the inbox.'),
}
_filing = {'t': None, 'result': None}


def filing_now():
    thread = _filing['t']
    return bool(thread and thread.is_alive())


def _file_soon(kind):
    """File behind the page: looking at the inbox first can take half a minute."""
    def work():
        try:
            _filing['result'] = filing.file(kind)
        except Exception:  # noqa: BLE001 - rows already marked stay marked and wait for the owner
            store.log('mail filing failed', triage.NAMES[kind])
            _filing['result'] = {'status': 'error', 'count': 0}
    thread = threading.Thread(target=work, name='gmail-file', daemon=True)
    _filing['t'] = thread
    thread.start()


def wait(timeout=30):
    thread = _filing['t']
    if thread:
        thread.join(timeout)


def _ago(days):
    return 'today' if days < 1 else 'yesterday' if days == 1 else f'{days} days ago'


def _emails(count):
    return f'{count:,} email{"s" * (count != 1)}'


def _search(query):
    return '/gmail/search?' + urlencode({'q': query})


def find(row):
    """A read-only search that brings up one sorted message."""
    return _search(f'from:{row["sender"]} newer_than:{row["days"] + 2}d')


def find_asked(row):
    return _search(f'in:sent older_than:{max(row["days"] - 1, 1)}d newer_than:{row["days"] + 2}d')


def _notice(said, count):
    tone, text = SAID.get(said, (None, None))
    return {'tone': tone, 'text': text.format(n=_emails(max(count, 0)))} if text else None


def backlog_item():
    """The backlog clear if it is in the plan and not finished."""
    key = actions.sweep_key(nightly.backlog_spec())
    return next((i for i in store.items() if i['decision'] == 'sweep' and i['status'] in ('planned', 'applying')
                 and (i['sender'] == key or i['sender'].startswith(key + ':'))), None)


def shelves(seen, now, show=''):
    """One entry per kind Hub may file: what is in the inbox, what was filed, and the owner's level."""
    undoable = filing.batches(now)
    found = []
    for kind in triage.FILED:
        entry, action = seen['kinds'][kind], catalog.action(filing.ACTIONS[kind])
        filed = [r for r in entry['filed'] if (r['filed_at'] or 0) >= now - filing.UNDO_DAYS * 86400]
        filed.sort(key=lambda r: -(r['filed_at'] or 0))
        everything = show == kind
        found.append({
            'kind': kind, 'name': entry['name'], 'new': entry['new'], 'count': len(entry['open']),
            'rows': entry['open'] if everything else entry['open'][:SHOWN], 'more': 0 if everything else max(0, len(entry['open']) - SHOWN),
            'filed': filed if everything else filed[:SHOWN], 'filed_count': len(filed),
            'filed_more': 0 if everything else max(0, len(filed) - SHOWN),
            'level': state.trust(action), 'levels': action.levels(), 'ready': len(filing.ready(kind, now)),
            'label': filing.LABELS[kind], 'wait': WAITS.get(kind),
            'batches': [b for b in undoable if b['kind'] == kind], 'open': everything})
    return found


@router.get("", response_class=HTMLResponse)
def inbox(request: Request, said: str = "", n: int = 0, show: str = ""):
    now = time.time()
    seen = triage.picture(now)
    working = 'sorting' if triage.sorting() else 'filing' if filing_now() else ''
    result = None if working else _filing['result']
    if not working:
        _filing['result'] = None  # shown once
    notice = _notice(result['status'], result['count']) if result else _notice(said, n)
    last = state.last_run(home.today().isoformat(), triage.KIND)
    unsorted = seen['kinds'][triage.UNSORTED]
    backlog = nightly.saved_backlog()
    review = [r for r in filing.waiting_review() if r['state'] == 'review' or working != 'filing']  # rows mid-filing aren't in doubt yet
    return page(
        request, "triage.html", "today", seen=seen, synced=common.age_label(seen['synced_at'], now) if seen['synced_at'] else None,
        working=working, notice=notice, failed=bool(last and last['status'] == 'error' and not working),
        shelves=shelves(seen, now, show), show=show, unsorted=unsorted,
        unsorted_rows=unsorted['open'] if show == triage.UNSORTED else unsorted['open'][:SHOWN],
        unsorted_more=0 if show == triage.UNSORTED else max(0, len(unsorted['open']) - SHOWN),
        sorted_text=triage.counts_text({kind: entry['new'] for kind, entry in seen['kinds'].items()}),
        review=len(review), review_changes=sorted({r['change_id'] for r in review if r['change_id']}),
        paused=is_paused(), configured=luna.configured(), words=FILE_WORDS, kinds=triage.KINDS, names=triage.NAMES,
        waiting_days=triage.WAITING_DAYS, ago=_ago, find=find, find_asked=find_asked, emails=_emails,
        backlog=backlog if backlog and backlog['count'] >= BACKLOG_WORTH else None,
        backlog_text=nightly.backlog_text(backlog) if backlog else '', backlog_planned=backlog_item(),
        charge_limit=triage.charge_limit(), overrides=sorted(triage.sender_kinds().items()))


@router.post("/sort")
def sort_now():
    triage.sort_soon()
    return go("/gmail")


@router.post("/file/undo")
def file_undo(change_id: str = Form(...)):
    return go("/gmail", said={'nothing': 'gone'}.get(found := filing.undo(change_id), found))


@router.post("/file/restore")
def file_restore(message_id: str = Form(...)):
    return go("/gmail", said={'nothing': 'gone'}.get(found := filing.restore(message_id), found))


@router.post("/file/checked")
def file_checked():
    filing.checked()
    return go("/gmail", said='checked')


@router.post("/file/{kind}")
def file_kind(kind: str):
    if kind not in filing.LABELS or filing.trusted(kind) == 'suggest':
        return go("/gmail", said='off')
    if triage.sorting() or filing_now():
        return go("/gmail", said='busy')
    _file_soon(kind)
    return go("/gmail")


@router.post("/move")
def move(message_id: str = Form(...), kind: str = Form(...), always: int = Form(0)):
    row = triage.get(message_id)
    if not row or kind not in triage.KINDS:
        return go("/gmail", said='no')
    if always:
        triage.always(row['sender'], kind)
    return go("/gmail", said='moved' if triage.move(message_id, kind) else 'no')


@router.post("/always")
def always(sender: str = Form(...), kind: str = Form(...)):
    return go("/gmail", said='saved' if triage.always(sender, kind) else 'no')


@router.post("/done")
def done(message_id: str = Form(...)):
    return go("/gmail", **({} if triage.done(message_id) else {'said': 'no'}))


@router.post("/ok")
def looks_fine(message_id: str = Form(...)):
    return go("/gmail", **({} if triage.looks_fine(message_id) else {'said': 'no'}))


@router.post("/asked/dismiss")
def asked_dismiss(thread_id: str = Form(...)):
    return go("/gmail", **({} if triage.dismiss_asked(thread_id) else {'said': 'no'}))


@router.post("/trust")
def trust(kind: str = Form(...), level: str = Form(...)):
    ok = kind in filing.ACTIONS and state.set_trust(catalog.action(filing.ACTIONS[kind]), level)
    return go("/gmail", said='saved' if ok else 'no')


@router.post("/charge-limit")
def charge_limit(dollars: int = Form(...)):
    return go("/gmail", said='saved' if triage.set_charge_limit(dollars) else 'no')


@router.get("/backlog", response_class=HTMLResponse)
def backlog(request: Request):
    spec = nightly.backlog_spec()
    found = nightly.backlog()
    sample = bridge.get("/gmail/search", ttl=300, query=actions.sweep_query(spec), limit=5).get("messages", [])
    mine = backlog_item()
    others = sum(1 for i in store.items() if i['decision'] in store.ACTIONS and i['status'] == 'planned'
                 and not (mine and i['id'] == mine['id']))
    return page(request, "backlog.html", "today", found=found, amount=nightly.backlog_text(found), sample=sample,
                days=nightly.BACKLOG_DAYS, planned=mine, paused=is_paused(), others=others)


@router.post("/backlog/start")
def backlog_start():
    """Put the backlog clear in the plan, and begin it when nothing else is waiting there."""
    from ... import apply
    if not backlog_item():
        spec = nightly.backlog_spec()
        found = nightly.backlog()
        key = actions.sweep_key(spec)
        if any(i['sender'] == key for i in store.items()):  # an earlier clear finished: keep its own undo history
            key = f'{key}:{int(time.time())}'
        if store.set_decision(key, 'sweep', found['floor'], found['how'] == 'exact', spec=spec):
            store.log('cleanup planned', actions.describe_sweep(spec, found['floor'], found['how'] == 'exact'))
    mine = backlog_item()
    if mine and not is_paused() and [i['id'] for i in apply.work_items()] == [mine['id']]:
        worker.start()
    return go("/gmail/plan")
