"""The designer slice, for real: Skye dispatches a designer contract to the vendored Root, whose
worker inspects a finite fixture page through the dynamic-tool gateway (Playwright rendering the
actual HTML) and returns its verdict/reasons contract. No paid model: Root's scripted model plays
the worker. The quality signal is Root's contract check on the settled job plus proof the UI
inspection ran - Skye adds no blanket design-quality claim on top.
"""

from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path

import pytest

from test_integration_root import requires_runtime, root_server, text, tool_call  # noqa: F401 - shared fixture


@pytest.fixture
def designer_script(tmp_path, monkeypatch):
    from tura_code import launcher

    script = tmp_path / "designer-script.json"
    script.write_text(json.dumps([
        tool_call("i", "use_tool", {"name": "inspect_page",
                                    "arguments": {"path": "index.html", "width": 900, "height": 600}}),
        text('Cards fine, red bar overflows. {"verdict": "block", "reasons": ["page overflows horizontally"]}'),
    ]))
    monkeypatch.setenv(launcher.SCRIPTED_ECHO_ENV, str(script))


@requires_runtime
def test_a_designer_contract_inspects_a_real_rendered_page(root_server, designer_script, tmp_path):
    pytest.importorskip("playwright")
    from skye.dispatcher import dispatch
    from skye.events import FileBridge
    from skye.policy import Policy

    url, home = root_server
    token = (home / "token").read_text().strip()

    def api(method: str, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(
            f"{url}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json", "authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read())

    api("PUT", "/api/settings", {"dynamic_tools": True})

    repo = tmp_path / "ws"
    repo.mkdir()
    fixture = Path(__file__).resolve().parents[1] / "root" / "server" / "tests" / "fixtures" / "site" / "index.html"
    shutil.copy(fixture, repo / "index.html")

    outcome = dispatch(
        {"goal": "review the shop page for layout problems", "repo": str(repo), "role": "designer",
         "adapter": "root", "gates": [], "contract_keys": ["verdict", "reasons"], "budget": {"max_steps": 8}},
        Policy({"allow_unattended": [{"adapter": "root", "role": "designer"}]}),
        bridge=FileBridge(tmp_path / "events.jsonl"))
    assert outcome.status == "done", outcome.reason
    result = json.loads(outcome.worker_output)
    assert result["contract"]["ok"] and result["contract"]["data"]["verdict"] == "block"

    # Proof the UI check ran against the rendered fixture, not just that text came back:
    # the settled Root job's session shows the inspect_page tool result with the fixture's content.
    job = api("GET", f"/api/jobs/{result['job']}")
    events = api("GET", f"/api/sessions/{job['session']}/events")["events"]
    shown = [e for e in events if e["type"] == "ToolResult" and e["payload"].get("tool_call_id") == "i"]
    assert shown, "inspect_page never ran"
    assert not shown[0]["payload"]["return_value"]["is_error"]
    assert "Fixture shop" in json.dumps(shown[0]["payload"]["return_value"]["output"])


@requires_runtime
def test_a_pm_contract_returns_a_verdict_through_the_same_path(root_server, tmp_path, monkeypatch):
    """The PM slice: a plan review contract settles with its verdict/reasons checked. Text-only
    (no UI tool), same dispatch path - it exists to prove the role catalogue covers pm, not to
    claim planning quality."""
    from tura_code import launcher

    from skye.dispatcher import dispatch
    from skye.events import FileBridge
    from skye.policy import Policy

    script = tmp_path / "pm-script.json"
    script.write_text(json.dumps([
        text('Plan is missing rollout. {"verdict": "block", "reasons": ["no rollout step"]}'),
    ]))
    monkeypatch.setenv(launcher.SCRIPTED_ECHO_ENV, str(script))
    repo = tmp_path / "ws"
    repo.mkdir()
    outcome = dispatch(
        {"goal": "review the migration plan", "repo": str(repo), "role": "pm", "adapter": "root",
         "gates": [], "contract_keys": ["verdict", "reasons"], "budget": {"max_steps": 8}},
        Policy({"allow_unattended": [{"adapter": "root", "role": "pm"}]}),
        bridge=FileBridge(tmp_path / "events.jsonl"))
    assert outcome.status == "done", outcome.reason
    result = json.loads(outcome.worker_output)
    assert result["contract"]["data"]["verdict"] == "block"
