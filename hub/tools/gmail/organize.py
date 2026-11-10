"""Direct, deterministic sender actions using the existing reviewed plan pipeline."""
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ... import actions, apply, bridge, senders, store
from .common import overview, page
from .missions import go, planned_new_labels, preview_spec, sender_summary, user_labels

router = APIRouter()


PRESETS = [("Over a year old", "in:inbox older_than:1y"), ("Over 90 days old", "in:inbox older_than:90d"),
           ("Unread, over 30 days", "in:inbox is:unread older_than:30d"), ("Promotions", "in:inbox category:promotions"),
           ("Social", "in:inbox category:social"), ("Updates", "in:inbox category:updates")]


@router.get("/senders", response_class=HTMLResponse)
def choose_sender(request: Request, q: str = "", added: str = "", invalid: int = 0):
    q = q.strip()[:500]
    sample = overview(q) if q else overview()
    rows = [{"sender": sender, "sample": count} for sender, count in senders.ranked(sample)]
    saved = {item["sender"].lower(): item for item in store.items()}
    return page(request, "senders.html", "senders", rows=rows, q=q, sample=sample, saved=saved, presets=PRESETS,
                added=added if senders.valid_sender(added) else "", invalid=bool(invalid))


@router.post("/senders/archive")
def quick_archive(sender: str = Form(...), q: str = Form("")):
    """Plan 'archive this sender's inbox mail' straight from a list. Same reviewed plan/apply path."""
    if not senders.valid_sender(sender):
        return go("/gmail/senders", invalid=1)
    count = bridge.get("/gmail/count", ttl=0, query=senders.inbox_query(sender), max=senders.COUNT_CAP)
    if store.set_decision(sender, "archive", count["count"], count["exact"]):
        store.log("decided", f"{sender}: archive")
    return go("/gmail/senders", q=q[:500], added=sender)


def editor_values(spec):
    return {"scope": "both" if spec["future"] and spec["existing"] else "future" if spec["future"] else "existing",
            "include": "\n".join(spec["match"]["include"]), "exclude": "\n".join(spec["match"]["exclude"]),
            "days": spec["older_than_days"] or "", **spec["effects"]}


def editor_page(request, sender, values=None, error=None):
    saved = next((item for item in store.items() if item["sender"].lower() == sender.lower()), None)
    # Preserve the exact stored identity when editing an existing plan.
    sender = saved["sender"] if saved else sender.lower()
    if values is None:
        spec = apply.spec_for(saved) if saved and saved["decision"] in store.ACTIONS else actions.builtin(sender, "archive")
        values = editor_values(spec)
    return page(request, "editor.html", "senders", **sender_summary(sender), values=values,
                labels=user_labels(), error=error, frozen=bool(saved and saved["status"] != "planned"), saved=saved)


@router.get("/organize", response_class=HTMLResponse)
def editor(request: Request, sender: str = ""):
    sender = sender.strip()
    if not senders.valid_sender(sender):
        return go("/gmail/senders", invalid=1)
    return editor_page(request, sender)


@router.post("/organize/edit", response_class=HTMLResponse)
def edit_proposal(request: Request, sender: str = Form(...), spec: str = Form(...)):
    if not senders.valid_sender(sender):
        return go("/gmail/senders", invalid=1)
    try:
        proposal = actions.revalidate(json.loads(spec), sender, user_labels(), planned_new_labels(sender))
    except (ValueError, TypeError) as err:
        return editor_page(request, sender, error=str(err) if isinstance(err, actions.ActionError) else "That proposal couldn't be read.")
    return editor_page(request, sender, editor_values(proposal))


@router.post("/organize/preview", response_class=HTMLResponse)
def editor_preview(request: Request, sender: str = Form(...), scope: str = Form("existing"),
                   include: str = Form(""), exclude: str = Form(""), days: str = Form(""),
                   skip_inbox: bool = Form(False), mark_read: bool = Form(False), label: str = Form("")):
    if not senders.valid_sender(sender):
        return go("/gmail/senders", invalid=1)
    saved = next((item for item in store.items() if item["sender"].lower() == sender.lower()), None)
    sender = saved["sender"] if saved else sender.lower()
    values = {"scope": scope, "include": include[:1000], "exclude": exclude[:1000], "days": days[:10],
              "skip_inbox": skip_inbox, "mark_read": mark_read, "label": label[:100]}
    try:
        if saved and saved["status"] != "planned":
            raise actions.ActionError("This sender already has an applied or interrupted action. Review it in Plan; it cannot be overwritten.")
        if scope not in {"existing", "future", "both"}:
            raise actions.ActionError("Choose existing mail, future mail, or both")
        if len(include) > 1000 or len(exclude) > 1000 or len(label) > 100 or len(days) > 10:
            raise actions.ActionError("Keep the rule within the displayed field limits")
        if days.strip() and (not days.strip().isascii() or not days.strip().isdigit()):
            raise actions.ActionError("Age must be a whole number of days")
        raw = {"unsupported": None, "future": scope in {"future", "both"}, "existing": scope in {"existing", "both"},
               "older_than_days": int(days) if days.strip() else None,
               "subject_include": include.splitlines() if include.strip() else [],
               "subject_exclude": exclude.splitlines() if exclude.strip() else [],
               "skip_inbox": skip_inbox, "mark_read": mark_read, "label": label.strip() or None}
        spec = actions.validate(raw, sender, user_labels(), planned_new_labels(sender))
    except actions.ActionError as err:
        return editor_page(request, sender, values, str(err))
    summary = sender_summary(sender)
    return page(request, "custom_preview.html", "senders", **summary, spec=spec, spec_json=json.dumps(spec),
                instruction="", manual=True, preview=preview_spec(spec, summary), describe=actions.describe,
                future_query=actions.future_query, existing_query=actions.existing_query)
