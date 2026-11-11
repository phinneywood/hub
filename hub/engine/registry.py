"""The shapes every source and action has."""
from dataclasses import dataclass
from typing import Callable, Optional

# How much the owner lets an action do without asking, least first.
#   suggest  show it in “Needs you” and link to its preview page
#   undo     offer it as one tap; it must be reversible
#   auto     run it unasked and report afterwards
TRUST = ('suggest', 'undo', 'auto')
TRUST_WORDS = {'suggest': 'Suggest only', 'undo': 'One tap, with undo', 'auto': 'Just do it and tell me'}


@dataclass(frozen=True)
class Lookup:
    """Something Ask Hub may look up for the owner, read-only. The chat's tools are exactly these."""
    slug: str            # the tool's name
    name: str            # what the chat calls it: “Looked at <name>”
    reads: str           # plain words: what Hub looks at
    sends: str           # plain words: what of it the model sees
    page: str            # the Hub page an answer built on it links to; Hub picks the link, never the model
    open: str            # that link's words
    run: Callable        # typed arguments and a docstring the model reads; returns a small bounded dict


@dataclass(frozen=True)
class Source:
    slug: str
    name: str
    reads: str                                   # plain words: what Hub looks at
    sends: str                                   # plain words: what of it a model may see
    items: Optional[Callable[[], list]] = None   # rule-based “Needs you” items; no model
    evidence: Optional[Callable] = None          # evidence() -> bounded dict for the overnight review, or None
    prepare: Optional[Callable[[], None]] = None  # once a day with the morning job: gets its pages ready; no model
    sort: Optional[Callable[[], None]] = None     # with the morning job, first: sorts what is new; has its own limits
    lookups: tuple = ()                           # what Ask Hub may look up here; read-only


@dataclass(frozen=True)
class Action:
    slug: str
    name: str
    does: str                    # plain words
    undo: str                    # plain words: how it is reversed
    page: str                    # preview page inside Hub
    post: Optional[str] = None   # existing same-origin route that performs it in one tap
    default: str = 'suggest'     # trust level until the owner changes it
    ceiling: str = 'suggest'     # the most the code supports today, whatever the owner picks
    auto: Optional[Callable[[], Optional[str]]] = None  # does it unasked; returns what it did in words, or None

    def levels(self):
        return TRUST[:TRUST.index(self.ceiling) + 1]
