"""A synthetic app for interaction tests: a notes list with a form. It has deliberate defects for a review to find:
the Save button is not disabled while saving (a double click saves twice), and the name "fail" returns a server error
that the page shows without a way to retry. State is in memory; every POST is recorded, so a test can prove which writes
happened."""

from __future__ import annotations

import http.server
import json
import threading
import time

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>Notes</title>
<style>body{font-family:system-ui;max-width:520px;margin:40px auto;color:#1d1d1f}h1{font-size:22px}
input{padding:8px;width:260px}button{padding:8px 14px;border:0;background:#1d1d1f;color:#fff;border-radius:6px}
#status{min-height:20px;color:#555;margin:10px 0}.err{color:#b00020}li{padding:4px 0}</style></head><body>
<h1>Notes</h1>
<form id="f"><label for="n">Note</label><br><input id="n" name="n" placeholder="Write a note"> <button id="save" type="submit">Save</button></form>
<div id="status" role="status"></div>
<ul id="list"></ul><p id="empty">No notes yet.</p>
<script>
async function load(){const r=await fetch('/api/notes');const d=await r.json();
 document.getElementById('list').innerHTML=d.map(x=>'<li>'+x.replace(/</g,'&lt;')+'</li>').join('');
 document.getElementById('empty').style.display=d.length?'none':'block';}
document.getElementById('f').addEventListener('submit',async e=>{e.preventDefault();
 const s=document.getElementById('status');s.className='';s.textContent='Saving...';
 const r=await fetch('/api/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:document.getElementById('n').value})});
 if(!r.ok){s.className='err';s.textContent='Could not save.';return}
 s.textContent='Saved.';document.getElementById('n').value='';load();});
load();
</script></body></html>"""


class State:
    notes: list[str] = []
    posts: list[str] = []
    lock = threading.Lock()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/":
            self._send(200, PAGE, "text/html")
        elif self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", "http://other.test:%d/" % self.server.server_address[1])
            self.end_headers()
        elif self.path == "/autopost":
            self._send(200, b"<h1>auto</h1><script>fetch('/api/notes',{method:'POST',body:JSON.stringify({text:'sneaky'})})</script>", "text/html")
        elif self.path == "/api/notes":
            self._send(200, json.dumps(State.notes).encode())
        else:
            self._send(404, b"{}")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(length) or b"{}")
        with State.lock:
            State.posts.append(f"{self.path} {data.get('text')}")
        time.sleep(0.4)  # a visible "Saving..." state, and a window for a double click
        if data.get("text") == "fail":
            self._send(500, b'{"error": "boom"}')
            return
        with State.lock:
            State.notes.append(str(data.get("text")))
        self._send(201, b"{}")


def start() -> tuple[http.server.ThreadingHTTPServer, int]:
    State.notes.clear()
    State.posts.clear()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]
