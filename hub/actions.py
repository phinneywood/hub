"""Grammar for custom sender actions.

A model may propose an action, but only values that pass this module are ever used:
fixed fields, bounded terms, no raw Gmail syntax, and every query is built here and always
starts with from:<the sender>. The same spec shape is used for the built-in choices.
"""
import re

from . import senders

TERM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '&.,!$%-]{0,39}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &_/-]{0,39}$")
RESERVED = {"inbox", "sent", "sent mail", "draft", "drafts", "spam", "trash", "starred", "important", "unread", "chats",
            "all mail", "scheduled", "snoozed", "muted", "outbox", "personal", "social", "promotions", "updates", "forums"}
OPERATORS = {"or", "and", "not"}
MAX_NEW_LABELS = 3
FIELDS = {"unsupported", "future", "existing", "older_than_days", "subject_include", "subject_exclude",
          "skip_inbox", "mark_read", "label"}

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": sorted(FIELDS),
    "properties": {
        "unsupported": {"type": ["string", "null"]},
        "future": {"type": "boolean"}, "existing": {"type": "boolean"},
        "older_than_days": {"type": ["integer", "null"]},
        "subject_include": {"type": "array", "items": {"type": "string"}},
        "subject_exclude": {"type": "array", "items": {"type": "string"}},
        "skip_inbox": {"type": "boolean"}, "mark_read": {"type": "boolean"},
        "label": {"type": ["string", "null"]},
    },
}


class ActionError(ValueError):
    pass


SWEEP_CATEGORIES = ("promotions", "social", "updates", "forums")
SWEEP_EFFECTS = ("archive", "read")


def sweep(older_than_days, category=None, effect="archive", protect_important=True):
    """An inbox-wide cleanup built only from fixed choices; no free text reaches the query.

    Starred mail is never touched. Marking read keeps mail in the inbox.
    """
    if isinstance(older_than_days, bool) or not isinstance(older_than_days, int) or not 1 <= older_than_days <= 3650:
        raise ActionError("“Older than” must be between 1 and 3650 days")
    if category is not None and category not in SWEEP_CATEGORIES:
        raise ActionError("Unknown category")
    if effect not in SWEEP_EFFECTS:
        raise ActionError("Unknown cleanup")
    return {"kind": "sweep", "sender": None, "match": {"include": [], "exclude": []},
            "effects": {"skip_inbox": effect == "archive", "mark_read": effect == "read", "label": None, "label_new": False},
            "future": False, "existing": True, "older_than_days": older_than_days, "category": category,
            "protect_important": bool(protect_important)}


def sweep_key(spec):
    effect = "read" if spec["effects"]["mark_read"] else "archive"
    return f"sweep:{effect}:{spec['category'] or 'all'}:{spec['older_than_days']}:{int(spec['protect_important'])}"


def is_sweep(spec):
    return isinstance(spec, dict) and spec.get("kind") == "sweep"


def sweep_query(spec):
    parts = ["in:inbox", "-is:starred"]
    if spec["protect_important"]:
        parts.append("-is:important")
    if spec["category"]:
        parts.append(f"category:{spec['category']}")
    if spec["effects"]["mark_read"]:
        parts.append("is:unread")
    parts.append(f"older_than:{spec['older_than_days']}d")
    return " ".join(parts)


def age_text(days):
    if days % 365 == 0:
        years = days // 365
        return f"{years} year{'s' * (years != 1)}"
    if days % 30 == 0 and days >= 60:
        return f"{days // 30} months"
    return f"{days} day{'s' * (days != 1)}"


def describe_sweep(spec, count=None, exact=True):
    amount = (f"{count:,} " if exact else f"more than {count:,} ") if isinstance(count, int) else ""
    where = f"{spec['category'].capitalize()} " if spec["category"] else ""
    guard = "starred or important" if spec["protect_important"] else "starred"
    age = age_text(spec["older_than_days"])
    if spec["effects"]["mark_read"]:
        return f"Mark {amount}unread {where}inbox messages older than {age} as read. They stay in the inbox; {guard} mail is left alone."
    return f"Archive {amount}{where}inbox messages older than {age}. Never {guard} mail."


def builtin(sender, decision):
    return {"sender": sender, "match": {"include": [], "exclude": []},
            "effects": {"skip_inbox": True, "mark_read": False, "label": None, "label_new": False},
            "future": decision in ("filter", "filter_archive"), "existing": decision in ("filter_archive", "archive"),
            "older_than_days": None}


def clean_terms(value, what):
    if not isinstance(value, list) or len(value) > 5:
        raise ActionError(f"Use at most 5 {what} words or phrases")
    terms = []
    for raw in value:
        term = raw.strip() if isinstance(raw, str) else ""
        if not TERM_RE.fullmatch(term) or term.lower() in OPERATORS:
            raise ActionError(f"“{str(raw)[:40]}” can't be used as a subject word")
        terms.append(term)
    return terms


def find_label(name, labels):
    wanted = name.strip().lower()
    return next((x for x in labels if x.get("type") == "user" and x["name"].lower() == wanted), None)


def clean_label(name, labels):
    """Returns {'name', 'exists', 'id'} or raises ActionError."""
    name = re.sub(r"\s+", " ", name.strip()) if isinstance(name, str) else ""
    if not LABEL_RE.fullmatch(name):
        raise ActionError("Label names can use letters, numbers, spaces and - & _ / (up to 40 characters)")
    found = find_label(name, labels)
    if found:
        return {"name": found["name"], "exists": True, "id": found["id"]}
    if name.lower() in RESERVED or name.upper().startswith("CATEGORY") or name.endswith("/") or "//" in name:
        raise ActionError("That label name is reserved or invalid")
    if "/" in name and not find_label(name.rsplit("/", 1)[0], labels):
        raise ActionError("Nested labels need an existing parent label")
    return {"name": name, "exists": False, "id": None}


def validate(raw, sender, labels, other_new_labels=()):
    """Turn a model proposal into a spec. Raises ActionError with a user-readable reason."""
    if not senders.valid_sender(sender):
        raise ActionError("Enter one sender email address, without Gmail search syntax")
    if not isinstance(raw, dict) or set(raw) != FIELDS:
        raise ActionError("The proposal wasn't in the expected format")
    if raw["unsupported"]:
        raise ActionError(str(raw["unsupported"])[:300])
    for flag in ("future", "existing", "skip_inbox", "mark_read"):
        if not isinstance(raw[flag], bool):
            raise ActionError("The proposal wasn't in the expected format")
    days = raw["older_than_days"]
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650):
        raise ActionError("“Older than” must be between 1 and 3650 days")
    if days is not None and not raw["existing"]:
        raise ActionError("An age limit only applies to existing mail")
    if not (raw["future"] or raw["existing"]):
        raise ActionError("The action has to affect future mail, existing mail, or both")
    label = clean_label(raw["label"], labels) if raw["label"] else None
    if not (raw["skip_inbox"] or raw["mark_read"] or label):
        raise ActionError("The action has to skip the inbox, mark read, or add a label")
    if label and not label["exists"] and len({label["name"].lower(), *map(str.lower, other_new_labels)}) > MAX_NEW_LABELS:
        raise ActionError(f"A plan can create at most {MAX_NEW_LABELS} new labels")
    return {"sender": sender,
            "match": {"include": clean_terms(raw["subject_include"], "include"), "exclude": clean_terms(raw["subject_exclude"], "exclude")},
            "effects": {"skip_inbox": raw["skip_inbox"], "mark_read": raw["mark_read"],
                        "label": label["name"] if label else None, "label_new": bool(label and not label["exists"])},
            "future": raw["future"], "existing": raw["existing"], "older_than_days": days}


def revalidate(spec, sender, labels, other_new_labels=()):
    """Re-check a spec that round-tripped through a form."""
    if not isinstance(spec, dict) or spec.get("sender") != sender:
        raise ActionError("The proposal doesn't match this sender")
    if not isinstance(spec.get("match"), dict) or not isinstance(spec.get("effects"), dict):
        raise ActionError("The proposal wasn't in the expected format")
    raw = {"unsupported": None, "future": spec.get("future"), "existing": spec.get("existing"),
           "older_than_days": spec.get("older_than_days"),
           "subject_include": spec.get("match", {}).get("include"), "subject_exclude": spec.get("match", {}).get("exclude"),
           "skip_inbox": spec.get("effects", {}).get("skip_inbox"), "mark_read": spec.get("effects", {}).get("mark_read"),
           "label": spec.get("effects", {}).get("label")}
    return validate(raw, sender, labels, other_new_labels)


def _terms(spec):
    parts = []
    quote = lambda t: f'"{t}"' if " " in t else t
    if spec["match"]["include"]:
        parts.append("subject:(" + " OR ".join(quote(t) for t in spec["match"]["include"]) + ")")
    if spec["match"]["exclude"]:
        parts.append("-subject:(" + " OR ".join(quote(t) for t in spec["match"]["exclude"]) + ")")
    return parts


def label_term(name):
    return "label:" + re.sub(r"[ /]", "-", name)


def future_query(spec):
    return " ".join([f"from:{spec['sender']}", *_terms(spec)])


def pattern_query(spec):
    return future_query(spec)


def existing_query(spec):
    if is_sweep(spec):
        return sweep_query(spec)
    effects = spec["effects"]
    parts = [f"from:{spec['sender']}", *_terms(spec)]
    if effects["skip_inbox"]:
        parts.append("in:inbox")
    # Archiving must include already-read inbox messages too. Marking them read
    # again is harmless; intersecting with is:unread silently leaves them behind.
    if effects["mark_read"] and not effects["skip_inbox"] and not effects["label"]:
        parts.append("is:unread")
    if spec["older_than_days"]:
        parts.append(f"older_than:{spec['older_than_days']}d")
    if effects["label"] and not (effects["skip_inbox"] or effects["mark_read"]):
        parts.append("-" + label_term(effects["label"]))
    elif effects["label"] and effects["mark_read"] and not effects["skip_inbox"]:
        parts.append("{is:unread -" + label_term(effects["label"]) + "}")
    return " ".join(parts)


def label_ids(spec, label_id):
    effects = spec["effects"]
    add = [label_id] if effects["label"] and label_id else []
    remove = [x for flag, x in (("skip_inbox", "INBOX"), ("mark_read", "UNREAD")) if effects[flag]]
    return add, remove


def body(query, spec, label_id, **extra):
    add, remove = label_ids(spec, label_id)
    result = {"query": query, **extra}
    if add:
        result["add_label_ids"] = add
    if remove:
        result["remove_label_ids"] = remove
    return result


def stages(spec):
    return (["label"] if spec["effects"]["label"] else []) + (["filter"] if spec["future"] else []) + \
           (["cleanup_preview"] if spec["existing"] else [])


def describe(spec, count=None, exact=True):
    effects, bits = spec["effects"], []
    if effects["skip_inbox"]:
        bits.append("skip the inbox")
    if effects["mark_read"]:
        bits.append("be marked read")
    if effects["label"]:
        bits.append(f"get the label “{effects['label']}”")
    what = ", ".join(bits[:-1]) + (" and " if len(bits) > 1 else "") + bits[-1]
    match = ""
    if spec["match"]["include"]:
        match += " when the subject contains " + " or ".join(f"“{t}”" for t in spec["match"]["include"])
    if spec["match"]["exclude"]:
        match += (", except" if match else " except") + " when the subject contains " + " or ".join(f"“{t}”" for t in spec["match"]["exclude"])
    when = []
    if spec["future"]:
        when.append("new mail")
    if spec["existing"]:
        amount = f"{count:,}{'' if exact else '+'} " if isinstance(count, int) else ""
        age = f" older than {spec['older_than_days']} days" if spec["older_than_days"] else ""
        when.append(f"{amount}existing message{'s' if count != 1 else ''}{age}")
    return f"{' and '.join(when).capitalize()} from this sender will {what}{match}."
