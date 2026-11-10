"""Inbox-wide cleanups by age and category.

Every query comes from fixed choices in actions.sweep (no free text, no model output).
Previews are read-only counts and samples; nothing changes until the plan is applied,
and each 1,000-message batch can be undone from the Plan page.
"""
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ... import actions, bridge, senders, store
from .common import page
from .missions import go, is_paused

router = APIRouter()

AGES = {"archive": (90, 180, 365, 730, 1825), "category": (7, 30, 90, 365), "read": (30, 90, 365)}
DEFAULTS = {"archive": 365, "category": 30, "read": 90}


def _spec(kind, days, category, protect):
    if kind not in AGES or days not in AGES[kind]:
        raise actions.ActionError("Choose one of the listed ages")
    if kind == "category" and category not in actions.SWEEP_CATEGORIES:
        raise actions.ActionError("Choose a category")
    return actions.sweep(days, category if kind == "category" else None,
                         "read" if kind == "read" else "archive", protect)


QUICK_COUNT = 1000  # counting every ID takes ~2 s per 500; Gmail's own estimate is unreliable


def _count(spec, full=False):
    """(count, exact): exact up to 1,000 by default, or up to the 5,000 cap when asked."""
    found = bridge.get("/gmail/count", ttl=300, query=actions.sweep_query(spec),
                       max=senders.COUNT_CAP if full else QUICK_COUNT)
    return found["count"], found["exact"]


def planned_sweeps():
    return [i for i in store.items() if i["decision"] == "sweep"]


@router.get("/sweep", response_class=HTMLResponse)
def sweep_home(request: Request, added: int = 0):
    items = planned_sweeps()
    return page(request, "sweep.html", "sweep", ages=AGES, defaults=DEFAULTS, categories=actions.SWEEP_CATEGORIES,
                age_text=actions.age_text, planned=[i for i in items if i["status"] == "planned"],
                finished=[i for i in items if i["status"] != "planned"], added=bool(added),
                describe=senders.describe_item, paused=is_paused())


@router.get("/sweep/preview", response_class=HTMLResponse)
def sweep_preview(request: Request, kind: str, days: int, category: str = "", protect: int = 1, full: int = 0):
    try:
        spec = _spec(kind, days, category or None, bool(protect))
    except actions.ActionError as err:
        return page(request, "sweep_preview.html", "sweep", error=str(err))
    count, exact = _count(spec, bool(full))
    sample = bridge.get("/gmail/search", ttl=300, query=actions.sweep_query(spec), limit=5).get("messages", [])
    return page(request, "sweep_preview.html", "sweep", spec=spec, count=count, exact=exact, sample=sample,
                sentence=actions.describe_sweep(spec, count, exact), kind=kind, days=days, category=category,
                protect=int(bool(protect)), full=bool(full), cap=senders.COUNT_CAP)


@router.post("/sweep/add")
def sweep_add(kind: str = Form(...), days: int = Form(...), category: str = Form(""), protect: int = Form(1)):
    try:
        spec = _spec(kind, days, category or None, bool(protect))
    except actions.ActionError:
        return go("/gmail/sweep")
    count, exact = _count(spec)
    key = actions.sweep_key(spec)
    # A finished cleanup can be planned again (new mail ages into it); keep each run's own undo history.
    taken = {i["sender"] for i in store.items()}
    if key in taken and store.get_by_sender(key)["status"] != "planned":
        key = f"{key}:{int(time.time())}"
    if store.set_decision(key, "sweep", count, exact, spec=spec):
        store.log("cleanup planned", actions.describe_sweep(spec, count, exact))
    return go("/gmail/sweep", added=1)
