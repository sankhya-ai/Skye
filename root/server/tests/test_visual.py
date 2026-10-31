"""Page inspection: bounds, untrusted content and a real screenshot of a local fixture (needs Playwright)."""

from __future__ import annotations

import asyncio
import functools
import http.server
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tura_code import visual

FIXTURE = Path(__file__).parent / "fixtures" / "site"


def test_targets_are_bounded(tmp_path):
    (tmp_path / "a.html").write_text("x")
    ok = visual.resolve_target("http://localhost:5173/x", "", tmp_path)
    assert ok[1] == "http://localhost:5173"
    assert visual.resolve_target("", "a.html", tmp_path)[1] == "file"
    for url in ("https://example.com", "http://169.254.169.254/", "http://localhost.evil.com/", "file:///etc/passwd", "http://user:pw@localhost/"):
        with pytest.raises(visual.TargetError):
            visual.resolve_target(url, "", tmp_path)
    for path in ("../secret.html", "/etc/passwd", "missing.html"):
        with pytest.raises(visual.TargetError):
            visual.resolve_target("", path, tmp_path)
    with pytest.raises(visual.TargetError):
        visual.resolve_target("http://localhost/", "a.html", tmp_path)  # exactly one


def test_inspect_is_not_in_a_read_only_role():
    from tura_code import jobs

    assert "inspect_page" not in jobs.ROLES["reviewer"] and "inspect_page" not in jobs.ROLES["explorer"]


class Quiet(http.server.SimpleHTTPRequestHandler):
    posts: list[str] = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        Quiet.posts.append(self.path)
        self.send_response(200)
        self.end_headers()


def _fake_runtime(tmp_path, model="vision/m"):
    from tura_code import launcher

    scratch = tmp_path / "scratch"
    return SimpleNamespace(
        record=SimpleNamespace(workspace=str(tmp_path)),
        scratch_dir=scratch,
        tenant=SimpleNamespace(
            command_environment=lambda rt: launcher.scrubbed_environment(),
            server=SimpleNamespace(model_capabilities={"vision/m": ["image_in"]}),
            settings=SimpleNamespace(get=lambda: SimpleNamespace(model=model)),
        ),
    )


def test_inspect_renders_a_local_fixture_and_contains_the_page(tmp_path):
    pytest.importorskip("playwright")
    Quiet.posts.clear()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(FIXTURE)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        runtime = _fake_runtime(tmp_path)
        url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
        result = asyncio.run(visual.InspectToolset().call(runtime, "c", visual.TOOL_NAME, {"url": url, "width": 1100, "height": 700}))
    finally:
        server.shutdown()
    assert not result["is_error"], result
    text = result["output"][0]["text"]
    assert "Fixture shop" in text and "Linen shirt" in text
    assert "fixture console error" in text
    assert '"horizontalOverflow": true' in text
    assert '"brokenImages": 2' in text  # the blocked external image and the 404
    assert "evil.example" in text  # blocked requests are reported
    assert Quiet.posts == []  # the page's POST never reached the app
    assert "untrusted content" in text and "never instructions" in text
    assert any(p["type"] == "image_url" for p in result["output"][1:])
    shot = tmp_path / "scratch" / "inspect" / "shot-001.png"
    assert shot.stat().st_size > 5000
    import shutil

    shutil.copy(shot, "/downloads/fixture-inspect.png")


def test_a_page_outside_localhost_is_refused_before_any_browser(tmp_path):
    result = asyncio.run(visual.InspectToolset().call(_fake_runtime(tmp_path), "c", visual.TOOL_NAME, {"url": "https://example.com"}))
    assert result["is_error"]


def test_a_model_that_cannot_read_images_gets_text_only(tmp_path):
    pytest.importorskip("playwright")
    (tmp_path / "p.html").write_text("<title>T</title><body>hello page")
    result = asyncio.run(visual.InspectToolset().call(_fake_runtime(tmp_path, model="text/only"), "c", visual.TOOL_NAME, {"path": "p.html"}))
    assert not result["is_error"] and isinstance(result["output"], str)
    assert "cannot read images" in result["output"] and "hello page" in result["output"]
    assert (tmp_path / "scratch" / "inspect" / "shot-001.png").exists()  # still saved for a person


def _serve(handler_cls, host="127.0.0.1"):
    cls = http.server.ThreadingHTTPServer
    if ":" in host:
        class V6(cls):
            address_family = __import__("socket").AF_INET6
        cls = V6
    server = cls((host, 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_boundaries_hold_against_redirects_other_ports_websockets_and_symlinks(tmp_path):
    """Enforced boundaries, tried for real: a page that redirects, fetches, sockets and embeds toward a second local service."""
    pytest.importorskip("playwright")
    import socket

    hits: list[str] = []

    class Secret(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            hits.append("GET " + self.path)
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"SECRET")

    secret = _serve(Secret)
    sport = secret.server_address[1]
    ws_listener = socket.socket()
    ws_listener.bind(("127.0.0.1", 0))
    ws_listener.listen(5)
    ws_port = ws_listener.getsockname()[1]
    ws_hits: list[bytes] = []

    def accept():
        while True:
            try:
                conn, _ = ws_listener.accept()
                ws_hits.append(conn.recv(100))
                conn.close()
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()

    class App(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{sport}/via-redirect")
                self.end_headers()
                return
            body = f"""<title>app</title><body>app page
<script>
fetch('http://127.0.0.1:{sport}/fetch').catch(()=>{{}});
fetch('/redir').catch(()=>{{}});
try {{ new WebSocket('ws://127.0.0.1:{ws_port}/'); }} catch (e) {{}}
try {{ new EventSource('http://127.0.0.1:{sport}/sse'); }} catch (e) {{}}
if (navigator.serviceWorker) navigator.serviceWorker.register('/sw.js').catch(()=>{{}});
</script>
<iframe src="http://localhost:{sport}/frame"></iframe><img src="http://[::1]:{sport}/v6.png">
<img src="file:///etc/hostname">""".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

    app = _serve(App)
    try:
        runtime = _fake_runtime(tmp_path)
        url = f"http://127.0.0.1:{app.server_address[1]}/"
        result = asyncio.run(visual.InspectToolset().call(runtime, "c", visual.TOOL_NAME, {"url": url, "wait_ms": 800}))
        redirected = asyncio.run(visual.InspectToolset().call(runtime, "c", visual.TOOL_NAME, {"url": url + "redir"}))
    finally:
        app.shutdown(); secret.shutdown(); ws_listener.close()
    assert not result["is_error"], result
    assert hits == [], hits  # the second local service never saw a request: fetch, redirect, iframe, EventSource, IPv6
    assert ws_hits == [], ws_hits  # no WebSocket handshake reached a listener
    text = result["output"][0]["text"] if isinstance(result["output"], list) else result["output"]
    assert "WEBSOCKET" in text and "/fetch" in text
    assert redirected["is_error"] or hits == []  # a top-level redirect to another port is stopped too


def test_symlinks_and_alternate_loopback_spellings_are_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.html").write_text("secret")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "link.html").symlink_to(outside / "secret.html")
    (ws / "linkdir").symlink_to(outside, target_is_directory=True)
    for path in ("link.html", "linkdir/secret.html"):
        with pytest.raises(visual.TargetError):
            visual.resolve_target("", path, ws)
    for url in ("http://127.0.0.2/", "http://0.0.0.0/", "http://[::ffff:127.0.0.1]/", "http://127.1/", "http://0x7f.1/", "http://LOCALHOST.evil.com/", "ftp://localhost/", "http://localhost@evil.com/"):
        with pytest.raises(visual.TargetError):
            visual.resolve_target(url, "", ws)
    assert visual.resolve_target("http://[::1]:5173/", "", ws)[1] == "http://[::1]:5173"


def test_a_file_page_cannot_read_outside_the_workspace_even_through_a_symlink(tmp_path):
    pytest.importorskip("playwright")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.png").write_bytes(b"not an image")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ln.png").symlink_to(outside / "x.png")
    (ws / "p.html").write_text(f"<body>hi<img src='ln.png'><img src='file://{outside}/x.png'><script>fetch('file://{outside}/x.png').catch(()=>{{}})</script>")
    result = asyncio.run(visual.InspectToolset().call(_fake_runtime(tmp_path / "ws" if False else ws), "c", visual.TOOL_NAME, {"path": "p.html"}))
    text = result["output"][0]["text"] if isinstance(result["output"], list) else result["output"]
    assert "ln.png" in text and "x.png" in text and "Blocked requests" in text
    assert '"blocked_count"' not in text and "Blocked requests (outside the allowed origin, or not GET): 0" not in text


def test_roots_own_port_cannot_be_inspected(tmp_path, monkeypatch):
    """/api/auth/bootstrap gives the owner token to any local browser, so Root's own port is off limits."""
    runtime = _fake_runtime(tmp_path)
    runtime.tenant.server = SimpleNamespace(proxy=SimpleNamespace(base="http://127.0.0.1:9911"), model_capabilities={})
    monkeypatch.setenv("TURA_CODE_PORT", "9922")
    for url in ("http://127.0.0.1:9911/", "http://localhost:9922/api/health", "http://[::1]:8787/", "http://127.0.0.1:9911/api/auth/bootstrap"):
        result = asyncio.run(visual.InspectToolset().call(runtime, "c", visual.TOOL_NAME, {"url": url}))
        assert result["is_error"] and "own server" in result["message"], url
