"""Applies the plan through the bridge, one bridge write per step, with undo.

Every step is persisted before the next one, uncertain outcomes stop for review, and
a paused bridge stops everything without losing the plan. Built-in choices and custom
actions share one spec, so there is a single code path.
"""
import time

from . import actions, bridge, store

WORK_STATUSES = ("planned", "applying")
BUDGET_SECONDS = 20
MAX_ARCHIVED_PER_ITEM = 10000
MAX_SWEPT_PER_ITEM = 100000  # inbox-wide cleanups run in 1,000-message batches
WRITE_PENDING = "write_pending:"


class Paused(Exception):
    pass


def _bridge_call(path, body):
    try:
        return bridge.post(path, body)
    except bridge.BridgeError as err:
        if err.status == 403 and "paused" in str(err).lower():
            raise Paused
        raise


def _write(item, path, body):
    """Leave a durable uncertainty marker until the result is saved by the caller."""
    store.update(item["id"], stage=WRITE_PENDING + path)
    try:
        return _bridge_call(path, body)
    except Paused:
        # An explicit paused refusal means no change was accepted.
        store.update(item["id"], stage=item["stage"])
        raise


def _interrupted_write(item):
    if (item["stage"] or "").startswith(WRITE_PENDING):
        _fail(item, "A write was interrupted before its outcome was saved. Inspect Activity and Gmail; it will not be retried.",
              "undo_partial" if item["status"] == "undoing" else "needs_review")
        return True
    return False


def _fail(item, message, status="failed"):
    store.update(item["id"], status=status, error=message[:300])
    store.log("needs review" if status == "needs_review" else "failed", f"{item['sender']}: {message}")


def spec_for(item):
    return item["spec"] if item["decision"] in ("custom", "sweep") else actions.builtin(item["sender"], item["decision"])


def _next_stage(spec, stage):
    order = actions.stages(spec)
    stage = "cleanup_preview" if stage == "cleanup" else stage
    later = order[order.index(stage) + 1:]
    return later[0] if later else None


def _move(item, spec, finished_stage, **fields):
    following = _next_stage(spec, finished_stage)
    if following:
        store.update(item["id"], stage=following, **fields)
    else:
        store.update(item["id"], status="done", stage="done", **fields)


def _ensure_label(item, spec):
    wanted = spec["effects"]["label"]
    labels = bridge.get("/gmail/labels", ttl=0).get("labels", [])
    found = actions.find_label(wanted, labels)
    if found:
        return found["id"], False
    created = _write(item, "/gmail/labels/create", {"name": wanted})
    store.log("label created", wanted)
    return created["id"], True


def advance(item):
    """One unit of work. Raises Paused if the bridge refuses changes."""
    if _interrupted_write(item):
        return
    sender, spec = item["sender"], spec_for(item)
    stage = item["stage"] or actions.stages(spec)[0]
    store.update(item["id"], status="applying", stage=stage)
    try:
        if stage == "label":
            label_id, created = _ensure_label(item, spec)
            _move(item, spec, "label", label_id=label_id, label_created=int(created or item["label_created"]))
        elif stage == "filter":
            body = actions.body(actions.future_query(spec), spec, item["label_id"])
            preview = _bridge_call("/gmail/filters/preview", body)
            result = _write(item, "/gmail/filters/apply", {"preview_id": preview["preview_id"]})
            if result.get("applied"):
                store.update(item["id"], filter_change_id=result["change_id"])
                store.log("filter created", sender)
            elif result.get("already_exists"):
                store.log("filter already existed", sender)
            else:
                return _fail(item, "The filter change had an uncertain outcome. Check Gmail before retrying.", "needs_review")
            _move(item, spec, "filter")
        elif stage == "cleanup_preview":
            body = actions.body(actions.existing_query(spec), spec, item["label_id"], max_messages=1000)
            preview = _bridge_call("/gmail/cleanup/preview", body)
            if preview["selected_messages"] == 0:
                _move(item, spec, "cleanup")
                store.log("nothing left to change", sender)
            else:
                store.update(item["id"], preview_id=preview["preview_id"], stage="cleanup")
        elif stage == "cleanup":
            result = _write(item, "/gmail/cleanup/apply", {"preview_id": item["preview_id"]})
            total = item["archived_prior"] + result["processed"]
            fresh = [c for c in result["change_ids"] if c not in item["change_ids"]]
            store.update(item["id"], archived=total, change_ids=item["change_ids"] + fresh)
            if result.get("needs_review"):
                return _fail(item, "A batch had an uncertain outcome. Check Gmail before continuing.", "needs_review")
            if result["complete"]:
                cap = MAX_SWEPT_PER_ITEM if item["decision"] == "sweep" else MAX_ARCHIVED_PER_ITEM
                if result.get("more_matches_exist") and total < cap:
                    store.update(item["id"], stage="cleanup_preview", archived_prior=total)
                else:
                    store.update(item["id"], status="done", stage="done",
                                 error="More matching mail remains; run this again to continue." if result.get("more_matches_exist") else None)
                    store.log("changed existing mail", f"{sender}: {total} messages")
            else:
                store.update(item["id"], stage="cleanup")
    except Paused:
        raise
    except bridge.BridgeError as err:
        uncertain = (store.get(item["id"])["stage"] or "").startswith(WRITE_PENDING)
        _fail(item, "A write could not be confirmed. Inspect Activity and Gmail before continuing." if uncertain else str(err),
              "needs_review" if uncertain else "failed")


def work_items():
    return [i for i in store.items() if i["decision"] in store.ACTIONS and i["status"] in WORK_STATUSES]


def undo_items():
    return [i for i in store.items() if i["status"] == "undoing"]


def start_undo(item_id):
    item = store.get(item_id)
    if not item or item["status"] != "done":
        return False
    queue = list(reversed(item["change_ids"]))
    if item["filter_change_id"]:
        queue.append(item["filter_change_id"])
    store.update(item_id, status="undoing", undo_queue=queue, error=None)
    store.log("undo started", item["sender"])
    return True


def undo_step(item):
    if _interrupted_write(item):
        return
    if not item["undo_queue"]:
        store.update(item["id"], status="undone")
        store.log("undone", item["sender"])
        return
    change_id, rest = item["undo_queue"][0], item["undo_queue"][1:]
    try:
        result = _write(item, "/gmail/undo", {"change_id": change_id})
    except bridge.BridgeError as err:
        return _fail(item, "Undo failed: " + str(err), "undo_partial")
    if not result.get("undone"):
        return _fail(item, "Some messages changed after this action, so they were left as they are.", "undo_partial")
    store.update(item["id"], stage="done", undo_queue=rest)


def run(budget=BUDGET_SECONDS):
    """Process queued work until done or out of time. Returns {'paused': bool, 'remaining': int}."""
    deadline = time.monotonic() + budget
    paused = False
    current = None
    try:
        while time.monotonic() < deadline:
            queue = undo_items()
            if queue:
                current = None
                undo_step(queue[0])
                continue
            queue = work_items()
            if not queue:
                break
            current = queue[0]
            advance(current)
    except Paused:
        paused = True
        latest = store.get(current["id"]) if current else None
        if latest and latest["status"] == "applying" and not latest["filter_change_id"] and not latest["archived"] \
                and not latest["change_ids"] and not latest["label_created"]:
            store.update(latest["id"], status="planned", stage=None, preview_id=None)
        store.log("paused", "The bridge is not accepting changes. The plan is saved.")
    return {"paused": paused, "remaining": len(work_items()) + len(undo_items())}
