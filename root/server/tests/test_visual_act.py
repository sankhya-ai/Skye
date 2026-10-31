"""Operating a local app and looking at a deployed page: authority, bounds, and a real browser run against a synthetic app.

Needs Playwright with Chromium for the browser tests. The "deployed" browser test uses a stand-in on loopback with the public
check stubbed: it exercises name pinning, GET-only and origin blocking, NOT real TLS or a real public site.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "app"))
import server as fixture  # noqa: E402

from tura_code import launcher  # noqa: E402
from tura_code.visual import TargetError, act  # noqa: E402

pw = pytest.importorskip("playwright")


def rt(tmp_path, visual, model="vision/m"):
    return SimpleNamespace(
        record=SimpleNamespace(workspace=str(tmp_path), visual=visual, job_role="pm"),
        scratch_dir=tmp_path / "scratch",
        tenant=SimpleNamespace(command_environment=lambda r: launcher.scrubbed_environment(), settings=SimpleNamespace(get=lambda: SimpleNamespace(model=model)), server=SimpleNamespace(model_capabilities={model: ["image_in"]}, proxy=SimpleNamespace(base=""))),
    )


@pytest.fixture
def app():
    server, port = fixture.start()
    yield port
    server.shutdown()


def call(runtime, name, args):
    return asyncio.run(act.ActToolset().call(runtime, "c", name, args))


def text_of(result):
    out = result["output"]
    return out if isinstance(out, str) else out[0]["text"]


def test_no_authority_means_no_tools_and_no_calls(tmp_path):
    runtime = rt(tmp_path, {})
    assert act.ActToolset().definitions(runtime) == []
    assert call(runtime, "interact_page", {})["is_error"] and call(runtime, "inspect_deployed", {"url": "https://example.com"})["is_error"]


def test_deployed_urls_are_bounded():
    for bad in ("http://example.com", "https://user:pw@example.com", "https://127.0.0.1/", "https://[::1]/", "https://localhost/", "https://x.internal/", "ftp://example.com"):
        with pytest.raises(TargetError):
            act.check_deployed_url(bad)
    assert act.check_deployed_url("https://example.com/a?b=1")[0] == "https://example.com"


def test_public_check_refuses_private_addresses(monkeypatch):
    import socket

    def fake(host, *a, **k):
        return [(2, 1, 6, "", ({"private.test": "10.0.0.5", "loop.test": "127.0.0.1", "meta.test": "169.254.169.254", "pub.test": "93.184.216.34", "mixed.test": "93.184.216.34"}[host], 0))] + ([(2, 1, 6, "", ("10.0.0.9", 0))] if host == "mixed.test" else [])

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    for host in ("private.test", "loop.test", "meta.test", "mixed.test"):
        with pytest.raises(TargetError):
            act.resolve_public(host)
    assert act.resolve_public("pub.test") == ["93.184.216.34"]


def test_interact_refuses_paths_off_the_origin_and_too_many_steps(tmp_path):
    runtime = rt(tmp_path, {"interact": {"origin": "http://127.0.0.1:45999", "allow_writes": False}})
    assert call(runtime, "interact_page", {"path": "//evil.example/x"})["is_error"]
    assert call(runtime, "interact_page", {"steps": [{"action": "screenshot"}] * 13})["is_error"]


def test_deployed_refuses_an_unlisted_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(act, "resolve_public", lambda h: ["93.184.216.34"])
    runtime = rt(tmp_path, {"deployed": {"urls": ["https://listed.example/"]}})
    assert call(runtime, "inspect_deployed", {"url": "https://other.example/"})["is_error"]


def test_a_blocked_write_is_reported_and_the_app_never_sees_it(tmp_path, app):
    runtime = rt(tmp_path, {"interact": {"origin": f"http://127.0.0.1:{app}", "allow_writes": False}})
    steps = [{"action": "fill", "target": {"label": "Note"}, "value": "hello"}, {"action": "click", "target": {"role": "button", "name": "Save"}}, {"action": "wait_ms", "value": "600"}]
    result = call(runtime, "interact_page", {"steps": steps})
    out = text_of(result)
    assert not result["is_error"] and f"POST http://127.0.0.1:{app}/api/notes" in out and "Blocked" in out
    assert fixture.State.posts == [] and fixture.State.notes == []


def test_with_writes_allowed_a_real_form_flow_runs_and_shows_the_defects(tmp_path, app):
    runtime = rt(tmp_path, {"interact": {"origin": f"http://127.0.0.1:{app}", "allow_writes": True}})
    save = {"role": "button", "name": "Save"}
    flow = [
        {"action": "screenshot"},  # empty state
        {"action": "fill", "target": {"label": "Note"}, "value": "buy milk"},
        {"action": "click", "target": save, "shot": True},  # "Saving..." state
        {"action": "wait_for_text", "value": "Saved.", "shot": True},
        {"action": "fill", "target": {"label": "Note"}, "value": "double"},
        {"action": "dblclick", "target": save},
        {"action": "wait_ms", "value": "1200"},
        {"action": "fill", "target": {"label": "Note"}, "value": "fail"},
        {"action": "click", "target": save},
        {"action": "wait_for_text", "value": "Could not save.", "shot": True},
        {"action": "tab", "value": "2", "shot": True},
    ]
    result = call(runtime, "interact_page", {"steps": flow, "width": 700, "height": 520})
    out = text_of(result)
    assert not result["is_error"], out
    assert "FAILED" not in out, out
    assert fixture.State.notes.count("buy milk") == 1
    assert fixture.State.notes.count("double") == 2, "the double click should expose the missing double-submit protection"
    assert fixture.State.posts.count("/api/notes fail") == 1
    shots = sorted(p.name for p in (runtime.scratch_dir / "inspect").glob("shot-*.png"))
    assert len(shots) >= 5 and all((runtime.scratch_dir / "inspect" / s).stat().st_size > 2000 for s in shots)
    assert "Could not save." in out and "focus:" in out


def test_passwords_are_never_filled_and_cross_origin_is_blocked(tmp_path):
    (tmp_path / "p.html").write_text('<input type=password id=p><script>fetch("https://evil.example/x").catch(()=>{})</script>')
    import functools
    import http.server
    import threading

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        runtime = rt(tmp_path, {"interact": {"origin": f"http://127.0.0.1:{port}", "allow_writes": True}})
        out = text_of(call(runtime, "interact_page", {"path": "/p.html", "steps": [{"action": "fill", "target": {"css": "#p"}, "value": "hunter2"}]}))
        assert "refused: password fields are never filled" in out and "evil.example" in out
    finally:
        srv.shutdown()


def test_deployed_standin_is_get_only_pinned_and_origin_bound(tmp_path, monkeypatch, app):
    """Stand-in: http on loopback, name `deployed.test` pinned to 127.0.0.1 by the stubbed public check. Not real TLS."""
    monkeypatch.setattr(act, "DEPLOYED_SCHEMES", ("https", "http"))
    monkeypatch.setattr(act, "resolve_public", lambda h: ["127.0.0.1"])
    url = f"http://deployed.test:{app}/"
    runtime = rt(tmp_path, {"deployed": {"urls": [url]}})
    result = call(runtime, "inspect_deployed", {"url": url})
    out = text_of(result)
    assert not result["is_error"], out
    assert "Notes" in out and "GET /api/notes -> 200" in out
    assert (runtime.scratch_dir / "inspect" / "deploy-001.png").is_file()
    assert fixture.State.posts == []


def test_deployed_stand_in_blocks_a_redirect_to_another_host_and_page_writes(tmp_path, monkeypatch, app):
    monkeypatch.setattr(act, "DEPLOYED_SCHEMES", ("https", "http"))
    monkeypatch.setattr(act, "resolve_public", lambda h: ["127.0.0.1"])
    base = f"http://deployed.test:{app}"
    runtime = rt(tmp_path, {"deployed": {"urls": [base + "/", base + "/redir", base + "/autopost"]}})
    res = call(runtime, "inspect_deployed", {"url": base + "/redir"})
    assert res["is_error"], res  # navigation fails closed; the unlisted host is never contacted
    res = call(runtime, "inspect_deployed", {"url": base + "/autopost"})
    out = text_of(res)
    assert not res["is_error"] and "Blocked" in out and f"POST {base}/api/notes" in out
    assert fixture.State.posts == [] and fixture.State.notes == []


# ---- redirects, bypasses and zero-request checks, against counting servers ----

import http.server  # noqa: E402
import threading  # noqa: E402


class Counting(http.server.BaseHTTPRequestHandler):
    hits: list = []
    other_port = 0

    def log_message(self, *a):
        pass

    def _go(self):
        type(self).hits.append(f"{self.command} {self.path}")
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        p = self.path.split("?")[0]
        if p.startswith("/to-"):  # /to-307-other, /to-302-same
            code, where = p[4:].split("-")
            loc = f"http://127.0.0.1:{type(self).other_port}/hit" if where == "other" else "/secret"
            self.send_response(int(code))
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = {
            "/": b"<h1>home</h1>",
            "/secret": b"<h1>secret</h1>",
            "/form307": b"<form method=post action=/to-307-other><button id=b>go</button></form>",
            "/form308": b"<form method=post action=/to-308-other><button id=b>go</button></form>",
            "/form303": b"<form method=post action=/to-303-other><button id=b>go</button></form>",
            "/ws": b"<script>try{new WebSocket('ws://127.0.0.1:%d/sock')}catch(e){}</script>ws" % type(self).other_port,
            "/sw": b"<script>navigator.serviceWorker&&navigator.serviceWorker.register('/sw.js').catch(()=>{})</script>sw",
            "/dl": b"<a id=d href=/file.bin download>dl</a><script>document.getElementById('d').click()</script>dl",
        }.get(p, b"nf")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream" if p == "/file.bin" else "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_HEAD = _go  # noqa: N815


def serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def pair():
    class Main(Counting):
        hits: list = []

    class Other(Counting):
        hits: list = []

    other = serve(Other)
    Main.other_port = other.server_address[1]
    main = serve(Main)
    yield Main, Other, main.server_address[1]
    main.shutdown()
    other.shutdown()


@pytest.mark.parametrize("form,code", [("/form307", "307"), ("/form308", "308"), ("/form303", "303")])
def test_interact_post_redirect_to_another_origin_sends_it_zero_requests(tmp_path, pair, form, code):
    Main, Other, port = pair
    runtime = rt(tmp_path, {"interact": {"origin": f"http://127.0.0.1:{port}", "allow_writes": True}})
    out = text_of(call(runtime, "interact_page", {"path": form, "steps": [{"action": "click", "target": {"css": "#b"}}]}))
    assert Other.hits == [], Other.hits
    assert any(h.startswith("POST /to-") for h in Main.hits)  # the owner-authorized write itself happened
    assert "REDIRECT" in out or "Blocked" in out


def test_interact_without_writes_sends_the_form_post_nowhere(tmp_path, pair):
    Main, Other, port = pair
    runtime = rt(tmp_path, {"interact": {"origin": f"http://127.0.0.1:{port}", "allow_writes": False}})
    call(runtime, "interact_page", {"path": "/form307", "steps": [{"action": "click", "target": {"css": "#b"}}]})
    assert [h for h in Main.hits if h.startswith("POST")] == [] and Other.hits == []


def deployed_runtime(tmp_path, monkeypatch, port, listed):
    monkeypatch.setattr(act, "DEPLOYED_SCHEMES", ("https", "http"))
    monkeypatch.setattr(act, "resolve_public", lambda h: ["127.0.0.1"])
    return rt(tmp_path, {"deployed": {"urls": [f"http://deployed.test:{port}{p}" for p in listed]}})


def test_deployed_needs_the_exact_listed_page_not_the_whole_origin(tmp_path, monkeypatch, pair):
    Main, Other, port = pair
    runtime = deployed_runtime(tmp_path, monkeypatch, port, ["/"])
    res = call(runtime, "inspect_deployed", {"url": f"http://deployed.test:{port}/secret"})
    assert res["is_error"] and "exact pages" in res["message"] and Main.hits == []


def test_deployed_same_origin_redirect_outside_the_listed_page_is_blocked(tmp_path, monkeypatch, pair):
    Main, Other, port = pair
    runtime = deployed_runtime(tmp_path, monkeypatch, port, ["/to-302-same"])
    res = call(runtime, "inspect_deployed", {"url": f"http://deployed.test:{port}/to-302-same"})
    out = res.get("message") if res["is_error"] else text_of(res)
    print(out)
    assert Main.hits == ["GET /to-302-same"], Main.hits  # the redirect target received zero requests
    assert res["is_error"] or "FINAL PAGE NOT A LISTED PAGE" in out or "Blocked" in out


@pytest.mark.parametrize("path", ["/ws", "/sw", "/dl"])
def test_deployed_websocket_service_worker_and_download_cannot_escape(tmp_path, monkeypatch, pair, path):
    Main, Other, port = pair
    runtime = deployed_runtime(tmp_path, monkeypatch, port, [path])
    res = call(runtime, "inspect_deployed", {"url": f"http://deployed.test:{port}{path}"})
    assert Other.hits == [], Other.hits
    assert not any(h.startswith("POST") for h in Main.hits)
    if path == "/ws":
        assert "WEBSOCKET" in text_of(res)
    if path == "/dl":
        assert not [f for f in (runtime.scratch_dir / "inspect").glob("*") if not f.name.endswith(".png")]
