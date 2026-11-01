"""Presentation-layer redaction: credential shapes are masked inside free text on display
payloads; legitimate prose and payload structure survive; the raw journal is never altered."""
from __future__ import annotations

import json

from skye import redact
from skye.adapters.base import Result
from skye.mission import run_mission
from skye.ui.server import mission_payload, mission_graph
from tests.test_multirepo import two_repos
from tests.test_workspaces import ON


def test_credential_shapes_masked_prose_untouched():
    cases = {
        "aws": ("key AKIAIOSFODNN7EXAMPLE here", "[redacted-aws-key]"),
        "github": ("use ghp_" + "a" * 30, "[redacted-github-token]"),
        "pat": ("github_pat_" + "A1_" * 15, "[redacted-github-token]"),
        "slack": ("xoxb-1234567890-abcdef", "[redacted-slack-token]"),
        "jwt": ("eyJhbGciOiJ9.eyJzdWIiOiIxMjM0fQ.signature99", "[redacted-jwt]"),
        "bearer": ("Authorization: Bearer abcdef123456", "Bearer [redacted]"),
        "assignment": ('password="hunter2"', 'password="[redacted]"'),
        "apikey": ("api_key: sk-1234567890", "api_key: [redacted]"),
    }
    for name, (text, masked) in cases.items():
        result = redact.redact_text(text)
        assert masked in result, name
        # nothing outside the secret is eaten
        for innocent in ("key ", "here", "use ", "Authorization:"):
            if innocent in text:
                assert innocent in result, (name, innocent)
    private = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc123\n-----END OPENSSH PRIVATE KEY-----"
    assert redact.redact_text(private) == "[redacted-private-key]"
    # ordinary mission prose passes through byte-identical
    prose = ["the API lives in backend", "step 'ui' failed: tests timed out after 30s",
             "workspace recovered; 3 files lost", "goal: ship cart totals end to end"]
    for text in prose:
        assert redact.redact_text(text) == text


def _mission_with_secret(tmp_path, state, secret):
    backend, _frontend = two_repos(tmp_path)
    def run(spec):
        return Result(ok=True, output=f"done, leaked {secret}", command=["fake"])
    mission = {"id": "m-secret", "goal": f"debug why {secret} fails",  # secret in the GOAL
               "repo": str(backend),
               "steps": [{"id": "one", "role": "developer", "adapter": "claude-code",
                          "goal": "work", "gates": []}]}
    return mission, run


def test_detail_route_masks_values_but_keeps_fields(tmp_path):
    state = tmp_path / "state"
    secret = "AKIAIOSFODNN7EXAMPLE"
    mission, run = _mission_with_secret(tmp_path, state, secret)
    out = run_mission(mission, ON, runner=run, state_dir=state, catalogues_dir="catalogues")
    assert out.status == "done"
    payload = mission_payload(state, "m-secret")
    text = json.dumps(payload)
    assert secret not in text                       # nowhere in the display payload
    assert "[redacted-aws-key]" in text             # masked where it appeared
    # the trace view's fields survive with structure intact
    settled = [e for e in payload["journal"] if e.get("event") == "settled"]
    assert settled and "output" in settled[0] and "reason" in settled[0]
    assert "done, leaked" in settled[0]["output"]   # legitimate content not stripped
    # the raw journal on disk is untouched - it is the recovery record
    raw = (state / "missions" / "m-secret" / "journal.jsonl").read_text()
    assert secret in raw


def test_secret_inside_reason_and_rationale_fields_masked(tmp_path):
    """Not only output tails: reasons, rationales and error strings carry the same risk."""
    state = tmp_path / "state"
    backend, _frontend = two_repos(tmp_path)
    secret = "ghp_" + "s3cr3t" * 6
    def run(spec):
        return Result(ok=False, output="boom", returncode=1, command=["fake"])
    mission = {"id": "m-reason", "goal": "x", "repo": str(backend),
               "steps": [{"id": "one", "role": "developer", "adapter": "claude-code",
                          "goal": f"uses token {secret}", "gates": []}]}
    out = run_mission(mission, ON, runner=run, state_dir=state, catalogues_dir="catalogues")
    assert out.status == "failed"
    detail = json.dumps(mission_payload(state, "m-reason"))
    graph = mission_graph(state, "m-reason")
    graph_text = json.dumps(graph)
    assert secret not in detail and secret not in graph_text
    assert "[redacted-github-token]" in graph_text  # masked inside the node goal
    # and a secret planted in a planner's repo_reason is masked in graph nodes
    state2 = tmp_path / "state2"
    (tmp_path / "b").mkdir()
    backend2, frontend2 = two_repos(tmp_path / "b")
    proposals = json.dumps({"propose_steps": [
        {"id": "api", "role": "developer", "adapter": "claude-code", "goal": "g",
         "repo": "backend", "repo_reason": f"token {secret} lives there"}]})
    def run2(spec):
        if spec["goal"] == "plan it":
            return Result(ok=True, output=proposals, command=["fake"])
        return Result(ok=True, output="done", command=["fake"])
    mission2 = {"id": "m-rr", "goal": "x",
                "repos": {"backend": str(backend2), "frontend": str(frontend2)},
                "steps": [{"id": "plan", "role": "developer", "adapter": "claude-code",
                           "goal": "plan it", "repo": "backend", "gates": []}]}
    out2 = run_mission(mission2, ON, runner=run2, state_dir=state2,
                       catalogues_dir="catalogues")
    assert out2.status == "done"
    graph2_text = json.dumps(mission_graph(state2, "m-rr"))
    assert secret not in graph2_text
    assert "lives there" in graph2_text  # the rationale prose survives around the mask
