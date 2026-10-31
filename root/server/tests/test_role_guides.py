"""Role guides for the designer and pm job roles: on-demand loading, the source-clue scan, and the evidence-anchor check.

What these tests do NOT show: that the guides improve a model's reviews, that a finding is true, or that anything
is tested interactively. They check the plumbing and the honesty of its claims.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import requires_runtime, text, tool_call

from tura_code import guides, jobs
from tura_code.guides import texts


def fake_runtime(role: str, workspace: Path):
    return SimpleNamespace(record=SimpleNamespace(job_role=role, workspace=str(workspace)))


def test_the_job_prompt_carries_only_an_index_never_the_guide_text():
    for role in ("pm", "designer"):
        prompt = guides.index_for(role)
        assert "role_guide" in prompt and "evidence" in prompt
        for name in guides.guides_for(role):
            body = texts.GUIDES[name][1]
            assert body.splitlines()[2] not in prompt and len(prompt) < len(body)
    assert guides.index_for("developer") == "" and guides.index_for("reviewer") == ""


def test_each_role_gets_only_its_own_guides():
    assert "pm-walkthrough" in guides.guides_for("pm") and "designer-critique" not in guides.guides_for("pm")
    assert "designer-critique" in guides.guides_for("designer") and "pm-reconcile" not in guides.guides_for("designer")
    assert all(n in texts.GUIDES for r in texts.ROLE_GUIDES.values() for n in r)


def test_tools_are_invisible_to_other_roles_and_loading_is_gated(tmp_path):
    ts = guides.GuideToolset()
    assert ts.definitions(fake_runtime("developer", tmp_path)) == [] and ts.definitions(fake_runtime("", tmp_path)) == []
    assert [d["name"] for d in ts.definitions(fake_runtime("pm", tmp_path))] == ["role_guide"]
    assert [d["name"] for d in ts.definitions(fake_runtime("designer", tmp_path))] == ["role_guide", "design_scan"]
    run = lambda rt, name, args: asyncio.run(ts.call(rt, "c", name, args))  # noqa: E731
    assert not run(fake_runtime("pm", tmp_path), "role_guide", {"name": "pm-walkthrough"})["is_error"]
    assert run(fake_runtime("pm", tmp_path), "role_guide", {"name": "designer-critique"})["is_error"]
    assert run(fake_runtime("pm", tmp_path), "design_scan", {})["is_error"]
    assert run(fake_runtime("developer", tmp_path), "role_guide", {"name": "evidence"})["is_error"]
    listing = run(fake_runtime("designer", tmp_path), "role_guide", {})["output"]
    assert "designer-critique" in listing and "pm-reconcile" not in listing


def test_guides_state_what_cannot_be_checked_here():
    assert "not run" in texts.INTERACTION_STATES and "not interaction testing" in texts.INTERACTION_STATES.replace("is\na reading", "is a reading").replace("\n", " ") or "reading of states" in texts.INTERACTION_STATES
    assert "deployed: not verified" in texts.EVIDENCE and "inspect_deployed" in texts.EVIDENCE and "owner authorized" in texts.EVIDENCE
    assert "never claim" in texts.DESIGNER_CRITIQUE.lower() or "Never claim" in texts.DESIGNER_CRITIQUE
    assert all(s["revision"] and s["licence"] for s in texts.SOURCES)


def test_design_scan_finds_clues_with_locations_and_stays_in_bounds(tmp_path):
    (tmp_path / "a.html").write_text('<h1>Unleash your potential</h1>\n<img src="x.png">\n<div class="bg-gradient-to-r from-indigo-500 to-purple-600 backdrop-blur">\n')
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "b.css").write_text("a{transition: all .3s}")
    secret = tmp_path.parent / "outside.html"
    secret.write_text("<h1>Lorem ipsum</h1>")
    (tmp_path / "link.html").symlink_to(secret)
    result = guides.scan(tmp_path)
    ids = {(c["id"], c["where"]) for c in result["clues"]}
    assert ("hype-copy", "a.html:1") in ids and ("img-without-alt", "a.html:2") in ids and ("indigo-violet-default", "a.html:3") in ids and ("glass-blur", "a.html:3") in ids
    assert not any("node_modules" in w or "link.html" in w for _, w in ids)
    with pytest.raises(ValueError):
        guides.scan(tmp_path, "../")
    assert "not findings" in guides.render_scan(result)


def good(**over):
    base = {"title": "t", "observed": "o", "inferred": "i", "impact": "x", "confidence": "high", "evidence": "app.js:2", "recommendation": "r", "verification": "not run: click it"}
    return {**base, **over}


def test_anchor_check_accepts_real_anchors_and_flags_invented_ones(tmp_path):
    ws, shots = tmp_path / "ws", tmp_path / "shots"
    ws.mkdir()
    shots.mkdir()
    (ws / "app.js").write_text("a\nb\nc\n")
    (shots / "shot-001.png").write_bytes(b"x")
    data = {"findings": [good(), good(evidence="shot-001.png"), good(evidence="supplied: design.fig notes")], "limits": ["no interaction"]}
    res = guides.check_findings(data, ws, shots)
    assert res["ok"] and res["anchored"] == 3 and res["checked"] == "evidence-anchors"
    bad = {"findings": [good(evidence="shot-009.png"), good(evidence="app.js:99"), good(evidence="../ws2/app.js"), good(evidence=""), good(confidence="sure"), good(observed="")]}
    res = guides.check_findings(bad, ws, shots)
    assert not res["ok"] and res["anchored"] == 2  # only the two with a real app.js:2
    joined = " ".join(res["problems"])
    assert "shot-009.png" in joined and "app.js:99" in joined and "limits" in joined and "confidence" in joined and "observed" in joined
    assert not guides.check_findings({}, ws, shots)["ok"]


def _wait(client, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        item = client.get(f"/api/jobs/{job_id}").json()
        if item["status"] != "running":
            return item
        time.sleep(0.2)
    raise AssertionError("job did not finish")


@requires_runtime
def test_a_designer_job_loads_guides_runs_the_scan_and_gets_its_anchors_checked(client, workspace, script):
    (workspace / "page.html").write_text("<h1>Seamlessly supercharge</h1>\n<img src=a.png>\n")
    findings = {"verdict": "block", "reasons": ["hype copy"], "limits": ["no interaction; deployed not verified"], "findings": [good(evidence="page.html:1"), good(evidence="shot-042.png")]}
    script([
        tool_call("g", "role_guide", {"name": "evidence"}),
        tool_call("s", "design_scan", {}),
        text("done ```json " + json.dumps(findings) + " ```"),
    ])
    item = _wait(client, client.post("/api/jobs", json={"goal": "review", "workspace": str(workspace), "role": "designer"}).json()["id"])
    events = client.get(f"/api/sessions/{item['session']}/events").json()["events"]
    out = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in events if e["type"] == "ToolResult"}
    assert "Evidence rules" in json.dumps(out["g"]["output"]) and not out["g"]["is_error"]
    assert "page.html:1" in json.dumps(out["s"]["output"]) and "CLUES" in json.dumps(out["s"]["output"])
    check = item["result"]["evidence_check"]
    assert check["checked"] == "evidence-anchors" and check["anchored"] == 1 and not check["ok"]
    assert any("shot-042.png" in p for p in check["problems"])
    assert "role_guide" in item["host_tools_outside_jail"] if item.get("host_tools_outside_jail") else True


@requires_runtime
def test_a_pm_job_cannot_load_the_designer_guide(client, workspace, script):
    script([tool_call("g", "role_guide", {"name": "designer-critique"}), tool_call("d", "design_scan", {}), text('x {"verdict": "pass", "reasons": []}')])
    item = _wait(client, client.post("/api/jobs", json={"goal": "review", "workspace": str(workspace), "role": "pm"}).json()["id"])
    events = client.get(f"/api/sessions/{item['session']}/events").json()["events"]
    out = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in events if e["type"] == "ToolResult"}
    assert out["g"]["is_error"] and out["d"]["is_error"]
    assert item["result"]["evidence_check"]["ok"] is False  # no findings were returned: reported, not hidden


def test_roles_and_contract_defaults_are_unchanged_otherwise():
    assert jobs.VERDICT_ROLES["pm"] == ["verdict", "reasons"]
    assert "role_guide" in jobs.ROLES["pm"] and "design_scan" not in jobs.ROLES["pm"] and "design_scan" in jobs.ROLES["designer"]


def test_review_loop_lists_need_the_weakest_honest_anchor(tmp_path):
    ws, shots = tmp_path / "ws", tmp_path / "shots"
    ws.mkdir()
    shots.mkdir()
    (ws / "app.js").write_text("a\nb\n")
    (ws / "design.md").write_text("spec")
    (shots / "shot-001.png").write_bytes(b"x")
    (shots / "deploy-001.png").write_bytes(b"x")
    ok = {
        "findings": [good(id="F1")], "limits": ["x"],
        "inventory": [
            {"feature": "add note", "status": "present", "evidence": "shot-001.png"},
            {"feature": "export", "status": "in code only", "evidence": "app.js:2"},
            {"feature": "sharing", "status": "absent", "searched": "grep share in src, page text"},
            {"feature": "sso", "status": "unknown", "reason": "needs a login"},
        ],
        "design_diffs": [{"design": "design.md", "built": "shot-001.png", "differs": "button is dark, spec says blue"}],
        "deployed": [{"state": "seen", "evidence": "deploy-001.png"}, {"state": "not verified", "reason": "URL not listed"}],
        "rechecks": [{"id": "P1", "status": "fixed", "evidence": "shot-001.png"}, {"id": "P2", "status": "not-run", "reason": "app would not start"}],
    }
    res = guides.check_findings(ok, ws, shots, {"P1", "P2"})
    assert res["ok"], res["problems"]
    assert res["loop"] == {"inventory": 4, "design_diffs": 1, "deployed": 2, "rechecks": 2}
    bad = {
        "findings": [good()], "limits": ["x"],
        "inventory": [{"feature": "a", "status": "present", "evidence": "app.js:1"}, {"feature": "b", "status": "present", "evidence": "shot-009.png"}, {"feature": "c", "status": "absent"}, {"feature": "d", "status": "maybe"}, {"feature": "e", "status": "in code only", "evidence": "app.js:50"}],
        "design_diffs": [{"design": "nothing.md", "built": "shot-001.png", "differs": ""}],
        "deployed": [{"state": "seen", "evidence": "shot-001.png"}, {"state": "seen", "evidence": "supplied:"}],
        "rechecks": [{"id": "ZZ", "status": "fixed", "evidence": "app.js:1"}],
    }
    problems = " | ".join(guides.check_findings(bad, ws, shots, {"P1"})["problems"])
    for needle in ("inventory 0", "inventory 1", "inventory 2", "inventory 3", "inventory 4", "design_diffs 0", "deployed 0", "deployed 1", "rechecks 0"):
        assert needle in problems, (needle, problems)
    assert "`present` needs a screenshot this job made" in problems and "not a finding id from a job this one was handed" in problems
    assert guides.check_findings({"findings": [good()], "limits": ["x"]}, ws, shots)["loop"] == {}  # lists are optional


@requires_runtime
def test_authority_is_checked_before_a_job_starts_and_tools_exist_only_with_it(client, workspace, script):
    bad = [
        {"role": "developer", "interact": {"origin": "http://127.0.0.1:45999"}},
        {"role": "pm", "interact": {"origin": "https://example.com"}},
        {"role": "pm", "interact": {}},
        {"role": "pm", "deployed": {"urls": ["http://example.com"]}},
        {"role": "pm", "deployed": {"urls": ["https://127.0.0.1/"]}},
        {"role": "pm", "deployed": {"urls": ["https://localhost/"]}},
    ]
    for extra in bad:
        r = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), **extra})
        assert r.status_code >= 400, extra
    script([tool_call("a", "interact_page", {}), tool_call("b", "inspect_deployed", {"url": "https://example.com"}), text('x {"verdict": "pass", "reasons": []}')])
    item = _wait(client, client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "pm"}).json()["id"])
    events = client.get(f"/api/sessions/{item['session']}/events").json()["events"]
    out = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in events if e["type"] == "ToolResult"}
    assert out["a"]["is_error"] and out["b"]["is_error"] and "visual_authority" not in item


@requires_runtime
def test_a_pm_job_operates_the_synthetic_app_and_its_rechecks_are_anchored(client, workspace, script):
    import sys

    pytest.importorskip("playwright")
    sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "app"))
    import server as fixture

    srv, port = fixture.start()
    try:
        save = {"role": "button", "name": "Save"}
        steps = [{"action": "fill", "target": {"label": "Note"}, "value": "hi"}, {"action": "click", "target": save, "shot": True}, {"action": "wait_for_text", "value": "Saved.", "shot": True}]
        first = {"verdict": "block", "reasons": ["no empty-state guidance"], "limits": ["one flow, one viewport"], "findings": [good(id="F1", evidence="shot-002.png")], "inventory": [{"feature": "add a note", "status": "present", "evidence": "shot-002.png"}]}
        script([tool_call("i", "interact_page", {"steps": steps, "width": 600, "height": 400}), text("done ```json " + json.dumps(first) + " ```")])
        job = client.post("/api/jobs", json={"goal": "walk the app", "workspace": str(workspace), "role": "pm", "interact": {"origin": f"http://127.0.0.1:{port}", "allow_writes": True}}).json()
        assert job["visual_authority"]["interact"]["allow_writes"] is True
        item = _wait(client, job["id"])
        assert item["result"]["evidence_check"]["ok"], item["result"]["evidence_check"]
        assert fixture.State.notes == ["hi"]
        recheck = {"verdict": "pass", "reasons": [], "limits": ["one flow"], "findings": [], "rechecks": [{"id": "F1", "status": "fixed", "evidence": "shot-001.png"}, {"id": "F9", "status": "fixed", "evidence": "shot-001.png"}]}
        script([tool_call("i", "interact_page", {"steps": [{"action": "screenshot"}]}), text("done ```json " + json.dumps(recheck) + " ```")])
        second = _wait(client, client.post("/api/jobs", json={"goal": "recheck", "workspace": str(workspace), "role": "pm", "handoff_from": [job["id"]], "interact": {"origin": f"http://127.0.0.1:{port}"}}).json()["id"])
        problems = second["result"]["evidence_check"]["problems"]
        assert any("F9" in p for p in problems) and not any("F1" in p and "rechecks" in p for p in problems), problems
    finally:
        srv.shutdown()


def test_a_passing_verdict_names_the_optional_checks_that_were_not_done(tmp_path):
    from tura_code import guides

    (tmp_path / "a.py").write_text("x = 1\n")
    data = {"findings": [{"id": "F1", "what": "w", "evidence": "a.py:1", "severity": "low", "confidence": "low", "fix": "f"}], "limits": ["no live app"]}
    out = guides.check_findings(data, tmp_path, tmp_path / "none", set())
    assert set(out["not_performed"]) == {"inventory", "design_diffs", "deployed", "rechecks"}
    assert "does not mean any finding is true" in out["meaning"]
