from email.utils import parseaddr

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import bridge, senders
from . import common, health as checks
from .common import page

router = APIRouter()


@router.get("/explore", response_class=HTMLResponse)
def explore(request: Request):
    return page(request, "explore.html", "explore")


@router.get("/overview")
def overview():
    """The inbox overview is the app's Mail board now (counts, by kind, the inbox over time)."""
    return RedirectResponse("/app/#board/mail", status_code=302)


@router.get("/filters", response_class=HTMLResponse)
def filters(request: Request):
    result = bridge.get("/gmail/filters", limit=100)
    labels = bridge.get("/gmail/labels").get("labels", [])
    rules = [common.describe_filter(x, labels) for x in result.get("filters", [])]
    return page(request, "filters.html", "explore", child=True, rules=rules, total=result.get("total", 0),
                duplicates=result.get("exact_duplicate_groups", []))


@router.get("/health", response_class=HTMLResponse)
def health_page(request: Request, run: int = 0):
    result = bridge.get("/gmail/filters", limit=100)
    rules = result.get("filters", [])
    labels = bridge.get("/gmail/labels").get("labels", [])
    activity = checks.run_activity(rules, labels) if run else None
    issues = checks.static_issues(rules, labels, result.get("exact_duplicate_groups", []))
    quiet, dead = [], []
    if activity:
        for rule in rules:
            found = activity["filters"].get(rule["id"])
            if isinstance(found, dict) and found["recent"] == 0:
                (quiet if found["year"] else dead).append(common.describe_filter(rule, labels))
    empty = [x for x in labels if activity and activity["labels"].get(x["id"]) == 0]
    return page(request, "health.html", "explore", child=True, issues=issues, activity=activity, quiet=quiet, dead=dead,
                unused=checks.unused_labels(rules, labels), empty=empty, days=checks.ACTIVITY_DAYS, total=len(rules),
                label_total=sum(x.get("type") == "user" for x in labels))


@router.get("/labels")
def labels():
    """The label list is retired: Ask labels mail (after a tap) and the Mail board shows what is in the inbox."""
    return RedirectResponse("/app/#board/mail", status_code=302)


@router.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", page_token: str = ""):
    result = bridge.get("/gmail/search", query=q, limit=25, page_token=page_token) if q else None
    addresses = {m.get("from", ""): parseaddr(m.get("from", ""))[1] for m in result.get("messages", [])} if result else {}
    addresses = {source: address for source, address in addresses.items() if senders.valid_sender(address)}
    return page(request, "search.html", "explore", child=True, q=q, result=result, addresses=addresses)
