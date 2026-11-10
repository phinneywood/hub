"""Disabled, offline-only judge routing. No credentials, transport or publication.

Injected judges are test doubles, not a supported live adapter. Probabilities are
four separate predicates; no score can override a deterministic hold or human review.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import time
from datetime import date

AXES = ('supported', 'privacy_safe', 'scope_appropriate', 'useful')
STATES = ('eligible_in_trial', 'rejected_in_trial', 'held_uncertain')


@dataclass(frozen=True)
class Policy:
    enabled: bool = False
    escalation: bool = False
    accept: float = .95
    reject: float = .05
    max_calls: int = 100
    max_cost_micros: int = 100_000
    max_seconds: float = 30
    max_input_bytes: int = 9000
    audit_every: int = 10

    def __post_init__(self):
        if (type(self.enabled) is not bool or type(self.escalation) is not bool or
                not all(type(x) in (int, float) and math.isfinite(x) for x in (self.accept, self.reject)) or
                not 0 <= self.reject < .5 < self.accept <= 1 or
                any(type(x) is not int or x < 0 for x in
                    (self.max_calls, self.max_cost_micros, self.max_input_bytes, self.audit_every)) or
                type(self.max_seconds) not in (int, float) or not math.isfinite(self.max_seconds) or self.max_seconds <= 0):
            raise ValueError('Invalid offline routing policy.')


@dataclass(frozen=True)
class Route:
    model: str
    provider: str
    max_cost_micros: int

    def __post_init__(self):
        if (not isinstance(self.model, str) or not self.model.startswith('fixture/') or
                self.provider != 'offline-fixture' or type(self.max_cost_micros) is not int or self.max_cost_micros < 0):
            raise ValueError('Only explicit offline fixture routes are supported.')


CHEAP = Route('fixture/cheap', 'offline-fixture', 10)
STRONG = Route('fixture/strong', 'offline-fixture', 100)


def scores(value):
    if (not isinstance(value, dict) or set(value) != set(AXES) or
            any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in value.values())):
        raise ValueError('Unusable offline judge scores.')
    return {axis: float(value[axis]) for axis in AXES}


def verdict(value, policy):
    value = scores(value)
    if any(v <= policy.reject for v in value.values()):
        return 'rejected_in_trial'
    return 'eligible_in_trial' if all(v >= policy.accept for v in value.values()) else 'held_uncertain'


def verdict_reasons(value, policy):
    value = scores(value)
    rejected = [axis + '_negative' for axis in AXES if value[axis] <= policy.reject]
    return rejected or [axis + '_uncertain' for axis in AXES if value[axis] < policy.accept]


class Runner:
    """Single-run, nonconcurrent budget accounting for offline fixtures only.

    Reserve worst-case estimated cost BEFORE each call, including failed calls.
    Elapsed limits reject late results and prevent subsequent calls; synchronous
    injected code cannot be interrupted. A future live adapter needs its own
    approved timeouts, durable budget/uncertainty journal and provider controls.
    """
    def __init__(self, policy=Policy(), *, clock=time.monotonic):
        self.policy = policy
        self.clock = clock
        self.started = clock()
        self.calls = 0
        self.reserved_micros = 0
        self.stopped = False

    def _call(self, judge, route, state):
        if self.stopped:
            return None, 'held_run_stopped'
        if self.clock() - self.started >= self.policy.max_seconds:
            self.stopped = True
            return None, 'held_time_limit'
        if (self.calls >= self.policy.max_calls or
                self.reserved_micros + route.max_cost_micros > self.policy.max_cost_micros):
            self.stopped = True
            return None, 'held_budget'
        if getattr(judge, 'offline', False) is not True or getattr(judge, 'route', None) != route:
            self.stopped = True
            return None, 'held_route'
        self.calls += 1
        self.reserved_micros += route.max_cost_micros
        try:
            # A fresh copy prevents one fixture mutating the next judge's input.
            result = judge(json.loads(json.dumps(state)))
            if self.clock() - self.started >= self.policy.max_seconds:
                self.stopped = True
                return None, 'held_time_limit'
            if (not isinstance(result, dict) or set(result) != {'model', 'provider', 'scores'} or
                    result['model'] != route.model or result['provider'] != route.provider):
                raise ValueError()
            return scores(result['scores']), None
        except Exception:
            self.stopped = True
            return None, 'held_judge_failure'

    def judge(self, state, cheap=None, strong=None, *, deterministic_hold=False, mode='hybrid'):
        """No default inference. Holds and disabled state precede any injected call."""
        result = {'state': 'held_disabled', 'cheap_scores': None, 'strong_scores': None,
                  'escalated': False, 'audited': False, 'human_review_required': True,
                  'hold_reasons': ['routing_disabled']}

        def finish(status, *reasons, **changes):
            return {**result, **changes, 'state': status, 'hold_reasons': list(reasons)}

        if not self.policy.enabled:
            return result
        if deterministic_hold is not False:
            return finish('held_deterministic', 'deterministic_hold')
        if mode not in ('cheap', 'strong', 'hybrid'):
            return finish('held_mode', 'invalid_routing_mode')
        from .family_gate import GateError, display_roles, public_copy
        try:
            if (not isinstance(state, dict) or set(state) != {'copy', 'evidence', 'display_role', 'as_of_date'} or
                    not isinstance(state['evidence'], str) or len(state['evidence']) > 500 or
                    state['display_role'] not in display_roles()):
                raise ValueError()
            if date.fromisoformat(state['as_of_date']).isoformat() != state['as_of_date']:
                raise ValueError()
            minimized = public_copy(state['copy'])
            if minimized != state['copy']:
                raise ValueError()
            encoded = json.dumps(state, sort_keys=True, ensure_ascii=False).encode()
            if len(encoded) > self.policy.max_input_bytes:
                raise ValueError()
        except GateError as error:
            return finish('held_input', error.code)
        except (ValueError, TypeError, KeyError):
            return finish('held_input', 'invalid_routing_input')
        if mode == 'strong':
            if not self.policy.escalation:
                return finish('held_escalation_disabled', 'escalation_disabled')
            value, error = self._call(strong, STRONG, state)
            return finish(error or verdict(value, self.policy),
                          *([error] if error else verdict_reasons(value, self.policy)), strong_scores=value)
        value, error = self._call(cheap, CHEAP, state)
        result = {**result, 'cheap_scores': value}
        if error:
            return finish(error, error)
        decision = verdict(value, self.policy)
        audit = bool(self.policy.audit_every and
                     int(hashlib.sha256(encoded).hexdigest(), 16) % self.policy.audit_every == 0 and
                     decision != 'held_uncertain')
        if mode == 'cheap' or (decision != 'held_uncertain' and not audit):
            return finish(decision, *verdict_reasons(value, self.policy))
        if not self.policy.escalation:
            return finish('held_escalation_disabled', 'escalation_disabled', *verdict_reasons(value, self.policy))
        # A confident rejection of one predicate must not conceal uncertainty in
        # another during auditing. Strong evidence never overrides confident conflict.
        value2, error = self._call(strong, STRONG, state)
        result = {**result, 'strong_scores': value2, 'escalated': True, 'audited': audit}
        if error:
            return finish(error, error)
        conflict = any((value[a] >= self.policy.accept and value2[a] <= self.policy.reject) or
                       (value[a] <= self.policy.reject and value2[a] >= self.policy.accept) for a in AXES)
        if conflict:
            return finish('held_disagreement', 'confident_predicate_disagreement')
        return finish(verdict(value2, self.policy), *verdict_reasons(value2, self.policy))
