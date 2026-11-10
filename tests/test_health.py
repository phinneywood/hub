from hub.tools.gmail import health

LABELS = [{"id": "Label_1", "name": "Bills", "type": "user"}, {"id": "Label_2", "name": "Old stuff", "type": "user"},
          {"id": "INBOX", "name": "INBOX", "type": "system"}]


def rule(id, criteria, add=(), remove=()):
    return {"id": id, "criteria": criteria, "action": {"addLabelIds": list(add), "removeLabelIds": list(remove)}}


def test_criteria_query():
    assert health.criteria_query({"from": "a@b.c", "negatedQuery": "x"}) == "from:(a@b.c) -(x)"
    assert health.criteria_query({"query": "q", "hasAttachment": True}) == "(q) has:attachment"
    assert health.criteria_query({"from": "a", "forward": "x"}) is None
    assert health.criteria_query({}) is None


def test_missing_label_and_contradiction():
    rules = [rule("1", {"from": "a"}, add=["Label_9"]), rule("2", {"from": "b"}, add=["Label_1"], remove=["Label_1"])]
    kinds = [i["kind"] for i in health.static_issues(rules, LABELS, [])]
    assert kinds == ["Missing label", "Contradiction"]


def test_duplicates_and_same_match():
    same = [rule("1", {"from": "a"}, add=["Label_1"]), rule("2", {"from": "a"}, remove=["INBOX"])]
    assert [i["kind"] for i in health.static_issues(same, LABELS, [])] == ["Same match, different actions"]
    assert [i["kind"] for i in health.static_issues(same[:1], LABELS, [["1", "2"]])] == ["Exact duplicates"]


def test_unused_labels():
    assert [x["id"] for x in health.unused_labels([rule("1", {"from": "a"}, add=["Label_1"])], LABELS)] == ["Label_2"]


def test_activity_stops_when_rate_limited(monkeypatch):
    calls = []
    def estimate(query):
        calls.append(query)
        if len(calls) == 3:
            raise health.bridge.BridgeError("Wait before making more requests")
        return 0
    monkeypatch.setattr(health, "estimate", estimate)
    rules = [rule("1", {"from": "a"}), rule("2", {"from": "b"}), rule("3", {"from": "c"}), rule("4", {"forward": "x"})]
    result = health.run_activity(rules, LABELS)
    assert result["filters"] == {"1": {"recent": 0, "year": 0}, "2": "not checked", "3": "not checked", "4": "unsupported"}
    assert result["labels"] == {"Label_1": "not checked", "Label_2": "not checked"}
    assert not result["complete"] and len(calls) == 3
