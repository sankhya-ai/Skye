"""Checkpoints: a snapshot of the workspace before each turn, and restoring one."""

from __future__ import annotations

import asyncio
import subprocess

from conftest import of_type, requires_runtime, run_turn, text, tool_call

from tura_code.sessions.checkpoints import Checkpoints


def test_restore_puts_back_changed_deleted_and_removes_created_files(tmp_path, workspace):
    (workspace / "keep.txt").write_text("v1\n")
    (workspace / "gone.txt").write_text("I will be deleted\n")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "dep.js").write_text("ignored\n")
    checkpoints = Checkpoints(tmp_path / "cp", workspace)

    async def scenario():
        before = await checkpoints.snapshot("Before: change things")
        assert before is not None
        same = await checkpoints.snapshot("Before: nothing changed")
        assert same is not None and same.id == before.id

        (workspace / "keep.txt").write_text("v2\n")
        (workspace / "gone.txt").unlink()
        (workspace / "src" / "deep").mkdir(parents=True)
        (workspace / "src" / "deep" / "new.py").write_text("print('new')\n")
        (workspace / "node_modules" / "dep.js").write_text("changed, but not ours\n")

        changes = {c["path"]: c["status"] for c in await checkpoints.changes_since(before.id)}
        assert changes == {"keep.txt": "M", "gone.txt": "D", "src/deep/new.py": "A"}

        result = await checkpoints.restore(before.id)
        assert sorted(result["restored"]) == ["gone.txt", "keep.txt"]
        assert result["removed"] == ["src/deep/new.py"]
        assert (workspace / "keep.txt").read_text() == "v1\n"
        assert (workspace / "gone.txt").exists()
        assert not (workspace / "src").exists(), "folders left empty by the restore go too"
        assert (workspace / "node_modules" / "dep.js").read_text() == "changed, but not ours\n"

        # The restore is itself undoable.
        await checkpoints.restore(result["undo"]["id"])
        assert (workspace / "keep.txt").read_text() == "v2\n"
        assert not (workspace / "gone.txt").exists()
        assert (workspace / "src" / "deep" / "new.py").exists()
        labels = [c.label for c in await checkpoints.list()]
        assert labels[0].startswith("Before restoring") and "Before: change things" in labels

    asyncio.run(scenario())


def test_the_projects_own_git_is_never_touched(tmp_path, workspace):
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base"], cwd=workspace, check=True)
    (workspace / ".gitignore").write_text("secret.env\n")
    (workspace / "secret.env").write_text("TOKEN=1\n")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True).stdout
    checkpoints = Checkpoints(tmp_path / "cp", workspace)

    async def scenario():
        before = await checkpoints.snapshot("Before: edit")
        (workspace / "a.txt").write_text("a\n")
        changes = await checkpoints.changes_since(before.id)
        assert [c["path"] for c in changes] == ["a.txt"], "ignored files are not in a checkpoint"
        await checkpoints.restore(before.id)

    asyncio.run(scenario())
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True).stdout == head
    status = subprocess.run(["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True).stdout
    assert sorted(status.split()) == [".gitignore", "??", "??", "README.md"], "nothing staged in the project's index"
    assert (workspace / "secret.env").read_text() == "TOKEN=1\n"


def _session(client, workspace) -> str:
    response = client.post("/api/sessions", json={"workspace": str(workspace)})
    assert response.status_code == 200, response.text
    return response.json()["id"]


@requires_runtime
def test_a_turn_can_be_undone_from_the_api(client, workspace, script):
    script(
        [
            "\n".join(
                [
                    tool_call("c1", "WriteFile", {"path": "made.py", "content": "x = 1\n"}),
                    tool_call("c2", "StrReplaceFile", {"path": "README.md", "edit": [{"old": "# demo", "new": "# changed"}]}),
                ]
            ),
            text("Done."),
        ]
    )
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "make a file and edit the readme")
    checkpoint = of_type(events, "Checkpoint")[0]["payload"]
    assert of_type(events, "TurnFinished")[-1]["payload"]["checkpoint"] == checkpoint["id"]
    assert (workspace / "made.py").exists()

    listed = client.get(f"/api/sessions/{session_id}/checkpoints").json()["checkpoints"]
    assert listed[0]["id"] == checkpoint["id"] and listed[0]["label"] == "Before: make a file and edit the readme"
    changes = client.get(f"/api/sessions/{session_id}/checkpoints/{checkpoint['id']}/changes").json()["changes"]
    assert sorted(c["path"] for c in changes) == ["README.md", "made.py"]

    response = client.post(f"/api/sessions/{session_id}/rollback", json={"checkpoint": checkpoint["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["removed"] == ["made.py"] and response.json()["restored"] == ["README.md"]
    assert not (workspace / "made.py").exists()
    assert (workspace / "README.md").read_text() == "# demo\n"


@requires_runtime
def test_slash_rollback_undoes_the_latest_turn(client, workspace, script):
    script([tool_call("c1", "WriteFile", {"path": "README.md", "content": "overwritten\n"}), text("ok")])
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        run_turn(ws, "overwrite the readme")
        assert (workspace / "README.md").read_text() == "overwritten\n"
        ws.send_json({"type": "prompt", "text": "/rollback"})
        while True:
            message = ws.receive_json()
            if message["type"] == "event" and message["event"]["type"] == "Rollback":
                break
    assert (workspace / "README.md").read_text() == "# demo\n"


@requires_runtime
def test_plan_mode_takes_no_checkpoint(client, workspace, script):
    script([text("Here is a plan.")])
    session_id = _session(client, workspace)
    client.patch(f"/api/sessions/{session_id}", json={"permission_mode": "plan"})
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "plan something")
    assert of_type(events, "Checkpoint") == []
    # The turn says it was a plan, so only it — not a turn before plan mode was chosen — is offered for approval.
    assert of_type(events, "TurnFinished")[0]["payload"]["mode"] == "plan"
    assert client.post(f"/api/sessions/{session_id}/rollback", json={}).status_code == 409
