"""Review-mandated multi-repo edge cases: name/path safety, proposal scoping, claims
scoping, true cross-repo parallelism, and partial-failure resume without rerunning
settled work."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from skye import workspaces
from skye.adapters.base import Result
from skye.mission import MissionError, load_mission, run_mission
from skye.policy import Policy
from tests.test_multirepo import two_repos, two_repo_mission, writer
from tests.test_workspaces import make_repo


ON = Policy({"workspaces": {"enabled": True},
             "allow_unattended": [{"adapter": "claude-code", "role": "*"}],
             "test_command": "true"})
# Parallel-wave tests need the policy's concurrency cap lifted past 1 (the honest default
# serializes waves); the cap is doing its job in the serial case, so these lift it explicitly.
ON4 = Policy({"workspaces": {"enabled": True},
              "allow_unattended": [{"adapter": "claude-code", "role": "*"}],
              "test_command": "true", "max_concurrent": 4})


def load(tmp: Path, mission: dict) -> dict:
    path = tmp / "m.json"
    path.write_text(json.dumps(mission))
    return load_mission(path)


def test_repo_name_validation(tmp_path):
    backend, frontend = two_repos(tmp_path)
    for bad in ("../evil", "/abs", "a/b", "a\\b", ".."):
        m = two_repo_mission("m-n", backend, frontend)
        m["repos"][bad] = str(frontend)
        with pytest.raises(MissionError, match="not a plain label"):
            load(tmp_path / bad.replace("/", "_").replace("\\", "_"), m) if False else \
                load(tmp_path, m)


def test_duplicate_realpath_rejected(tmp_path):
    backend, frontend = two_repos(tmp_path)
    link = tmp_path / "link-to-backend"
    link.symlink_to(backend)
    m = {"id": "m-dup", "goal": "x",
         "repos": {"backend": str(backend), "sneaky": str(link)},
         "steps": [{"id": "s", "role": "developer", "adapter": "claude-code",
                    "goal": "g", "repo": "backend", "gates": []}]}
    with pytest.raises(MissionError, match="same path"):
        load(tmp_path, m)


def test_subdirs_of_one_repo_rejected(tmp_path):
    mono = tmp_path / "mono"
    (mono / "backend").mkdir(parents=True)
    (mono / "frontend").mkdir()
    from tests.test_workspaces import git
    git(mono, "init", "-q"); git(mono, "config", "user.email", "t@t.t")
    git(mono, "config", "user.name", "t")
    (mono / "README").write_text("x"); git(mono, "add", "-A"); git(mono, "commit", "-qm", "i")
    m = {"id": "m-sub", "goal": "x",
         "repos": {"backend": str(mono / "backend"), "frontend": str(mono / "frontend")},
         "steps": [{"id": "s", "role": "developer", "adapter": "claude-code",
                    "goal": "g", "repo": "backend", "gates": []}]}
    with pytest.raises(MissionError, match="subdirectories of the same git repository"):
        load(tmp_path, m)


def test_default_repo_same_realpath_as_named_deduped(tmp_path):
    backend, frontend = two_repos(tmp_path)
    m = two_repo_mission("m-co", backend, frontend)
    m["repo"] = str(backend) + "/."  # same repo, different spelling
    state = tmp_path / "state"
    rec = workspaces.prepare(m, state_dir=state, policy=ON)
    # one worktree for backend, not two; no second branch collision
    names = [k for k, v in rec["repos"].items() if v.get("worktree")]
    assert names.count("backend") == 1 and "default" not in names


def _proposal_mission(tmp_path, backend, frontend):
    m = two_repo_mission("m-prop", backend, frontend)
    m["steps"] = [{"id": "pm", "role": "pm", "adapter": "claude-code",
                   "goal": "plan", "repo": "backend", "gates": []}]
    return m


def test_worker_proposal_cannot_escape_declared_repos(tmp_path):
    backend, frontend = two_repos(tmp_path)
    outside = make_repo(tmp_path, "outside")
    # one output, one proposal list: the escape attempt and the legitimate follow-up together
    proposals = json.dumps({"propose_steps": [
        {"id": "evil", "role": "developer", "adapter": "claude-code",
         "goal": "write outside the owner's repos", "repo": str(outside), "needs": ["pm"],
         "gates": []},
        {"id": "ui2", "role": "developer", "adapter": "claude-code",
         "goal": "follow-up in the frontend", "repo": "frontend", "needs": ["pm"],
         "gates": []}]})
    outputs = iter([proposals, "done"])
    ran = []
    def runner(spec):
        ran.append(spec["goal"])
        return Result(ok=True, output=next(outputs, "done"), command=["fake"])
    out = run_mission(_proposal_mission(tmp_path, backend, frontend), ON, runner=runner,
                      state_dir=tmp_path / "state", catalogues_dir="catalogues")
    assert out.status == "done"
    assert "write outside the owner's repos" not in ran     # escaped proposal never ran
    assert "follow-up in the frontend" in ran               # declared-repo proposal ran
    journal = (tmp_path / "state" / "missions" / "m-prop" / "journal.jsonl").read_text()
    assert "proposal-rejected" in journal and "literal repo path" in journal


def test_same_relative_filename_in_distinct_repos_does_not_block(tmp_path):
    backend, frontend = two_repos(tmp_path)
    m = two_repo_mission("m-claims", backend, frontend)
    for step in m["steps"]:
        step["needs"] = []
        step["claims"] = ["src/index.ts"]  # identical relative path, different repos
    ran = []
    out = run_mission(m, ON4, runner=lambda s: ran.append(s["repo"]) or writer(s),
                      state_dir=tmp_path / "state", catalogues_dir="catalogues")
    assert out.status == "done" and len(ran) == 2  # neither held back
    journal = (tmp_path / "state" / "missions" / "m-claims" / "journal.jsonl").read_text()
    assert "claim-held" not in journal


def test_same_file_same_repo_still_blocks(tmp_path):
    backend, frontend = two_repos(tmp_path)
    m = two_repo_mission("m-conf", backend, frontend)
    m["steps"][1]["repo"] = "backend"  # both steps now write the same repo
    for step in m["steps"]:
        step["needs"] = []
        step["claims"] = ["src/index.ts"]
    journal_path = tmp_path / "state" / "missions" / "m-conf" / "journal.jsonl"
    out = run_mission(m, ON4, runner=writer, state_dir=tmp_path / "state",
                      catalogues_dir="catalogues")
    assert out.status == "done"  # serialized by the claims gate, not blocked forever
    assert "claim-held" in journal_path.read_text()  # the hold actually fired


def test_parallel_independent_steps_across_repos_actually_overlap(tmp_path):
    backend, frontend = two_repos(tmp_path)
    m = two_repo_mission("m-par", backend, frontend)
    for step in m["steps"]:
        step["needs"] = []  # independent: same wave
    barrier = threading.Barrier(2, timeout=10)
    def runner(spec):
        barrier.wait()  # deadlocks unless both steps run concurrently
        return writer(spec)
    out = run_mission(m, ON4, runner=runner, state_dir=tmp_path / "state",
                      catalogues_dir="catalogues")
    assert out.status == "done"  # proves the wave ran both repos' agents concurrently


def test_partial_failure_resume_reruns_only_the_failed_repo_step(tmp_path):
    """Cross-repo partial progress: the settled backend step's effects are never re-run. An
    interrupted frontend step pauses for the operator (no blind repeat of unknown partial
    effects), and the explicit redispatch reruns exactly that step. A settled FAILURE stays
    settled: the journal refuses to re-attempt it without the operator's reset."""
    from skye.mission import Journal
    backend, frontend = two_repos(tmp_path)
    m = two_repo_mission("m-partial", backend, frontend)
    state = tmp_path / "state"
    journal = Journal(state / "missions" / "m-partial" / "journal.jsonl")
    # backend settled; the process died with the frontend step mid-flight
    journal.append("api", "dispatched", fingerprint="x")
    journal.append("api", "settled", status="done", reason="", output="endpoint added",
                   role="developer", gates=[])
    journal.append("ui", "dispatched", fingerprint="y")
    calls = []
    def runner(spec):
        calls.append(spec["goal"])
        return Result(ok=True, output="done", command=["fake"])
    paused = run_mission(m, ON, runner=runner, state_dir=state,
                         catalogues_dir="catalogues")
    assert paused.status == "interrupted" and calls == []   # nothing reran on assumption
    outcome = run_mission(m, ON, runner=runner, state_dir=state,
                          catalogues_dir="catalogues", redispatch="ui")
    assert outcome.status == "done"
    assert calls == ["call the endpoint"]   # only the redispatched repo's step ran
