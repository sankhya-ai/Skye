import json

from skye import backlog


def test_propose_persists_pending_and_skips_duplicates(tmp_path):
    path = tmp_path / "skye.backlog.json"
    added = backlog.propose([{"title": "Add search", "brief": "full text search"}],
                            project="skye", mission="m1", path=path)
    assert len(added) == 1
    assert added[0]["status"] == "pending"
    assert added[0]["proposed_by"] == "m1"
    again = backlog.propose([{"title": "add search", "brief": "same thing, re-cased"}],
                            project="skye", path=path)
    assert again == []
    features = backlog.list_features(path)
    assert len(features) == 1
    assert json.loads(path.read_text("utf-8"))["features"] == features


def test_propose_validates_title_and_brief(tmp_path):
    path = tmp_path / "skye.backlog.json"
    for entry in ({"title": "", "brief": "x"}, {"title": "x", "brief": ""}):
        try:
            backlog.propose([entry], path=path)
            raise AssertionError("expected BacklogError")
        except backlog.BacklogError:
            pass
    assert backlog.list_features(path) == []


def test_owner_decide_approve_and_reject(tmp_path):
    path = tmp_path / "skye.backlog.json"
    feature = backlog.propose([{"title": "F1", "brief": "b1"}], path=path)[0]
    decided = backlog.decide(feature["id"], "approved", note="go", path=path)
    assert decided["status"] == "approved"
    assert decided["decision_note"] == "go"
    other = backlog.propose([{"title": "F2", "brief": "b2"}], path=path)[0]
    rejected = backlog.decide(other["id"], "rejected", path=path)
    assert rejected["status"] == "rejected"
    try:
        backlog.decide(feature["id"], "rejected", path=path)
        raise AssertionError("expected BacklogError")
    except backlog.BacklogError:
        pass
    try:
        backlog.decide("missing", "approved", path=path)
        raise AssertionError("expected BacklogError")
    except backlog.BacklogError:
        pass
    try:
        backlog.decide(feature["id"], "bogus", path=path)
        raise AssertionError("expected BacklogError")
    except backlog.BacklogError:
        pass


def test_mark_started_and_delivered_round_trip(tmp_path):
    path = tmp_path / "skye.backlog.json"
    feature = backlog.propose([{"title": "F1", "brief": "b1"}], path=path)[0]
    backlog.decide(feature["id"], "approved", path=path)
    backlog.mark_started(feature["id"], "mission-9", path=path)
    stored = backlog.list_features(path)[0]
    assert stored["mission_id"] == "mission-9"
    assert stored["status"] == "approved"
    # work done without journey evidence -> awaiting verification, NOT delivered
    backlog.mark_delivered("mission-9", journey_ok=False, path=path)
    stored = backlog.list_features(path)[0]
    assert stored["status"] == "awaiting-verification"
    # a journey replay pass delivers it; a failure would leave it waiting
    backlog.record_verification(feature["id"], False, evidence="still broken", path=path)
    assert backlog.list_features(path)[0]["status"] == "awaiting-verification"
    backlog.record_verification(feature["id"], True, evidence="journey clean", path=path)
    stored = backlog.list_features(path)[0]
    assert stored["status"] == "delivered"
    assert stored["mission_id"] == "mission-9"
    assert stored["verification"]["journey_passed"] is True


def test_pending_feature_is_not_deliverable(tmp_path):
    path = tmp_path / "skye.backlog.json"
    feature = backlog.propose([{"title": "F1", "brief": "b1"}], path=path)[0]
    backlog.mark_started(feature["id"], "mission-1", path=path)
    backlog.mark_delivered("mission-1", journey_ok=True, path=path)
    assert backlog.list_features(path)[0]["status"] == "pending"
