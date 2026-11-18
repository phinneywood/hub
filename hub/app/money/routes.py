"""The app's door to Money. Every route, looking included, needs a member: a signed-in device whose login
`hub/people.py` knows. No login (a program on this Mac, the kitchen wall) and a login Hub doesn't know get a 403
and nothing else, so money is never on the wall or in the morning push.
"""
from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ... import home
from . import ask, budget, changes, picture, share, sync, taps

router = APIRouter()
FILE_LIMIT = 5 * 1024 * 1024


def _login(request):
    return request.headers.get('tailscale-user-login')


def _refuse(words, status=409):
    return JSONResponse({'error': str(words)[:300]}, status_code=status)


def _member(request):
    login = _login(request)
    return login if share.person(login) is not None else None


def _away():
    return _refuse('Money is only shown on your own phone or laptop.', 403)


@router.get('/api/money')
def money_screen(request: Request):
    """The Money screen for this member, from Money's own tables. No bank is read and no model is asked."""
    login = _member(request)
    return picture.screen(login) if login else _away()


@router.get('/api/money/today')
def money_today(request: Request):
    """Today's one line about money, for a member; {"line": null} for anyone else, so Today simply leaves it out."""
    login = _member(request)
    return {'line': picture.today_line(login) if login else None}


@router.get('/api/money/spending')
def money_spending(request: Request, search: str = '', months: int = 3):
    """What was spent on a payee or a category, for an Ask card: the matching transactions this member sees."""
    login = _member(request)
    if not login:
        return _away()
    found = ask.matching(login, search[:60], months) if share.model_on(login) else None
    if found is None:           # the same reading, without the model's allow-list: this is the member's own screen
        from datetime import timedelta
        since = (home.today() - timedelta(days=31 * max(1, min(months, 13)))).isoformat()
        rows = share.txns(login, 'day>=? and pending=0', (since,))
        key = ask.categories.named(search)
        words = ask.categories.clean(search)
        rows = [r for r in rows if (key and r['category'] == key) or (not key and words and words in ask.categories.clean(r['payee']))]
        found = {'since': since, 'count': len(rows), 'total': round(sum(r['amount'] for r in rows), 2),
                 'transactions': [picture._txn(r) for r in rows[:ask.MATCHES]]}
    return found


@router.post('/api/money/connect')
def money_connect(request: Request, body: dict = Body(default={})):
    """{"token": a SimpleFIN setup token}: claim it for this member and read their banks."""
    login = _member(request)
    if not login:
        return _away()
    try:
        said = sync.connect(str(body.get('token') or ''), login)
    except sync.Refused as problem:
        return _refuse(problem)
    return {'ok': True, 'said': said}


@router.post('/api/money/read')
def money_read(request: Request, body: dict = Body(default={})):
    """Read every connection (or {"link": n}) now: at most once an hour, and within the day's limit."""
    login = _member(request)
    if not login:
        return _away()
    try:
        ids = [int(body['link'])] if body.get('link') else [link['id'] for link in sync.links()]
    except (TypeError, ValueError):
        return _refuse('Hub couldn’t read that request.', 400)
    if not ids:
        return _refuse('No banks are connected yet.')
    said = []
    for link in ids:
        try:
            said.append(sync.read(link, why='tap'))
        except sync.Refused as problem:
            said.append(str(problem))
    return {'ok': True, 'said': ' '.join(said)}


@router.post('/api/money/file')
async def money_file(request: Request, name: str = '', account: str = '', flip: str = '', new_name: str = '', new_kind: str = ''):
    """A statement file as the request's body (OFX, QFX or CSV). `account`: which account it is, or "new" with
    `new_name` and `new_kind`; OFX names its own. `flip`: "1" when a card's file writes charges as positive."""
    login = _member(request)
    if not login:
        return _away()
    data = await request.body()
    if len(data) > FILE_LIMIT:
        return _refuse('That file is bigger than a statement should be (over 5 MB).')
    try:
        said = sync.add_file(name, data, login, account or None, {'1': True, '0': False}.get(flip), new_name or None, new_kind or None)
    except sync.Refused as problem:
        return _refuse(problem)
    return {'ok': True, 'said': said}


@router.post('/api/money/undo/{batch}')
def money_undo(batch: str, request: Request):
    login = _member(request)
    if not login:
        return _away()
    try:
        said = changes.undo(batch, login)
    except changes.Refused as problem:
        return _refuse(problem)
    return {'ok': True, 'said': f'Put back: {said}'}


@router.post('/api/money/{do}')
def money_tap(do: str, request: Request, body: dict = Body(default={})):
    """A tap on the Money screen: budget ({category, amount}), suggested, sort ({payee or id, category}), goal
    ({do, name, ...}), bill ({do, name, ...}), account ({id, kind, name, in_budget}), private ({id, on}),
    model ({on}), plan ({cushion, ...}), disconnect ({link})."""
    login = _member(request)
    if not login:
        return _away()
    try:
        if do == 'budget':
            said, batch = taps.set_budget(login, body.get('category'), body.get('amount'), via='app')
        elif do == 'suggested':
            said, batch = taps.use_suggested(login, budget.suggest(home.today()), via='app')
        elif do == 'sort':
            said, batch = taps.sort(login, body.get('payee') or '', body.get('category'), via='app',
                                    only=body.get('id') if not body.get('always') else None)
        elif do == 'goal':
            said, batch = taps.goal(login, body.get('do') or 'add', body.get('name'), body.get('target'), body.get('by'), body.get('saved'),
                                    body.get('account'), via='app')
        elif do == 'bill':
            said, batch = taps.bill(login, body.get('do') or 'not', body.get('name'), body.get('amount'), body.get('every') or 'month',
                                    body.get('next'), body.get('category'), via='app')
        elif do == 'account':
            found = share.accounts(login)[0]
            named = next((a['id'] for a in found if a['id'] == body.get('id')), None)
            if named is None:
                return _refuse('Hub doesn’t know that account.')
            said, batch = taps.account(login, named, body.get('kind'), body.get('name'), body.get('in_budget'), via='app')
        elif do == 'private':
            said, batch = taps.private(login, str(body.get('id') or ''), bool(body.get('on')))
        elif do == 'model':
            said, batch = taps.model(login, bool(body.get('on')))
        elif do == 'plan':
            said, batch = taps.set_plan(login, via='app', **{k: body.get(k) for k in taps.PLAN_KEYS})
        elif do == 'disconnect':
            try:
                link = int(body.get('link') or 0)
            except (TypeError, ValueError):
                return _refuse('Hub couldn’t read that request.', 400)
            return {'ok': True, 'said': sync.disconnect(link, login)}
        else:
            return _refuse('Hub couldn’t read that request.', 404)
    except (taps.Refused, sync.Refused) as problem:
        return _refuse(problem)
    return {'ok': True, 'said': said, 'batch': batch}
