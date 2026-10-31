"""Jobs: role scope, contract check, and limits, with the scripted model (no provider, no cost)."""

from __future__ import annotations

import time

from conftest import requires_runtime, text, tool_call

from tura_code import catalogue, jobs


def test_contract_reads_the_last_json_block_and_names_missing_keys():
    reply = 'Done.\n```json\n{"summary": "x", "files_changed": []}\n```'
    assert jobs.check_contract(reply, ["summary", "files_changed"])["ok"]
    bad = jobs.check_contract(reply, ["summary", "checks_run"])
    assert not bad["ok"] and bad["missing"] == ["checks_run"]
    assert not jobs.check_contract("no json here", ["summary"])["ok"]
    assert jobs.check_contract("anything", [])["ok"]


def test_roles_with_a_code_runner_are_not_called_a_boundary():
    from types import SimpleNamespace

    tenant = SimpleNamespace(settings=SimpleNamespace(get=lambda: SimpleNamespace(tool_scope=[])))
    for role, scope in jobs.ROLES.items():
        level = catalogue.guarantee(tenant, SimpleNamespace(tool_scope=scope))["level"]
        assert level == ("tool-names" if role in ("explorer", "reviewer", "designer", "pm") else "none"), role


def _wait(client, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        item = client.get(f"/api/jobs/{job_id}").json()
        if item["status"] == "running":
            time.sleep(0.2)
            continue
        return item
    raise AssertionError("job did not finish")


@requires_runtime
def test_job_runs_unattended_with_contract_and_scope(client, workspace, script):
    script([text('Looked. {"summary": "readme only"}')])
    created = client.post("/api/jobs", json={"goal": "summarise the repo", "workspace": str(workspace), "role": "explorer", "contract": ["summary"]})
    assert created.status_code == 200, created.text
    assert created.json()["scope_guarantee"]["level"] == "tool-names"
    item = _wait(client, created.json()["id"])
    assert item["result"]["contract"]["ok"] and item["result"]["contract"]["data"]["summary"] == "readme only"


@requires_runtime
def test_job_scope_refuses_tools_outside_the_role(client, workspace, script):
    script([tool_call("p", "process", {"action": "list"}), text('{"summary": "s"}')])
    item = _wait(client, client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "reviewer", "contract": ["summary"]}).json()["id"])
    assert item["status"] != "running" and item["result"]["contract"]["ok"]
    events = client.get(f"/api/sessions/{item['session']}/events").json()["events"]
    refused = [e for e in events if e["type"] == "ToolResult" and e["payload"].get("tool_call_id") == "p"]
    # Either the runtime never offered `process` to a reviewer, or the host refused it: it must not have run.
    assert not refused or refused[0]["payload"]["return_value"]["is_error"]


def test_unknown_role_and_empty_goal_are_refused(client, workspace):
    assert client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "wizard"}).status_code == 400
    assert client.post("/api/jobs", json={"goal": " ", "workspace": str(workspace)}).status_code == 400


def _fake_tenant(root):
    from types import SimpleNamespace

    return SimpleNamespace(root=root)


def test_a_job_running_at_restart_is_marked_interrupted_not_lost(tmp_path):
    import json

    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "abc.json").write_text(json.dumps({"id": "abc", "role": "developer", "status": "running", "session": "s1"}))
    (tmp_path / "jobs" / "def.json").write_text(json.dumps({"id": "def", "role": "tester", "status": "finished", "session": "s2"}))
    store = jobs.Jobs(_fake_tenant(tmp_path))
    assert store.items["abc"]["status"] == "interrupted" and "restarted" in store.items["abc"]["note"]
    assert store.items["def"]["status"] == "finished"
    assert json.loads((tmp_path / "jobs" / "abc.json").read_text())["status"] == "interrupted"  # persisted, not just in memory


def test_handoff_passes_reported_contract_data_only_and_rejects_bad_refs(tmp_path):
    import pytest

    store = jobs.Jobs(_fake_tenant(tmp_path))
    store.items["a"] = {"id": "a", "role": "developer", "status": "finished", "result": {"reply": "LONG TRANSCRIPT", "contract": {"data": {"summary": "did x"}}}}
    store.items["r"] = {"id": "r", "role": "tester", "status": "running"}
    store.items["n"] = {"id": "n", "role": "tester", "status": "finished", "result": {"contract": {"data": None}}}
    text = store._handoff(["a"])
    assert "did x" in text and "LONG TRANSCRIPT" not in text and "unverified" in text
    for bad in (["zzz"], ["r"], ["n"]):
        with pytest.raises(jobs.JobError):
            store._handoff(bad)


@requires_runtime
def test_native_token_cap_stops_a_job_through_the_real_runtime(client, workspace):
    from mock_provider import MockProvider

    calls = [{"tool_calls": [{"name": "find_tools", "arguments": {"query": "pdf"}}]}] * 6  # each reply reports 15 tokens
    with MockProvider(calls) as mock:
        client.put("/api/settings", json={"base_url": mock.base_url, "openrouter_api_key": "sk-test", "model": "mock/m", "dynamic_tools": True})
        job = client.post("/api/jobs", json={"goal": "keep searching", "workspace": str(workspace), "role": "developer", "budget": {"max_tokens": 20}}).json()
        item = _wait(client, job["id"], timeout=60)
    assert item["status"] == "stopped" and item["stopped_by"] == "token_budget", item
    assert len(mock.requests) <= 3  # 15 tokens after call 1, 30 after call 2 (over 20): no third step planned


@requires_runtime
def test_native_wall_clock_stops_a_job_through_the_real_runtime(client, workspace):
    from mock_provider import MockProvider

    calls = [{"delay": 1.2, "tool_calls": [{"name": "find_tools", "arguments": {"query": "pdf"}}]}] * 10
    with MockProvider(calls) as mock:
        client.put("/api/settings", json={"base_url": mock.base_url, "openrouter_api_key": "sk-test", "model": "mock/m", "dynamic_tools": True})
        job = client.post("/api/jobs", json={"goal": "keep searching", "workspace": str(workspace), "role": "developer", "budget": {"timeout_seconds": 2}}).json()
        item = _wait(client, job["id"], timeout=60)
    assert item["status"] == "stopped" and item["stopped_by"] in ("timeout", "timeout_backstop"), item
    assert len(mock.requests) < 10


@requires_runtime
def test_native_cost_cap_stops_a_job_when_the_provider_reports_cost(client, workspace):
    from mock_provider import MockProvider

    calls = [{"cost": 0.04, "tool_calls": [{"name": "find_tools", "arguments": {"query": "pdf"}}]}] * 6
    with MockProvider(calls) as mock:
        client.put("/api/settings", json={"base_url": mock.base_url, "openrouter_api_key": "sk-test", "model": "mock/m", "dynamic_tools": True})
        job = client.post("/api/jobs", json={"goal": "keep searching", "workspace": str(workspace), "role": "developer", "budget": {"max_cost_usd": 0.05}}).json()
        item = _wait(client, job["id"], timeout=60)
    assert item["status"] == "stopped" and item["stopped_by"] == "cost_budget", item
    assert item["budget_used"]["cost_used_usd"] >= 0.08 and len(mock.requests) <= 3  # $0.04 after call 1, $0.08 after call 2 (over $0.05)


@requires_runtime
def test_cost_cap_is_blind_when_the_provider_reports_no_cost(client, workspace):
    from mock_provider import MockProvider

    calls = [{"tool_calls": [{"name": "find_tools", "arguments": {"query": "pdf"}}]}] * 3 + [{"text": "done"}]
    with MockProvider(calls) as mock:
        client.put("/api/settings", json={"base_url": mock.base_url, "openrouter_api_key": "sk-test", "model": "mock/m", "dynamic_tools": True})
        job = client.post("/api/jobs", json={"goal": "search", "workspace": str(workspace), "role": "developer", "budget": {"max_cost_usd": 0.01}}).json()
        item = _wait(client, job["id"], timeout=60)
    assert item["status"] != "stopped"  # no cost reported, so the dollar cap never fires: do not rely on it for such providers


@requires_runtime
def test_unattended_job_cannot_read_outside_its_workspace(client, workspace, tmp_path, script):
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("TOP-SECRET-VALUE")
    script([tool_call("r", "ReadFile", {"path": str(secret)}), text("done")])
    job = client.post("/api/jobs", json={"goal": "read it", "workspace": str(workspace), "role": "reviewer"}).json()
    item = _wait(client, job["id"])
    events = client.get(f"/api/sessions/{item['session']}/events").json()["events"]
    import json as _json

    assert "TOP-SECRET-VALUE" not in _json.dumps(events)
    result = [e for e in events if e["type"] == "ToolResult" and e["payload"].get("tool_call_id") == "r"]
    assert result and result[0]["payload"]["return_value"]["is_error"]
    # And inside the workspace the same read works.
    (workspace / "ok.txt").write_text("INSIDE")
    script([tool_call("r2", "ReadFile", {"path": str(workspace / "ok.txt")}), text("done")])
    item2 = _wait(client, client.post("/api/jobs", json={"goal": "read ok", "workspace": str(workspace), "role": "reviewer"}).json()["id"])
    events2 = client.get(f"/api/sessions/{item2['session']}/events").json()["events"]
    assert "INSIDE" in _json.dumps(events2)


def test_verdict_contract_is_shape_only_and_says_so():
    ok = jobs.check_contract('{"verdict": "block", "reasons": ["cards overflow"]}', ["verdict", "reasons"])
    assert ok["ok"] and ok["checked"] == "shape"
    for bad in ('{"verdict": "maybe", "reasons": ["x"]}', '{"verdict": "block", "reasons": []}', '{"verdict": "block"}'):
        assert not jobs.check_contract(bad, ["verdict", "reasons"])["ok"]
    assert jobs.check_contract('{"verdict": "pass", "reasons": []}', ["verdict", "reasons"])["ok"]


def test_designer_and_pm_can_inspect_but_have_no_code_runner():
    from types import SimpleNamespace

    tenant = SimpleNamespace(settings=SimpleNamespace(get=lambda: SimpleNamespace(tool_scope=[])))
    for role in ("designer", "pm"):
        assert "inspect_page" in jobs.ROLES[role]
        assert catalogue.guarantee(tenant, SimpleNamespace(tool_scope=jobs.ROLES[role]))["level"] == "tool-names"
        assert jobs.VERDICT_ROLES[role] == ["verdict", "reasons"]


@requires_runtime
def test_designer_job_inspects_a_page_through_the_gateway_and_returns_a_verdict(client, workspace, script):
    import shutil

    pytest = __import__("pytest")
    pytest.importorskip("playwright")
    from pathlib import Path

    shutil.copy(Path(__file__).parent / "fixtures" / "site" / "index.html", workspace / "index.html")
    client.put("/api/settings", json={"dynamic_tools": True})
    script([
        tool_call("i", "use_tool", {"name": "inspect_page", "arguments": {"path": "index.html", "width": 900, "height": 600}}),
        text('Cards fine, red bar overflows. {"verdict": "block", "reasons": ["page overflows horizontally"]}'),
    ])
    item = _wait(client, client.post("/api/jobs", json={"goal": "review the page", "workspace": str(workspace), "role": "designer"}).json()["id"], timeout=60)
    assert item["result"]["contract"]["ok"] and item["result"]["contract"]["data"]["verdict"] == "block"
    assert item["result"]["contract"]["checked"] == "shape"
    events = client.get(f"/api/sessions/{item['session']}/events").json()["events"]
    shown = [e for e in events if e["type"] == "ToolResult" and e["payload"].get("tool_call_id") == "i"]
    assert shown and not shown[0]["payload"]["return_value"]["is_error"]
    import json as _json

    assert "Fixture shop" in _json.dumps(shown[0]["payload"]["return_value"]["output"])


@requires_runtime
def test_the_host_starts_and_stops_the_app_a_designer_job_looks_at(client, workspace, script):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (workspace / "index.html").write_text("<title>t</title><body>served")
    script([text('Looked at it. {"verdict": "pass", "reasons": []}')])
    created = client.post("/api/jobs", json={
        "goal": "look at the app", "workspace": str(workspace), "role": "designer",
        "serve": {"command": f"python3 -m http.server {port} --bind 127.0.0.1", "port": port, "ready_seconds": 15},
    })
    assert created.status_code == 200, created.text
    item = _wait(client, created.json()["id"])
    assert item["serve"]["ready"] and item["serve"]["stopped"], item
    with socket.socket() as s:
        assert s.connect_ex(("127.0.0.1", port)) != 0  # stopped: nothing listens any more


@requires_runtime
def test_an_app_that_never_listens_fails_the_job_and_is_cleaned_up(client, workspace, script):
    script([text("never reached")])
    created = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "designer", "serve": {"command": "sleep 60", "port": 45999, "ready_seconds": 2}})
    item = _wait(client, created.json()["id"])
    assert item["status"].startswith("error") and item["serve"]["ready"] is False
    import subprocess

    assert subprocess.run(["pgrep", "-f", "sleep 60"], capture_output=True).returncode != 0  # the stalled app was killed


def _free_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening(port):
    import socket

    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def test_stopping_kills_children_that_outlive_the_leader_and_ownership_is_recorded(tmp_path):
    """The leader shell exits at once; the server it started stays in the group. Stop must still end it. This is the
    path cancellation, the timeout backstop and host shutdown all take (the stop runs shielded in the job's finally)."""
    import asyncio
    from types import SimpleNamespace

    port = _free_port()
    (tmp_path / "run.sh").write_text(f"#!/bin/sh\npython3 -m http.server {port} --bind 127.0.0.1 &\nsleep 0.3\n")  # leader exits, child stays
    j = jobs.Jobs(SimpleNamespace(root=tmp_path / "data"))
    item = {"id": "x", "serve": {}}

    async def go():
        serve = jobs.Serve(command="sh run.sh", port=port, ready_seconds=10)
        proc, url, problem = await j._serve(serve, tmp_path, item)
        assert problem is None, problem
        assert item["serve"]["port_owned_by_spawned_process"] is True and item["serve"]["listens_beyond_loopback"] is False
        assert _listening(port)
        await asyncio.sleep(0.6)  # the leader has exited by now
        assert proc.returncode is not None and _listening(port)
        await asyncio.shield(j._stop(proc))

    asyncio.run(go())
    time.sleep(0.3)
    assert not _listening(port), "a child of the app survived stop"


@requires_runtime
def test_a_port_someone_else_holds_is_not_taken_for_ours(client, workspace, script):
    import http.server
    import threading

    port = _free_port()
    other = http.server.ThreadingHTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        script([text("never")])
        item = _wait(client, client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "designer", "serve": {"command": "sleep 30", "port": port, "ready_seconds": 3}}).json()["id"])
        assert item["serve"]["ready"] is False and "already in use" in item["serve"]["problem"]
    finally:
        other.shutdown()
    assert not _listening(port) or True


def test_a_squatter_that_grabs_the_port_after_our_command_starts_is_detected(tmp_path):
    """Readiness is not ownership: a process outside the spawned group on the port must not count as the app."""
    import socket
    import subprocess

    from tura_code import jobs

    port = _free_port()
    squatter = subprocess.Popen(["python3", "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=tmp_path, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        end = time.time() + 5
        while time.time() < end and not _listening(port):
            time.sleep(0.1)
        mine = subprocess.Popen(["sleep", "20"], start_new_session=True)
        try:
            info = jobs.listener_info(port, mine.pid)
            assert info["listening"] and info["owned"] is False
            assert jobs.listener_info(port, squatter.pid)["owned"] is True
            assert info["exposed"] is False
        finally:
            mine.kill()
    finally:
        squatter.kill()
    assert socket  # imported for clarity of intent


def test_orphaned_app_of_a_dead_host_is_killed_on_recovery_only_if_the_command_matches(tmp_path):
    import json
    import subprocess

    from tura_code import jobs

    proc = subprocess.Popen(["sleep", "47"], start_new_session=True)
    decoy = subprocess.Popen(["sleep", "48"], start_new_session=True)  # a recycled pid: other command line
    try:
        d = tmp_path / "jobs"
        d.mkdir()
        for name, p, argv in (("a", proc, ["sleep", "47"]), ("b", decoy, ["sleep", "47"])):
            (d / f"{name}.json").write_text(json.dumps({"id": name, "role": "designer", "status": "running", "serve": {"pgid": p.pid, "argv": argv}}))
        from types import SimpleNamespace

        jobs.Jobs(SimpleNamespace(root=tmp_path))
        time.sleep(1.2)
        assert proc.poll() is not None, "the orphaned app was left running"
        assert decoy.poll() is None, "a process with a different command line was killed"
    finally:
        proc.kill()
        decoy.kill()


@requires_runtime
def test_cancelling_a_running_served_job_stops_the_app_and_its_children(client, workspace):
    """Real runtime, local mock provider that answers slowly so the job is still running when it is cancelled."""
    from mock_provider import MockProvider

    port = _free_port()
    (workspace / "run.sh").write_text(f"#!/bin/sh\npython3 -m http.server {port} --bind 127.0.0.1 &\nwait\n")  # server is a child of the shell
    with MockProvider([{"delay": 12, "text": "late"}]) as mock:
        client.put("/api/settings", json={"base_url": mock.base_url, "openrouter_api_key": "sk-test-mock", "model": "mock/model"})
        created = client.post("/api/jobs", json={"goal": "look", "workspace": str(workspace), "role": "designer", "serve": {"command": "sh run.sh", "port": port, "ready_seconds": 15}})
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        end = time.time() + 15
        item = {}
        while time.time() < end:
            item = client.get(f"/api/jobs/{job_id}").json()
            if (item.get("serve") or {}).get("ready"):
                break
            time.sleep(0.1)
        assert item["serve"]["ready"] and item["status"] == "running", item
        assert item["serve"]["port_owned_by_spawned_process"] is True and _listening(port)
        end = time.time() + 10
        while time.time() < end and not mock.requests:  # the model call is in flight (and slow)
            time.sleep(0.1)
        assert mock.requests, "the runtime never called the model"
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
        done = _wait(client, job_id, timeout=30)
    assert done["status"] != "running" and done["stopped_by"] == "cancelled", done
    assert done["serve"]["stopped"] is True
    time.sleep(0.5)
    assert not _listening(port), "the app (a child of the spawned shell) outlived the cancelled job"
