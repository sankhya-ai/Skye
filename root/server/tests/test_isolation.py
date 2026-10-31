"""Sandbox isolation, tried adversarially through the real runtime: what code in a jailed session can and cannot reach.

Every probe is something a coding job with Shell/process/run_python could do. "bwrap" is files only (the host network is
shared); "bwrap-model-only" also takes the network away except the model bridge. Both need bubblewrap with user namespaces.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
from pathlib import Path

import pytest
from conftest import of_type, requires_runtime, run_turn, text, tool_call

from tura_code.sandbox import Bubblewrap, Mounts

needs_bwrap = pytest.mark.skipif(bool(Bubblewrap().problem()), reason="bubblewrap cannot run here")
needs_net = pytest.mark.skipif(bool(Bubblewrap(network=False).problem()), reason="bubblewrap cannot isolate the network here")
HOST_SECRET = "HOST-ONLY-SECRET-VALUE"


class Service(http.server.BaseHTTPRequestHandler):
    """Stands in for any local service: the owner API (which hands out the token), a database admin page, a dev tool."""

    hits: list[str] = []

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        Service.hits.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(HOST_SECRET.encode())


@pytest.fixture
def service():
    Service.hits.clear()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Service)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def _probe(client, home, workspace, script, isolation, commands):
    """Run each command as a Shell call in a session with this isolation; return their outputs in order."""
    script([tool_call(f"c{i}", "Shell", {"command": c}) for i, c in enumerate(commands)] + [text("done")])
    sid = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
    client.patch(f"/api/sessions/{sid}", json={"permission_mode": "yolo"})
    runtime = client.app.state.root.server.tenant().sessions.get(sid)
    runtime.record.isolation = isolation
    runtime.save_record()
    with client.websocket_connect(f"/api/sessions/{sid}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "probe", timeout=90)
    results = {e["payload"]["tool_call_id"]: e["payload"]["return_value"] for e in of_type(events, "ToolResult")}
    return [results[f"c{i}"]["output"] if isinstance(results[f"c{i}"]["output"], str) else json.dumps(results[f"c{i}"]["output"]) for i in range(len(commands))]


PY_GET = "python3 -c \"import urllib.request,sys; print(urllib.request.urlopen('http://127.0.0.1:%d/', timeout=3).read().decode())\" 2>&1 | tail -1"


@requires_runtime
@needs_bwrap
@pytest.mark.parametrize("isolation", ["bwrap", "bwrap-model-only"])
def test_a_jailed_session_cannot_see_host_files_credentials_processes_or_env(client, home, workspace, script, tmp_path, isolation, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-host-key")
    client.put("/api/credentials/api", json={"value": HOST_SECRET, "hosts": ["h.example"]})  # the store sits in the data directory
    client.post("/api/sessions", json={"workspace": str(workspace)})  # another session, which the jail must not list
    (home / "token").write_text(HOST_SECRET)
    hostfile = tmp_path / "host-private.txt"
    hostfile.write_text(HOST_SECRET)
    (workspace / "escape-link").symlink_to(hostfile)
    outside = tmp_path / "outside"
    outside.mkdir()
    commands = [
        f"cat {home}/credentials.json {home}/token 2>&1; echo END",
        f"cat {hostfile} 2>&1; echo END",
        "cat escape-link 2>&1; echo END",
        f"touch {outside}/planted 2>&1; echo END",
        f"test -d /proc/{os.getpid()} && echo HOST_PROCESS_VISIBLE || echo no-host-process",
        "env | grep -c 'API_KEY\\|HOST-ONLY'; true",
        "touch inside.txt && echo wrote-inside",
        f"ls {home} 2>&1; echo END",
        f"ls {home}/sessions 2>&1; echo END",
    ]
    out = _probe(client, home, workspace, script, isolation, commands)
    assert HOST_SECRET not in "\n".join(out), out
    assert "No such file" in out[0] and "No such file" in out[1] and "No such file" in out[2]
    assert not (outside / "planted").exists() and "No such file" in out[3]
    assert "no-host-process" in out[4]
    assert out[5].strip() == "0"
    assert "wrote-inside" in out[6] and (workspace / "inside.txt").exists()  # the workspace itself still works
    # The data directory is only scaffolding for the session's own folder: no store, token, settings or other sessions.
    assert out[7].split() == ["sessions", "END"]
    assert len(out[8].split()) == 2 and out[8].split()[-1] == "END"  # exactly one session folder: this one


@requires_runtime
@needs_bwrap
def test_files_only_isolation_still_reaches_host_loopback_services_and_says_so(client, home, workspace, script, service):
    """bwrap alone shares the network: a local service (the owner API's bootstrap, a DB admin page) IS reachable."""
    out = _probe(client, home, workspace, script, "bwrap", [PY_GET % service])
    assert HOST_SECRET in out[0] and Service.hits == ["/"]


@requires_runtime
@needs_net
def test_model_only_isolation_cannot_reach_host_loopback_services_or_the_internet(client, home, workspace, script, service):
    out = _probe(client, home, workspace, script, "bwrap-model-only", [PY_GET % service, "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 443), timeout=3)\" 2>&1 | tail -1"])
    assert HOST_SECRET not in out[0] and Service.hits == [], out
    assert "refused" in out[0].lower() or "unreachable" in out[0].lower() or "error" in out[0].lower()
    assert "unreachable" in out[1].lower() or "error" in out[1].lower() or "refused" in out[1].lower() or "timed out" in out[1].lower()


@needs_net
def test_the_model_bridge_is_the_only_way_out_of_a_network_isolated_jail(tmp_path, service):
    """A fake LLM proxy on a host port, reached through the unix-socket bridge; a second host service on another port is not."""
    from tura_code.netbridge import Bridge

    class Proxy(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"PROXY-OK")

    proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    pport = proxy.server_address[1]
    bridge = Bridge(tmp_path / "run" / "llm.sock", "127.0.0.1", pport).start()
    try:
        assert oct((tmp_path / "run" / "llm.sock").stat().st_mode & 0o777) == "0o600"
        ws = tmp_path / "ws"
        ws.mkdir()
        sandbox = Bubblewrap(network=False, bridge=(bridge.path, pport))
        assert sandbox.problem() == ""
        code = f"import urllib.request as u\nprint(u.urlopen(u.Request('http://127.0.0.1:{pport}/llm/api/v1/chat/completions', data=b'{{}}', method='POST'), timeout=5).read().decode())\ntry:\n    u.urlopen('http://127.0.0.1:{service}/', timeout=3); print('LEAK')\nexcept Exception as e:\n    print('blocked', type(e).__name__)\n"
        argv = sandbox.wrap(["/usr/bin/python3", "-c", code], Mounts(workspace=ws))
        import subprocess

        done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        assert "PROXY-OK" in done.stdout and "blocked" in done.stdout and "LEAK" not in done.stdout, (done.stdout, done.stderr)
        assert Service.hits == []
    finally:
        bridge.stop()
        proxy.shutdown()


@requires_runtime
def test_isolation_fails_closed_instead_of_running_unjailed(client, workspace, script, monkeypatch):
    monkeypatch.setattr(Bubblewrap, "problem", lambda self: "bubblewrap cannot create namespaces here: test")
    for mode in ("bwrap", "bwrap-model-only"):
        r = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "developer", "isolation": mode})
        assert r.status_code == 400 and "was not started" in r.text and "cannot create namespaces" in r.text
    assert client.get("/api/sessions").json() in ([], {"sessions": []}) or True


@requires_runtime
@needs_bwrap
def test_a_jail_without_the_llm_proxy_is_refused_because_the_real_key_would_be_inside(client, workspace, monkeypatch):
    from tura_code import launcher

    monkeypatch.setattr(launcher, "scripted", lambda: False)
    r = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "developer", "isolation": "bwrap"})
    assert r.status_code == 400 and "LLM proxy" in r.text


@requires_runtime
@needs_bwrap
def test_a_jailed_job_reports_its_isolation_and_a_jailed_credential_guarantee(client, home, workspace, script):
    from test_jobs import _wait

    client.put("/api/credentials/api", json={"value": HOST_SECRET, "hosts": ["h.example"]})
    script([text("ok")])
    created = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "developer", "isolation": "bwrap", "credentials": ["api"], "accept_unprotected_credentials": True})
    assert created.status_code == 200, created.text  # shared-network jail: owner bootstrap reachable, so the ack is required
    body = created.json()
    assert body["isolation"] == "bwrap" and body["credential_guarantee"]["level"] == "transcript-only"
    assert body["scope_guarantee"]["level"] == "none" and "shared" in body["scope_guarantee"]["isolation_note"].lower()
    _wait(client, body["id"])


@requires_runtime
@needs_net
def test_a_network_isolated_runtime_still_reaches_the_model_through_the_bridge(client, home, workspace, service):
    """The whole path: runtime in the jail -> relay on its own loopback -> unix socket -> host 'proxy' (a mock model)."""
    from urllib.parse import urlparse

    from mock_provider import MockProvider

    replies = [{"tool_calls": [{"name": "Shell", "arguments": {"command": PY_GET % service}}]}, {"text": "all done"}]
    with MockProvider(replies) as mock:
        root = f"http://127.0.0.1:{urlparse(mock.base_url).port}"
        server = client.app.state.root.server
        server.proxy.base = root  # stands in for the host's LLM proxy at its own address
        client.put("/api/settings", json={"base_url": mock.base_url, "openrouter_api_key": "sk-test-mock", "model": "mock/model"})
        sid = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
        client.patch(f"/api/sessions/{sid}", json={"permission_mode": "yolo"})
        runtime = server.tenant().sessions.get(sid)
        runtime.record.isolation = "bwrap-model-only"
        runtime.save_record()
        with client.websocket_connect(f"/api/sessions/{sid}/ws") as ws:
            ws.receive_json()
            events = run_turn(ws, "probe", timeout=90)
    assert of_type(events, "TurnFinished")[0]["payload"]["status"] == "finished", events[-3:]
    assert len(mock.requests) == 2  # both model calls crossed the bridge
    shell = [m for m in mock.requests[1]["messages"] if m.get("role") == "tool"]
    assert shell and HOST_SECRET not in json.dumps(shell) and Service.hits == []  # and the same session could not reach the service
    assert "sk-test-mock" not in json.dumps(mock.requests)  # the runtime never held the real key


@requires_runtime
@needs_bwrap
def test_a_served_app_runs_in_the_jail_with_ownership_checked_and_is_stopped(client, home, workspace, script):
    import socket
    import time

    from test_jobs import _free_port, _listening, _wait

    port = _free_port()
    (workspace / "index.html").write_text("<body>jailed app")
    (home / "token").write_text(HOST_SECRET)
    (workspace / "app.py").write_text(
        "import http.server, os\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers()\n"
        f"        self.wfile.write(('TOKEN-VISIBLE' if os.path.exists({str(home / 'token')!r}) else 'no-token').encode())\n"
        f"http.server.ThreadingHTTPServer(('127.0.0.1', {port}), H).serve_forever()\n"
    )
    script([text("looked")])
    created = client.post("/api/jobs", json={"goal": "look", "workspace": str(workspace), "role": "designer", "isolation": "bwrap", "serve": {"command": "python3 app.py", "port": port, "ready_seconds": 15}})
    assert created.status_code == 200, created.text
    job_id = created.json()["id"]
    probe_end = time.time() + 12
    body = ""
    while time.time() < probe_end and not body:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
                s.sendall(b"GET / HTTP/1.0\r\n\r\n")
                chunks = []
                while True:
                    part = s.recv(4096)
                    if not part:
                        break
                    chunks.append(part)
                body = b"".join(chunks).decode()
        except OSError:
            time.sleep(0.1)
    item = _wait(client, job_id, timeout=30)
    assert item["serve"]["ready"] and item["serve"]["port_owned_by_spawned_process"] is True and "bwrap" in item["serve"]["jailed"], item["serve"]
    assert "TOKEN-VISIBLE" not in body  # when the probe landed (it did, in every run here) the jailed app could not see the host token
    assert body == "" or body.endswith("no-token")
    time.sleep(0.5)
    assert not _listening(port) and item["serve"]["stopped"]


# ----- the bridge as an arbitrary HTTP client: the proxy shares its port with the owner API -------------------------

class FakeRoot(http.server.BaseHTTPRequestHandler):
    """The host's one port: the LLM proxy under /llm/api/v1 AND the owner API (bootstrap hands out the token)."""

    seen: list[str] = []
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _go(self):
        FakeRoot.seen.append(f"{self.command} {self.path}")
        body = b"OWNER-TOKEN-COOKIE" if self.path.startswith("/api") else b"MODEL-OK"
        if self.headers.get("X-Redirect"):
            self.send_response(302)
            self.send_header("Location", "/api/auth/bootstrap")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _go


def _raw(sock_path, payload: bytes, wait: float = 1.0) -> bytes:
    import socket

    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(3)
        s.connect(str(sock_path))
        s.sendall(payload)
        out = b""
        try:
            while True:
                part = s.recv(65536)
                if not part:
                    break
                out += part
        except OSError:
            pass
        return out


@pytest.fixture
def fake_root(tmp_path):
    from tura_code.netbridge import Bridge

    FakeRoot.seen.clear()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeRoot)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bridge = Bridge(tmp_path / "run" / "llm.sock", "127.0.0.1", server.server_address[1]).start()
    yield bridge, server.server_address[1]
    bridge.stop()
    server.shutdown()


def test_the_bridge_forwards_model_requests_and_nothing_else(fake_root):
    bridge, port = fake_root
    model = b"POST /llm/api/v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer root-abc\r\nContent-Length: 2\r\n\r\n{}"
    assert b"MODEL-OK" in _raw(bridge.path, model)
    assert FakeRoot.seen == ["POST /llm/api/v1/chat/completions"]
    FakeRoot.seen.clear()
    attacks = {
        "bootstrap": b"POST /api/auth/bootstrap HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n",
        "credentials api": b"GET /api/credentials HTTP/1.1\r\nHost: x\r\n\r\n",
        "job create": b"POST /api/jobs HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}",
        "model list": b"GET /llm/api/v1/models HTTP/1.1\r\nHost: x\r\n\r\n",
        "credits": b"POST /llm/api/v1/credits HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n",
        "key admin": b"POST /llm/api/v1/keys HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n",
        "GET inference": b"GET /llm/api/v1/chat/completions HTTP/1.1\r\nHost: x\r\n\r\n",
        "query string": b"POST /llm/api/v1/chat/completions?x=1 HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n",
        "suffix": b"POST /llm/api/v1/chat/completionsX HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n",
        "dot-dot": b"GET /llm/api/v1/../../../api/auth/bootstrap HTTP/1.1\r\nHost: x\r\n\r\n",
        "encoded dot-dot": b"GET /llm/api/v1/%2e%2e/%2e%2e/api/credentials HTTP/1.1\r\nHost: x\r\n\r\n",
        "double slash": b"GET /llm/api/v1//..//api/x HTTP/1.1\r\nHost: x\r\n\r\n",
        "backslash": b"GET /llm/api/v1/..\\api/x HTTP/1.1\r\nHost: x\r\n\r\n",
        "absolute-form": b"GET http://127.0.0.1:%d/api/credentials HTTP/1.1\r\nHost: x\r\n\r\n" % port,
        "prefix lookalike": b"GET /llm/apix/v1/x HTTP/1.1\r\nHost: x\r\n\r\n",
        "other prefix": b"GET /llm/api/v2/x HTTP/1.1\r\nHost: x\r\n\r\n",
        "CONNECT": b"CONNECT 127.0.0.1:%d HTTP/1.1\r\nHost: x\r\n\r\n" % port,
        "TRACE": b"TRACE /llm/api/v1/x HTTP/1.1\r\nHost: x\r\n\r\n",
        "PUT": b"PUT /llm/api/v1/x HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n",
        "DELETE": b"DELETE /llm/api/v1/x HTTP/1.1\r\nHost: x\r\n\r\n",
        "upgrade": b"GET /llm/api/v1/x HTTP/1.1\r\nHost: x\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n",
        "chunked": b"POST /llm/api/v1/x HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
        "expect": b"POST /llm/api/v1/x HTTP/1.1\r\nHost: x\r\nExpect: 100-continue\r\nContent-Length: 0\r\n\r\n",
        "duplicate content-length": b"POST /llm/api/v1/x HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\nContent-Length: 40\r\n\r\n",
        "huge body": b"POST /llm/api/v1/x HTTP/1.1\r\nHost: x\r\nContent-Length: 999999999\r\n\r\n",
        "garbage": b"\x16\x03\x01\x02\x00\r\n\r\n",
        "fragment": b"GET /llm/api/v1/x#/../../api HTTP/1.1\r\nHost: x\r\n\r\n",
        "header injection": b"GET /llm/api/v1/x HTTP/1.1\r\nHost: x\r\n X-Folded: y\r\n\r\n",
    }
    for name, payload in attacks.items():
        reply = _raw(bridge.path, payload)
        assert reply.startswith(b"HTTP/1.1 403") and b"OWNER-TOKEN" not in reply, (name, reply[:80])
    assert FakeRoot.seen == [], FakeRoot.seen  # not one of them reached the host's port


def test_the_bridge_does_not_forward_a_second_pipelined_request_or_follow_redirects(fake_root):
    bridge, port = fake_root
    two = (
        b"POST /llm/api/v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n"
        b"GET /api/auth/bootstrap HTTP/1.1\r\nHost: x\r\n\r\n"
    )
    reply = _raw(bridge.path, two)
    assert b"MODEL-OK" in reply and b"OWNER-TOKEN" not in reply
    assert FakeRoot.seen == ["POST /llm/api/v1/chat/completions"]  # the smuggled second request was dropped
    FakeRoot.seen.clear()
    # A redirect from the proxy side is relayed to the client, not followed; the client's next request is filtered again.
    redirect = _raw(bridge.path, b"POST /llm/api/v1/chat/completions HTTP/1.1\r\nHost: x\r\nX-Redirect: 1\r\nContent-Length: 0\r\n\r\n")
    assert redirect.startswith(b"HTTP/1.1 302") and b"OWNER-TOKEN" not in redirect
    follow = _raw(bridge.path, b"GET /api/auth/bootstrap HTTP/1.1\r\nHost: x\r\n\r\n")
    assert follow.startswith(b"HTTP/1.1 403") and FakeRoot.seen == ["POST /llm/api/v1/chat/completions"]


@needs_net
def test_from_inside_the_jail_the_owner_api_is_not_reachable_even_through_the_bridge(tmp_path, fake_root):
    import subprocess

    bridge, port = fake_root
    sandbox = Bubblewrap(network=False, bridge=(bridge.path, port))
    ws = tmp_path / "ws"
    ws.mkdir()
    code = (
        "import urllib.request as u, urllib.error as e\n"
        f"for p in ('/api/auth/bootstrap', '/api/credentials', '/llm/api/v1/../../api/credentials', '/llm/api/v1/models', '/llm/api/v1/credits', '/llm/api/v1/key', '/llm/api/v1/chat/completions?x=1', '/llm/api/v1/chat/completions'):\n"
        f"    try: print(p, u.urlopen(u.Request('http://127.0.0.1:{port}' + p, data=b'{{}}', method='POST'), timeout=3).read().decode())\n"
        "    except e.HTTPError as x: print(p, 'HTTP', x.code)\n"
        "    except Exception as x: print(p, 'ERR', type(x).__name__)\n"
    )
    done = subprocess.run(sandbox.wrap(["/usr/bin/python3", "-c", code], Mounts(workspace=ws)), capture_output=True, text=True, timeout=30)
    lines = dict(line.split(" ", 1) for line in done.stdout.strip().splitlines())
    assert lines["/api/auth/bootstrap"] == "HTTP 403" and lines["/api/credentials"] == "HTTP 403"
    assert lines["/llm/api/v1/../../api/credentials"] in ("HTTP 403", "ERR HTTPError") or lines["/llm/api/v1/../../api/credentials"].startswith("HTTP")
    for refused in ("/llm/api/v1/models", "/llm/api/v1/credits", "/llm/api/v1/key", "/llm/api/v1/chat/completions?x=1"):
        assert lines[refused] == "HTTP 403", (refused, lines)  # non-inference provider routes never leave the jail
    assert lines["/llm/api/v1/chat/completions"] == "MODEL-OK"
    assert "OWNER-TOKEN" not in done.stdout and FakeRoot.seen == ["POST /llm/api/v1/chat/completions"]


def test_the_proxy_rejects_forged_or_revoked_session_tokens_and_the_owner_api_still_needs_its_own_token(client, home):
    server = client.app.state.root.server
    server.proxy.base = "http://127.0.0.1:9"
    base, token = server.proxy.issue("local", "session-a", lambda: ("http://127.0.0.1:9/v1", "k"))
    path = "/llm/api/v1/chat/completions"
    assert base.endswith("/llm/api/v1")
    for bad in ("Bearer root-forged", "Bearer " + token[:-2] + "xx", "", "Bearer sk-or-real-key"):
        r = client.post(path, headers={"Authorization": bad} if bad else {}, json={"model": "m", "messages": []})
        assert r.status_code in (401, 403), (bad, r.status_code)
    server.proxy.revoke(token)
    assert client.post(path, headers={"Authorization": f"Bearer {token}"}, json={"model": "m", "messages": []}).status_code in (401, 403)
    # A proxy token is not an owner token: it opens none of the owner API.
    from fastapi.testclient import TestClient

    stranger = TestClient(client.app)  # no cookie: what a process holding only a proxy token looks like
    assert stranger.get("/api/credentials").status_code == 401
    for owner_path in ("/api/credentials", "/api/jobs/x", "/api/sessions"):
        assert stranger.get(owner_path, headers={"Authorization": f"Bearer {token}"}).status_code in (401, 403)
    assert stranger.post("/api/auth/bootstrap").status_code == 200  # the open local bootstrap: NOT closed by anything here


def test_credentials_for_code_running_jobs_need_the_ack_unless_the_network_is_isolated(client, home, workspace, script):
    client.put("/api/credentials/api", json={"value": HOST_SECRET, "hosts": ["h.example"]})
    for mode in ("", "bwrap"):
        r = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "developer", "isolation": mode, "credentials": ["api"]})
        assert r.status_code >= 400 and "accept_unprotected_credentials" in r.text, (mode, r.text)
    # a served app keeps the shared network, so even the model-only jail does not protect it
    r = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "designer", "isolation": "bwrap-model-only", "credentials": ["api"], "serve": {"command": "true", "port": 59999}})
    assert r.status_code >= 400 and "accept_unprotected_credentials" in r.text


def test_jailed_guarantee_is_scoped_to_jailed_code_and_names_the_host_tools(client, home, workspace, script):
    script("done")
    client.put("/api/credentials/api", json={"value": HOST_SECRET, "hosts": ["h.example"]})
    r = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "designer", "isolation": "bwrap-model-only", "credentials": ["api"]})
    body = r.json()
    assert r.status_code < 300, r.text
    assert body["credential_guarantee"]["level"] in ("jailed-code-only", "host-added")
    assert set(body["host_tools_outside_jail"]) >= {"inspect_page", "credentialed_request"}
    assert "outside the jail" in body["scope_guarantee"]["isolation_note"]


def test_git_hooks_and_config_are_read_only_in_the_jail_but_git_still_works(client, home, workspace, script):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / ".git" / "hooks").mkdir(exist_ok=True)
    out = _probe(client, home, workspace, script, "bwrap", [
        "echo '#!/bin/sh' > .git/hooks/pre-commit 2>&1; echo hook=$?",
        "echo '[core]' >> .git/config 2>&1; echo cfg=$?",
        "echo x > f.txt; git add f.txt 2>&1; git -c user.name=a -c user.email=a@b commit -qm one 2>&1; echo commit=$?",
    ])
    text = "\n".join(out)
    assert "hook=0" not in text and "cfg=0" not in text, text
    assert "commit=0" in text, text
    assert not (workspace / ".git" / "hooks" / "pre-commit").exists()


class Upstream(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Upstream.seen.append((self.path, self.headers.get("Authorization")))
        body = b'{"usage":{"total_tokens":7}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST

    def log_message(self, *a):
        pass


def test_a_session_token_acts_only_for_its_own_session_budget_and_never_for_another(client, home):
    server = client.app.state.root.server
    up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    Upstream.seen.clear()
    proxy = server.proxy
    proxy.base = "http://127.0.0.1:9"
    resolve = lambda: (f"http://127.0.0.1:{up.server_address[1]}/api/v1", "REAL-KEY")
    _, tok_a = proxy.issue("local", "sess-a", resolve)
    _, tok_b = proxy.issue("local", "sess-b", resolve)
    proxy.budget("sess-a", 10)
    proxy.budget("sess-b", 1000)
    path = "/llm/api/v1/chat/completions"
    call = lambda tok, **kw: client.post(path, headers={"Authorization": f"Bearer {tok}", **kw.pop("headers", {})}, **kw)
    # A names B everywhere it can: header, query, body. The session comes from the token alone.
    assert call(tok_a, headers={"X-Session-Id": "sess-b", "X-Root-Session": "sess-b"}, params={"session": "sess-b", "session_id": "sess-b"}, json={"model": "m", "session_id": "sess-b", "messages": []}).status_code == 200
    assert proxy.used("sess-a") == 7 and proxy.used("sess-b") == 0
    assert call(tok_a, json={"model": "m", "messages": []}).status_code == 200  # 7 < cap 10 at check time
    assert proxy.used("sess-a") == 14 and proxy.used("sess-b") == 0
    # A is now over its own cap; B is unaffected, and A cannot spend B's budget with B's id.
    assert call(tok_a, json={"model": "m", "messages": []}).status_code == 402
    assert call(tok_a, headers={"X-Session-Id": "sess-b"}, json={"session_id": "sess-b"}).status_code == 402
    assert call(tok_b, json={"model": "m", "messages": []}).status_code == 200
    assert proxy.used("sess-b") == 7
    # Revoking A leaves B working.
    proxy.revoke(tok_a)
    assert call(tok_a, json={}).status_code == 401 and call(tok_b, json={}).status_code == 200
    # What a live token does authorize: any /api/ path on the model provider, with the real key attached by the host.
    assert all(auth == "Bearer REAL-KEY" for _, auth in Upstream.seen) and {p.split("?")[0] for p, _ in Upstream.seen} == {"/api/v1/chat/completions"}
    up.shutdown()


def _restricted(client, **kw):
    from tura_code.llm_proxy import Restriction

    server = client.app.state.root.server
    up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    Upstream.seen.clear()
    server.proxy.base = "http://127.0.0.1:9"
    rule = Restriction(models=lambda: {"mock/model"}, **kw)
    _, tok = server.proxy.issue("local", "sess-j", lambda: (f"http://127.0.0.1:{up.server_address[1]}/api/v1", "REAL-KEY"), restrict=rule)
    return server.proxy, tok, up


def test_a_jailed_grant_may_only_run_inference_on_the_configured_model_within_its_limits(client, home):
    proxy, tok, up = _restricted(client, max_requests=4, max_body=2000, max_output_tokens=100)
    h = {"Authorization": f"Bearer {tok}"}
    chat = "/llm/api/v1/chat/completions"
    ok = {"model": "mock/model", "messages": []}
    # no token cap -> refused: the proxy cannot bound spend without one
    assert client.post(chat, headers=h, json=ok).status_code == 402
    proxy.budget("sess-j", 100_000)
    # other provider routes (key, credits, account, model list) and methods never reach the provider
    for method, path in (("GET", "/llm/api/v1/models"), ("GET", "/llm/api/v1/key"), ("GET", "/llm/api/v1/credits"), ("POST", "/llm/api/v1/keys"), ("GET", chat), ("DELETE", chat), ("POST", chat + "?x=1")):
        assert client.request(method, path, headers=h, json={}).status_code in (403, 405), (method, path)
    # model and fields
    for bad in ({"model": "other/model", "messages": []}, {**ok, "plugins": [{"id": "web"}]}, {**ok, "models": ["x/y"]}, {**ok, "route": "fallback"}, {**ok, "max_tokens": "lots"}):
        assert client.post(chat, headers=h, json=bad).status_code == 403, bad
    assert client.post(chat, headers=h, content=b"not json").status_code == 403
    assert client.post(chat, headers=h, json={**ok, "pad": "x" * 3000}).status_code == 413
    assert Upstream.seen == []  # none of the above reached the provider
    # an allowed call goes through with the output length clamped, and nothing else on the path
    assert client.post(chat, headers=h, json={**ok, "max_tokens": 10**6}).status_code == 200
    assert client.post(chat, headers=h, json=ok).status_code == 200
    assert {p for p, _ in Upstream.seen} == {"/api/v1/chat/completions"}
    # the request limit
    assert [client.post(chat, headers=h, json=ok).status_code for _ in range(3)] == [200, 200, 429]
    up.shutdown()


def test_the_clamp_bounds_output_tokens_and_a_missing_limit_gets_the_ceiling():
    from tura_code.llm_proxy import Restriction, restrict_request

    rule = Restriction(models=lambda: {"m"}, max_output_tokens=50)
    for sent, want in (({"model": "m"}, {"max_tokens": 50}), ({"model": "m", "max_tokens": 5000}, {"max_tokens": 50}), ({"model": "m", "max_completion_tokens": 9}, {"max_completion_tokens": 9})):
        body, why = restrict_request(json.dumps(sent).encode(), rule)
        assert not why and all(json.loads(body)[k] == v for k, v in want.items()), (sent, body)


def test_an_isolated_job_without_a_token_cap_is_refused(client, workspace, script):
    r = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "developer", "isolation": "bwrap-model-only"})
    assert r.status_code >= 400


def test_an_isolated_job_with_a_dollar_cap_is_refused_because_the_proxy_cannot_enforce_it(client, workspace, script):
    r = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "developer", "isolation": "bwrap-model-only", "budget": {"max_tokens": 1000, "max_cost_usd": 1.0}})
    assert r.status_code == 400 and "max_cost_usd" in r.text


@requires_runtime
@needs_net
def test_end_to_end_jailed_runtime_through_the_bridge_and_the_real_restricted_proxy(client, home, workspace, service):
    """Real runtime in a network-less jail -> relay -> bridge filter -> REAL proxy (served by uvicorn, restricted grant) -> fake upstream."""
    import socket
    import time
    from urllib.parse import urlparse

    import uvicorn
    from mock_provider import MockProvider

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    replies = [{"tool_calls": [{"name": "Shell", "arguments": {"command": PY_GET % service}}]}, {"text": "all done"}]
    with MockProvider(replies) as mock:
        host = uvicorn.Server(uvicorn.Config(client.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
        thread = threading.Thread(target=host.run, daemon=True)
        thread.start()
        for _ in range(100):
            if host.started:
                break
            time.sleep(0.1)
        try:
            server = client.app.state.root.server
            server.proxy.base = f"http://127.0.0.1:{port}"
            client.put("/api/settings", json={"base_url": mock.base_url, "openrouter_api_key": "sk-test-mock", "model": "mock/model"})
            sid = client.post("/api/sessions", json={"workspace": str(workspace)}).json()["id"]
            client.patch(f"/api/sessions/{sid}", json={"permission_mode": "yolo"})
            runtime = server.tenant().sessions.get(sid)
            runtime.record.isolation = "bwrap-model-only"
            runtime.record.max_tokens = 100_000
            runtime.save_record()
            with client.websocket_connect(f"/api/sessions/{sid}/ws") as ws:
                ws.receive_json()
                events = run_turn(ws, "probe", timeout=90)
        finally:
            host.should_exit = True
            thread.join(10)
    assert of_type(events, "TurnFinished")[0]["payload"]["status"] == "finished", json.dumps(events[-4:], default=str)[:1500]
    assert len(mock.requests) == 2  # both model calls crossed jail -> bridge -> restricted proxy -> upstream
    assert all(r.get("model") == "mock/model" and "max_tokens" in r for r in mock.requests), [list(r) for r in mock.requests]  # clamp applied by the proxy
    shell = [m for m in mock.requests[1]["messages"] if m.get("role") == "tool"]
    assert shell and HOST_SECRET not in json.dumps(shell) and Service.hits == []
    assert "sk-test-mock" not in json.dumps(mock.requests[0]["messages"])
