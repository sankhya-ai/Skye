"""Trusted automatic pickup: reservation idempotency, revocation safety, freshness, off-by-default."""

import json
import threading
from pathlib import Path

import pytest

from skye import backlog, pickup
from skye.policy import Policy


def _feature(tmp_path, **overrides):
    path = tmp_path / "backlog.json"
    feature = {"id": "f-1", "title": "cart api", "brief": "add the endpoint",
               "status": "approved", "project": "", "decided_at": 100.0, "thread": []}
    feature.update(overrides)
    path.write_text(json.dumps({"features": [feature]}))
    return path


def _cfg(**overrides):
    cfg = {"enabled": True, "include_reopened": False, "max_missions": 1, "adapter": ""}
    cfg.update(overrides)
    return cfg


def test_policy_defaults_pickup_fully_off():
    assert pickup.config(Policy()) == {"enabled": False, "include_reopened": False,
                                       "max_missions": 1, "adapter": ""}
    assert pickup.config(Policy({"auto_pickup": "nonsense"}))["enabled"] is False
    assert pickup.config(Policy({"auto_pickup": {"enabled": True}}))["enabled"] is True


def test_simultaneous_pollers_exactly_one_reserves(tmp_path):
    path = _feature(tmp_path)
    winners, losers = [], []

    def poller(name):
        try:
            winners.append(pickup.reserve("f-1", picker=name, path=path)["pickup"]["picker"])
        except pickup.PickupError:
            losers.append(name)

    threads = [threading.Thread(target=poller, args=(f"picker-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(winners) == 1 and len(losers) == 7


def test_restart_between_reservation_and_launch(tmp_path):
    path = _feature(tmp_path)
    pickup.reserve("f-1", picker="picker-a", path=path, now=1000.0)
    # a fresh reservation blocks a second picker
    with pytest.raises(pickup.PickupError):
        pickup.reserve("f-1", picker="picker-b", path=path, now=1001.0)
    # after picker-a crashed (stale reservation, still no mission), picker-b takes over
    feature = pickup.reserve("f-1", picker="picker-b", path=path, now=1000.0 + 200.0)
    assert feature["pickup"]["picker"] == "picker-b"
    # and picker-a can no longer launch against the stolen reservation
    with pytest.raises(pickup.PickupError):
        pickup.launch("f-1", picker="picker-a", path=path,
                      create_mission=lambda spec: "m-x")


def test_revoked_approval_aborts_the_launch(tmp_path):
    path = _feature(tmp_path)
    pickup.reserve("f-1", picker="p", path=path)
    backlog.decide  # (backlog API available; simulate revocation by direct status change)
    data = json.loads(path.read_text())
    data["features"][0]["status"] = "pending"
    path.write_text(json.dumps(data))
    with pytest.raises(pickup.PickupError, match="aborted"):
        pickup.launch("f-1", picker="p", path=path, create_mission=lambda spec: "m-1")
    feature = json.loads(path.read_text())["features"][0]
    assert "pickup" not in feature and not feature.get("mission_id")


def test_successful_launch_attaches_mission_and_journals(tmp_path):
    path = _feature(tmp_path)
    pickup.reserve("f-1", picker="p", path=path)
    seen = {}
    def _create(spec):
        seen["spec"] = spec
        return "m-9"
    result = pickup.launch("f-1", picker="p", path=path, create_mission=_create)
    assert result["mission_id"] == "m-9"
    feature = json.loads(path.read_text())["features"][0]
    assert feature["mission_id"] == "m-9" and "pickup" not in feature
    assert seen["spec"] == {"goal": "cart api: add the endpoint", "plan": True, "project": ""}
    assert any("started mission m-9 automatically" in e["body"] for e in feature["thread"])
    # a second launch cannot happen: the ticket now has a mission
    with pytest.raises(pickup.PickupError):
        pickup.reserve("f-1", picker="p2", path=path)


def test_failed_mission_creation_clears_the_reservation(tmp_path):
    path = _feature(tmp_path)
    pickup.reserve("f-1", picker="p", path=path)
    with pytest.raises(pickup.PickupError, match="creation failed"):
        pickup.launch("f-1", picker="p", path=path, create_mission=lambda spec: "")
    assert "pickup" not in json.loads(path.read_text())["features"][0]
    # and the ticket can be reserved again afterwards
    pickup.reserve("f-1", picker="p2", path=path)


def test_eligibility_rules(tmp_path):
    base = {"status": "approved", "mission_id": "", "reopen_count": 0}
    assert pickup.eligible(dict(base), _cfg()) == ""
    assert "not approved" in pickup.eligible(dict(base, status="pending"), _cfg())
    assert "already has a mission" in pickup.eligible(dict(base, mission_id="m-1"), _cfg())
    reopened = dict(base, reopen_count=1, decided_at=50.0,
                    thread=[{"kind": "status", "ts": 100.0,
                             "body": "reopened (occurrence 1); the owner decides"}])
    assert "stay manual" in pickup.eligible(reopened, _cfg())
    assert "predates the latest reopen" in pickup.eligible(
        reopened, _cfg(include_reopened=True))
    fresh = dict(reopened, decided_at=150.0)
    assert pickup.eligible(fresh, _cfg(include_reopened=True)) == ""


def test_abort_clears_only_own_reservation(tmp_path):
    path = _feature(tmp_path)
    pickup.reserve("f-1", picker="p", path=path)
    pickup.abort("f-1", picker="someone-else", path=path, reason="nope")
    assert json.loads(path.read_text())["features"][0]["pickup"]["picker"] == "p"
    pickup.abort("f-1", picker="p", path=path, reason="owner revoked")
    feature = json.loads(path.read_text())["features"][0]
    assert "pickup" not in feature
    assert any("aborted: owner revoked" in e["body"] for e in feature["thread"])


def test_simultaneous_poller_PROCESSES_exactly_one_reserves(tmp_path):
    """The real deployment shape: separate poller processes, one backlog file. The flock in
    reserve() must admit exactly one winner; every loser gets a clean PickupError."""
    import subprocess
    import sys
    path = _feature(tmp_path)
    script = (
        "import sys; from skye import pickup\n"
        f"try:\n pickup.reserve('f-1', picker=sys.argv[1], path=r'{path}')\n"
        "except pickup.PickupError:\n sys.exit(3)\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script, f"proc-{i}"])
             for i in range(6)]
    codes = [p.wait() for p in procs]
    assert codes.count(0) == 1 and codes.count(3) == 5
