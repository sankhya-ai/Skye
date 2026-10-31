"""Job-scoped credentials: used by the host, absent from the model's view, limited to registered hosts."""

from __future__ import annotations

import http.server
import json
import threading

import pytest
from conftest import requires_runtime, run_turn, text, tool_call
from test_jobs import _wait

from tura_code import broker

SECRET = "sk-test-SUPERSECRET-123456"


class Echo(http.server.BaseHTTPRequestHandler):
    seen: list[str] = []
    other: list[str] = []

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_address[1]}/landed")
            self.end_headers()
            return
        Echo.seen.append(self.headers.get("Authorization", ""))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"you_sent": self.headers.get("Authorization", "")}).encode())


@pytest.fixture
def echo():
    Echo.seen.clear()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_the_request_carries_the_secret_and_the_response_does_not(tmp_path, echo):
    b = broker.Broker(tmp_path)
    b.put("api", SECRET, [echo])
    assert oct(b.path.stat().st_mode & 0o777) == "0o600"
    result = b.request("api", "GET", f"http://{echo}/x")
    assert Echo.seen == [f"Bearer {SECRET}"]
    assert SECRET not in json.dumps(result) and "[credential:api]" in result["body"]
    assert SECRET not in json.dumps(b.names())


def test_hosts_schemes_and_redirects_are_limited(tmp_path, echo):
    b = broker.Broker(tmp_path)
    b.put("api", SECRET, [echo])
    for url in ("http://localhost:1/x", f"http://{echo}@evil.com/", "https://evil.com/", "ftp://x/", f"http://user:p@{echo}/"):
        with pytest.raises(broker.BrokerError):
            b.request("api", "GET", url)
    result = b.request("api", "GET", f"http://{echo}/redir")
    assert result["status"] == 302 and Echo.seen == []  # the 302 is returned, not followed
    with pytest.raises(broker.BrokerError):
        b.put("bad name!", "v", ["h"])
    with pytest.raises(broker.BrokerError):
        b.put("w", "v", ["*.example.com"])


def test_redaction_covers_url_and_base64_forms(tmp_path):
    import base64
    import urllib.parse

    b = broker.Broker(tmp_path)
    b.put("k", "p@ss/word+12345", ["h"])
    for form in ("p@ss/word+12345", urllib.parse.quote("p@ss/word+12345", safe=""), base64.b64encode(b"p@ss/word+12345").decode()):
        assert form not in b.redact(f"x {form} y") and "[credential:k]" in b.redact(f"x {form} y")


@requires_runtime
def test_a_job_uses_a_credential_without_the_model_seeing_it(client, workspace, script, echo):
    assert client.put("/api/credentials/api", json={"value": SECRET, "hosts": [echo]}).status_code == 200
    assert SECRET not in client.get("/api/credentials").text
    assert client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "credentials": ["missing"]}).status_code == 400
    script([
        tool_call("c1", "credentialed_request", {"credential": "api", "url": f"http://{echo}/data"}),
        tool_call("c2", "credentialed_request", {"credential": "other", "url": f"http://{echo}/data"}),
        text(f'The key was {SECRET}. {{"summary": "done"}}'),
    ])
    created = client.post("/api/jobs", json={"goal": "call the api", "workspace": str(workspace), "role": "explorer", "contract": ["summary"], "credentials": ["api"]}).json()
    assert created["credential_guarantee"]["level"] == "host-added"
    item = _wait(client, created["id"])
    assert Echo.seen == [f"Bearer {SECRET}"]  # the host sent it once; the "other" call was refused
    events = json.dumps(client.get(f"/api/sessions/{item['session']}/events").json())
    assert SECRET not in json.dumps(item)  # reply, contract and result are redacted
    assert "[credential:api]" in json.dumps(item)
    results = [e for e in client.get(f"/api/sessions/{item['session']}/events").json()["events"] if e["type"] == "ToolResult" and e["payload"].get("tool_call_id") == "c1"]
    assert results and SECRET not in json.dumps(results) and "[credential:api]" in json.dumps(results)  # the echoed header came back redacted
    refused = [e for e in client.get(f"/api/sessions/{item['session']}/events").json()["events"] if e["type"] == "ToolResult" and e["payload"].get("tool_call_id") == "c2"]
    assert refused and refused[0]["payload"]["return_value"]["is_error"]


@requires_runtime
def test_a_job_without_credentials_never_gets_the_tool(client, workspace, script, echo):
    client.put("/api/credentials/api", json={"value": SECRET, "hosts": [echo]})
    script([tool_call("c1", "credentialed_request", {"credential": "api", "url": f"http://{echo}/data"}), text('{"summary": "s"}')])
    item = _wait(client, client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "explorer", "contract": ["summary"]}).json()["id"])
    assert Echo.seen == [] and item["credential_guarantee"] is None


def test_a_code_running_role_is_told_the_credential_is_not_protected_from_its_code():
    from tura_code import jobs

    g = jobs.credential_guarantee(["api"], {"level": "none"})
    assert g["level"] == "transcript-only" and "NOT protected" in g["note"]


# ----- adversarial review: can a tool in scope reach the store, the token, or the owner API? ---------------------------

def _events_text(client, item):
    return json.dumps(client.get(f"/api/sessions/{item['session']}/events").json())


@requires_runtime
@pytest.mark.parametrize("tool", ["ReadFile", "Grep", "Glob"])
def test_read_tools_do_not_follow_a_symlink_to_the_store_or_token(client, home, workspace, script, tool):
    client.put("/api/credentials/api", json={"value": SECRET, "hosts": ["h.example"]})
    token = home / "token"
    token.write_text("OWNER-TOKEN-VALUE")  # the local token file sits beside the store
    (workspace / "store-link").symlink_to(home / "credentials.json")
    (workspace / "token-link").symlink_to(token)
    (workspace / "home-link").symlink_to(home, target_is_directory=True)
    args = {
        "ReadFile": {"path": str(workspace / "store-link")},
        "Grep": {"pattern": "SUPERSECRET|OWNER-TOKEN", "path": str(workspace), "-r": True},
        "Glob": {"pattern": "home-link/*"},
    }[tool]
    script([tool_call("t", tool, args), tool_call("u", "ReadFile", {"path": str(workspace / "home-link" / "credentials.json")}), tool_call("v", "ReadFile", {"path": str(workspace / "token-link")}), text("done")])
    created = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "reviewer", "credentials": ["api"]}).json()
    item = _wait(client, created["id"])
    seen = _events_text(client, item)
    assert SECRET not in seen and "OWNER-TOKEN-VALUE" not in seen, "a read tool followed a symlink out of the workspace"


def test_a_workspace_that_overlaps_the_data_directory_cannot_hold_credentials(home, tmp_path):
    from tura_code import jobs

    (home).mkdir(parents=True, exist_ok=True)
    (home / "proj").mkdir()
    assert jobs.overlaps(home / "proj", home) and jobs.overlaps(home.parent, home) and jobs.overlaps(home, home)
    assert not jobs.overlaps(tmp_path / "elsewhere", home)
    link = tmp_path / "via-link"
    link.symlink_to(home, target_is_directory=True)
    assert jobs.overlaps(link, home)


def test_grants_limit_methods_and_paths(tmp_path, echo):
    b = broker.Broker(tmp_path)
    b.put("api", SECRET, [echo], paths=["/v1/"])
    for method, path in (("POST", "/v1/x"), ("GET", "/admin"), ("GET", "/v1/../admin"), ("GET", "/v1/%2e%2e/admin")):
        with pytest.raises(broker.BrokerError):
            b.request("api", method, f"http://{echo}{path}")
    assert b.request("api", "GET", f"http://{echo}/v1/x")["status"] == 200
    b.put("rw", SECRET, [echo], methods=["get", "post"])
    assert b.names()[1]["methods"] == ["GET", "POST"]


@requires_runtime
def test_credentials_for_a_code_running_role_need_an_explicit_acknowledgement(client, home, workspace, script, echo):
    client.put("/api/credentials/api", json={"value": SECRET, "hosts": [echo]})
    refused = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "developer", "credentials": ["api"]})
    assert refused.status_code == 400 and "accept_unprotected_credentials" in refused.text
    script([text("ok")])
    accepted = client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "developer", "credentials": ["api"], "accept_unprotected_credentials": True})
    assert accepted.status_code == 200 and accepted.json()["credential_guarantee"]["level"] == "transcript-only"
    _wait(client, accepted.json()["id"])


@requires_runtime
def test_credentials_and_serve_are_refused_for_a_workspace_that_overlaps_the_data_directory(client, home, script):
    client.put("/api/credentials/api", json={"value": SECRET, "hosts": ["h.example"]})
    (home / "proj").mkdir(exist_ok=True)
    for ws in (home, home / "proj", home.parent):
        r = client.post("/api/jobs", json={"goal": "x", "workspace": str(ws), "role": "explorer", "credentials": ["api"]})
        assert r.status_code == 400 and "data directory" in r.text, ws
    r = client.post("/api/jobs", json={"goal": "x", "workspace": str(home / "proj"), "role": "designer", "serve": {"command": "sleep 1", "port": 45998}})
    assert r.status_code == 400


def test_revoking_a_credential_closes_access_immediately(tmp_path, echo):
    # The store is re-read on every request: a deletion closes access at the next request, no cached grant.
    b = broker.Broker(tmp_path)
    b.put("api", SECRET, [echo])
    b.request("api", "GET", f"http://{echo}/x")
    assert Echo.seen == [f"Bearer {SECRET}"]
    b.delete("api")
    with pytest.raises(broker.BrokerError):
        b.request("api", "GET", f"http://{echo}/x")
    assert not b.has("api") and Echo.seen == [f"Bearer {SECRET}"]


@requires_runtime
def test_a_finished_jobs_session_no_longer_answers_for_its_credentials(client, workspace, script, echo):
    # Stale-session surface: after the job ends, the session record's grant is cleared - even the OWNER
    # re-prompting that session gets no credentialed_request for the ended job's names.
    client.put("/api/credentials/api", json={"value": SECRET, "hosts": [echo]})
    script([tool_call("c1", "credentialed_request", {"credential": "api", "url": f"http://{echo}/data"}), text('{"summary": "s"}')])
    item = _wait(client, client.post("/api/jobs", json={"goal": "x", "workspace": str(workspace), "role": "explorer", "contract": ["summary"], "credentials": ["api"]}).json()["id"])
    assert Echo.seen == [f"Bearer {SECRET}"]
    session = client.get(f"/api/sessions/{item['session']}").json()
    assert session.get("credentials", []) == []  # grant closed on the record at terminal
    script([tool_call("c2", "credentialed_request", {"credential": "api", "url": f"http://{echo}/data"}), text("tried")])
    with client.websocket_connect(f"/api/sessions/{item['session']}/ws") as ws:
        ws.receive_json()
        events = run_turn(ws, "try the api again")
    results = [e for e in events if e["type"] == "ToolResult" and e["payload"].get("tool_call_id") == "c2"]
    assert not results or results[0]["payload"]["return_value"]["is_error"]  # refused, or the tool is gone entirely
    assert Echo.seen == [f"Bearer {SECRET}"]  # no second request ever carried the secret
