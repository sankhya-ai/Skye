"""Runs in its own process: drive one page in headless Chromium through a short list of steps, or look at a deployed page.

Reads one JSON request on stdin, writes one JSON result on stdout. Two modes:
* ``interact``: a LOCAL app (origin fixed by the host). Click, type, press keys, wait. Writes (POST, PUT, ...) reach the app
  only if ``allow_writes`` is true; otherwise they are blocked and reported.
* ``deployed``: a PUBLIC page the owner listed. Always GET/HEAD only, no cookies, no typing, no clicking that changes state
  (a click can still run page scripts, but every non-GET request is blocked). Host names are pinned to addresses the host
  already checked are public (``resolver_rules``), so a name cannot be switched to a private address mid-run.

Every request outside ``origins`` is blocked and counted, redirects out of them included. Page content is untrusted data.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

MAX_TEXT = 4000
MAX_LOG = 40
STEP_TIMEOUT_MS = 5000
FOCUS_JS = """() => { const e = document.activeElement; if (!e || e === document.body) return null;
  return {tag: e.tagName.toLowerCase(), id: e.id || null, type: e.getAttribute('type'), text: (e.innerText || e.value || e.getAttribute('aria-label') || '').slice(0, 40),
          outline: getComputedStyle(e).outlineStyle !== 'none' || getComputedStyle(e).boxShadow !== 'none'}; }"""


def locate(page, target: dict):
    if "role" in target:
        return page.get_by_role(target["role"], name=target.get("name")) if target.get("name") else page.get_by_role(target["role"])
    if "text" in target:
        return page.get_by_text(target["text"])
    if "label" in target:
        return page.get_by_label(target["label"])
    if "placeholder" in target:
        return page.get_by_placeholder(target["placeholder"])
    if "css" in target:
        return page.locator(target["css"])
    raise ValueError("target needs one of role, text, label, placeholder, css")


MAX_BODY = 10_000_000
DROP_HEADERS = {"set-cookie", "set-cookie2", "content-length", "transfer-encoding", "content-encoding", "connection", "keep-alive"}


def pinned_fetch(url: str, pins: dict, headers: dict):
    """GET ``url`` from an address the host already verified is public. No redirect following, no cookies, no re-resolution.

    Every deployed request, and each redirect hop the browser then makes, goes through here, so the owner-listed-page check runs
    BEFORE any request is sent to a redirect target. The TLS certificate and host name are verified by the default context.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    last: Exception | None = None
    for ip in pins.get(host, []):
        try:
            sock = socket.create_connection((ip, port), timeout=15)
            break
        except OSError as error:
            last = error
    else:
        raise OSError(f"no verified address for {host}: {last}")
    if parsed.scheme == "https":
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
    conn = http.client.HTTPConnection(host, port, timeout=15)
    conn.sock = sock
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    send = {k: v for k, v in headers.items() if k.lower() in ("user-agent", "accept", "accept-language")}
    send.update({"Accept-Encoding": "identity", "Connection": "close"})
    try:
        conn.request("GET", path, headers=send)
        resp = conn.getresponse()
        body = resp.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise OSError("response too large")
        return resp.status, [(k, v) for k, v in resp.getheaders() if k.lower() not in DROP_HEADERS], body
    finally:
        conn.close()


def main() -> None:
    req = json.load(sys.stdin)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(json.dumps({"error": "not_installed"}))
        return
    mode = req["mode"]
    origins = set(req["origins"])
    allow_writes = bool(req.get("allow_writes")) and mode == "interact"  # a deployed page is never written to
    blocked: list[str] = []
    network: list[str] = []
    errors: list[str] = []

    listed_pages = set(req.get("listed_pages") or [])  # deployed: the exact owner-listed pages a NAVIGATION may land on

    def allowed(url: str, method: str, nav: bool = False) -> bool:
        if url.startswith(("data:", "blob:")):
            return True
        parsed = urlparse(url)
        if f"{parsed.scheme}://{parsed.netloc}" not in origins:
            return False
        if nav and mode == "deployed" and url.split("#")[0] not in listed_pages:
            return False
        return method in ("GET", "HEAD") or allow_writes

    pins: dict = {}
    for rule in req.get("resolver_rules") or []:
        _, name, ip = rule.split()
        pins.setdefault(name, []).append(ip)
    args = ["--no-sandbox"] if req.get("no_sandbox") else []
    if req.get("resolver_rules"):
        # Pin each listed name to the address the host verified is public; every other name does not resolve, so a redirect
        # to another host fails at the network layer. (route() does not see redirect hops, and route.fetch resolves names
        # outside the browser, so neither could enforce this.) The pinned addresses are excluded from the catch-all.
        pinned = [rule.split()[-1] for rule in req["resolver_rules"]]
        args.append("--host-resolver-rules=" + ", ".join(req["resolver_rules"] + ["MAP * ~NOTFOUND"] + [f"EXCLUDE {ip}" for ip in sorted(set(pinned))]))
    with sync_playwright() as p:
        browser = p.chromium.launch(args=args)
        try:
            context = browser.new_context(viewport={"width": req["width"], "height": req["height"]}, accept_downloads=False, service_workers="block", permissions=[], ignore_https_errors=False)

            def route(r):
                request = r.request
                if not allowed(request.url, request.method, request.is_navigation_request()):
                    blocked.append(f"{request.method} {request.url[:120]}")
                    r.abort()
                    return
                if request.url.startswith(("data:", "blob:")):
                    r.continue_()
                    return
                if mode == "deployed":
                    try:
                        status, hdrs, body = pinned_fetch(request.url, pins, request.headers)
                        location = next((v for k, v in hdrs if k.lower() == "location"), None)
                        if 300 <= status < 400 and location:
                            nxt = urljoin(request.url, location)
                            if not allowed(nxt, "GET", request.is_navigation_request()):
                                blocked.append(f"REDIRECT {request.url[:60]} -> {nxt[:80]}")
                                r.abort()
                                return
                        r.fulfill(status=status, headers=dict(hdrs), body=body)
                    except Exception as error:  # noqa: BLE001 - TLS failure, unreachable, too large: fail closed
                        errors.append(f"fetch failed: {str(error)[:120]}")
                        r.abort()
                    return
                try:
                    fetched = r.fetch(max_redirects=0, timeout=15000)
                    location = fetched.headers.get("location")
                    if 300 <= fetched.status < 400 and location:
                        nxt = urljoin(request.url, location)
                        if not allowed(nxt, request.method if fetched.status in (307, 308) else "GET", request.is_navigation_request()):
                            blocked.append(f"REDIRECT {request.url[:60]} -> {nxt[:80]}")
                            r.abort()
                            return
                    r.fulfill(response=fetched)
                except Exception as error:  # noqa: BLE001
                    errors.append(f"fetch failed: {str(error)[:120]}")
                    r.abort()

            context.route("**/*", route)
            context.route_web_socket("**/*", lambda ws: blocked.append(f"WEBSOCKET {ws.url[:120]}"))
            page = context.new_page()
            page.set_default_timeout(STEP_TIMEOUT_MS)
            page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" and len(errors) < MAX_LOG else None)
            page.on("pageerror", lambda e: errors.append(str(e)[:200]) if len(errors) < MAX_LOG else None)
            page.on("response", lambda resp: network.append(f"{resp.request.method} {urlparse(resp.url).path or '/'} -> {resp.status}") if len(network) < MAX_LOG and not resp.url.startswith("data:") else None)
            shots: list[str] = []
            out_dir = Path(req["out_dir"])
            out_dir.mkdir(parents=True, exist_ok=True)
            counter = req["first_shot"]

            def shoot() -> str:
                nonlocal counter
                name = f"{req['prefix']}-{counter:03d}.png"
                counter += 1
                page.screenshot(path=str(out_dir / name), full_page=False)
                shots.append(name)
                return name

            log = []
            response = page.goto(req["start"], wait_until="load", timeout=req["timeout_ms"])
            log.append({"i": 0, "action": "goto", "ok": True, "status": response.status if response else None, "url": urlparse(page.url).path or "/"})
            for i, step in enumerate(req.get("steps") or [], 1):
                entry: dict = {"i": i, "action": step.get("action")}
                try:
                    act = step["action"]
                    if act == "goto":
                        page.goto(urljoin(req["start"], step["path"]), wait_until="load", timeout=req["timeout_ms"])
                    elif act == "wait_ms":
                        page.wait_for_timeout(min(int(step.get("value") or 300), 3000))
                    elif act == "wait_for_text":
                        page.get_by_text(str(step["value"])).first.wait_for(state="visible", timeout=STEP_TIMEOUT_MS)
                    elif act == "tab":
                        for _ in range(max(1, min(int(step.get("value") or 1), 20))):
                            page.keyboard.press("Tab")
                    elif act == "screenshot":
                        pass
                    elif act in ("click", "dblclick", "hover", "fill", "press", "select", "check", "uncheck"):
                        if mode == "deployed":
                            raise ValueError("a deployed page is looked at, not operated")
                        loc = locate(page, step.get("target") or {}).first
                        if act == "fill":
                            if (loc.get_attribute("type") or "").lower() == "password":
                                raise ValueError("refused: password fields are never filled by this tool")
                            loc.fill(str(step.get("value") or "")[:500])
                        elif act == "press":
                            loc.press(str(step["value"]))
                        elif act == "select":
                            loc.select_option(str(step["value"]))
                        else:
                            getattr(loc, act)()
                        page.wait_for_timeout(150)
                    else:
                        raise ValueError(f"unknown action {act!r}")
                    entry["ok"] = True
                except Exception as error:  # noqa: BLE001 - a failed step is a finding, not a crash
                    entry["ok"] = False
                    entry["error"] = str(error).splitlines()[0][:200]
                entry["url"] = urlparse(page.url).path or "/"
                try:
                    entry["focus"] = page.evaluate(FOCUS_JS)
                except Exception:  # noqa: BLE001
                    entry["focus"] = None
                if (step.get("shot") or step.get("action") == "screenshot" or not entry["ok"]) and len(shots) < req["max_shots"]:
                    entry["shot"] = shoot()
                log.append(entry)
                if not entry["ok"] and step.get("stop_on_error", True):
                    break
            if not shots or log[-1].get("shot") is None:
                if len(shots) < req["max_shots"]:
                    log[-1]["shot"] = shoot()
            final = urlparse(page.url)
            if mode == "deployed" and page.url not in ("about:blank", "") and page.url.split("#")[0] not in listed_pages:
                blocked.append(f"FINAL PAGE NOT A LISTED PAGE {page.url[:100]}")
            if f"{final.scheme}://{final.netloc}" not in origins and page.url not in ("about:blank", ""):
                blocked.append(f"FINAL PAGE OUTSIDE ALLOWED ORIGINS {page.url[:100]}")
            text = page.inner_text("body")[:MAX_TEXT] if page.query_selector("body") else ""
            info = page.evaluate("() => ({links: document.querySelectorAll('a[href]').length, buttons: document.querySelectorAll('button,[role=button]').length, horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth})")
            print(json.dumps({"title": page.title()[:200], "log": log, "text": text, "info": info, "network": network, "console_errors": errors, "blocked": blocked[:MAX_LOG], "blocked_count": len(blocked), "shots": shots}))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
