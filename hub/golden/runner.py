"""Runs golden scenarios through the real Ask pipeline and checks what happened.

Each turn runs exactly as the /ask route runs it: `agent.build()`, the same instructions, tools, limits and
recall (`agent.settings`), streamed. Only the model differs by mode:

  offline  a scripted model that follows each scenario's script. No network; runs in the test suite and CI.
  live     the real model on Ask's pinned zero-retention route (`model.build()`, with its guard), over the
           owner's real Hub data. Writes go through the dry-run layer. A hard cost cap per run, its own
           monthly limit, and one line in the engine's record of runs ("golden"). Started by the owner only:
           from Settings or the command line, never on a schedule.

Live results are kept in Hub's private data folder (golden/runs/), because they hold real names and replies.
"""
import asyncio
import json
import os
import time
from datetime import datetime

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .. import config, home, household
from . import checks, dryrun, scenarios

KIND = 'golden'               # these runs in the engine's record, with a monthly limit of their own
RUN_COST_CAP = 0.50           # dollars a live run may spend; it stops before a turn that could go over
MONTH_COST_CAP = 5.00         # dollars of live runs in a calendar month
RUN_CALLS_CAP = 80            # model calls a live run may make, for when no cost is reported
LOGIN = 'golden-test@hub.invalid'   # who "asks" in a golden run; marks any live-write change as a test's
KEEP = 30                     # result files kept
FOLDER = 'golden'
REQUESTS_PER_TURN = 6         # as agent.REQUESTS_PER_TURN; checked equal in the tests


class Stopped(Exception):
    """The run stopped before this turn. The message is safe to show."""


def names(live=False):
    """Alias -> name. Offline: the made-up ones. Live: the owner's own from household.json, where set."""
    if not live:
        return dict(scenarios.ALIASES)
    own = household.section('golden').get('names', {})
    return {**scenarios.ALIASES, **{k: str(v) for k, v in own.items() if k in scenarios.ALIASES and str(v).strip()}}


def _fill(value, aliases, lower=False):
    if isinstance(value, str):
        return value.format(**({k: v.lower() for k, v in aliases.items()} if lower else aliases))
    if isinstance(value, (list, tuple)):
        return [_fill(v, aliases, lower) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, aliases, lower) for k, v in value.items()}
    return value


# ---- the scripted model (offline) ----

def _since_prompt(messages):
    """(responses so far this turn, tool results so far this turn by tool name)."""
    responses, results = 0, {}
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    responses, results = 0, {}
                elif isinstance(part, ToolReturnPart):
                    results[part.tool_name] = part.content
        elif isinstance(message, ModelResponse):
            responses += 1
    return responses, results


def _picked(results):
    found = results.get('play_music')
    text = found.get('picked', '') if isinstance(found, dict) else ''
    return text.split(', a playlist')[0].split(', from your')[0].split(', from Apple')[0] or 'nothing'


def scripted(scenario, aliases):
    """A model that follows the scenario's script, one step per answer, turn by turn."""
    state = {'turn': -1}

    async def stream(messages, info):
        step, results = _since_prompt(messages)
        if step == 0:
            state['turn'] += 1
        plan = scenario.script[state['turn']]
        move = plan[min(step, len(plan) - 1)]
        if isinstance(move, scenarios.Use):
            args = {k: _fill(v, aliases) for k, v in move.args.items()}
            yield {0: DeltaToolCall(name=move.tool, json_args=json.dumps(args), tool_call_id=f'golden_{state["turn"]}_{step}')}
        else:
            yield move.words.format(**aliases, picked=_picked(results))
    return FunctionModel(stream_function=stream, model_name='golden-scripted')


# ---- one scenario ----

def _cost(messages):
    return sum(float((m.provider_details or {}).get('cost') or 0) for m in messages if isinstance(m, ModelResponse))


def _tool_args(part):
    try:
        return part.args_as_dict()
    except Exception:  # noqa: BLE001 - a model's broken arguments are part of what the trace shows
        return {'unreadable': str(part.args)[:300]}


def _house(trace):
    """Device and room names as Hub sees them, so checks can speak of aliases."""
    from ..tools.house import control, read
    try:
        found = read.snapshot(fresh=True)
    except Exception:  # noqa: BLE001 - broker.HomeDown or worse: a run without the house still checks the rest
        return
    trace.devices = {e: d['name'] for e, d in control.devices(found).items()}
    trace.rooms = {r['name']: [d['id'] for d in r['devices'] if d['kind'] == 'light'] for r in found['rooms']}
    from ..tools.house import nowplaying
    for call in trace.calls:            # what each Now playing card would show now, read as the page reads it
        card = call.result.get('card') if isinstance(call.result, dict) else None
        if card and card.get('kind') == 'music':
            trace.cards[card['id']] = nowplaying.state(card['id'], found)


class Spend:
    """What a run has spent, and the line it may not cross."""

    def __init__(self, cap=None, calls=None):
        self.cap, self.calls_cap, self.cost, self.calls, self.tokens_in, self.tokens_out, self.worst = (
            cap, calls, 0.0, 0, 0, 0, 0.0)

    def check(self):
        if self.cap is not None and self.cost + self.worst > self.cap:
            raise Stopped(f'Stopped at the ${self.cap:.2f} cost cap for one run.')
        if self.calls_cap is not None and self.calls + REQUESTS_PER_TURN > self.calls_cap:
            raise Stopped(f'Stopped at {self.calls_cap} model calls for one run.')

    def add(self, result):
        cost = _cost(result.new_messages())
        usage = result.usage
        self.cost += cost
        self.worst = max(self.worst, cost)
        self.calls += usage.requests
        self.tokens_in += usage.input_tokens or 0
        self.tokens_out += usage.output_tokens or 0


async def _turns(scenario, make_model, aliases, ledger, spend):
    from ..agent import agent
    trace = checks.Trace(names=aliases)
    history = []

    async def drain(ctx, events):
        async for _ in events:
            pass
    for n, words in enumerate(scenario.turns):
        spend.check()
        text = _fill(words, aliases, lower=True)
        ledger.turn = n
        trace.texts.append(text)
        settings = agent.settings(text, LOGIN, history)
        result = await agent.build().run(text, model=make_model(), message_history=history, event_stream_handler=drain,
                                         **settings)
        spend.add(result)
        new = result.new_messages()
        returns = {p.tool_call_id: p.content for m in new if isinstance(m, ModelRequest)
                   for p in m.parts if isinstance(p, ToolReturnPart)}
        trace.calls += [checks.Call(n, p.tool_name, _tool_args(p), returns.get(p.tool_call_id))
                        for m in new if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, ToolCallPart)]
        last = [m for m in new if isinstance(m, ModelResponse)][-1:]
        trace.replies.append(''.join(p.content for m in last for p in m.parts if isinstance(p, TextPart)))
        history = result.all_messages()
    return trace


def evaluate(scenario, trace):
    """[{'check', 'why'}] for each check that failed. Empty: the scenario passed."""
    failed = []
    for check in scenario.checks:
        try:
            why = check(trace)
        except Exception as error:  # noqa: BLE001 - a check that can't read the trace fails, it doesn't crash the run
            why = f'The check couldn’t run: {type(error).__name__}.'
        if why:
            failed.append({'check': str(check), 'why': why})
    return failed


def run(scenario, make_model, *, aliases, spend=None, writes=False):
    """Run one scenario. Returns (trace, failures). `make_model()` gives a fresh model for each turn.
    `writes` (live writes) is honoured only for a scenario marked harmless; everything else is a dry run."""
    from ..tools.house import read
    read.clear_cache()
    spend = spend or Spend()
    given = [(name, _fill(steps, aliases)) for name, steps in scenario.given]
    live = bool(writes and scenario.harmless)
    started = time.time()
    with dryrun.layer(house_writes=live, recipes=given) as ledger:
        try:
            trace = asyncio.run(_turns(scenario, make_model, aliases, ledger, spend))
            trace.changes = list(ledger.changes)
            _house(trace)
            failures = evaluate(scenario, trace)
        finally:
            undone = _put_back(started) if live else []
    if undone:
        trace.undone = undone
        failures += [{'check': 'is put back afterwards', 'why': u['why']} for u in undone if u.get('why')]
    return trace, failures


def _put_back(since):
    """Undo every house change a live-writes scenario made, newest first. [{'batch', 'why'?}]."""
    from ..tools.house import control
    out = []
    for row in control.recent():
        if row['login'] != LOGIN or row['ts'] < since or row['via'] == 'undo' or row.get('undone'):
            continue
        try:
            control.undo(row['batch'], login=LOGIN, via='golden')
            out.append({'batch': row['batch']})
        except control.Refused as error:
            out.append({'batch': row['batch'], 'why': f'Couldn’t put back: {error}'})
    return out


def offline(scenario):
    """One scenario with the scripted model and made-up names. For the test suite."""
    aliases = names()
    model = scripted(scenario, aliases)
    return run(scenario, lambda: model, aliases=aliases)


# ---- a live run ----

def _folder():
    folder = config.DATA_DIR / FOLDER / 'runs'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


def _lock():
    return config.DATA_DIR / FOLDER / 'running'


def running():
    """Whether a live run is going now (its process is alive)."""
    try:
        pid = int(_lock().read_text())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


def refusal(cap=RUN_COST_CAP):
    """Why a live run can't start now, in plain words, or None."""
    from .. import luna
    from ..engine import state
    if not luna.configured():
        return 'The model key isn’t set up.'
    if running():
        return 'A golden run is already going.'
    spent = state.spent(home.today().isoformat(), KIND)['month_cost']
    if spent + min(cap, RUN_COST_CAP) > MONTH_COST_CAP:
        return f'This month’s golden-test budget (${MONTH_COST_CAP:.0f}) is used up.'
    return None


def _turn_view(trace, n):
    return {'text': trace.texts[n], 'reply': trace.replies[n] if n < len(trace.replies) else '',
            'calls': [{'tool': c.tool, 'args': c.args} for c in trace.calls if c.turn == n],
            'changes': [{'to': c['to'], 'what': c['what'], 'data': c['data']} for c in trace.changes if c['turn'] == n]}


def live(only=(), cap=RUN_COST_CAP, writes=False, model_for=None):
    """Run the scenarios (or `only` these ids) with the real model. Returns the stored result.
    `model_for(scenario, aliases)` gives the model maker for one scenario; only tests pass it."""
    from ..agent import model
    from ..engine import state
    cap = min(cap, RUN_COST_CAP)
    why_not = refusal(cap)
    if why_not:
        raise Stopped(why_not)
    chosen = [s for s in scenarios.SCENARIOS if not only or s.id in only]
    lock = _lock()
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock.write_text(str(os.getpid()))
    aliases, spend, out, stopped, broke = names(live=True), Spend(cap, RUN_CALLS_CAP), [], None, False
    try:
        for scenario in chosen:
            put_back = 0
            if stopped:
                out.append({'id': scenario.id, 'about': scenario.about, 'status': 'skipped', 'failures': [], 'turns': []})
                continue
            try:
                make = model_for(scenario, aliases) if model_for else model.build
                trace, failures = run(scenario, make, aliases=aliases, spend=spend, writes=writes)
                status = 'fail' if failures else 'pass'
                turns = [_turn_view(trace, n) for n in range(len(trace.texts))]
                put_back = len(trace.undone)
            except Stopped as error:
                stopped, status, failures, turns = str(error), 'skipped', [], []
            except Exception as error:  # noqa: BLE001 - one broken scenario is reported, the rest still run
                broke, status, turns = True, 'error', []
                failures = [{'check': 'runs to the end', 'why': f'It stopped with {type(error).__name__}.'}]
            out.append({'id': scenario.id, 'about': scenario.about, 'status': status, 'failures': failures, 'turns': turns,
                        'live_writes': bool(writes and scenario.harmless), 'put_back': put_back})
    finally:
        lock.unlink(missing_ok=True)
        state.record_run(home.today().isoformat(), KIND, 'error' if broke else 'ok', luna_calls=spend.calls,
                         tokens_in=spend.tokens_in, tokens_out=spend.tokens_out, cost=spend.cost)
    result = {'at': time.time(), 'mode': 'live', 'writes': 'live for harmless scenarios' if writes else 'dry run',
              'cost': round(spend.cost, 4), 'cap': cap, 'calls': spend.calls, 'stopped': stopped, 'scenarios': out}
    folder = _folder()
    path = folder / f'{datetime.now():%Y%m%d-%H%M%S}.json'
    path.write_text(json.dumps(result, indent=1, ensure_ascii=False))
    path.chmod(0o600)
    for old in sorted(folder.glob('*.json'))[:-KEEP]:
        old.unlink()
    return result


def latest():
    """The newest live result, summed up for Insights and Settings, or None. Counts and ids only."""
    try:
        newest = sorted(_folder().glob('*.json'))[-1]
        found = json.loads(newest.read_text())
    except (IndexError, ValueError, OSError):
        return None
    rows = found.get('scenarios', [])
    return {'at': found.get('at'), 'when': datetime.fromtimestamp(found.get('at') or 0, home.PACIFIC).strftime('%b %-d, %-I:%M %p'),
            'passed': sum(r['status'] == 'pass' for r in rows), 'total': len(rows),
            'failed': [r['id'] for r in rows if r['status'] in ('fail', 'error')],
            'skipped': sum(r['status'] == 'skipped' for r in rows), 'cost': found.get('cost', 0),
            'writes': found.get('writes', 'dry run'), 'stopped': found.get('stopped'), 'scenarios': rows}
