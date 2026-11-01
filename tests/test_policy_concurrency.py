"""Roles registry and policy/concurrency coverage."""

import pytest

from skye import gateway
from skye.contract import ContractError, validate
from skye.mission import run_mission
from tests.test_mission import _mission_file, _policy



def test_roles_registry_extends_vocabulary(tmp_path, monkeypatch):
    """An owner-defined specialist validates, resolves a tool catalogue, and survives runtime
    workflow growth - the roles are dynamic, not the shipped seven."""
    from skye.roles import add, known, catalogue_for, describe
    registry = tmp_path / "skye.roles.json"
    monkeypatch.setenv("SKYE_ROLES", str(registry))
    monkeypatch.chdir(tmp_path)

    record = add({"name": "security-auditor", "description": "audits for vulnerabilities",
                  "catalogue": {"tools": ["Read", "Grep"], "writable": False}})
    assert record["name"] == "security-auditor"
    assert "security-auditor" in known()
    assert catalogue_for("security-auditor") == {"tools": ["Read", "Grep"], "writable": False}

    validated = validate({"goal": "audit the auth module", "repo": "/tmp/x",
                          "role": "security-auditor", "adapter": "root"})
    assert validated["role"] == "security-auditor"
    assert gateway.resolve("security-auditor", tmp_path)["source"] == "roles-registry"

    # unknown roles still fail loudly
    with pytest.raises(ContractError):
        validate({"goal": "x", "repo": "/tmp/x", "role": "wizard", "adapter": "root"})
    # and a worker-proposed step may name the registry role (owner already granted it)
    path = _mission_file(tmp_path, [{"id": "scan", "role": "security-auditor",
                                     "adapter": "claude-code",
                                     "goal": "scan for injection", "gates": []}])
    from skye.adapters.base import Result
    from skye.mission import load_mission
    seen = []

    def runner(spec):
        seen.append(spec["role"])
        return Result(ok=True, output="scanned clean")

    outcome = run_mission(load_mission(path), _policy(), runner=runner,
                          state_dir=tmp_path / "st")
    assert outcome.status == "done", outcome.reason
    assert seen == ["security-auditor"]


def test_roles_registry_refines_builtin_catalogue(tmp_path, monkeypatch):
    """A registry entry named like a builtin narrows/widens its tools - owner refinement."""
    from skye.roles import add, describe
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "skye.roles.json"))
    monkeypatch.chdir(tmp_path)
    add({"name": "developer", "description": "read-only dev on this team",
         "catalogue": {"tools": ["Read", "Grep"], "writable": False}})
    resolved = gateway.resolve("developer", tmp_path)
    assert resolved["writable"] is False and resolved["source"] == "roles-registry"
    assert {d["name"]: d["source"] for d in describe()}["developer"] == "registry-override"
    # malformed registry input fails loudly
    from skye.roles import RoleError
    with pytest.raises(RoleError):
        add({"name": "BAD NAME!", "catalogue": {}})
