"""The real slice, no paid model: Skye's dispatcher drives the vendored Root server (its actual
HTTP API, its actual job machinery) with Root's scripted model standing in for the provider.

What this proves: contract -> policy -> catalogue -> adapter -> a REAL Root job that REALLY writes
the file through Root's tool path -> the settled job result back in Skye -> the policy's test gate
-> lifecycle events. What it does not prove: any real model's behavior (scripted by design) and
isolation (no jail in this test; that is Root's own test matrix's job).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT_SERVER = Path(__file__).resolve().parents[1] / "root" / "server"
if str(ROOT_SERVER) not in sys.path:
    sys.path.insert(0, str(ROOT_SERVER))

from tura_code import launcher  # noqa: E402


def _runtime_available() -> bool:
    try:
        launcher.find_runtime_binary()
        return True
    except launcher.RuntimeNotFound:
        return False


requires_runtime = pytest.mark.skipif(not _runtime_available(), reason="kimi-agent runtime binary not found")


def tool_call(call_id: str, name: str, arguments: dict) -> str:
    return "tool_call: " + json.dumps({"id": call_id, "name": name, "arguments": json.dumps(arguments)})


def text(value: str) -> str:
    return "text: " + value


@pytest.fixture
def root_server(tmp_path, monkeypatch):
    """The vendored Root, served over a real socket, fully scripted (no provider, no cost)."""
    home = tmp_path / "root-home"
    monkeypatch.setenv("TURA_CODE_HOME", str(home))
    monkeypatch.setenv("TURA_CODE_OFFLINE", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TURA_CODE_TOKEN", raising=False)
    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        tool_call("w", "WriteFile", {"path": "hello.py", "content": "print('hello from skye')\n"}),
        text('Wrote it. {"summary": "hello.py written"}'),  # the echo DSL is line-based: one line, inline JSON
    ]))
    monkeypatch.setenv(launcher.SCRIPTED_ECHO_ENV, str(script))

    import uvicorn  # noqa: PLC0415
    from tura_code.app import create_app  # noqa: PLC0415

    app = create_app(home)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("vendored Root did not start")
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("SKYE_ROOT_URL", url)
    monkeypatch.setenv("SKYE_ROOT_HOME", str(home))
    yield url, home
    server.should_exit = True
    thread.join(15)


@requires_runtime
def test_a_contract_runs_a_real_root_job_through_its_gates(root_server, tmp_path):
    from skye.dispatcher import dispatch
    from skye.events import FileBridge
    from skye.policy import Policy

    url, home = root_server
    repo = tmp_path / "ws"
    repo.mkdir()
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "developer"}],
                     "test_command": "python3 -c \"import pathlib; assert 'hello from skye' in pathlib.Path('hello.py').read_text()\""})
    log = tmp_path / "events.jsonl"
    outcome = dispatch(
        {"goal": "write hello.py printing hello from skye", "repo": str(repo), "role": "developer",
         "adapter": "root", "gates": ["tests"], "contract_keys": ["summary"], "budget": {"max_steps": 8}},
        policy, bridge=FileBridge(log),
        runner=None)  # no test double: the real RootAdapter
    assert outcome.status == "done", outcome.reason
    # The file exists in the repo because Root's runtime really wrote it.
    assert "hello from skye" in (repo / "hello.py").read_text()
    # The worker output is the settled Root job, not a "job started" receipt.
    result = json.loads(outcome.worker_output)
    assert result["status"] == "finished" and result["contract"]["ok"]
    job_record = json.loads((home / "jobs" / f"{result['job']}.json").read_text())
    assert job_record["status"] == "finished" and job_record["role"] == "developer"
    # The lifecycle events landed: accepted, the passing tests gate, done.
    kinds = [json.loads(line)["kind"] for line in log.read_text().splitlines()]
    assert kinds == ["accepted", "gate", "done"], kinds


@requires_runtime
def test_a_worker_that_breaks_its_contract_fails_the_dispatch(root_server, tmp_path, monkeypatch):
    from skye.dispatcher import dispatch
    from skye.events import FileBridge
    from skye.policy import Policy

    url, _home = root_server
    # Re-script: the model answers without the required JSON block, so Root's contract check fails.
    script = tmp_path / "script2.json"
    script.write_text(json.dumps([text("no json here")]))
    monkeypatch.setenv(launcher.SCRIPTED_ECHO_ENV, str(script))
    repo = tmp_path / "ws"
    repo.mkdir()
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "explorer"}],
                     "test_command": "true"})
    outcome = dispatch(
        {"goal": "summarise", "repo": str(repo), "role": "explorer", "adapter": "root",
         "gates": ["tests"], "contract_keys": ["summary"]},
        policy, bridge=FileBridge(tmp_path / "events.jsonl"))
    assert outcome.status == "failed" and "contract" in outcome.worker_output
    # The tests gate never ran: the worker failing ends the dispatch before gates.
    assert outcome.gates == []


@requires_runtime
def test_review_sendback_repairs_and_passes_through_real_root(root_server, tmp_path, monkeypatch):
    """The PM review loop for real, no mock runner: an implementation step dispatched to the
    vendored Root; a REAL reviewer job (second Root process) requests changes; the SAME step is
    redispatched with the feedback in its brief; the repair lands; a second real reviewer passes.
    The scripted model is re-scripted per job from the test seam (the model is scaffolding);
    everything else - contracts, gates, journal, Root's job machinery - is the real path."""
    import skye.dispatcher as dispatcher
    import skye.mission as mission_mod
    from skye.mission import load_mission, run_mission
    from skye.policy import Policy

    url, home = root_server
    repo = tmp_path / "ws"
    repo.mkdir()
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "issues.json"))

    scripts = {
        ("developer", 1): [tool_call("w1", "WriteFile", {"path": "cart.py",
                                                          "content": "def total(items):\n    return sum(i['price'] for i in items)\n"}),
                            text('done {"summary": "cart total, v1"}')],
        ("developer", 2): [tool_call("w2", "WriteFile", {"path": "cart.py",
                                                          "content": "def total(items):\n    return sum(i['price'] for i in items) if items else 0\n"}),
                            text('fixed {"summary": "empty cart returns 0"}')],
        ("reviewer", 1): [text('Reviewed. {"verdict": "request-changes", "feedback": ["empty cart must return 0"]}')],
        ("reviewer", 2): [text('Reviewed again. {"verdict": "pass"}')],
    }
    calls: dict[str, int] = {"developer": 0, "reviewer": 0}

    def write_script(role: str) -> None:
        calls[role] += 1
        path = tmp_path / f"script-{role}-{calls[role]}.json"
        path.write_text(json.dumps(scripts[(role, calls[role])]))
        monkeypatch.setenv(launcher.SCRIPTED_ECHO_ENV, str(path))

    real_dispatch = dispatcher.dispatch

    def per_job_scripting(contract, *args, **kwargs):
        write_script(contract["role"])  # swap the script BEFORE Root launches this job
        return real_dispatch(contract, *args, **kwargs)

    # Both dispatch entry points route through the seam: mission's bound name (worker jobs)
    # and dispatcher's module global (the review gate's reviewer jobs).
    monkeypatch.setattr(mission_mod, "dispatch", per_job_scripting)
    monkeypatch.setattr(dispatcher, "dispatch", per_job_scripting)

    mission_path = tmp_path / "mission.json"
    mission_path.write_text(json.dumps({
        "id": "m-review", "goal": "cart totals", "repo": str(repo),
        "max_review_rounds": 3,
        "steps": [{"id": "build", "role": "developer", "adapter": "root",
                   "goal": "write cart.py with a total()", "gates": ["review"],
                   "contract_keys": ["summary"], "budget": {"max_steps": 6}}]}))
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "*"}],
                     "review_role": "reviewer"})
    state_dir = tmp_path / ".skye"
    outcome = run_mission(load_mission(mission_path), policy, runner=None, state_dir=state_dir)
    assert outcome.status == "done", outcome.reason
    assert calls == {"developer": 2, "reviewer": 2}

    # Four real Root jobs ran: two implementations, two reviews.
    jobs = [json.loads(p.read_text()) for p in (home / "jobs").glob("*.json")]
    dev_jobs = sorted((j for j in jobs if j["role"] == "developer"), key=lambda j: j["started"])
    review_jobs = sorted((j for j in jobs if j["role"] == "reviewer"), key=lambda j: j["started"])
    assert len(dev_jobs) == 2 and len(review_jobs) == 2
    # The redo's brief really carried the reviewer's feedback into the worker's job.
    assert "empty cart must return 0" in dev_jobs[1]["goal"]
    # The repaired file is what the reviewer approved.
    assert "else 0" in (repo / "cart.py").read_text()
    # The loop is journaled: one review-round with feedback, then a settled done.
    journal = [json.loads(line)
               for line in (state_dir / "missions/m-review/journal.jsonl").read_text().splitlines()]
    kinds = [(e["step"], e["event"]) for e in journal]
    assert ("build", "review-round") in kinds
    assert kinds[-1] == ("build", "settled")
    assert journal[-1]["status"] == "done"


class SimulatedCrash(Exception):
    """The process 'dies' right after the review-round journal entry is fsync'd."""


@requires_runtime
def test_crash_after_review_sendback_recovers_through_real_root(root_server, tmp_path, monkeypatch):
    """Root + crash repair loop, the previously outstanding combination: real worker job, real
    reviewer job requesting changes, then a CRASH after the review-round entry is journaled but
    before the redo starts. The resume (a fresh run_mission over the journal alone) re-seeds the
    feedback into the redo's brief, the repair runs as a NEW real Root job, a second real
    reviewer passes. Phase 1 and phase 2 both go through the vendored Root binary."""
    import skye.dispatcher as dispatcher
    import skye.mission as mission_mod
    from skye.mission import load_mission, run_mission
    from skye.policy import Policy

    url, home = root_server
    repo = tmp_path / "ws"
    repo.mkdir()
    monkeypatch.setenv("SKYE_BACKLOG", str(tmp_path / "backlog.json"))
    monkeypatch.setenv("SKYE_ROLES", str(tmp_path / "roles.json"))
    monkeypatch.setenv("SKYE_ISSUES", str(tmp_path / "issues.json"))

    scripts = {
        ("developer", 1): [tool_call("w1", "WriteFile", {"path": "cart.py",
                                                          "content": "def total(items):\n    return sum(i['price'] for i in items)\n"}),
                            text('done {"summary": "cart total, v1"}')],
        ("developer", 2): [tool_call("w2", "WriteFile", {"path": "cart.py",
                                                          "content": "def total(items):\n    return sum(i['price'] for i in items) if items else 0\n"}),
                            text('fixed {"summary": "empty cart returns 0"}')],
        ("reviewer", 1): [text('Reviewed. {"verdict": "request-changes", "feedback": ["empty cart must return 0"]}')],
        ("reviewer", 2): [text('Reviewed again. {"verdict": "pass"}')],
    }
    calls: dict[str, int] = {"developer": 0, "reviewer": 0}

    def per_job_scripting(contract, *args, **kwargs):
        role = contract["role"]
        calls[role] += 1
        path = tmp_path / f"crash-script-{role}-{calls[role]}.json"
        path.write_text(json.dumps(scripts[(role, calls[role])]))
        monkeypatch.setenv(launcher.SCRIPTED_ECHO_ENV, str(path))
        return real_dispatch(contract, *args, **kwargs)

    real_dispatch = dispatcher.dispatch
    monkeypatch.setattr(mission_mod, "dispatch", per_job_scripting)
    monkeypatch.setattr(dispatcher, "dispatch", per_job_scripting)

    # Crash seam: the journal append for the review-round lands (fsync'd), then the run dies.
    real_append = mission_mod.Journal.append
    crashed_once = {"done": False}

    def append_then_crash(self, step, event, **fields):
        real_append(self, step, event, **fields)
        if event == "review-round" and not crashed_once["done"]:
            crashed_once["done"] = True
            raise SimulatedCrash("process died right after journaling the send-back")

    monkeypatch.setattr(mission_mod.Journal, "append", append_then_crash)

    mission_path = tmp_path / "mission.json"
    mission_path.write_text(json.dumps({
        "id": "m-crash", "goal": "cart totals", "repo": str(repo),
        "max_review_rounds": 3,
        "steps": [{"id": "build", "role": "developer", "adapter": "root",
                   "goal": "write cart.py with a total()", "gates": ["review"],
                   "contract_keys": ["summary"], "budget": {"max_steps": 6}}]}))
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "*"}],
                     "review_role": "reviewer"})
    state_dir = tmp_path / ".skye"

    # Phase 1: real worker job, real reviewer job, send-back journaled, then the crash.
    with pytest.raises(SimulatedCrash):
        run_mission(load_mission(mission_path), policy, runner=None, state_dir=state_dir)
    assert calls == {"developer": 1, "reviewer": 1}
    journal_path = state_dir / "missions/m-crash/journal.jsonl"
    entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
    assert [e["event"] for e in entries].count("review-round") == 1
    assert not any(e["event"] == "settled" for e in entries)
    assert "sum(i['price']" in (repo / "cart.py").read_text()

    # Phase 2: resume from the journal alone. The redo is a NEW real Root job whose brief
    # carries the reviewer's feedback; the second real reviewer passes.
    monkeypatch.setattr(mission_mod.Journal, "append", real_append)
    outcome = run_mission(load_mission(mission_path), policy, runner=None, state_dir=state_dir)
    assert outcome.status == "done", outcome.reason
    assert calls == {"developer": 2, "reviewer": 2}

    jobs = [json.loads(p.read_text()) for p in (home / "jobs").glob("*.json")]
    dev_jobs = sorted((j for j in jobs if j["role"] == "developer"), key=lambda j: j["started"])
    assert len(dev_jobs) == 2
    assert "empty cart must return 0" in dev_jobs[1]["goal"]  # feedback survived the crash
    assert "else 0" in (repo / "cart.py").read_text()
    entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
    assert [e["event"] for e in entries].count("dispatched") == 2  # original + redo, no more
    assert entries[-1]["event"] == "settled" and entries[-1]["status"] == "done"
