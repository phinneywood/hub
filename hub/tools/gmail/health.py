"""Filter and label checks. Pure functions plus bounded, cached activity probes."""
from collections import defaultdict

from ... import bridge

ACTIVITY_DAYS = 90
ACTIVITY_TTL = 900


def criteria_query(criteria):
    parts = []
    for key, template in (("from", "from:({})"), ("to", "to:({})"), ("subject", "subject:({})"),
                          ("query", "({})"), ("negatedQuery", "-({})")):
        if criteria.get(key):
            parts.append(template.format(criteria[key]))
    if criteria.get("hasAttachment"):
        parts.append("has:attachment")
    if criteria.get("excludeChats"):
        parts.append("-in:chats")
    if criteria.get("size") and criteria.get("sizeComparison") in ("larger", "smaller"):
        parts.append(f"{criteria['sizeComparison']}:{criteria['size']}")
    known = {"from", "to", "subject", "query", "negatedQuery", "hasAttachment", "excludeChats", "size", "sizeComparison"}
    if not parts or set(criteria) - known:
        return None
    return " ".join(parts)


def label_search_term(name):
    return "label:" + name.replace(" ", "-")


def static_issues(rules, labels, duplicate_groups):
    known = {x["id"]: x["name"] for x in labels}
    issues = []
    for rule in rules:
        action = rule.get("action", {})
        for key in ("addLabelIds", "removeLabelIds"):
            for label_id in action.get(key, []):
                if label_id not in known:
                    issues.append({"kind": "Missing label", "filter": rule["id"],
                                   "detail": f"Filter points at label {label_id}, which no longer exists."})
        if set(action.get("addLabelIds", [])) & set(action.get("removeLabelIds", [])):
            issues.append({"kind": "Contradiction", "filter": rule["id"],
                           "detail": "Filter adds and removes the same label."})
    for group in duplicate_groups:
        issues.append({"kind": "Exact duplicates", "filter": ", ".join(group),
                       "detail": "These filters have identical matching and actions; all but one can go."})
    by_criteria = defaultdict(list)
    for rule in rules:
        by_criteria[repr(sorted(rule.get("criteria", {}).items()))].append(rule)
    in_duplicates = {x for group in duplicate_groups for x in group}
    for group in by_criteria.values():
        if len(group) > 1 and not all(r["id"] in in_duplicates for r in group):
            issues.append({"kind": "Same match, different actions", "filter": ", ".join(r["id"] for r in group),
                           "detail": "Several filters match exactly the same mail. Their actions combine, which is easy to forget."})
    return issues


def unused_labels(rules, labels):
    used = {x for r in rules for k in ("addLabelIds", "removeLabelIds") for x in r.get("action", {}).get(k, [])}
    return [x for x in labels if x.get("type") == "user" and x["id"] not in used]


def estimate(query):
    result = bridge.get("/gmail/search", ttl=ACTIVITY_TTL, query=query, limit=1)
    return result.get("estimated_matches", 0)


def run_activity(rules, labels):
    """One request per filter and user label (cached 15 minutes by the bridge client). Stops early if rate-limited."""
    filters, label_counts, stopped = {}, {}, False
    for rule in rules:
        query = criteria_query(rule.get("criteria", {}))
        if query is None:
            filters[rule["id"]] = "unsupported"
        elif stopped:
            filters[rule["id"]] = "not checked"
        else:
            try:
                recent = estimate(f"{query} newer_than:{ACTIVITY_DAYS}d")
                year = estimate(f"{query} newer_than:365d") if recent == 0 else None
                filters[rule["id"]] = {"recent": recent, "year": year}
            except bridge.BridgeError:
                filters[rule["id"]], stopped = "not checked", True
    for label in labels:
        if label.get("type") != "user":
            continue
        if stopped:
            label_counts[label["id"]] = "not checked"
            continue
        try:
            label_counts[label["id"]] = estimate(label_search_term(label["name"]))
        except bridge.BridgeError:
            label_counts[label["id"]], stopped = "not checked", True
    return {"filters": filters, "labels": label_counts, "complete": not stopped}
