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
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from .. import ask_trace, home, luna
from ..engine import friction, state
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
FAILED_CHANGED = 'Hub couldn’t finish that answer. Something may have changed: check Home changes before trying again.'
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


@router.get('/about')
def about(ref: str = ''):
    """What an “Ask about this” link stands for: Hub's own question for that card or chart. Reads only."""
    from .. import cards
    return cards.about(ref) or _error(404, 'Hub doesn’t know that card any more.')


@router.get('/threads')
def threads():
    return store.threads()


@router.get('/threads/{thread_id}/messages')
def messages(thread_id: str):
    if not _thread(thread_id):
        return _error(404, 'No such chat.')
    return [m.model_dump(by_alias=True, exclude_none=True, mode='json')
            for m in VercelAIAdapter.dump_messages(agent.cite(store.history(thread_id)), sdk_version=7)]


@router.get('/card/{kind}/{proposal_id}')
def card_status(kind: str, proposal_id: str):
    """What became of something a chat card offered: waiting, saved, skipped, made, deleted, expired or gone."""
    from ..engine import waiting
    return {'status': waiting.status(kind, proposal_id)}


def _turns(history):
    """The thread's model messages, one list per owner message."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    turns = []
    for message in history:
        if isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts):
            turns.append([])
        if turns:
            turns[-1].append(message)
    return turns


@router.post('/threads/{thread_id}/improve')
async def improve(thread_id: str, request: Request):
    """“Improve this” under an answer: files a brief of what Hub could and couldn't do on that turn, at once.
    `turn` counts the owner's messages before that answer, from 0. No message or answer text is kept. A failed
    answer is saved too, so it can be improved; its trace adds how it failed and how each tool answered."""
    from ..engine import improve as improvements
    if not _thread(thread_id):
        return _error(404, 'No such chat.')
    login = request.headers.get('tailscale-user-login')
    if login is None:
        return _error(403, 'Send this from your phone or laptop.')
    try:
        turn = int(json.loads(await request.body())['turn'])
    except (ValueError, KeyError, TypeError):
        return _error(400, 'That couldn’t be read.')
    turns = _turns(await run_in_threadpool(store.history, thread_id))
    if not 0 <= turn < len(turns):
        return _error(404, 'Hub can’t find that answer any more.')
    traced = await run_in_threadpool(lambda: ask_trace.turn(trace_id(turns[turn])) if trace_id(turns[turn]) else None)
    entry = await run_in_threadpool(lambda: improvements.file(improvements.brief_for(turns[turn], trace=traced),
                                                              source='ask', login=login))
    return {'ok': True, 'said': 'Filed for the next building session. It’s on Today and the Engine page.', 'id': entry['id']}


@router.delete('/threads/{thread_id}')
def delete(thread_id: str):
    if _thread(thread_id):
        store.delete(thread_id)
    return {'ok': True}


def trace_id(messages):
    """The trace of a saved turn, from its answer, or None for turns saved before traces."""
    return next((m.provider_details['hub_turn'] for m in reversed(messages) if isinstance(m, ModelResponse)
                 and isinstance(m.provider_details, dict) and m.provider_details.get('hub_turn')), None)


def failed_turn(text, trace):
    """What a failed turn leaves in its thread: the owner's message and an answer that says it failed, what Hub
    tried and whether anything may have changed, so the thread shows what happened and “Improve this” finds it.
    Tool names and outcome kinds only, never what a lookup returned."""
    names = agent.describe()
    words = {'read': 'read', 'unavailable': 'couldn’t read it', 'error': 'broke', 'done': 'done', 'asked': 'asked'}
    tried = [f'{names.get(c.tool, {}).get("name", c.tool)} ({words.get(c.outcome, c.outcome.replace("_", " "))})'
             for c in trace.calls]
    said = '**This answer failed.** ' + (FAILED_CHANGED if trace.changed() else FAILED)
    if tried:
        said += '\n\nWhat Hub tried: ' + ', '.join(tried) + '.'
    return [ModelRequest(parts=[UserPromptPart(content=text)]),
            ModelResponse(parts=[TextPart(content=said)], finish_reason='error',
                          provider_details={'hub_turn': trace.id, 'hub_failed': True})]


def _record(day, status, result=None, trace=None):
    """Counts and cost only. A run that failed counts the model calls it made, and at least one, against the day."""
    if result is None:
        calls = max(1, trace.model_calls if trace else 0)
        return state.record_run(day, KIND, status, luna_calls=calls, tokens_in=trace.tokens_in if trace else 0,
                                tokens_out=trace.tokens_out if trace else 0, cost=trace.cost if trace else 0)
    usage = result.usage
    cost = sum(float((m.provider_details or {}).get('cost') or 0) for m in result.new_messages() if isinstance(m, ModelResponse))
    state.record_run(day, KIND, status, luna_calls=usage.requests, tokens_in=usage.input_tokens or 0,
                     tokens_out=usage.output_tokens or 0, cost=cost)


def _watched(adapter, trace):
    """The adapter, with the error that ends a run kept on the turn's trace before the page gets its fixed line."""
    build = adapter.build_event_stream

    def build_event_stream():
        events = build()
        report = events.on_error

        async def on_error(error):
            trace.fail(error)
            async for chunk in report(error):
                yield chunk
        events.on_error = on_error
        return events
    adapter.build_event_stream = build_event_stream
    return adapter


def _end_failed(thread_id, history, text, day, trace):
    """A turn that didn't finish: saved as a failed answer, counted, and traced. Never raises."""
    try:
        store.save(thread_id, history + failed_turn(text, trace))
    except Exception as error:  # noqa: BLE001 - the trace still says what happened
        trace.fail(error)
    try:
        _record(day, 'error', trace=trace)
    finally:
        trace.finish('failed' if trace.error else 'stopped')


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
    trace = await run_in_threadpool(ask_trace.Trace, thread_id)
    done = []

    def on_complete(result):
        messages = result.all_messages()
        answer = next((m for m in reversed(messages) if isinstance(m, ModelResponse)), None)
        if answer is not None:
            answer.provider_details = {**(answer.provider_details or {}), 'hub_turn': trace.id}
        store.save(thread_id, older + messages)
        _record(day, 'ok', result)
        friction.from_turn(text, result.new_messages(), shown)   # scrubbed one-liners for the morning's fix cards
        done.append(True)

    adapter = _watched(VercelAIAdapter(agent=agent.build(), run_input=run_input, accept=request.headers.get('accept'),
                                       sdk_version=7, manage_message_history='server'), trace)
    # Who asked, the owner's saved notes, what Kitchen answered before (as data), the limits and the turn's trace.
    settings = await run_in_threadpool(agent.settings, text, request.headers.get('tailscale-user-login'), shown, trace)
    events = adapter.run_stream(model=agent.Traced(chat_model, trace), message_history=shown, on_complete=on_complete,
                                **settings)

    async def stream():
        try:
            async for chunk in events:
                if getattr(chunk, 'type', '') == 'error':   # never a provider's or library's own words
                    chunk = chunk.model_copy(update={'error_text': FAILED_CHANGED if trace.changed() else FAILED})
                yield chunk
        finally:
            _running.discard(thread_id)
            if done:
                trace.finish('ok')
            else:
                _end_failed(thread_id, older + shown, text, day, trace)

    return adapter.streaming_response(stream())
