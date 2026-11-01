"""The CLI: the console entry point that the README promises, plus approve/mission plumbing."""

from __future__ import annotations

import json

import pytest

from skye import approvals
from skye.cli import main


def _job(tmp_path, **overrides):
    contract = {"goal": "add the endpoint", "repo": str(tmp_path), "role": "developer",
                "adapter": "claude-code"}
    contract.update(overrides)
    path = tmp_path / "job.json"
    path.write_text(json.dumps(contract))
    return path


def test_console_entry_point_runs_without_argv(tmp_path, monkeypatch, capsys):
    # pyproject's [project.scripts] skye = "skye.cli:main" calls main() with no arguments.
    job = _job(tmp_path)
    monkeypatch.setattr("sys.argv", ["skye", "check", str(job)])
    assert main() == 0
    assert '"role": "developer"' in capsys.readouterr().out


def test_check_rejects_a_broken_contract(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert main(["check", str(bad)]) == 2
    assert "invalid contract" in capsys.readouterr().err


def test_run_reports_needs_owner_with_the_next_step(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SKYE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SKYE_APPROVALS", str(tmp_path / "approvals.json"))
    monkeypatch.chdir(tmp_path)
    job = _job(tmp_path)
    assert main(["run", str(job)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "needs-owner" and out["fingerprint"]


def test_approve_then_run_roundtrip(tmp_path, monkeypatch, capsys):
    store = tmp_path / "approvals.json"
    monkeypatch.setenv("SKYE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SKYE_APPROVALS", str(store))
    monkeypatch.chdir(tmp_path)
    job = _job(tmp_path)
    assert main(["approve", str(job)]) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["approved"] and len(approvals.pending(store)) == 1
    # The run reaches the worker stage now - and fails honestly because claude is not installed
    # here, instead of bouncing back to needs-owner.
    assert main(["run", str(job)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "failed" and "not installed" in out["reason"]
    assert approvals.pending(store) == []  # consumed


def test_approvals_lists_the_store(tmp_path, monkeypatch, capsys):
    store = tmp_path / "approvals.json"
    monkeypatch.setenv("SKYE_APPROVALS", str(store))
    job = _job(tmp_path)
    approvals.approve(json.loads(job.read_text()), store)
    assert main(["approvals"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1
