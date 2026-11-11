from email.utils import parseaddr
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import json
import re

from ... import actions, apply, bridge, delayed, luna, senders, store, worker
from . import common
from .common import page

router = APIRouter()


def go(path, **query):
    return RedirectResponse(path + ("?" + urlencode(query) if query else ""), status_code=303)


def pause_state():
    """'ok', 'paused' (the maintenance marker on the Mac mini) or 'unreachable' (the Gmail helper didn't answer)."""
    try:
        profile = bridge.get("/gmail/profile", ttl=15, timeout=bridge.QUICK_TIMEOUT)
    except bridge.BridgeError:
        return "unreachable"
    return "paused" if profile.get("organization_paused") else "ok"


def is_paused():
    """Whether changes must wait. Fails closed: a helper that isn't answering counts as paused."""
    return pause_state() != "ok"


def decided_senders():
    """Include enabled delayed rules, without changing or duplicating plan items."""
    return {s.lower() for s in store.decided_senders()} | {
        rule["sender"].lower() for rule in delayed.rules() if rule["enabled"]
    }


@router.get("/tools", response_class=HTMLResponse)
def tools(request: Request):
    """The clean-up tools: loud senders, bulk steps, 30-day rules, filters."""
    ov = common.overview()
    common.track_inbox(ov)
    inbox = common.inbox_counts(ov)
    decided = decided_senders()
    todo, _ = common.sender_queue(decided, senders.ranked(ov))
    items = store.items()
    from . import nightly
    saved = nightly.load()
    ready = nightly.waiting(saved, decided)
    old_mail = (saved or {}).get("old_mail") or {}
    return page(request, "tools.html", "tools", inbox=inbox, share=senders.top_share(ov), sample=ov.get("sample_size"),
                ready=len(ready), picked=sum(1 for r in ready if r.get("suggested")),
                old_mail=old_mail if isinstance(old_mail.get("count"), int) and old_mail["count"] else None,
                old_mail_href=nightly.OLD_MAIL_PREVIEW,
                todo=len(todo), planned=store.plan_count(), paused=is_paused(), trend=common.inbox_trend(),
                reviewed=len(decided), kept=sum(1 for i in items if i["decision"] == "keep"),
                applied=sum(1 for i in items if i["status"] == "done"),
                rules=sum(1 for rule in delayed.rules() if rule["enabled"]))


def card_signals(sender, count, exact):
    """Read-only engagement hint: how much of this sender's inbox mail is unread."""
    unread = bridge.get("/gmail/count", ttl=300, query=senders.inbox_query(sender) + " is:unread", max=senders.COUNT_CAP)
    if not exact or not count:
        return {"unread": unread["count"], "unread_exact": unread["exact"], "unread_share": None}
    return {"unread": unread["count"], "unread_exact": unread["exact"],
            "unread_share": round(100 * min(unread["count"], count) / count) if unread["exact"] else None}


@router.get("/quiet", response_class=HTMLResponse)
def quiet(request: Request, delayed_enabled: int = 0):
    ov = common.overview()
    items = store.items()
    decided = decided_senders()
    todo, sample = common.sender_queue(decided, senders.ranked(ov))
    deferred = sum(1 for i in items if i["decision"] == "later")
    context = {"reviewed": len(decided), "left": len(todo), "planned": store.plan_count(), "deferred": deferred,
               "delayed_enabled": bool(delayed_enabled), "sample": sample.get("sample_size")}
    if not todo:
        return page(request, "quiet_done.html", "tools", **context)
    sender, sampled = todo[0]
    query = senders.inbox_query(sender)
    count = bridge.get("/gmail/count", ttl=300, query=query, max=senders.COUNT_CAP)
    recent = bridge.get("/gmail/search", ttl=300, query=query, limit=3).get("messages", [])
    rules = bridge.get("/gmail/filters", limit=100).get("filters", [])
    name = parseaddr(recent[0]["from"])[0] if recent else ""
    for message in recent:
        message["unread"] = "UNREAD" in (message.get("labels") or [])
    return page(request, "quiet_card.html", "tools", sender=sender, display_name=name, count=count["count"], exact=count["exact"],
                recent=recent, covered=senders.covered(sender, rules), sampled=sampled, paused=is_paused(),
                **card_signals(sender, count["count"], count["exact"]), **context)


@router.post("/quiet/decide")
def decide(sender: str = Form(...), decision: str = Form(...), count: int = Form(0), exact: int = Form(1),
           display_name: str = Form("")):
    if not senders.valid_sender(sender) or decision not in senders.DECISIONS:
        return go("/gmail/quiet")
    if store.set_decision(sender, decision, max(count, 0), bool(exact), display_name=display_name):
        store.log("decided", f"{sender}: {decision}")
    return go("/gmail/quiet")


@router.post("/quiet/reset-later")
def reset_later():
    store.reset_later()
    return go("/gmail/quiet")


def item_title(item):
    if item["decision"] == "sweep":
        return "Mark old mail read" if item["spec"]["effects"]["mark_read"] else "Inbox cleanup"
    return item.get("display_name") or item["sender"]


def leaves_inbox(item):
    spec = item["spec"]
    return item["decision"] in ("filter_archive", "archive") or (
        item["decision"] in ("custom", "sweep") and spec["existing"] and spec["effects"]["skip_inbox"])


@router.get("/plan", response_class=HTMLResponse)
def plan(request: Request, paused: int = 0):
    items = store.items()
    planned = sorted((i for i in items if i["decision"] in store.ACTIONS and i["status"] == "planned"),
                     key=lambda i: -(i["count"] or 0))
    running = [i for i in items if i["status"] in ("applying", "undoing")]
    finished = sorted((i for i in items if i["status"] in ("done", "undone")), key=lambda i: -(i["updated_at"] or 0))
    problems = [i for i in items if i["status"] in ("failed", "needs_review", "undo_partial")]
    kept = [i for i in items if i["decision"] == "keep"]
    later = sorted((i for i in items if i["decision"] == "later" and i["status"] == "planned"), key=lambda i: -(i["count"] or 0))
    messages = sum(i["count"] or 0 for i in planned if leaves_inbox(i))
    working = worker.running()
    queued = len(apply.work_items()) + len(apply.undo_items())
    batch = [i for i in items if i["decision"] in store.ACTIONS and i["status"] in ("planned", "applying", "done")]
    done = [i for i in items if i["status"] == "done"]
    totals = {"archived": sum(i["archived"] or 0 for i in done if leaves_inbox(i)),
              "filters": sum(1 for i in done if apply.spec_for(i)["future"]),
              "senders": sum(1 for i in done if i["decision"] != "sweep")}
    inbox = common.inbox_counts(common.overview()) or {}
    return page(request, "plan.html", "plan", planned=planned, running=running, finished=finished, problems=problems, kept=kept,
                later=later, messages=messages, paused=is_paused(), working=working,
                stalled=bool(queued and not working and running),
                done_count=sum(1 for i in batch if i["status"] == "done"), batch_count=len(batch),
                paused_notice=bool(paused), describe=senders.describe_item, step_text=senders.step_text,
                title=item_title, totals=totals, inbox=inbox, trend=common.inbox_trend(30), rules=delayed.health())


@router.post("/plan/remove")
def plan_remove(item_id: int = Form(...)):
    item = store.get(item_id)
    if item and store.remove(item_id):
        store.log("removed from plan", item["sender"])
    return go("/gmail/plan")


@router.post("/plan/apply")
@router.post("/plan/continue")
def plan_apply():
    if is_paused():
        return go("/gmail/plan", paused=1)
    worker.start()
    return go("/gmail/plan")


@router.post("/plan/undo")
def plan_undo(item_id: int = Form(...)):
    if is_paused():
        return go("/gmail/plan", paused=1)
    apply.start_undo(item_id)
    worker.start()
    return go("/gmail/plan")


@router.get("/activity", response_class=HTMLResponse)
def activity(request: Request):
    journal = bridge.get("/gmail/changes", limit=30)
    return page(request, "activity.html", "activity", log=store.recent_activity(), result=journal)


@router.get("/delayed", response_class=HTMLResponse)
def delayed_rules(request: Request, checking: int = 0, resumed: int = 0, refused: int = 0):
    from ... import config
    all_rules = delayed.rules()
    return page(request, "delayed.html", "delayed", rules=all_rules, runs=delayed.runs(),
                must_check={r["id"] for r in all_rules if r["error"] and delayed.needs_check(r["id"])},
                automatic=config.BACKGROUND_JOBS_ENABLED, checking=bool(checking) or delayed_checking(),
                resumed=bool(resumed), refused=bool(refused), paused=is_paused())


_delayed_thread = {"t": None}


def delayed_checking():
    thread = _delayed_thread["t"]
    return bool(thread and thread.is_alive())


@router.post("/delayed/resume")
def delayed_resume(rule_id: int = Form(...), checked: int = Form(0)):
    ok = delayed.resume(rule_id, checked=bool(checked))
    return go("/gmail/delayed", **({"resumed": 1} if ok else {"refused": 1}))


@router.post("/delayed/resume-safe")
def delayed_resume_safe():
    for rule in delayed.rules():
        if rule["error"] and not delayed.needs_check(rule["id"]):
            delayed.resume(rule["id"])
    return go("/gmail/delayed", resumed=1)


@router.post("/delayed/run-now")
def delayed_run_now():
    import threading
    if is_paused():
        return go("/gmail/delayed")
    if not delayed_checking():
        thread = threading.Thread(target=delayed.run_now, name="delayed-now", daemon=True)
        _delayed_thread["t"] = thread
        thread.start()
    return go("/gmail/delayed", checking=1)


@router.get("/delayed/review", response_class=HTMLResponse)
def delayed_review(request: Request, sender: str, source: str = "rules"):
    if not senders.valid_sender(sender):
        return go("/gmail/quiet")
    count = bridge.get("/gmail/count", ttl=0, query=delayed.query(sender), max=senders.COUNT_CAP)
    recent = bridge.get("/gmail/search", ttl=0, query=delayed.query(sender), limit=3).get("messages", [])
    rules = bridge.get("/gmail/filters", limit=100).get("filters", [])
    return page(request, "delayed_review.html", "delayed", sender=sender, count=count, recent=recent,
                source="quiet" if source == "quiet" else "rules", covered=senders.covered(sender, rules))


@router.post("/delayed/enable")
def delayed_enable(sender: str = Form(...), source: str = Form("rules")):
    if not senders.valid_sender(sender):
        return go("/gmail/quiet")
    try:
        delayed.enable(sender)
    except ValueError:
        return go("/gmail/delayed", error=1)
    return go("/gmail/quiet", delayed_enabled=1) if source == "quiet" else go("/gmail/delayed")


@router.post("/delayed/stop")
def delayed_stop(rule_id: int = Form(...)):
    delayed.disable(rule_id)
    return go("/gmail/delayed")


@router.post("/delayed/undo")
def delayed_undo(run_id: int = Form(...)):
    delayed.undo(run_id)
    return go("/gmail/delayed")


def user_labels():
    return [x for x in bridge.get("/gmail/labels").get("labels", []) if x.get("type") == "user"]


def planned_new_labels(except_sender=None):
    return [i["spec"]["effects"]["label"] for i in store.items()
            if i["decision"] == "custom" and i["status"] == "planned" and i["sender"] != except_sender
            and i["spec"]["effects"].get("label_new")]


def sender_summary(sender):
    query = senders.inbox_query(sender)
    count = bridge.get("/gmail/count", ttl=0, query=query, max=senders.COUNT_CAP)
    recent = bridge.get("/gmail/search", ttl=0, query=query, limit=3).get("messages", [])
    return {"sender": sender, "count": count["count"], "exact": count["exact"], "recent": recent,
            "display_name": parseaddr(recent[0]["from"])[0] if recent else ""}


def preview_spec(spec, summary):
    out = {"stays": None, "count": None, "exact": True, "pattern": None}
    if spec["existing"]:
        found = bridge.get("/gmail/count", ttl=0, query=actions.existing_query(spec), max=senders.COUNT_CAP)
        out.update(count=found["count"], exact=found["exact"])
        if spec["effects"]["skip_inbox"] and summary["exact"] and found["exact"]:
            out["stays"] = max(summary["count"] - found["count"], 0)
        sample_query = actions.existing_query(spec)
    else:
        sample_query = actions.future_query(spec)
    out["sample"] = bridge.get("/gmail/search", ttl=0, query=sample_query, limit=3).get("messages", [])
    if spec["match"]["include"] or spec["match"]["exclude"]:
        found = bridge.get("/gmail/count", ttl=0, query=actions.pattern_query(spec), max=senders.COUNT_CAP)
        out["pattern"] = found["count"] if found["exact"] else f"{found['count']}+"
    return out


def custom_page(request, sender, **extra):
    return page(request, "custom.html", "tools", configured=luna.configured(), blocked=store.ai_blocked(sender),
                instruction=extra.pop("instruction", ""), error=extra.pop("error", None), **sender_summary(sender), **extra)


@router.get("/quiet/custom", response_class=HTMLResponse)
def custom(request: Request, sender: str):
    if not senders.valid_sender(sender):
        return go("/gmail/quiet")
    return custom_page(request, sender)


@router.post("/quiet/custom/propose", response_class=HTMLResponse)
def custom_propose(request: Request, sender: str = Form(...), instruction: str = Form(""), feedback: str = Form(""),
                   spec: str = Form("")):
    if not senders.valid_sender(sender):
        return go("/gmail/quiet")
    instruction = instruction.strip()[:500]
    if store.ai_blocked(sender):
        return custom_page(request, sender, instruction=instruction)
    if not instruction:
        return custom_page(request, sender, error="Tell me what you'd like to happen with this sender.")
    requested = instruction + " " + feedback
    if re.search(r'\b(?:\d+|one|two|three|four)\s*(?:days?|weeks?|months?)\b', requested, re.I) and re.search(
            r'\b(?:after|then|later|stay|keep|automatically)\b', requested, re.I):
        return custom_page(request, sender, instruction=instruction,
                           error="For delayed archiving, use Archive after 30 days below. A Gmail filter cannot wait.")
    if not luna.configured():
        return custom_page(request, sender, instruction=instruction)
    summary = sender_summary(sender)
    labels = user_labels()
    try:
        previous = json.loads(spec) if spec else None
    except ValueError:
        previous = None
    try:
        raw = luna.propose(sender, summary["count"], [m["subject"] for m in summary["recent"]], [x["name"] for x in labels],
                           instruction, feedback.strip()[:300] or None, previous)
        proposal = actions.validate(raw, sender, labels, planned_new_labels(sender))
    except (luna.LunaError, actions.ActionError) as err:
        store.log("custom action not proposed", f"{sender}: {err}")
        return custom_page(request, sender, instruction=instruction, error=str(err))
    store.log("custom action proposed", sender)
    return page(request, "custom_preview.html", "tools", spec=proposal, spec_json=json.dumps(proposal), instruction=instruction,
                sentence=actions.describe(proposal, None), preview=preview_spec(proposal, summary), summary=summary,
                describe=actions.describe, future_query=actions.future_query, existing_query=actions.existing_query, **summary)


@router.post("/quiet/custom/accept", response_class=HTMLResponse)
def custom_accept(request: Request, sender: str = Form(...), spec: str = Form(...), source: str = Form("")):
    if not senders.valid_sender(sender):
        return go("/gmail/quiet")
    saved = next((item for item in store.items() if item["sender"].lower() == sender.lower()), None)
    if saved and saved["sender"] != sender:
        return custom_page(request, sender, error="Reopen this sender from the sender picker to edit its existing plan.")
    try:
        proposal = actions.revalidate(json.loads(spec), sender, user_labels(), planned_new_labels(sender))
    except (ValueError, TypeError) as err:
        return custom_page(request, sender, error=str(err) if isinstance(err, actions.ActionError) else "That proposal couldn't be read.")
    detail = preview_spec(proposal, sender_summary(sender))
    count = detail["count"] if proposal["existing"] else (detail["pattern"] if isinstance(detail["pattern"], int) else 0)
    name = parseaddr((detail.get("sample") or [{}])[0].get("from", ""))[0]
    if store.set_decision(sender, "custom", count, detail["exact"], spec=proposal, display_name=name):
        store.log("custom action planned", f"{sender}: {actions.describe(proposal, count)}")
        return go({"quiet": "/gmail/quiet", "review": "/gmail/review"}.get(source, "/gmail/plan"))
    return custom_page(request, sender, error="This action has already started or finished and cannot be replaced. Review it in Plan.")


@router.post("/quiet/ai-off")
def ai_off(sender: str = Form(...), off: int = Form(1)):
    if senders.valid_sender(sender):
        store.set_ai_blocked(sender, bool(off))
    return go("/gmail/quiet/custom?sender=" + sender)
