"""Snapshot-approval enforcement: a changed source re-import must never ride an old approval.

Design: the approved snapshot is immutable while it has authority; a changed source
INVALIDATES the decision (ticket back to pending, reservation cleared) so only a fresh owner
decision can start work on the new content. Exercised against the real pickup paths:
approved->update->poll and reserve->update->launch.
"""
from __future__ import annotations

import pytest

from skye import backlog, intake_github, issues, pickup
from skye.policy import Policy
from tests.test_intake_github import ISSUES, FakeResponse


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "issues.json"))
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    intake_github.import_issues("acme/app", project="app",
                                opener=lambda r, timeout=0: FakeResponse(ISSUES))
    feature = [f for f in backlog.list_features() if "Safari" in f["title"]][0]
    return feature


def changed_opener(request, timeout=0):
    edited = [dict(ISSUES[0], body="EDITED: rewrite the whole auth system instead"),
              dict(ISSUES[1])]
    return FakeResponse(edited)


ON = Policy({"auto_pickup": {"enabled": True}})


def test_approved_then_source_update_then_poll_starts_nothing(store):
    backlog.decide(store["id"], "approved")
    result = intake_github.import_issues("acme/app", opener=changed_opener)
    assert result["content_changed"]
    feature = backlog.get(store["id"])
    assert feature["status"] == "pending"          # approval invalidated
    assert feature["decided_at"] is None
    assert "EDITED" in feature["brief"]            # new content has NO authority now
    started = []

    def create_mission(snapshot):
        started.append(snapshot["goal"])
        return "mission-x"

    poll = pickup.poll_once(policy=ON, picker="p1", create_mission=create_mission)
    assert started == []                           # nothing auto-picked under the old approval
    assert poll["started"] == []


def test_reserve_then_source_update_then_launch_aborts(store):
    backlog.decide(store["id"], "approved")
    pickup.reserve(store["id"], picker="p1")
    intake_github.import_issues("acme/app", opener=changed_opener)
    feature = backlog.get(store["id"])
    assert not feature.get("pickup")               # reservation cleared
    with pytest.raises(pickup.PickupError):
        pickup.launch(store["id"], picker="p1",
                      create_mission=lambda snap: "mission-x")
    assert backlog.get(store["id"]).get("mission_id", "") == ""  # nothing started


def test_started_mission_keeps_its_approved_snapshot(store):
    backlog.decide(store["id"], "approved")
    feature = backlog.get(store["id"])
    original_brief = feature["brief"]
    from pathlib import Path
    data = backlog._read(backlog.default_path())
    for f in data["features"]:
        if f["id"] == store["id"]:
            f["mission_id"] = "mission-running"
    backlog._write(Path(backlog.default_path()), data)
    intake_github.import_issues("acme/app", opener=changed_opener)
    after = backlog.get(store["id"])
    assert after["status"] == "approved"           # running work is not invalidated
    assert after["brief"] == original_brief        # its snapshot is immutable
    notes = [e["body"] for e in after["thread"] if e.get("actor") == "intake"]
    assert any("keeps its approved snapshot" in n for n in notes)


def test_fresh_decision_after_invalidation_binds_new_digest(store):
    backlog.decide(store["id"], "approved")
    intake_github.import_issues("acme/app", opener=changed_opener)
    backlog.decide(store["id"], "approved")        # the owner re-decides on the NEW content
    feature = backlog.get(store["id"])
    assert feature["status"] == "approved"
    assert "EDITED" in feature["brief"]            # this approval binds the new snapshot
    started = []
    poll = pickup.poll_once(policy=ON, picker="p1",
                            create_mission=lambda snap: started.append(snap["goal"]) or "m-1")
    assert started and "EDITED" in started[0]      # pickup now starts the re-approved content
