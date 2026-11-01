"""The dedicated auto_pickup toggle endpoint: narrow, validated, lock-serialized.

It exists because POST /api/policy is a whole-file replace: a UI that wanted to flip one
boolean would have to GET-merge-POST, a lost-update hazard against a security policy file.
This endpoint changes ONLY the auto_pickup block, preserving every other value, and fails
closed when the current file can't be parsed - it never rewrites a file it didn't read.
"""
from __future__ import annotations

import json
import threading

import pytest

from tests.test_ui_server import ui_server, _req  # reuse the live-server fixture/helpers


def test_toggle_preserves_unrelated_policy_values(ui_server, tmp_path, monkeypatch):
    url, _ = ui_server
    policy = tmp_path / "policy.json"
    monkeypatch.setenv("SKYE_POLICY", str(policy))
    policy.write_text(json.dumps({
        "allow_unattended": [{"adapter": "claude-code", "role": "*"}],
        "test_command": "make test", "journey_command": "make journey",
        "auto_pickup": {"enabled": False}}))
    code, data = _req(url, "/api/policy/auto_pickup", method="POST",
                      body={"auto_pickup": {"enabled": True, "max_missions": 3}})
    assert code == 200 and data["auto_pickup"]["enabled"] is True
    on_disk = json.loads(policy.read_text())
    assert on_disk["auto_pickup"] == {"enabled": True, "max_missions": 3}
    # every unrelated value survived untouched
    assert on_disk["allow_unattended"] == [{"adapter": "claude-code", "role": "*"}]
    assert on_disk["test_command"] == "make test"
    assert on_disk["journey_command"] == "make journey"


def test_creates_block_when_absent(ui_server, tmp_path, monkeypatch):
    url, _ = ui_server
    policy = tmp_path / "policy.json"
    monkeypatch.setenv("SKYE_POLICY", str(policy))
    policy.write_text(json.dumps({"test_command": "true"}))
    code, _ = _req(url, "/api/policy/auto_pickup", method="POST",
                   body={"auto_pickup": {"enabled": True}})
    assert code == 200
    assert json.loads(policy.read_text())["auto_pickup"] == {"enabled": True}


@pytest.mark.parametrize("block", [
    {"enabled": "yes"},                       # wrong type
    {"include_reopened": 1},                  # wrong type
    {"max_missions": 0},                      # out of range
    {"max_missions": "many"},                 # wrong type
    {"enabled": True, "surprise": True},      # unknown key
])
def test_invalid_block_rejected_file_untouched(ui_server, tmp_path, monkeypatch, block):
    url, _ = ui_server
    policy = tmp_path / "policy.json"
    monkeypatch.setenv("SKYE_POLICY", str(policy))
    before = {"auto_pickup": {"enabled": False}, "test_command": "true"}
    policy.write_text(json.dumps(before))
    code, data = _req(url, "/api/policy/auto_pickup", method="POST",
                      body={"auto_pickup": block})
    assert code == 400 and "error" in data
    assert json.loads(policy.read_text()) == before


def test_corrupt_current_file_fails_closed(ui_server, tmp_path, monkeypatch):
    url, _ = ui_server
    policy = tmp_path / "policy.json"
    monkeypatch.setenv("SKYE_POLICY", str(policy))
    policy.write_text("{ not json")
    code, data = _req(url, "/api/policy/auto_pickup", method="POST",
                      body={"auto_pickup": {"enabled": True}})
    assert code == 409 and "refusing" in data["error"]
    assert policy.read_text() == "{ not json"  # never rewritten


def test_concurrent_toggles_serialize_no_lost_updates(ui_server, tmp_path, monkeypatch):
    """Two toggles racing the flock serialize: each reads the file the other left, so no
    write is lost to a stale snapshot, and unrelated values survive both."""
    url, _ = ui_server
    policy = tmp_path / "policy.json"
    monkeypatch.setenv("SKYE_POLICY", str(policy))
    policy.write_text(json.dumps({"test_command": "true"}))
    results = []

    def toggle(enabled, extra=None):
        body = {"auto_pickup": {"enabled": enabled, **(extra or {})}}
        results.append(_req(url, "/api/policy/auto_pickup", method="POST", body=body)[0])

    threads = [threading.Thread(target=toggle, args=(True, {"max_missions": 2})),
               threading.Thread(target=toggle, args=(False,))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert results == [200, 200]
    final = json.loads(policy.read_text())
    assert set(final["auto_pickup"]) <= {"enabled", "include_reopened", "max_missions"}
    assert final["test_command"] == "true"  # unrelated value intact after both writes
    # the GET endpoint reflects the final toggled state
    _, state = _req(url, "/api/policy")
    assert state["auto_pickup"]["enabled"] == final["auto_pickup"]["enabled"]


# ----- whole-file replace: same lock, revision conflict protection -----

def test_get_exposes_revision(ui_server, tmp_path, monkeypatch):
    url, _ = ui_server
    policy = tmp_path / "policy.json"
    monkeypatch.setenv("SKYE_POLICY", str(policy))
    policy.write_text(json.dumps({"test_command": "true"}))
    code, data = _req(url, "/api/policy")
    assert code == 200 and data["revision"]
    policy.write_text(json.dumps({"test_command": "false"}))
    _, data2 = _req(url, "/api/policy")
    assert data2["revision"] != data["revision"]


def test_replace_requires_current_base_revision(ui_server, tmp_path, monkeypatch):
    url, _ = ui_server
    policy = tmp_path / "policy.json"
    monkeypatch.setenv("SKYE_POLICY", str(policy))
    policy.write_text(json.dumps({"test_command": "true"}))
    # no base_revision at all: rejected, with the current revision handed back
    code, data = _req(url, "/api/policy", method="POST",
                      body={"policy": {"test_command": "false"}})
    assert code == 409 and "base_revision" in data["error"] and data["revision"]
    assert json.loads(policy.read_text()) == {"test_command": "true"}
    # stale base_revision: rejected
    code, data = _req(url, "/api/policy", method="POST",
                      body={"policy": {"test_command": "false"},
                            "base_revision": "deadbeefdeadbeef"})
    assert code == 409 and "changed since" in data["error"]
    # current base_revision: accepted
    _, current = _req(url, "/api/policy")
    code, data = _req(url, "/api/policy", method="POST",
                      body={"policy": {"test_command": "false"},
                            "base_revision": current["revision"]})
    assert code == 200
    assert json.loads(policy.read_text()) == {"test_command": "false"}


def test_stale_settings_save_cannot_wipe_toggle(ui_server, tmp_path, monkeypatch):
    """The exact race: the Settings form loads the policy, the pickup toggle flips under the
    narrow endpoint, then the form's stale whole-file Save arrives. The backend rejects it."""
    url, _ = ui_server
    policy = tmp_path / "policy.json"
    monkeypatch.setenv("SKYE_POLICY", str(policy))
    policy.write_text(json.dumps({"test_command": "true",
                                  "auto_pickup": {"enabled": False}}))
    _, loaded = _req(url, "/api/policy")           # form loads at this revision
    stale_copy = loaded["raw"]
    _req(url, "/api/policy/auto_pickup", method="POST",
         body={"auto_pickup": {"enabled": True}})  # toggle lands meanwhile
    code, data = _req(url, "/api/policy", method="POST",
                      body={"policy": stale_copy, "base_revision": loaded["revision"]})
    assert code == 409
    on_disk = json.loads(policy.read_text())
    assert on_disk["auto_pickup"]["enabled"] is True  # the toggle survived


def test_first_write_needs_no_revision(ui_server, tmp_path, monkeypatch):
    url, _ = ui_server
    monkeypatch.setenv("SKYE_POLICY", str(tmp_path / "policy.json"))
    code, data = _req(url, "/api/policy", method="POST",
                      body={"policy": {"test_command": "true"}})
    assert code == 200 and data["revision"]
