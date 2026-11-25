"""The Instinct adapter against a stub job server: auth, polling, safe file write-back."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from skye.adapters import get
from skye.adapters.base import AdapterError
from skye.adapters.instinct import InstinctAdapter


def stub(files, statuses=("running", "finished"), token="tok"):
    seen = {"posts": [], "polls": 0}
    states = list(statuses)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code); self.send_header("content-length", str(len(data))); self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("authorization") != f"Bearer {token}":
                return self._send(401, {})
            if self.path == "/health":
                return self._send(200, {"ok": True})
            seen["polls"] += 1
            st = states.pop(0) if len(states) > 1 else states[0]
            self._send(200, {"id": "j1", "status": st, "reply": "done", "files": files if st == "finished" else {}})

        def do_POST(self):
            if self.headers.get("authorization") != f"Bearer {token}":
                return self._send(401, {})
            seen["posts"].append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            self._send(200, {"id": "j1", "status": "queued"})

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", seen


def contract(tmp_path):
    (tmp_path / "business-profile.md").write_text("# Cafe\n", "utf-8")
    return {"repo": str(tmp_path), "goal": "write day 1", "role": "designer"}


def test_runs_and_writes_files(tmp_path):
    url, seen = stub({"drafts/day-1.md": "hook"})
    a = InstinctAdapter(url, token="tok", poll_seconds=0.01)
    assert a.available()
    r = a.run(contract(tmp_path))
    assert r.ok and r.job_id == "j1"
    assert (tmp_path / "drafts" / "day-1.md").read_text() == "hook"
    assert seen["posts"][0]["files"] == {"business-profile.md": "# Cafe\n"}


def test_refuses_paths_outside_workspace(tmp_path):
    url, _ = stub({"../evil.md": "x", "/abs.md": "x", "drafts/ok.md": "y"}, statuses=("finished",))
    r = InstinctAdapter(url, token="tok", poll_seconds=0.01).run(contract(tmp_path))
    assert not r.ok and "evil" in r.output
    assert not (tmp_path.parent / "evil.md").exists()


def test_bad_token_and_unset(tmp_path):
    url, _ = stub({})
    assert not InstinctAdapter(url, token="wrong").available()
    with pytest.raises(AdapterError):
        InstinctAdapter("", token="").run(contract(tmp_path))


def test_registered():
    assert get("instinct").name == "instinct"
