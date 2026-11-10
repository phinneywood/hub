import pytest
from fastapi.testclient import TestClient

from hub import apply, bridge, senders, store, worker
from hub.main import app
from tests.test_app import ORIGIN, PROFILE, STATE, fake_get

client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_bridge(monkeypatch):
    STATE["profile"] = dict(PROFILE)
    monkeypatch.setattr(bridge, "get", fake_get)


class FakeWrites:
    def __init__(self, selected=120, batches_per_preview=1, fail_on=None, paused=False, needs_review=False):
        self.calls, self.selected, self.paused, self.needs_review = [], selected, paused, needs_review
        self.fail_on, self.processed, self.previews = fail_on, 0, 0

    def __call__(self, path, body):
        self.calls.append((path, body))
        if self.fail_on == path:
            raise bridge.BridgeError("Invalid or disallowed request parameters", 400)
        if self.paused and path in ("/gmail/filters/apply", "/gmail/cleanup/apply", "/gmail/undo"):
            raise bridge.BridgeError("Organization changes are temporarily paused for maintenance", 403)
        if path == "/gmail/filters/preview":
            return {"preview_id": "p" * 32}
        if path == "/gmail/filters/apply":
            return {"applied": True, "change_id": "f" * 32}
        if path == "/gmail/cleanup/preview":
            self.previews += 1
            self.processed = 0
            return {"preview_id": f"c{self.previews}" * 8, "selected_messages": self.selected if self.previews == 1 else 0}
        if path == "/gmail/cleanup/apply":
            self.processed = min(self.processed + 50, self.selected)
            ids = [f"{i:032x}" for i in range(1, (self.processed + 49) // 50 + 1)]
            return {"processed": self.processed, "complete": self.processed == self.selected, "more_matches_exist": False,
                    "change_ids": ids, "needs_review": self.needs_review}
        if path == "/gmail/undo":
            return {"undone": True}
        raise AssertionError(path)


def use(monkeypatch, **kwargs):
    writes = FakeWrites(**kwargs)
    monkeypatch.setattr(bridge, "post", writes)
    return writes


def test_sender_validation_blocks_query_injection():
    assert senders.valid_sender("a.b+c@example.com")
    for bad in ("a@b.example in:anywhere", "from:x", "a b@c.example", "a@b.example)", ""):
        assert not senders.valid_sender(bad)


def test_covered_detects_existing_skip_inbox_filter():
    rules = [{"criteria": {"from": "News@Loud.example"}, "action": {"removeLabelIds": ["INBOX"]}},
             {"criteria": {"from": "other@x.example"}, "action": {"addLabelIds": ["Label_1"]}}]
    assert senders.covered("news@loud.example", rules)
    assert not senders.covered("other@x.example", rules)


@pytest.mark.parametrize("criteria", [
    {"from": "news@loud.example", "subject": "receipt"},
    {"from": "other-news@loud.example"},
    {"query": "from:news@loud.example -subject:refund"},
    {"query": "-from:news@loud.example"},
    {"from": "news@loud.example", "hasAttachment": True},
])
def test_restricted_or_similar_filters_do_not_claim_full_sender_coverage(criteria):
    assert not senders.covered("news@loud.example", [{"criteria": criteria, "action": {"removeLabelIds": ["INBOX"]}}])


def test_decisions_are_stored_and_applied_ones_are_frozen():
    assert store.set_decision("a@b.example", "filter", 5)
    assert store.set_decision("a@b.example", "keep")
    item = store.items()[0]
    store.update(item["id"], status="done")
    assert not store.set_decision("a@b.example", "filter")
    assert not store.remove(item["id"])


def test_full_apply_filter_then_archive_in_batches(monkeypatch):
    writes = use(monkeypatch, selected=120)
    store.set_decision("news@loud.example", "filter_archive", 120)
    outcome = apply.run(budget=5)
    item = store.items()[0]
    assert outcome == {"paused": False, "remaining": 0}
    assert item["status"] == "done" and item["archived"] == 120 and item["filter_change_id"] == "f" * 32
    assert len(item["change_ids"]) == 3
    paths = [p for p, _ in writes.calls]
    assert paths[:2] == ["/gmail/filters/preview", "/gmail/filters/apply"]
    assert writes.calls[0][1] == {"query": "from:news@loud.example", "remove_label_ids": ["INBOX"]}
    assert writes.calls[2][1]["query"] == "from:news@loud.example in:inbox" and writes.calls[2][1]["remove_label_ids"] == ["INBOX"]
    assert "/gmail/send" not in paths


def test_keep_and_later_never_call_the_bridge(monkeypatch):
    writes = use(monkeypatch)
    store.set_decision("a@b.example", "keep")
    store.set_decision("c@d.example", "later")
    assert apply.run(budget=2) == {"paused": False, "remaining": 0}
    assert writes.calls == []


def test_paused_bridge_keeps_the_plan(monkeypatch):
    use(monkeypatch, paused=True)
    store.set_decision("a@b.example", "filter", 3)
    assert apply.run(budget=2)["paused"] is True
    item = store.items()[0]
    assert item["status"] == "planned" and item["stage"] is None


def test_uncertain_batch_stops_for_review_and_is_not_retried(monkeypatch):
    writes = use(monkeypatch, selected=100, needs_review=True)
    store.set_decision("a@b.example", "archive", 100)
    apply.run(budget=2)
    item = store.items()[0]
    assert item["status"] == "needs_review"
    calls = len(writes.calls)
    apply.run(budget=2)
    assert len(writes.calls) == calls


def test_bridge_error_marks_item_failed_and_continues_with_others(monkeypatch):
    use(monkeypatch, selected=10, fail_on="/gmail/filters/preview")
    store.set_decision("a@b.example", "filter", 1)
    store.set_decision("c@d.example", "archive", 10)
    apply.run(budget=2)
    status = {i["sender"]: i["status"] for i in store.items()}
    assert status == {"a@b.example": "failed", "c@d.example": "done"}


def test_undo_reverses_batches_then_filter(monkeypatch):
    writes = use(monkeypatch, selected=100)
    store.set_decision("a@b.example", "filter_archive", 100)
    apply.run(budget=2)
    item = store.items()[0]
    assert apply.start_undo(item["id"])
    writes.calls.clear()
    apply.run(budget=2)
    undone = [b["change_id"] for p, b in writes.calls if p == "/gmail/undo"]
    assert undone == list(reversed(item["change_ids"])) + [item["filter_change_id"]]
    assert store.items()[0]["status"] == "undone"


def test_undo_conflict_is_reported_not_forced(monkeypatch):
    use(monkeypatch, selected=10)
    store.set_decision("a@b.example", "archive", 10)
    apply.run(budget=2)
    monkeypatch.setattr(bridge, "post", lambda path, body: {"undone": False})
    apply.start_undo(store.items()[0]["id"])
    apply.run(budget=2)
    assert store.items()[0]["status"] == "undo_partial"


def test_cards_and_decisions_flow(monkeypatch):
    assert "Loud News" in client.get("/gmail/quiet").text
    response = client.post("/gmail/quiet/decide", data={"sender": "news@loud.example", "decision": "filter_archive", "count": 1234},
                           headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303
    assert "bob@example.com" in client.get("/gmail/quiet").text
    plan = client.get("/gmail/plan").text
    assert "news@loud.example" in plan and "1,234" in plan and "Apply plan (paused)" in plan
    client.post("/gmail/quiet/decide", data={"sender": "bob@example.com", "decision": "keep"}, headers=ORIGIN)
    assert "No loud senders left" in client.get("/gmail/quiet").text


def test_home_leads_with_next_step():
    body = client.get("/gmail/tools").text
    assert "93,012" in body and "16,104" in body and "Quiet your loudest senders" in body


def test_apply_is_refused_while_paused_without_touching_the_bridge(monkeypatch):
    writes = use(monkeypatch)
    store.set_decision("a@b.example", "filter", 1)
    response = client.post("/gmail/plan/apply", headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and "paused=1" in response.headers["location"]
    assert writes.calls == []


def test_apply_route_runs_when_enabled(monkeypatch):
    STATE["profile"]["organization_paused"] = False
    writes = use(monkeypatch, selected=10)
    store.set_decision("a@b.example", "filter_archive", 10)
    response = client.post("/gmail/plan/apply", headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/gmail/plan"
    worker.wait(10)
    assert store.items()[0]["status"] == "done" and writes.calls


def test_posts_need_same_origin():
    data = {"sender": "a@b.example", "decision": "keep"}
    assert client.post("/gmail/quiet/decide", data=data).status_code == 403
    assert client.post("/gmail/quiet/decide", data=data, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/gmail/quiet/decide", data=data, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/gmail/quiet/decide", data=data, headers={"Sec-Fetch-Site": "same-origin"}, follow_redirects=False).status_code == 303
    assert store.items() and store.items()[0]["sender"] == "a@b.example"


def test_bad_decisions_are_ignored():
    client.post("/gmail/quiet/decide", data={"sender": "a@b.example in:anywhere", "decision": "filter"}, headers=ORIGIN)
    client.post("/gmail/quiet/decide", data={"sender": "a@b.example", "decision": "delete_everything"}, headers=ORIGIN)
    assert store.items() == []


def test_write_paths_are_fixed_and_exclude_sending():
    real_post = bridge.__dict__["post"]
    for path in ("/gmail/send", "/gmail/drafts", "/gmail/trash", "/gmail/archive", "/gmail/delete", "/gmail/forward"):
        with pytest.raises(bridge.BridgeError):
            real_post(path, {})
    # Filing sorted mail under a Hub label is the one message-level change, and it goes by message id.
    assert "/gmail/labels/apply" in bridge.WRITE_PATHS
    assert not [path for path in bridge.WRITE_PATHS if any(word in path for word in ("send", "draft", "trash", "delete", "forward"))]


# ---- custom actions -------------------------------------------------------------------------

from hub import luna  # noqa: E402

PROPOSAL = {"unsupported": None, "future": True, "existing": True, "older_than_days": None,
            "subject_include": [], "subject_exclude": ["refund"], "skip_inbox": True, "mark_read": True, "label": "Receipts"}


@pytest.fixture
def luna_ready(monkeypatch):
    monkeypatch.setattr(luna, "configured", lambda: True)
    calls = []
    monkeypatch.setattr(luna, "complete", lambda request: calls.append(request) or dict(PROPOSAL))
    return calls


def post(path, **data):
    return client.post(path, data=data, headers=ORIGIN, follow_redirects=False)


def test_propose_shows_validated_preview_with_new_label(luna_ready):
    body = post("/gmail/quiet/custom/propose", sender="news@loud.example", instruction="receipts please").text
    assert "what I" in body
    assert "New label will be created" in body and "Receipts" in body and "refund" in body
    assert len(luna_ready) == 1 and luna_ready[0]["provider"]["zdr"] is True
    assert store.items() == []


def test_accept_adds_a_custom_item_and_recomputes_everything(luna_ready):
    import json
    from hub import actions
    spec = actions.validate(dict(PROPOSAL), "news@loud.example", [], [])
    assert post("/gmail/quiet/custom/accept", sender="news@loud.example", spec=json.dumps(spec)).status_code == 303
    item = store.items()[0]
    assert item["decision"] == "custom" and item["spec"]["effects"]["label"] == "Receipts" and item["count"] == 1234
    plan = client.get("/gmail/plan").text
    assert "new label" in plan and "Receipts" in plan


def test_accept_rejects_a_tampered_spec(luna_ready):
    import json
    from hub import actions
    spec = actions.validate(dict(PROPOSAL), "news@loud.example", [], [])
    spec["match"]["include"] = ["x OR from:boss@work.example"]
    response = post("/gmail/quiet/custom/accept", sender="news@loud.example", spec=json.dumps(spec))
    assert response.status_code == 200 and store.items() == []


def test_model_output_outside_the_grammar_never_reaches_the_plan(monkeypatch):
    monkeypatch.setattr(luna, "configured", lambda: True)
    monkeypatch.setattr(luna, "complete", lambda request: {**PROPOSAL, "label": "Inbox", "delete": True})
    body = post("/gmail/quiet/custom/propose", sender="news@loud.example", instruction="x").text
    assert "expected format" in body and store.items() == []


def test_unsupported_request_shows_the_reason(monkeypatch):
    monkeypatch.setattr(luna, "configured", lambda: True)
    monkeypatch.setattr(luna, "complete", lambda request: {**PROPOSAL, "unsupported": "I can't delete mail"})
    assert "I can&#39;t delete mail" in post("/gmail/quiet/custom/propose", sender="news@loud.example", instruction="delete").text


def test_ai_off_sender_is_never_sent_to_the_model(luna_ready):
    post("/gmail/quiet/ai-off", sender="news@loud.example", off=1)
    body = post("/gmail/quiet/custom/propose", sender="news@loud.example", instruction="do it").text
    assert "AI is turned off" in body and luna_ready == []


def test_not_configured_explains_setup(monkeypatch):
    monkeypatch.setattr(luna, "configured", lambda: False)
    assert "set-openrouter-key" in client.get("/gmail/quiet/custom?sender=news@loud.example").text
    assert "set-openrouter-key" in post("/gmail/quiet/custom/propose", sender="news@loud.example", instruction="x").text


def test_luna_outage_is_reported_without_crashing(monkeypatch):
    monkeypatch.setattr(luna, "configured", lambda: True)
    def down(request): raise luna.LunaError("Luna couldn't be reached")
    monkeypatch.setattr(luna, "complete", down)
    assert "Luna couldn&#39;t be reached" in post("/gmail/quiet/custom/propose", sender="news@loud.example", instruction="x").text


def test_custom_apply_creates_label_then_filter_then_cleanup(monkeypatch):
    from hub import actions
    writes = FakeWrites(selected=60)
    original = writes.__call__
    def with_labels(path, body):
        if path == "/gmail/labels/create":
            writes.calls.append((path, body))
            return {"id": "Label_99", "name": body["name"]}
        return original(path, body)
    monkeypatch.setattr(bridge, "post", with_labels)
    spec = actions.validate(dict(PROPOSAL), "news@loud.example", [], [])
    store.set_decision("news@loud.example", "custom", 60, True, spec=spec)
    assert apply.run(budget=5) == {"paused": False, "remaining": 0}
    item = store.items()[0]
    assert item["status"] == "done" and item["label_id"] == "Label_99" and item["label_created"] == 1 and item["archived"] == 60
    paths = [p for p, _ in writes.calls]
    assert paths[:4] == ["/gmail/labels/create", "/gmail/filters/preview", "/gmail/filters/apply", "/gmail/cleanup/preview"]
    assert writes.calls[1][1] == {"query": "from:news@loud.example -subject:(refund)", "add_label_ids": ["Label_99"],
                                  "remove_label_ids": ["INBOX", "UNREAD"]}
    assert writes.calls[3][1]["query"] == "from:news@loud.example -subject:(refund) in:inbox"
    assert writes.calls[3][1]["max_messages"] == 1000


def test_custom_apply_reuses_an_existing_label(monkeypatch):
    from hub import actions
    labels = [{"id": "Label_5", "name": "Receipts", "type": "user"}]
    monkeypatch.setattr(bridge, "get", lambda path, ttl=60, **p: {"labels": labels} if path == "/gmail/labels" else fake_get(path, ttl, **p))
    writes = use(monkeypatch, selected=0)
    spec = actions.validate({**PROPOSAL, "existing": False}, "news@loud.example", labels, [])
    store.set_decision("news@loud.example", "custom", 0, True, spec=spec)
    apply.run(budget=5)
    assert "/gmail/labels/create" not in [p for p, _ in writes.calls]
    assert writes.calls[0][1]["add_label_ids"] == ["Label_5"] and store.items()[0]["status"] == "done"


def test_label_creation_path_is_allowed_but_label_delete_is_not():
    real_post = bridge.__dict__["post"]
    for path in ("/gmail/labels/delete", "/gmail/labels/rename"):
        with pytest.raises(bridge.BridgeError):
            real_post(path, {})


def test_status_luna_test_uses_only_made_up_data(monkeypatch):
    seen = []
    monkeypatch.setattr(luna, "configured", lambda: True)
    def fake(request):
        seen.append(request["messages"][1]["content"])
        if request["response_format"]["json_schema"]["name"] == "sender_assessment":
            return {"assessment": "Deals.", "options": [{"kind": "action", "title": "Archive", "why": "94% unread.",
                                                         "action": {**PROPOSAL, "label": None}}]}
        return dict(PROPOSAL)
    monkeypatch.setattr(luna, "complete", fake)
    monkeypatch.setattr(bridge, "health", lambda: True)
    body = post("/status/luna-test").text
    assert "Passed" in body and "Receipts" in body and "1 usable option" in body
    assert len(seen) == 2 and all("example.invalid" in s for s in seen)


def test_status_luna_test_reports_failure(monkeypatch):
    monkeypatch.setattr(luna, "configured", lambda: True)
    def down(request): raise luna.LunaError("Luna is unavailable right now (status 404)")
    monkeypatch.setattr(luna, "complete", down)
    assert "Failed: Luna is unavailable" in post("/status/luna-test").text


def test_worker_finishes_the_whole_plan_without_the_page(monkeypatch):
    STATE["profile"]["organization_paused"] = False
    use(monkeypatch, selected=120)
    for sender in ("a@b.example", "c@d.example", "e@f.example"):
        store.set_decision(sender, "filter_archive", 120)
    client.post("/gmail/plan/apply", headers=ORIGIN)
    worker.wait(10)
    assert [i["status"] for i in store.items()] == ["done"] * 3
    assert not worker.running()


def test_only_one_worker_runs_at_a_time(monkeypatch):
    import threading
    gate, started = threading.Event(), threading.Event()
    def slow(budget=20):
        started.set(); gate.wait(5); return {"paused": False, "remaining": 0}
    monkeypatch.setattr(apply, "run", slow)
    assert worker.start() is True
    started.wait(5)
    assert worker.start() is False
    gate.set(); worker.wait(5)


def test_plan_page_shows_progress_while_working(monkeypatch):
    STATE["profile"]["organization_paused"] = False
    store.set_decision("a@b.example", "filter_archive", 2445)
    store.set_decision("c@d.example", "filter_archive", 10)
    item = store.items()[0]
    store.update(item["id"], status="applying", stage="cleanup", archived=800)
    monkeypatch.setattr(worker, "running", lambda: True)
    body = client.get("/gmail/plan").text
    assert "Applying your plan" in body and "You can close this page" in body and 'http-equiv="refresh"' in body
    assert "800 of about 2,445 archived so far" in body and "Waiting your turn" in body
    assert "/gmail/plan/apply" not in body


def test_plan_page_offers_resume_when_stopped_partway(monkeypatch):
    STATE["profile"]["organization_paused"] = False
    store.set_decision("a@b.example", "filter_archive", 100)
    store.update(store.items()[0]["id"], status="applying", stage="cleanup", archived=50)
    monkeypatch.setattr(worker, "running", lambda: False)
    body = client.get("/gmail/plan").text
    assert "Tap Resume" in body and "/gmail/plan/continue" in body and 'http-equiv="refresh"' not in body


def test_undo_runs_in_the_background(monkeypatch):
    STATE["profile"]["organization_paused"] = False
    writes = use(monkeypatch, selected=100)
    store.set_decision("a@b.example", "filter_archive", 100)
    apply.run(budget=5)
    item_id = store.items()[0]["id"]
    client.post("/gmail/plan/undo", data={"item_id": item_id}, headers=ORIGIN)
    worker.wait(10)
    assert store.items()[0]["status"] == "undone"


def test_restart_resumes_only_work_that_had_begun(monkeypatch):
    STATE["profile"]["organization_paused"] = False
    use(monkeypatch, selected=50)
    store.set_decision("a@b.example", "filter_archive", 50)
    worker.resume_if_interrupted()
    worker.wait(5)
    assert store.items()[0]["status"] == "planned"
    store.update(store.items()[0]["id"], status="applying", stage="cleanup_preview")
    worker.resume_if_interrupted()
    worker.wait(10)
    assert store.items()[0]["status"] == "done"


# Deterministic editor: all reads/writes below use the fake bridge above.
def test_sender_picker_and_search_are_read_only(monkeypatch):
    writes = use(monkeypatch)
    body = client.get("/gmail/senders").text
    assert "news@loud.example" in body and "bob@example.com" in body
    body = client.get("/gmail/senders?q=in:inbox+older_than:90d").text
    assert "news@loud.example" in body and "Top senders among" in body and 'class="on">Over 90 days old' in body
    assert writes.calls == [] and store.items() == []


def test_manual_preview_works_without_ai_and_preserves_edit_options(monkeypatch):
    import html
    import re
    monkeypatch.setattr(luna, "configured", lambda: False)
    monkeypatch.setattr(luna, "propose", lambda *a, **k: pytest.fail("No model calls allowed"))
    writes = use(monkeypatch)
    assert "No AI calls" in client.get("/gmail/organize?sender=news@loud.example").text
    response = post("/gmail/organize/preview", sender="news@loud.example", scope="both", include="receipt\ninvoice",
                    exclude="refund", days="90", skip_inbox="true", mark_read="true", label="Receipts")
    assert response.status_code == 200
    body = response.text
    assert "1,234 existing messages older than 90 days" in body and "New label will be created" in body
    assert "New matching mail is changed immediately" in body and "Edit exact options" in body
    spec = html.unescape(re.search(r'name="spec" value="([^"]+)"', body).group(1))
    edited = post("/gmail/organize/edit", sender="news@loud.example", spec=spec).text
    assert "receipt\ninvoice" in edited and 'value="90"' in edited and 'value="Receipts"' in edited
    assert writes.calls == [] and store.items() == []
    accepted = post("/gmail/quiet/custom/accept", sender="news@loud.example", spec=spec)
    assert accepted.headers["location"] == "/gmail/plan"
    assert store.items()[0]["spec"]["match"]["exclude"] == ["refund"]
    assert writes.calls == []


@pytest.mark.parametrize("changes,reason", [
    ({"scope": "future", "days": "30"}, "age limit only applies"),
    ({"days": "-1"}, "whole number"),
    ({"days": "1.5"}, "whole number"),
    ({"days": "0"}, "between 1 and 3650"),
    ({"scope": "all"}, "Choose existing mail"),
    ({"include": "x OR from:boss@example.com"}, "subject word"),
    ({"exclude": "a\nb\nc\nd\ne\nf"}, "at most 5"),
    ({"label": "Inbox"}, "reserved"),
])
def test_manual_preview_rejects_invalid_rules(changes, reason):
    body = post("/gmail/organize/preview", **{"sender": "news@loud.example", "scope": "existing", "skip_inbox": "true", **changes}).text
    assert reason in body and store.items() == []


def test_manual_editor_requires_an_effect_and_preserves_input():
    body = post("/gmail/organize/preview", sender="news@loud.example", include="receipt").text
    assert "has to skip" in body and "receipt</textarea>" in body


def test_manual_edit_preserves_existing_identity_and_frozen_actions():
    store.set_decision("News@Loud.example", "archive", 3)
    body = client.get("/gmail/organize?sender=news@loud.example").text
    assert 'value="News@Loud.example"' in body
    store.update(store.items()[0]["id"], status="done")
    body = post("/gmail/organize/preview", sender="news@loud.example", skip_inbox="true").text
    assert "cannot be overwritten" in body
    assert store.items()[0]["status"] == "done" and len(store.items()) == 1


def test_label_only_plan_does_not_claim_mail_leaves_inbox():
    from hub import actions
    spec = actions.validate({**PROPOSAL, "skip_inbox": False, "mark_read": False}, "news@loud.example", [])
    store.set_decision("news@loud.example", "custom", 1234, spec=spec)
    body = client.get("/gmail/plan").text
    assert "messages leave your inbox" not in body and ">Edit</a>" in body


def test_manual_preview_counts_and_examples_bypass_cached_reads(monkeypatch):
    reads = []
    def capture(path, ttl=60, **params):
        reads.append((path, ttl, params))
        return fake_get(path, ttl, **params)
    monkeypatch.setattr(bridge, "get", capture)
    post("/gmail/organize/preview", sender="news@loud.example", skip_inbox="true")
    matching = [(path, ttl) for path, ttl, params in reads if params.get("query") == "from:news@loud.example in:inbox"]
    assert ("/gmail/count", 0) in matching and ("/gmail/search", 0) in matching


@pytest.mark.parametrize("decision,path", [("filter", "/gmail/filters/apply"), ("archive", "/gmail/cleanup/apply")])
def test_interrupted_write_is_never_repeated_after_resume(monkeypatch, decision, path):
    writes = use(monkeypatch, selected=10)
    def interrupted(route, body):
        if route == path:
            writes.calls.append((route, body))
            raise RuntimeError("simulated process interruption after request")
        return writes(route, body)
    monkeypatch.setattr(bridge, "post", interrupted)
    store.set_decision("news@loud.example", decision, 10)
    with pytest.raises(RuntimeError):
        apply.run(budget=2)
    assert store.items()[0]["stage"] == apply.WRITE_PENDING + path
    calls = len(writes.calls)
    worker.resume_if_interrupted()
    worker.wait(5)
    assert store.items()[0]["status"] == "needs_review" and len(writes.calls) == calls


def test_lost_write_response_requires_review_and_keeps_other_plan_working(monkeypatch):
    writes = use(monkeypatch, selected=10, fail_on="/gmail/filters/apply")
    store.set_decision("a@b.example", "filter", 10)
    store.set_decision("c@d.example", "archive", 10)
    apply.run(budget=2)
    assert [i["status"] for i in store.items()] == ["needs_review", "done"]
    assert len([p for p, _ in writes.calls if p == "/gmail/filters/apply"]) == 1


def test_interrupted_label_creation_is_not_repeated(monkeypatch):
    from hub import actions
    writes = use(monkeypatch)
    def interrupted(path, body):
        writes.calls.append((path, body))
        raise RuntimeError("simulated interruption")
    monkeypatch.setattr(bridge, "post", interrupted)
    spec = actions.validate(PROPOSAL, "news@loud.example", [])
    store.set_decision("news@loud.example", "custom", 10, spec=spec)
    with pytest.raises(RuntimeError):
        apply.run(budget=2)
    apply.run(budget=2)
    assert store.items()[0]["status"] == "needs_review"
    assert [p for p, _ in writes.calls] == ["/gmail/labels/create"]


def test_interrupted_undo_is_not_repeated(monkeypatch):
    writes = use(monkeypatch, selected=10)
    store.set_decision("news@loud.example", "archive", 10)
    apply.run(budget=2)
    apply.start_undo(store.items()[0]["id"])
    def interrupted(path, body):
        writes.calls.append((path, body))
        raise RuntimeError("simulated interruption")
    monkeypatch.setattr(bridge, "post", interrupted)
    with pytest.raises(RuntimeError):
        apply.run(budget=2)
    calls = len(writes.calls)
    apply.run(budget=2)
    assert store.items()[0]["status"] == "undo_partial" and len(writes.calls) == calls


def test_blocking_ai_does_not_block_manual_rule_editor(monkeypatch):
    store.set_ai_blocked("news@loud.example", True)
    monkeypatch.setattr(luna, "propose", lambda *a, **k: pytest.fail("Must not call Luna"))
    body = post("/gmail/organize/preview", sender="news@loud.example", skip_inbox="true").text
    assert "Add to plan" in body


@pytest.mark.parametrize("path,data", [
    ("/gmail/organize/preview", {"sender": "news@loud.example", "skip_inbox": "true"}),
    ("/gmail/organize/edit", {"sender": "news@loud.example", "spec": "{}"}),
])
def test_editor_posts_require_same_origin(path, data):
    assert client.post(path, data=data).status_code == 403
    assert store.items() == []


def test_editor_and_preview_escape_mail_subjects_and_owner_input():
    body = client.get("/gmail/organize?sender=news@loud.example").text
    assert "<script>x</script>" not in body and "&lt;script&gt;" in body
    body = post("/gmail/organize/preview", sender="news@loud.example", include="</textarea><script>x</script>").text
    assert "</textarea><script>" not in body and "&lt;/textarea&gt;" in body


def test_search_results_offer_sender_actions_not_search_writes():
    body = client.get("/gmail/search?q=in:inbox+older_than:90d").text
    assert 'href="/gmail/organize?sender=news%40loud.example"' in body
    assert store.items() == []


def test_read_only_progress_does_not_claim_archiving(monkeypatch):
    from hub import actions
    spec = actions.validate({**PROPOSAL, "skip_inbox": False, "label": None}, "news@loud.example", [])
    store.set_decision("news@loud.example", "custom", 100, spec=spec)
    store.update(store.items()[0]["id"], status="applying", stage="cleanup", archived=50)
    monkeypatch.setattr(worker, "running", lambda: True)
    body = client.get("/gmail/plan").text
    assert "Updating existing messages" in body and "50 of about 100 updated so far" in body
    assert "archived so far" not in body
