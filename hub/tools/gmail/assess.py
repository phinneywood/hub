"""Luna's per-sender assessment and the one-page review list.

Luna sees only counts, the unread share, cadence and up to eight recent subject lines (never
message bodies), through the same zero-retention route as custom actions. Every option it
proposes is re-validated by actions.validate before it is shown, and nothing reaches the plan
until the owner picks it. Senders the owner opted out of AI never leave the Mac mini.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

import json

from ... import actions, bridge, luna, senders, store, worker
from . import common, nightly
from .common import page
from .missions import card_signals, decided_senders, go, is_paused, planned_new_labels, preview_spec, user_labels

router = APIRouter()
CACHE_SECONDS = 3600
_cache = {}
REVIEW_LIST_SIZE = 12
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="gmail-review")


def age_days(value, now=None):
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0, ((now or datetime.now(timezone.utc)) - moment).days)


def evidence(sender):
    """Read-only facts about one sender. Counts and subject lines only."""
    inbox = bridge.get("/gmail/count", ttl=300, query=senders.inbox_query(sender), max=senders.COUNT_CAP)
    month = bridge.get("/gmail/count", ttl=300, query=f"from:{sender} newer_than:30d", max=senders.COUNT_CAP)
    everywhere = bridge.get("/gmail/count", ttl=300, query=f"from:{sender}", max=senders.COUNT_CAP)
    recent = bridge.get("/gmail/search", ttl=300, query=f"from:{sender}", limit=8).get("messages", [])
    signals = card_signals(sender, inbox["count"], inbox["exact"])
    rules = bridge.get("/gmail/filters", limit=100).get("filters", [])
    return {"sender": sender, "display_name": parseaddr(recent[0]["from"])[0] if recent else "",
            "messages_in_inbox": inbox["count"], "inbox_count_exact": inbox["exact"],
            "messages_last_30_days": month["count"], "messages_total": everywhere["count"],
            "total_count_exact": everywhere["exact"], "unread_share_percent": signals["unread_share"],
            "already_filtered_out_of_inbox": senders.covered(sender, rules),
            "recent_messages": [{"subject": m.get("subject", ""), "age_days": age_days(m.get("date")),
                                 "unread": "UNREAD" in (m.get("labels") or [])} for m in recent]}


def _option(raw, sender, facts, labels):
    kind = raw.get("kind") if isinstance(raw, dict) else None
    title = luna.clip(raw.get("title", ""), 70) if kind else ""
    why = luna.clip(raw.get("why", ""), 240) if kind else ""
    if kind == "keep":
        return {"kind": kind, "title": title or "Keep in inbox", "why": why}
    if kind == "archive_after_30_days":
        return {"kind": kind, "title": title or "Archive after 30 days", "why": why}
    if kind != "action" or not isinstance(raw.get("action"), dict):
        return None
    try:
        spec = actions.validate({**raw["action"], "unsupported": None}, sender, labels, planned_new_labels(sender))
    except actions.ActionError:
        return None
    detail = preview_spec(spec, {"count": facts["messages_in_inbox"], "exact": facts["inbox_count_exact"]})
    count = detail["count"] if spec["existing"] else None
    return {"kind": kind, "title": title or "Custom action", "why": why, "spec": spec, "spec_json": json.dumps(spec),
            "sentence": actions.describe(spec, count, detail["exact"]), "preview": detail}


def assessment(sender, feedback="", previous=None):
    key = (sender.lower(), feedback)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    facts = evidence(sender)
    labels = user_labels()
    raw = luna.assess({k: v for k, v in facts.items() if k != "display_name"}, [x["name"] for x in labels],
                      feedback or None, previous)
    options, seen = [], set()
    for item in raw["options"][:luna.MAX_OPTIONS]:
        option = _option(item, sender, facts, labels)
        signature = json.dumps(option.get("spec") if option and option.get("spec") else option and option["kind"], sort_keys=True)
        if option and signature not in seen:
            seen.add(signature)
            options.append(option)
    result = {"facts": facts, "summary": luna.clip(raw["assessment"], 600), "options": options,
              "dropped": len(raw["options"][:luna.MAX_OPTIONS]) - len(options),
              "previous": [{"kind": o["kind"], "title": o["title"], "action": o.get("spec")} for o in options]}
    _cache[key] = (time.monotonic(), result)
    store.log("luna assessed", sender)
    return result


def _assess_page(request, sender, source="", feedback="", error=None, result=None):
    blocked, configured = store.ai_blocked(sender), luna.configured()
    return page(request, "assess.html", "tools", sender=sender, source=source, feedback=feedback, error=error,
                result=result, blocked=blocked, configured=configured, paused=is_paused(),
                saved=store.get_by_sender(sender))


@router.get("/assess", response_class=HTMLResponse)
def assess_page(request: Request, sender: str, source: str = ""):
    if not senders.valid_sender(sender):
        return go("/gmail/quiet")
    cached = _cache.get((sender.lower(), ""))
    fresh = cached and time.monotonic() - cached[0] < CACHE_SECONDS
    return _assess_page(request, sender, source, result=cached[1] if fresh else None)


@router.post("/assess", response_class=HTMLResponse)
def assess_run(request: Request, sender: str = Form(...), source: str = Form(""), feedback: str = Form(""),
               previous: str = Form("")):
    if not senders.valid_sender(sender):
        return go("/gmail/quiet")
    feedback = feedback.strip()[:300]
    if store.ai_blocked(sender) or not luna.configured():
        return _assess_page(request, sender, source)
    try:
        prior = json.loads(previous) if previous and feedback else None
    except ValueError:
        prior = None
    try:
        result = assessment(sender, feedback, prior)
    except luna.LunaError as err:
        store.log("luna assessment failed", f"{sender}: {err}")
        return _assess_page(request, sender, source, feedback, error=str(err))
    if not result["options"]:
        return _assess_page(request, sender, source, feedback, result=result,
                            error="Luna's suggestions didn't pass Hub's safety checks. Try again, or choose yourself.")
    return _assess_page(request, sender, source, feedback, result=result)


# ---------- Review list: many senders on one page ----------

def suggestion(row):
    """A cautious default from read-only signals. The owner always confirms."""
    if row["covered"]:
        return "archive"
    share = row["unread_share"]
    if share is None:
        return ""
    if share >= 85:
        return "filter_archive"
    return ""  # read-heavy or mixed senders: the owner (or Luna) decides


def _row(sender, sampled, named=False):
    count = bridge.get("/gmail/count", ttl=300, query=senders.inbox_query(sender), max=1000)  # fast; card shows exact
    signals = card_signals(sender, count["count"], count["exact"])
    name = ""
    if named:  # one more read per sender: only when preparing ahead, never while the owner waits
        recent = bridge.get("/gmail/search", ttl=300, query=senders.inbox_query(sender), limit=1).get("messages", [])
        name = luna.clip(parseaddr(recent[0].get("from", ""))[0], 80) if recent else ""
    return {"sender": sender, "name": name, "sampled": sampled, "count": count["count"], "exact": count["exact"],
            "unread_share": signals["unread_share"], "covered": False}


def rows(todo, named=False):
    """Counts, unread share and a cautious suggestion for each (sender, sampled count)."""
    rules = bridge.get("/gmail/filters", limit=100).get("filters", [])
    jobs = [_pool.submit(_row, sender, sampled, named) for sender, sampled in todo]
    found = []
    for job in jobs:
        try:
            row = job.result(timeout=60)
        except Exception:  # noqa: BLE001 - one slow sender must not break the list
            continue
        row["covered"] = senders.covered(row["sender"], rules)
        row["suggested"] = suggestion(row)
        found.append(row)
    return found


@router.get("/review", response_class=HTMLResponse)
def review_list(request: Request, added: int = 0):
    decided = decided_senders()
    saved = nightly.load()
    ready = nightly.waiting(saved, decided)
    if ready:  # prepared with the morning job: nothing to wait for
        sample, prepared = saved["sample"], common.age_label(saved["at"])
    else:
        todo, queue = common.sender_queue(decided, senders.ranked(common.overview()))
        ready, sample, prepared = rows(todo[:REVIEW_LIST_SIZE]), queue.get("sample_size"), None
    return page(request, "review.html", "tools", rows=ready, sample=sample, added=added, prepared=prepared,
                picked=sum(1 for r in ready if r["suggested"]), paused=is_paused(), luna_ready=luna.configured())


@router.post("/review")
async def review_submit(request: Request):
    form = await request.form()
    added = 0
    for index in range(REVIEW_LIST_SIZE):
        sender, decision = form.get(f"sender_{index}", ""), form.get(f"decision_{index}", "")
        if not decision or not senders.valid_sender(sender) or decision not in senders.DECISIONS:
            continue
        try:
            count = max(int(form.get(f"count_{index}", 0)), 0)
        except ValueError:
            count = 0
        if store.set_decision(sender, decision, count, form.get(f"exact_{index}") == "1",
                              display_name=form.get(f"name_{index}", "")):
            store.log("decided", f"{sender}: {decision}")
            added += 1
    if form.get("then") == "apply" and added:  # the owner's explicit “apply now”: same step as the Plan page's button
        if is_paused():
            return go("/gmail/plan", paused=1)
        worker.start()
        return go("/gmail/plan")
    return go("/gmail/review", added=added)
