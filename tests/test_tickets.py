"""Persistent team tickets: labels, thread, reopen, repair intake, discussion folding."""

import pytest

from skye import backlog, issues


def _delivered(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "issues.json"))
    feature = backlog.propose([{"title": "Cart page", "brief": "build it"}],
                              project="web", mission="m0")[0]
    backlog.decide(feature["id"], "approved")
    backlog.mark_started(feature["id"], "m1")
    backlog.mark_delivered("m1", journey_ok=True, evidence="replay ok")
    return backlog.get(feature["id"])


def test_labels_are_validated(tmp_path, monkeypatch):
    feature = _delivered(tmp_path, monkeypatch)
    updated = backlog.set_labels(feature["id"], ["type:feature", "area:frontend"])
    assert updated["labels"] == ["type:feature", "area:frontend"]
    with pytest.raises(backlog.BacklogError):
        backlog.set_labels(feature["id"], ["NOT A LABEL!"])
    with pytest.raises(backlog.BacklogError):
        backlog.set_labels(feature["id"], [f"l{i}" for i in range(20)])


def test_delivered_ticket_reopens_with_evidence_and_labels(tmp_path, monkeypatch):
    feature = _delivered(tmp_path, monkeypatch)
    reopened = backlog.reopen(feature["id"], evidence="checkout 500s on empty cart",
                              labels=["type:bug", "severity:high"], actor="pm")
    assert reopened["status"] == "reopened"
    assert reopened["reopen_count"] == 1
    assert "severity:high" in reopened["labels"]
    kinds = [e["kind"] for e in reopened["thread"]]
    assert "evidence" in kinds and "status" in kinds
    # The owner decides the repair; then the normal intake can start it.
    decided = backlog.decide(feature["id"], "approved", note="fix it")
    assert decided["status"] == "approved"
    backlog.mark_started(feature["id"], "m2")
    ticket = backlog.get(feature["id"])
    assert ticket["mission_id"] == "m2" and ticket["missions"] == ["m1", "m2"]


def test_only_delivered_work_reopens(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    feature = backlog.propose([{"title": "T", "brief": "b"}], project="")[0]
    with pytest.raises(backlog.BacklogError):
        backlog.reopen(feature["id"], evidence="too early")


def test_pm_bug_report_drives_the_reopen_loop(tmp_path, monkeypatch):
    feature = _delivered(tmp_path, monkeypatch)
    issue, action = issues.report_for_feature(
        feature["id"], "user-report", "empty cart checkout 500s",
        detail="POST /cart/checkout -> 500 when items=[]", labels=["area:backend"], actor="pm")
    assert action == "reopened"
    ticket = backlog.get(feature["id"])
    assert ticket["status"] == "reopened"
    assert "type:bug" in ticket["labels"] and "area:backend" in ticket["labels"]
    assert issue["kind"] == "user-report"


def test_bug_on_awaiting_verification_sends_back(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "issues.json"))
    feature = backlog.propose([{"title": "T", "brief": "b"}], project="")[0]
    backlog.decide(feature["id"], "approved")
    backlog.mark_started(feature["id"], "m1")
    backlog.mark_delivered("m1", journey_ok=False)  # awaiting-verification
    _, action = issues.report_for_feature(feature["id"], "test-failure", "smoke fails")
    assert action == "sent-back"
    assert backlog.get(feature["id"])["status"] == "approved"


def test_thread_entries_are_bounded_and_typed(tmp_path, monkeypatch):
    feature = _delivered(tmp_path, monkeypatch)
    entry = backlog.add_thread(feature["id"], actor="pm", kind="comment", body="looks good")
    assert entry["kind"] == "comment"
    with pytest.raises(backlog.BacklogError):
        backlog.add_thread(feature["id"], actor="pm", kind="rant", body="x")
    with pytest.raises(backlog.BacklogError):
        backlog.add_thread(feature["id"], actor="pm", kind="comment", body="  ")


def test_discussion_folds_into_the_linked_ticket_once(tmp_path, monkeypatch):
    feature = _delivered(tmp_path, monkeypatch)
    entries = [{"id": "msg-1", "ts": 1.0, "author": "backend-dev", "kind": "contract-change",
                "body": "api v2"},
               {"id": "msg-2", "ts": 2.0, "author": "frontend-dev", "kind": "ack",
                "body": "adapting"}]
    assert backlog.fold_discussion("m1", entries) == 2
    assert backlog.fold_discussion("m1", entries) == 0  # idempotent
    thread = backlog.get(feature["id"])["thread"]
    assert [e["actor"] for e in thread if e["kind"] == "discussion"] == \
        ["backend-dev", "frontend-dev"]
    assert backlog.fold_discussion("nobody", entries) == 0
