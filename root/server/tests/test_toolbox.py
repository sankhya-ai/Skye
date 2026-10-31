"""The toolbox: install on demand into root's own Python, run code, hand back the files it made."""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

import pytest
from conftest import of_type, requires_runtime, run_turn, text, tool_call

from tura_code import hostos
from tura_code.toolbox.env import ToolboxError, check_requirements, requirement_name


def make_wheel(folder: Path) -> Path:
    """A real, installable wheel for a tiny package `rootdemo` (no network needed)."""
    files = {
        "rootdemo.py": "def shout(text):\n    return text.upper() + '!'\n",
        "rootdemo-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: rootdemo\nVersion: 1.0\n",
        "rootdemo-1.0.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    for name, body in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(body.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(body.encode())}")
    record.append("rootdemo-1.0.dist-info/RECORD,,")
    files["rootdemo-1.0.dist-info/RECORD"] = "\n".join(record) + "\n"
    folder.mkdir(parents=True, exist_ok=True)
    wheel = folder / "rootdemo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return wheel


@pytest.fixture
def local_index(tmp_path, monkeypatch):
    wheels = tmp_path / "wheels"
    make_wheel(wheels)
    for name, value in (("PIP_NO_INDEX", "1"), ("PIP_FIND_LINKS", str(wheels)), ("UV_NO_INDEX", "1"), ("UV_FIND_LINKS", str(wheels)), ("UV_OFFLINE", "1")):
        monkeypatch.setenv(name, value)
    return wheels


def test_only_plain_package_names_are_installed():
    assert check_requirements(["pandas", "reportlab>=4", "uvicorn[standard]", " "]) == ["pandas", "reportlab>=4", "uvicorn[standard]"]
    for bad in ("git+https://example.com/x.git", "-e .", "../evil", "pkg; rm -rf /", "https://x/y.whl", "--index-url=http://x"):
        with pytest.raises(ToolboxError):
            check_requirements([bad])
    assert requirement_name("Foo_Bar.baz[x]>=1") == "foo-bar-baz"


def _session(client, workspace) -> str:
    return client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]


@requires_runtime
def test_install_on_demand_run_and_hand_back_the_file(client, workspace, script, local_index, home):
    code = "import rootdemo, pathlib\npathlib.Path('report.txt').write_text(rootdemo.shout('ready'))\nprint('wrote it')\n"
    script(
        [
            tool_call("c1", "run_python", {"code": code, "packages": ["rootdemo"]}),
            tool_call("c2", "run_python", {"code": "import rootdemo; print(rootdemo.shout('again'))", "packages": ["rootdemo"]}),
            text("Your report is ready."),
        ]
    )
    session_id = _session(client, workspace)
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "make me a report", timeout=240)
    asked = [e["payload"]["description"] for e in of_type(events, "ApprovalRequest")]
    assert asked[0] == "Install into root's Python toolbox: pip install rootdemo"
    assert [a.split(":")[0] for a in asked[1:]] == ["Run Python", "Run Python"], "installed once, never again"
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    first = results["c1"]
    assert not first["is_error"], first
    assert "wrote it" in first["output"] and "scratch: report.txt" in first["output"]
    files = next(d for d in first["display"] if d["type"] == "files")["data"]["files"]  # the runtime keeps unknown kinds under `data`
    assert files == [{"path": "report.txt", "scope": "scratch", "size": 6}]
    assert "AGAIN!" in results["c2"]["output"]
    assert of_type(events, "TurnFinished")[-1]["payload"]["verified"] is True
    assert not (workspace / "report.txt").exists(), "deliverables go to scratch, not the project"

    listed = client.get(f"/api/sessions/{session_id}/outputs").json()["files"]
    assert [f["path"] for f in listed] == ["report.txt"]
    served = client.get(f"/api/sessions/{session_id}/file", params={"path": "report.txt"})
    assert served.text == "READY!" and served.headers["content-security-policy"] == "sandbox"
    assert hostos.venv_python(home / "toolbox" / "venv").exists(), "the toolbox lives in root's data, per person"


def test_files_are_served_safely(client, workspace):
    session_id = _session(client, workspace)
    scratch = client.app.state.root.server.tenant().sessions.get(session_id).scratch_dir
    scratch.mkdir(parents=True)
    (scratch / "chart.svg").write_text("<svg onload='alert(1)'/>")
    (workspace / "notes.txt").write_text("hello")
    svg = client.get(f"/api/sessions/{session_id}/file", params={"path": "chart.svg"})
    assert svg.headers["content-type"] == "application/octet-stream" and svg.headers["content-disposition"].startswith("attachment")
    assert client.get(f"/api/sessions/{session_id}/file", params={"path": "notes.txt", "scope": "workspace"}).text == "hello"
    assert client.get(f"/api/sessions/{session_id}/file", params={"path": "../../../settings.json"}).status_code == 404
    assert client.get(f"/api/sessions/{session_id}/file", params={"path": "x", "scope": "home"}).status_code == 400


@requires_runtime
def test_the_scratch_folder_is_the_sessions_own(client, workspace, script, home):
    session_id = _session(client, workspace)
    scratch = home / "sessions" / session_id / "scratch"
    script([tool_call("c1", "WriteFile", {"path": str(scratch / "notes.md"), "content": "draft\n"}), text("ok")])
    with client.websocket_connect(f"/api/sessions/{session_id}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "jot a note", approve=None)
    assert of_type(events, "ApprovalRequest") == [], "writing to its own scratch folder needs no approval"
    assert (scratch / "notes.md").read_text() == "draft\n"
