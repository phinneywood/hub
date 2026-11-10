import re

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9.-]{1,255}$")
COUNT_CAP = 5000
DECISIONS = {"filter", "filter_archive", "archive", "keep", "later"}


def valid_sender(value):
    return isinstance(value, str) and bool(EMAIL_RE.fullmatch(value))


def inbox_query(sender):
    return f"from:{sender} in:inbox"


def covered(sender, rules):
    """True when an existing filter already keeps this sender out of the inbox."""
    sender = sender.lower()
    for rule in rules:
        if "INBOX" not in rule.get("action", {}).get("removeLabelIds", []):
            continue
        criteria = rule.get("criteria", {})
        # A restricted subject/recipient/query rule does not cover every message
        # from this sender. Be conservative rather than hiding the filter choice.
        active = {k: v for k, v in criteria.items() if v not in (None, "", False)}
        if len(active) == 1 and "from" in active and str(active["from"]).strip().lower() == sender:
            return True
        if len(active) == 1 and "query" in active and str(active["query"]).strip().lower() == f"from:{sender}":
            return True
    return False


def ranked(overview):
    return [(s, n) for s, n in overview.get("top_senders_in_sample", []) if valid_sender(s)]


def pending(overview, decided):
    decided = {sender.lower() for sender in decided}
    return [(s, n) for s, n in ranked(overview) if s.lower() not in decided]


def top_share(overview, top=5):
    total = overview.get("sample_size") or 0
    senders = overview.get("top_senders_in_sample", [])[:top]
    return round(100 * sum(n for _, n in senders) / total) if total else 0


def describe(decision, count, exact=True):
    amount = f"{count:,}{'' if exact else '+'}" if isinstance(count, int) else "the"
    return {"filter": "New mail skips your inbox",
            "filter_archive": f"New mail skips your inbox, and {amount} messages are archived now",
            "archive": f"{amount} messages in your inbox are archived",
            "keep": "Stays in your inbox",
            "later": "Decide later"}[decision]


def describe_item(item):
    from . import actions
    if item["decision"] == "sweep":
        return actions.describe_sweep(item["spec"], item["count"], bool(item["count_exact"]))
    if item["decision"] == "custom":
        return actions.describe(item["spec"], item["count"], bool(item["count_exact"]))
    return describe(item["decision"], item["count"], bool(item["count_exact"]))


STEP_TEXT = {"label": "Creating the label", "filter": "Setting up the filter for new mail",
             "cleanup_preview": "Finding existing messages", "cleanup": "Archiving existing messages"}


def step_text(item):
    if item["status"] == "undoing":
        return "Undoing"
    if item["stage"] == "cleanup" and item["decision"] in ("custom", "sweep") and not item["spec"]["effects"]["skip_inbox"]:
        return "Updating existing messages"
    return STEP_TEXT.get(item["stage"], "Starting")
