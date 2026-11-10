"""What Hub gets ready for Gmail once a day, with the morning job.

Read-only, and no model: today's inbox count, the next loud senders with a cautious
pre-selection, how much inbox mail is over a year old, and how much the backlog step would
archive. Only addresses, sender names and counts are saved (no subjects), so the home list and
the pages answer at once.
"""
import json
import os
import time

from ... import actions, bridge, config, senders
from . import common

SAVED = "gmail-prepared.json"
FRESH_SECONDS = 36 * 60 * 60   # a missed morning falls back to working the list out on the page
OLD_MAIL = actions.sweep(365)  # the bulk step the backlog count stands for; never starred or important mail
OLD_MAIL_PREVIEW = "/gmail/sweep/preview?kind=archive&days=365"
BACKLOG_DAYS = 30
BACKLOG_PREVIEW = "/gmail/backlog"


def backlog_spec(protect_important=False):
    """The one-step backlog clear: every inbox message over 30 days old, never starred mail."""
    return actions.sweep(BACKLOG_DAYS, protect_important=protect_important)


def backlog(ov=None):
    """How much inbox mail the backlog step would archive: {'count', 'how', 'floor'}; how is exact, about or more.

    Counting every match stops at the helper's cap, so a large backlog is worked out from the inbox
    total less what is recent or starred. `floor` is what was actually counted.
    """
    def count(query):
        return bridge.get("/gmail/count", ttl=300, query=query, max=senders.COUNT_CAP)
    found = count(actions.sweep_query(backlog_spec()))
    if found["exact"]:
        return {"count": found["count"], "how": "exact", "floor": found["count"]}
    total = (common.inbox_counts(ov or common.overview()) or {}).get("messagesTotal")
    recent = count(f"in:inbox newer_than:{BACKLOG_DAYS}d")
    starred = count(f"in:inbox is:starred older_than:{BACKLOG_DAYS}d")
    if isinstance(total, int) and recent["exact"] and starred["exact"] and total - recent["count"] - starred["count"] > found["count"]:
        return {"count": total - recent["count"] - starred["count"], "how": "about", "floor": found["count"]}
    return {"count": found["count"], "how": "more", "floor": found["count"]}


def backlog_text(found):
    """“About 87,000”, “More than 5,000” or “1,234”."""
    return {"about": "About ", "more": "More than "}.get(found["how"], "") + f"{found['count']:,}"


def _path():
    return config.DATA_DIR / SAVED


def prepare(now=None):
    """Ask Gmail afresh and save the result. Returns what was saved."""
    from . import assess, missions
    now = time.time() if now is None else now
    ov = common.overview(fresh=True)
    common.track_inbox(ov)
    todo, sample = common.sender_queue(missions.decided_senders(), senders.ranked(ov))
    old = bridge.get("/gmail/count", ttl=300, query=actions.sweep_query(OLD_MAIL), max=senders.COUNT_CAP)
    saved = {"at": now, "sample": sample.get("sample_size"), "rows": assess.rows(todo[:assess.REVIEW_LIST_SIZE], named=True),
             "old_mail": {"count": old["count"], "exact": bool(old["exact"])}, "backlog": backlog(ov)}
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(saved))
    os.chmod(tmp, 0o600)
    os.replace(tmp, _path())
    return saved


def load(now=None):
    """The last prepared batch if it is recent enough, else None."""
    try:
        saved = json.loads(_path().read_text())
        if (time.time() if now is None else now) - float(saved["at"]) > FRESH_SECONDS:
            return None
        saved["rows"] = [r for r in saved["rows"] if senders.valid_sender(r.get("sender", ""))]
        return saved
    except (OSError, ValueError, TypeError, KeyError):
        return None


def saved_backlog(now=None):
    """The backlog size from the last prepared batch, or None."""
    found = (load(now) or {}).get("backlog")
    ok = isinstance(found, dict) and isinstance(found.get("count"), int) and found.get("how") in ("exact", "about", "more")
    return found if ok else None


def waiting(saved, decided):
    """Prepared senders the owner hasn't decided since."""
    return [r for r in (saved or {}).get("rows", []) if r["sender"].lower() not in decided]
