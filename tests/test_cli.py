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
    # Hide any real `claude` on this machine: the test proves the not-installed path and
    # must never start a real (billable) session.
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
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


def _say_state(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)


def _posted_bodies(state_dir, mission="m1"):
    import json as _json
    path = state_dir / "state" / "missions" / mission / "discussion.jsonl"
    if not path.exists():
        return []
    return [_json.loads(line)["body"] for line in path.read_text().splitlines() if line.strip()]


def test_say_body_never_contains_flag_values(tmp_path, monkeypatch, capsys):
    _say_state(tmp_path, monkeypatch)
    assert main(["say", "m1", "--as", "developer", "--kind", "note",
                 "api", "v2", "decided"]) == 0
    assert _posted_bodies(tmp_path) == ["api v2 decided"]
    capsys.readouterr()


def test_say_flags_after_body_words_and_repeated_flag_last_wins(tmp_path, monkeypatch, capsys):
    _say_state(tmp_path, monkeypatch)
    assert main(["say", "m1", "hello", "world", "--as", "nobody", "--as", "developer"]) == 0
    out = capsys.readouterr().out
    assert '"posted": true' in out
    import json as _json
    path = tmp_path / "state" / "missions" / "m1" / "discussion.jsonl"
    entry = _json.loads(path.read_text().splitlines()[0])
    assert entry["body"] == "hello world"
    assert entry["author"] == "developer"


def test_say_rejects_unknown_option_without_posting(tmp_path, monkeypatch, capsys):
    _say_state(tmp_path, monkeypatch)
    assert main(["say", "m1", "--as", "developer", "--require-ack", "tester", "hi"]) == 2
    assert "unknown option --require-ack" in capsys.readouterr().err
    assert _posted_bodies(tmp_path) == []


def test_say_rejects_missing_option_value(tmp_path, monkeypatch, capsys):
    _say_state(tmp_path, monkeypatch)
    assert main(["say", "m1", "--kind", "note", "hi", "--as"]) == 2
    assert "--as needs a value" in capsys.readouterr().err
    assert _posted_bodies(tmp_path) == []


def test_say_double_dash_protects_dashed_body_text(tmp_path, monkeypatch, capsys):
    _say_state(tmp_path, monkeypatch)
    assert main(["say", "m1", "--as", "developer", "--", "--force-with-lease", "is",
                 "off"]) == 0
    assert _posted_bodies(tmp_path) == ["--force-with-lease is off"]
    capsys.readouterr()


def test_say_requires_a_body(tmp_path, monkeypatch, capsys):
    _say_state(tmp_path, monkeypatch)
    assert main(["say", "m1", "--as", "developer", "--kind", "note"]) == 2
    assert "usage: skye say" in capsys.readouterr().err
    assert _posted_bodies(tmp_path) == []


def test_say_ack_needed_list_still_parses(tmp_path, monkeypatch, capsys):
    _say_state(tmp_path, monkeypatch)
    assert main(["say", "m1", "--as", "developer", "--kind", "contract-change",
                 "--ack-needed", "tester,backend-dev", "schema", "changed"]) == 0
    import json as _json
    path = tmp_path / "state" / "missions" / "m1" / "discussion.jsonl"
    entry = _json.loads(path.read_text().splitlines()[0])
    assert entry["body"] == "schema changed"
    assert entry["requires_ack"] == ["tester", "backend-dev"]
    capsys.readouterr()


def test_hear_rejects_a_non_integer_since_without_traceback(tmp_path, monkeypatch, capsys):
    _say_state(tmp_path, monkeypatch)
    main(["say", "m1", "--as", "developer", "one"])
    capsys.readouterr()
    assert main(["hear", "m1", "--since", "abc"]) == 2
    assert "integers" in capsys.readouterr().err


def test_hear_rejects_extra_positionals_and_unknown_flags(tmp_path, monkeypatch, capsys):
    _say_state(tmp_path, monkeypatch)
    assert main(["hear", "m1", "m2"]) == 2
    assert main(["hear", "m1", "--limit"]) == 2
    capsys.readouterr()


def test_ack_parses_note_flag_and_rejects_unknown_option(tmp_path, monkeypatch, capsys):
    _say_state(tmp_path, monkeypatch)
    main(["say", "m1", "--as", "developer", "--kind", "contract-change",
          "--ack-needed", "tester", "schema", "changed"])
    out = capsys.readouterr().out
    import json as _json, re as _re
    msg_id = _re.search(r'"id": "([^"]+)"', out).group(1)
    # --note takes ONE value; extra words are a usage error, not a silent note merge.
    assert main(["ack", "m1", msg_id, "--as", "tester", "--note", "adapting", "now"]) == 2
    assert main(["ack", "m1", msg_id, "--as", "tester", "--note", "adapting"]) == 0
    assert main(["ack", "m1", msg_id, "--as", "tester", "--bogus", "x"]) == 2
    assert "unknown option --bogus" in capsys.readouterr().err
