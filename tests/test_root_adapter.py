"""The Root adapter against a stub Root API: auth, polling to settled, honest status mapping."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from skye.adapters.base import AdapterError
from skye.adapters.root import RootAdapter


class StubRoot:
    """A minimal stand-in for Root's owner API: records requests, scripts the job's lifecycle."""

    def __init__(self, statuses: list[dict], token: str = "tok"):
        self.statuses = list(statuses)
        self.token = token
        self.requests: list[dict] = []
        handler = self._handler()
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _handler(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _auth_ok(self):
                return self.headers.get("authorization") == f"Bearer {stub.token}"

            def _json(self, code: int, body: dict):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if not self._auth_ok():
                    return self._json(401, {"detail": "unauthorized"})
                if self.path == "/api/jobs/roles":
                    return self._json(200, {"roles": {}})
                if self.path.startswith("/api/jobs/"):
                    item = stub.statuses.pop(0) if len(stub.statuses) > 1 else stub.statuses[0]
                    return self._json(200, item)
                return self._json(404, {"detail": "nope"})

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                stub.requests.append({"path": self.path, "body": body,
                                      "auth": self.headers.get("authorization")})
                if not self._auth_ok():
                    return self._json(401, {"detail": "unauthorized"})
                return self._json(200, {"id": "j1", "status": "running"})

        return Handler

    def stop(self):
        self.server.shutdown()
        self.thread.join(5)


FINISHED = {"id": "j1", "status": "finished",
            "result": {"reply": "done", "error": None, "contract": {"ok": True}, "files_touched": ["a.py"]}}


def test_run_polls_until_settled_and_returns_the_result():
    stub = StubRoot([{"id": "j1", "status": "running"}, {"id": "j1", "status": "running"}, FINISHED])
    adapter = RootAdapter(stub.url, token="tok", poll_seconds=0.05)
    result = adapter.run({"goal": "g", "brief": "", "repo": "/tmp/ws", "role": "developer", "budget": {}})
    stub.stop()
    assert result.ok and "j1" in result.output
    start = stub.requests[0]
    assert start["auth"] == "Bearer tok" and start["body"]["credentials"] == []
    assert start["body"]["budget"]["max_steps"] == 25  # a default ceiling, never an uncapped worker
    assert start["body"]["workspace"] == "/tmp/ws"


def test_non_finished_states_are_not_success():
    stub = StubRoot([{"id": "j1", "status": "stopped", "result": {"reply": "", "error": None}}])
    adapter = RootAdapter(stub.url, token="tok", poll_seconds=0.05)
    assert not adapter.run({"goal": "g", "repo": "/tmp", "role": "developer"}).ok
    stub.stop()


def test_a_finished_job_with_a_failed_contract_is_not_success():
    item = {"id": "j1", "status": "finished", "result": {"reply": "no json", "error": None,
                                                         "contract": {"ok": False, "missing": ["summary"]}}}
    stub = StubRoot([item])
    adapter = RootAdapter(stub.url, token="tok", poll_seconds=0.05)
    result = adapter.run({"goal": "g", "repo": "/tmp", "role": "developer"})
    stub.stop()
    assert not result.ok and "contract" in result.output


def test_auth_failure_and_a_missing_server_are_loud():
    stub = StubRoot([FINISHED])
    bad = RootAdapter(stub.url, token="wrong", poll_seconds=0.05)
    assert not bad.available()
    with pytest.raises(AdapterError):
        bad.run({"goal": "g", "repo": "/tmp", "role": "developer"})
    stub.stop()
    down = RootAdapter("http://127.0.0.1:1", token="tok", poll_seconds=0.05)
    assert not down.available()
    with pytest.raises(AdapterError):
        down.run({"goal": "g", "repo": "/tmp", "role": "developer"})


def test_token_comes_from_the_root_home():
    import pathlib
    import tempfile

    with tempfile.TemporaryDirectory() as home:
        pathlib.Path(home, "token").write_text("from-file\n")
        assert RootAdapter("http://127.0.0.1:1", root_home=home).token == "from-file"


def test_the_owner_token_never_reaches_outputs_or_events(tmp_path):
    """The Root owner token is host-side authorization for the adapter only. It must appear in NO
    worker output, NO dispatch outcome, NO lifecycle event and NO error text - success or failure."""
    token = "tk-live-secret-9f8e7d"
    from skye.dispatcher import dispatch
    from skye.events import FileBridge
    from skye.policy import Policy

    # Success path against the stub.
    stub = StubRoot([{"id": "j1", "status": "running"}, FINISHED], token=token)
    log = tmp_path / "events.jsonl"
    policy = Policy({"allow_unattended": [{"adapter": "root", "role": "developer"}], "test_command": "true"})
    outcome = dispatch({"goal": "g", "repo": str(tmp_path), "role": "developer", "adapter": "root",
                        "gates": ["tests"]},
                       policy, bridge=FileBridge(log),
                       runner=lambda spec: __import__("skye.adapters.root", fromlist=["RootAdapter"])
                       .RootAdapter(stub.url, token=token, poll_seconds=0.05).run(spec))
    stub.stop()
    assert outcome.status == "done"
    haystack = outcome.worker_output + outcome.reason + log.read_text()
    assert token not in haystack

    # Failure path: a 401 (wrong/expired token) must not echo the token either.
    bad = RootAdapter(stub.url, token=token, poll_seconds=0.05)
    stub2 = StubRoot([FINISHED], token="other-token")
    bad = RootAdapter(stub2.url, token=token, poll_seconds=0.05)
    try:
        bad.run({"goal": "g", "repo": "/tmp", "role": "developer"})
        raise AssertionError("expected AdapterError")
    except AdapterError as error:
        assert token not in str(error)
    stub2.stop()
