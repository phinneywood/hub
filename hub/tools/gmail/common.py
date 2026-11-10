import json
import os
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ... import bridge, config, senders, store
from ...templating import render

SAMPLE_SIZE = 500          # the bridge's maximum; 2.5x the old 200-message sample
INBOX_QUERY = "in:inbox"
OVERVIEW_FRESH = 900       # serve cached, refresh in the background after 15 minutes
QUERY_LIMIT = 1000         # bridge bound on Gmail query length
OVERVIEW = {"query": INBOX_QUERY, "sample_size": SAMPLE_SIZE}
PACIFIC = ZoneInfo("America/Los_Angeles")


SAVED_OVERVIEW = "gmail-overview.json"  # last inbox overview, so a restart doesn't wait on the bridge
_loaded = set()
_saved_at = {}  # data directory -> fetched_at of the copy last written


def _saved_path():
    return config.DATA_DIR / SAVED_OVERVIEW


def _load_saved():
    """Once per data directory, prime the cache with the last saved inbox overview."""
    marker = str(config.DATA_DIR)
    if marker in _loaded:
        return
    _loaded.add(marker)
    try:
        saved = json.loads(_saved_path().read_text())
        if saved.get("sample_size") == SAMPLE_SIZE and isinstance(saved.get("overview"), dict):
            when = float(saved["fetched_at"])
            if bridge.seed("/gmail/overview", saved["overview"], when, query=INBOX_QUERY, sample_size=SAMPLE_SIZE):
                _saved_at[marker] = when
    except (OSError, ValueError, TypeError, KeyError):
        pass


def _save(value, when):
    path = _saved_path()
    if _saved_at.get(str(config.DATA_DIR)) == when:
        return
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched_at": when, "sample_size": SAMPLE_SIZE, "overview": value}))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        _saved_at[str(config.DATA_DIR)] = when
    except OSError:
        pass


def overview(query=INBOX_QUERY, fresh=False):
    """The inbox sample. Served from cache and refreshed behind the page, unless `fresh` asks Gmail now."""
    inbox = query == INBOX_QUERY
    if inbox:
        _load_saved()
    if fresh:
        value = bridge.get("/gmail/overview", ttl=0, query=query, sample_size=SAMPLE_SIZE)
    else:
        value = bridge.get_swr("/gmail/overview", fresh=OVERVIEW_FRESH, query=query, sample_size=SAMPLE_SIZE)
    if inbox:
        when = overview_fetched_at()
        if when:
            _save(value, when)
    return value


def overview_fetched_at():
    """Epoch seconds when the cached inbox overview came from Gmail, or None."""
    return bridge.fetched_at("/gmail/overview", query=INBOX_QUERY, sample_size=SAMPLE_SIZE)


def age_label(when, now=None):
    """'just now', '12 min ago', '3 hr ago', '2 days ago'."""
    seconds = max(0, (now or time.time()) - when)
    if seconds < 120:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hr ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' * (days != 1)} ago"


def inbox_counts(ov):
    return next((x for x in ov.get("label_counts", []) if x.get("id") == "INBOX"), None)


def excluding(base, addresses):
    """`base` plus `-from:` terms for as many addresses as fit the bridge's query bound."""
    query = base
    for address in addresses:
        term = f" -from:{address}"
        if not senders.valid_sender(address) or len(query) + len(term) > QUERY_LIMIT:
            continue
        query += term
    return query


def sender_queue(decided, ranked_first=()):
    """Loud senders not yet decided, beyond the first sample's top 20 when needed.

    The bridge reports a sample's top 20 senders. Once those are all decided
    (kept, planned or deferred), sample the inbox again without them so review
    can continue while changes are paused or not yet applied. Returns
    (pending [(sender, sampled count)], overview used).
    """
    base = overview()
    todo = senders.pending(base, decided)
    if todo:
        return todo, base
    known = {s.lower(): n for s, n in ranked_first}
    order = sorted(decided, key=lambda s: -known.get(s.lower(), 0))
    more = overview(excluding(INBOX_QUERY, order))
    return senders.pending(more, decided), more


def track_inbox(ov):
    inbox = inbox_counts(ov) or {}
    store.record_inbox(datetime.now(PACIFIC).date().isoformat(), inbox.get("messagesTotal"), inbox.get("messagesUnread"))


def inbox_trend(days=7):
    """Change in inbox size versus the earliest snapshot within `days`, or None."""
    history = store.inbox_history(days + 1)
    if len(history) < 2 or history[0]["total"] is None or history[-1]["total"] is None:
        return None
    first, last = history[0], history[-1]
    span = (date.fromisoformat(last["day"]) - date.fromisoformat(first["day"])).days
    return {"change": last["total"] - first["total"], "days": span, "since": first["day"]}


def page(request, template, section, **context):
    from ... import delayed
    context.setdefault("delayed_stopped", delayed.health()["stopped"])
    return render(request, "gmail/" + template, active="gmail", section=section, plan_count=store.plan_count(), **context)


def describe_filter(rule, labels=None):
    labels = {x["id"]: x["name"] for x in (labels or bridge.get("/gmail/labels").get("labels", []))}
    criteria, action = rule.get("criteria", {}), rule.get("action", {})
    names = lambda ids: [labels.get(x, x) for x in ids or []]
    return {**rule,
            "match": [f"{k}: {v}" for k, v in criteria.items()],
            "add": names(action.get("addLabelIds")),
            "remove": names(action.get("removeLabelIds"))}
