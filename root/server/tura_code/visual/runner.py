"""Runs in its own process: load one page in headless Chromium and report what it shows.

Reads one JSON request on stdin, writes one JSON result on stdout. Playwright is an optional dependency of the
host; this file is the only place that imports it. Page content is untrusted data: it is returned as text, never run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

MAX_TEXT = 6000
MAX_ITEMS = 20


def main() -> None:
    req = json.load(sys.stdin)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(json.dumps({"error": "not_installed"}))
        return
    target = req["target"]
    origin = req["origin"]  # "http://127.0.0.1:PORT" or "file"
    root = Path(req["root"]).resolve() if req.get("root") else None
    blocked: list[str] = []
    errors: list[str] = []

    def allow(url: str, method: str) -> bool:
        if url.startswith(("data:", "blob:")):
            return True
        if method not in ("GET", "HEAD"):
            return False  # a page script cannot change anything in the app it is being looked at
        if origin == "file":
            if not url.startswith("file://"):
                return False
            return root is not None and Path(urlparse(url).path).resolve().is_relative_to(root)
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}" == origin

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"] if req.get("no_sandbox") else [])
        try:
            context = browser.new_context(viewport={"width": req["width"], "height": req["height"]}, accept_downloads=False, java_script_enabled=True, service_workers="block")

            def route(r):
                request = r.request
                if allow(request.url, request.method):
                    # continue_() would let the network stack follow redirects without asking us again, so fetch with no
                    # redirect following and hand the 3xx to the browser: its next hop comes back through this route.
                    try:
                        if origin == "file" or request.url.startswith(("data:", "blob:")):
                            r.continue_()
                            return
                        fetched = r.fetch(max_redirects=0, timeout=15000)
                        location = fetched.headers.get("location")
                        if 300 <= fetched.status < 400 and location:
                            target_url = urljoin(request.url, location)
                            if not allow(target_url, "GET"):  # a redirect out of the allowed origin is a blocked request
                                blocked.append(f"REDIRECT {request.url[:60]} -> {target_url[:80]}")
                                r.abort()
                                return
                        r.fulfill(response=fetched)
                    except Exception as error:  # noqa: BLE001
                        errors.append(f"fetch failed: {str(error)[:120]}")
                        r.abort()
                else:
                    blocked.append(f"{request.method} {request.url[:120]}")
                    r.abort()

            context.route("**/*", route)

            def socket(ws):  # route() does not see WebSockets: refuse every one and count it
                blocked.append(f"WEBSOCKET {ws.url[:120]}")
                pass  # never connected to the server: the page gets an inert mock socket

            context.route_web_socket("**/*", socket)
            page = context.new_page()
            page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" and len(errors) < MAX_ITEMS else None)
            page.on("pageerror", lambda e: errors.append(str(e)[:200]) if len(errors) < MAX_ITEMS else None)
            response = page.goto(target, wait_until="load", timeout=req["timeout_ms"])
            page.wait_for_timeout(req["wait_ms"])
            Path(req["out"]).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=req["out"], full_page=bool(req["full_page"]))
            text = page.inner_text("body")[:MAX_TEXT] if page.query_selector("body") else ""
            info = page.evaluate(
                """() => ({
                  links: document.querySelectorAll('a[href]').length,
                  buttons: document.querySelectorAll('button,[role=button]').length,
                  images: document.images.length,
                  brokenImages: [...document.images].filter(i => !i.complete || i.naturalWidth === 0).length,
                  horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth
                })"""
            )
            print(json.dumps({"status": response.status if response else None, "title": page.title()[:200], "text": text, "info": info, "console_errors": errors, "blocked": blocked[:MAX_ITEMS], "blocked_count": len(blocked)}))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
