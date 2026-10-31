"""A fake GitHub and a fake Google (OAuth + Gmail) on a real local port, for the connection tests."""

from __future__ import annotations

import base64
import hashlib
import socket
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class Fakes:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.device_polls = 0
        self.challenges: dict[str, str] = {}  # code -> PKCE challenge the "browser" saw
        self.refreshes = 0
        self.app = self._build()

    def _build(self) -> FastAPI:
        app = FastAPI()
        fakes = self

        def auth(authorization: str | None, token: str = "gh-token") -> None:
            if authorization != f"Bearer {token}":
                raise HTTPException(401, "bad token")

        # ----- GitHub ------------------------------------------------------------
        @app.get("/user")
        def user(authorization: str | None = Header(None)):
            auth(authorization)
            return {"login": "ada"}

        @app.get("/repos/o/r")
        def repo(authorization: str | None = Header(None)):
            auth(authorization)
            return {"full_name": "o/r", "private": False, "description": "demo", "default_branch": "main", "open_issues_count": 1}

        @app.get("/repos/o/r/issues/1")
        def issue(authorization: str | None = Header(None)):
            auth(authorization)
            return {"number": 1, "state": "open", "title": "Login fails", "body": "Steps: …", "user": {"login": "bob"}, "labels": [{"name": "bug"}]}

        @app.get("/repos/o/r/issues/1/comments")
        def comments(authorization: str | None = Header(None)):
            return [{"user": {"login": "carol"}, "created_at": "2026-10-01T00:00:00Z", "body": "Same here."}]

        @app.post("/repos/o/r/issues/1/comments")
        async def comment(request: Request, authorization: str | None = Header(None)):
            auth(authorization)
            fakes.calls.append(("POST", "comment", await request.json()))
            return {"html_url": "https://github.com/o/r/issues/1#c1"}

        @app.get("/repos/o/r/branches/{branch}")
        def branch(branch: str, authorization: str | None = Header(None)):
            if branch != "feature":
                raise HTTPException(404, "no branch")
            return {"name": branch}

        @app.post("/repos/o/r/pulls")
        async def create_pr(request: Request, authorization: str | None = Header(None)):
            auth(authorization)
            body = await request.json()
            fakes.calls.append(("POST", "pulls", body))
            return {"number": 7, "html_url": "https://github.com/o/r/pull/7"}

        @app.get("/repos/o/r/pulls/7")
        def get_pr(accept: str | None = Header(None)):
            if accept == "application/vnd.github.diff":
                return PlainTextResponse("diff --git a/x b/x\n+fixed\n")
            return {"number": 7, "state": "open", "title": "Fix login", "body": "", "draft": False, "merged": False, "mergeable": True,
                    "head": {"ref": "feature", "sha": "abc"}, "base": {"ref": "main"}, "user": {"login": "ada"}}

        @app.get("/repos/o/r/pulls/7/files")
        def pr_files():
            return [{"status": "modified", "filename": "app.py", "additions": 3, "deletions": 1}]

        @app.get("/repos/o/r/commits/abc/check-runs")
        def checks():
            return {"check_runs": [{"name": "tests", "status": "completed", "conclusion": "success"}]}

        @app.post("/login/device/code")
        def device_code(client_id: str = Form(...), scope: str = Form("")):
            return {"device_code": "dev-1", "user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device", "interval": 1, "expires_in": 900}

        @app.post("/login/oauth/access_token")
        def device_token(device_code: str = Form(...), client_id: str = Form(...), grant_type: str = Form(...)):
            fakes.device_polls += 1
            if fakes.device_polls == 1:
                return {"error": "authorization_pending"}
            return {"access_token": "gh-token", "token_type": "bearer"}

        # ----- Google ------------------------------------------------------------
        @app.post("/token")
        def token(
            grant_type: str = Form(...), code: str = Form(""), code_verifier: str = Form(""), refresh_token: str = Form(""),
            client_id: str = Form(...), client_secret: str = Form(""), redirect_uri: str = Form(""),
        ):
            if grant_type == "authorization_code":
                challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
                if fakes.challenges.get(code) != challenge:
                    return PlainTextResponse('{"error":"invalid_grant","error_description":"PKCE mismatch"}', status_code=400, media_type="application/json")
                return {"access_token": "g-access-1", "refresh_token": "g-refresh", "expires_in": 1, "scope": "gmail.readonly gmail.compose"}
            if grant_type == "refresh_token" and refresh_token == "g-refresh":
                fakes.refreshes += 1
                return {"access_token": "g-access-2", "expires_in": 3600}
            return PlainTextResponse('{"error":"invalid_grant"}', status_code=400, media_type="application/json")

        def gauth(authorization: str | None) -> None:
            if authorization not in ("Bearer g-access-1", "Bearer g-access-2"):
                raise HTTPException(401, "bad token")

        @app.get("/gmail/v1/users/me/profile")
        def profile(authorization: str | None = Header(None)):
            gauth(authorization)
            return {"emailAddress": "ada@example.com"}

        @app.get("/gmail/v1/users/me/messages")
        def search(q: str = "", authorization: str | None = Header(None)):
            gauth(authorization)
            fakes.calls.append(("GET", "search", q))
            return {"messages": [{"id": "m1", "threadId": "t1"}]}

        headers = [{"name": "From", "value": "Bob <bob@example.com>"}, {"name": "Subject", "value": "Crash on login"},
                   {"name": "Date", "value": "Mon, 12 Oct 2026"}, {"name": "Message-ID", "value": "<m1@example.com>"}]
        message = {"id": "m1", "threadId": "t1", "snippet": "It crashes when…",
                   "payload": {"mimeType": "multipart/mixed", "headers": headers, "parts": [
                       {"mimeType": "text/plain", "body": {"data": _b64("It crashes when I log in with SSO.")}},
                       {"mimeType": "application/pdf", "filename": "trace.pdf", "body": {"attachmentId": "a1"}}]}}

        @app.get("/gmail/v1/users/me/messages/m1")
        def read(authorization: str | None = Header(None)):
            gauth(authorization)
            return message

        @app.get("/gmail/v1/users/me/threads/t1")
        def thread(authorization: str | None = Header(None)):
            gauth(authorization)
            return {"id": "t1", "messages": [message]}

        @app.post("/gmail/v1/users/me/drafts")
        async def draft(request: Request, authorization: str | None = Header(None)):
            gauth(authorization)
            body = await request.json()
            fakes.calls.append(("POST", "draft", body))
            return {"id": "d1", "message": {"id": "m2"}}

        return app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Running:
    def __init__(self, fakes: Fakes) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(fakes.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "Running":
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(5)
