"""The agent definition schema: builtins are real, proposals are gated, promotion is the owner's."""

from skye import agents, roles


def test_builtins_carry_real_definitions():
    for name in ("senior-dev", "backend-dev", "frontend-dev", "pm", "reviewer"):
        definition = agents.definition_for(name)
        assert definition is not None, name
        assert definition["instructions"], name
        assert definition["version"] >= 1
    # backend-dev composes on senior-dev but keeps its own voice
    assert agents.definition_for("backend-dev")["instructions"] != \
        agents.definition_for("senior-dev")["instructions"]


def test_definition_validation_rejects_bad_shapes(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    for bad, why in [
        ({"name": "BAD CAPS", "instructions": "x"}, "name format"),
        ({"name": "ok-name"}, "missing instructions"),
        ({"name": "ok-name", "instructions": "x", "extends": "ghost"}, "unknown extends"),
        ({"name": "ok-name", "instructions": "x", "catalogue": {"tools": ["rm -rf"]}},
         "tool outside the universe"),
        ({"name": "ok-name", "instructions": "x" * 9000}, "instructions over the bound"),
    ]:
        try:
            agents.validate_definition(bad)
            raise AssertionError(f"accepted {why}: {bad}")
        except agents.AgentError:
            pass


def test_proposed_agents_cannot_run_until_promoted(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    accepted, rejected = agents.propose(
        [{"name": "security-auditor", "instructions": "Audit for secrets and injection.",
          "catalogue": {"tools": ["Read", "Grep"]}}], mission="m1")
    assert [a["name"] for a in accepted] == ["security-auditor"] and not rejected
    # Invisible to contracts: the policy gate is not the only line - validation itself refuses.
    assert "security-auditor" not in roles.known()
    listed = {d["name"]: d for d in agents.list_definitions()}
    assert listed["security-auditor"]["status"] == "proposed"
    assert listed["security-auditor"]["proposed_by"] == "m1"
    # The owner promotes; only then can a contract name it.
    agents.promote("security-auditor")
    assert "security-auditor" in roles.known()
    assert agents.definition_for("security-auditor")["source"] == "proposed"


def test_proposals_reject_duplicates_and_respect_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    accepted, rejected = agents.propose(
        [{"name": "developer", "instructions": "shadow a builtin"}], mission="m1")
    assert not accepted and "already has that name" in rejected[0]["reason"]
    many = [{"name": f"agent-{i}", "instructions": f"do thing {i}"} for i in range(12)]
    accepted, rejected = agents.propose(many, mission="m1")
    assert len(accepted) == agents.MAX_PROPOSED_PER_MISSION
    assert len(rejected) == 12 - agents.MAX_PROPOSED_PER_MISSION


def test_extract_proposals_handles_root_envelope_and_last_wins():
    plain = '{"propose_agents": [{"name": "a-one", "instructions": "first"}]}'
    assert agents.extract_proposals(plain)[0]["name"] == "a-one"
    envelope = '{"reply": "text {\\"propose_agents\\": [{\\"name\\": \\"a-two\\", \\"instructions\\": \\"second\\"}]} end"}'
    assert agents.extract_proposals(envelope)[0]["name"] == "a-two"
    assert agents.extract_proposals("no proposals here") == []


def test_registry_override_refines_a_builtin(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    roles.add({"name": "pm", "description": "our pm", "instructions": "Our PM writes one-line scopes."})
    definition = agents.definition_for("pm")
    assert definition["instructions"] == "Our PM writes one-line scopes."
    assert definition["source"] == "owner"
