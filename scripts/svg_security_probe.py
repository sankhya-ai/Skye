"""Empirical SVG/blob execution probe: does active SVG run (a) in <img>, (b) on direct
blob navigation, (c) in a sandboxed iframe? Verifies browser behavior, not header claims."""
import http.server, json, threading
from playwright.sync_api import sync_playwright

SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">
<script>window.__pwned = (window.__pwned||[]); window.__pwned.push("svg-script");</script>
<rect width="10" height="10" fill="red"/></svg>'''

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/x.svg":
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(SVG)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>probe</body></html>")
    def log_message(self, *a): pass

srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"

with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page()
    page.goto(base + "/")
    # (a) img from a blob built from the fetched SVG (exactly what the Skye UI does)
    page.evaluate("""async (u) => {
        const res = await fetch(u);
        window.__blob = URL.createObjectURL(await res.blob());
        const img = document.createElement('img');
        img.src = window.__blob;
        document.body.appendChild(img);
    }""", base + "/x.svg")
    page.wait_for_timeout(500)
    img_pwned = page.evaluate("window.__pwned || null")
    # (b) direct navigation to the blob URL within the SAME page (blob: URLs are scoped to
    # the creating context - a fresh tab cannot resolve them, verified by ERR_FILE_NOT_FOUND)
    blob = page.evaluate("window.__blob")
    page.goto(blob)
    page.wait_for_timeout(500)
    nav_pwned = page.evaluate("window.__pwned || null")
    page.goto(base + "/")
    page.evaluate("(u) => { window.__blob = u; }", blob)
    # (c) sandboxed iframe (no allow-scripts, no allow-same-origin)
    page.evaluate("""() => {
        const f = document.createElement('iframe');
        f.setAttribute('sandbox', '');
        f.src = window.__blob;
        document.body.appendChild(f);
    }""")
    page.wait_for_timeout(700)
    frame = page.frames[-1]
    iframe_pwned = frame.evaluate("window.__pwned || null")
    print(json.dumps({"img_blob": img_pwned, "direct_blob_navigation": nav_pwned,
                      "sandboxed_iframe": iframe_pwned}))
    b.close()
