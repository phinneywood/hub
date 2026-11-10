from email.utils import parseaddr

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ... import bridge, senders
from . import common, health as checks
from .common import page

router = APIRouter()


@router.get("/explore", response_class=HTMLResponse)
def explore(request: Request):
    return page(request, "explore.html", "explore")


@router.get("/overview", response_class=HTMLResponse)
def overview(request: Request):
    profile = bridge.get("/gmail/profile")
    try:
        data, problem = common.overview(), None
    except bridge.BridgeError as err:
        data, problem = None, str(err)
    inbox = common.inbox_counts(data) if data else None
    return page(request, "dashboard.html", "explore", child=True, profile=profile, overview=data, problem=problem, inbox=inbox)


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


@router.get("/labels", response_class=HTMLResponse)
def labels(request: Request):
    items = bridge.get("/gmail/labels").get("labels", [])
    user = sorted((x for x in items if x.get("type") == "user"), key=lambda x: x["name"].lower())
    system = sorted((x for x in items if x.get("type") != "user"), key=lambda x: x["name"])
    return page(request, "labels.html", "explore", child=True, user=user, system=system)


@router.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", page_token: str = ""):
    result = bridge.get("/gmail/search", query=q, limit=25, page_token=page_token) if q else None
    addresses = {m.get("from", ""): parseaddr(m.get("from", ""))[1] for m in result.get("messages", [])} if result else {}
    addresses = {source: address for source, address in addresses.items() if senders.valid_sender(address)}
    return page(request, "search.html", "explore", child=True, q=q, result=result, addresses=addresses)
