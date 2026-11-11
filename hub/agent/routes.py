"""The /ask page: the chat stream, saved threads, and the limits and record of every run.

The stream speaks the Vercel AI data stream protocol through Pydantic AI's adapter. From the browser
the server takes one thing, the owner's new message; the rest of the conversation comes from Hub's
database. Sign-in is Hub's own rule (the app-wide owner check), and writes are same-origin only.
"""
import json
import re
import threading

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic_ai import UsageLimits
from pydantic_ai.messages import ModelResponse
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from .. import home, luna
from ..engine import state
from ..templating import render
from . import agent, model, store

router = APIRouter()
KIND = 'chat'                 # these runs in the engine's record, with a monthly limit of their own
MONTH_COST_CAP = 10.0         # dollars of recorded model cost per calendar month, chat only
CALLS_PER_DAY = 300           # model calls a day; a backstop for a day when no cost is reported
MESSAGE_LENGTH = 2000
TURNS_SHOWN = 12              # how much of a long thread the model is shown
THREAD_ID = re.compile(r'[A-Za-z0-9-]{8,64}')
FAILED = 'Hub couldn’t finish that answer. Nothing was changed. Try again.'
_running, _lock = set(), threading.Lock()


def _day():
    return home.today().isoformat()


def refusal():
    """Why a chat can't run right now, in the owner's words, or None."""
    if not luna.configured():
        return 'The model key isn’t set up, so Ask Hub can’t answer yet.'
    spent = state.spent(_day(), KIND)
    if spent['month_cost'] >= MONTH_COST_CAP:
        return f'This month’s chat budget (${MONTH_COST_CAP:.0f}) is used up. It starts again next month.'
    if spent['calls_today'] >= CALLS_PER_DAY:
        return 'Today’s limit of chat model calls is reached. It starts again tomorrow.'
    return None


def _error(status, text):
    return JSONResponse({'error': text}, status_code=status)


def _thread(thread_id):
    return thread_id if THREAD_ID.fullmatch(thread_id) else None


@router.get('', response_class=HTMLResponse)
def page(request: Request):
    return render(request, 'ask/chat.html', active='ask')


@router.get('/status')
def status():
    spent = state.spent(_day(), KIND)
    return {'unavailable': refusal(), 'tools': agent.describe(),
            'budget': {'month_cost': round(spent['month_cost'], 4), 'cap': MONTH_COST_CAP}, 'text_days': store.TEXT_DAYS}


@router.get('/threads')
def threads():
    return store.threads()


@router.get('/threads/{thread_id}/messages')
def messages(thread_id: str):
    if not _thread(thread_id):
        return _error(404, 'No such chat.')
    return [m.model_dump(by_alias=True, exclude_none=True, mode='json')
            for m in VercelAIAdapter.dump_messages(agent.cite(store.history(thread_id)), sdk_version=7)]


@router.delete('/threads/{thread_id}')
def delete(thread_id: str):
    if _thread(thread_id):
        store.delete(thread_id)
    return {'ok': True}


def _record(day, status, result=None):
    """Counts and cost only. A run that failed still counts one call against the day."""
    if result is None:
        return state.record_run(day, KIND, status, luna_calls=1)
    usage = result.usage
    cost = sum(float((m.provider_details or {}).get('cost') or 0) for m in result.new_messages() if isinstance(m, ModelResponse))
    state.record_run(day, KIND, status, luna_calls=usage.requests, tokens_in=usage.input_tokens or 0,
                     tokens_out=usage.output_tokens or 0, cost=cost)


@router.post('/threads/{thread_id}/chat')
async def chat(thread_id: str, request: Request):
    if not _thread(thread_id):
        return _error(404, 'No such chat.')
    if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
        return _error(415, 'JSON only.')
    try:
        body = json.loads(await request.body())
        new = body['messages'][-1]
        text = ' '.join(p.get('text', '') for p in new['parts'] if p.get('type') == 'text')
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return _error(400, 'That message couldn’t be read.')
    if new.get('role') != 'user' or not text.strip():
        return _error(400, 'Send a message to continue.')
    if len(text) > MESSAGE_LENGTH:
        return _error(413, f'That message is too long. Keep it under {MESSAGE_LENGTH} characters.')
    why_not = await run_in_threadpool(refusal)
    if why_not:
        return _error(429 if 'limit' in why_not or 'budget' in why_not else 503, why_not)
    with _lock:
        if thread_id in _running:
            return _error(409, 'Hub is still answering in this chat.')
        _running.add(thread_id)
    try:
        # Only what a person can add is taken from the browser: the new message, as plain text.
        body['messages'] = [{'id': str(new.get('id') or ''), 'role': 'user', 'parts': [{'type': 'text', 'text': text}]}]
        body['trigger'] = 'submit-message'
        run_input = VercelAIAdapter.build_run_input(json.dumps(body).encode())
        chat_model = await run_in_threadpool(model.build)
    except Exception:  # noqa: BLE001 - nothing started; say so plainly and free the thread
        _running.discard(thread_id)
        return _error(400, 'That message couldn’t be read.')
    day = _day()
    older, shown = store.recent(await run_in_threadpool(store.history, thread_id), TURNS_SHOWN)
    done = []

    def on_complete(result):
        store.save(thread_id, older + result.all_messages())
        _record(day, 'ok', result)
        done.append(True)

    adapter = VercelAIAdapter(agent=agent.build(), run_input=run_input, accept=request.headers.get('accept'),
                              sdk_version=7, manage_message_history='server')
    events = adapter.run_stream(model=chat_model, message_history=shown, on_complete=on_complete,
                                usage_limits=UsageLimits(request_limit=agent.REQUESTS_PER_TURN))

    async def stream():
        try:
            async for chunk in events:
                if getattr(chunk, 'type', '') == 'error':
                    chunk = chunk.model_copy(update={'error_text': FAILED})   # never a provider's or library's own words
                yield chunk
        finally:
            _running.discard(thread_id)
            if not done:
                _record(day, 'error')

    return adapter.streaming_response(stream())
