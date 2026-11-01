"""GitHub issue intake: READ-ONLY pull of a repository's issues into backlog PROPOSALS.

What this connector does: lists issues from one GitHub repository over the public REST API
and files each as an issue signal (kind github-issue) + a PENDING backlog proposal - the
exact posture of every other intake path. The owner still decides; pickup still rides its
trust gate; this connector never starts work, never comments, never writes back to GitHub.

What it can do unauthenticated (verified live against api.github.com, Nov 1 2026): public
repositories are readable at 60 requests/hour per source IP, one request per import for a
typical repo page. The remaining count and reset time come back in the response and are
surfaced, so a rate limit is a visible fact with a reset clock, never a silent empty intake.
Private repositories and anything past the limit need a token in GITHUB_TOKEN - when no
token is set, the connector says so plainly instead of implying the repo has no issues.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from . import issues

API = "https://api.github.com"


class IntakeError(ValueError):
    pass


def fetch(repo: str, *, state: str = "open", per_page: int = 30,
          token: str | None = None, opener: Any = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """List issues for "owner/name". Returns (issues, rate) where rate carries the limit
    facts the API reported. Raises IntakeError with a usable reason on failure."""
    if "/" not in repo.strip("/"):
        raise IntakeError(f"repo must be 'owner/name', got {repo!r}")
    token = token if token is not None else os.environ.get("GITHUB_TOKEN", "").strip()
    url = f"{API}/repos/{repo.strip('/')}/issues?state={state}&per_page={int(per_page)}"
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "skye-intake",
        **({"Authorization": f"Bearer {token}"} if token else {})})
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=15) as response:
            payload = json.loads(response.read() or b"[]")
            headers = response.headers
    except urllib.error.HTTPError as error:
        headers = error.headers or {}
        remaining = headers.get("x-ratelimit-remaining", "")
        if error.code in (403, 429) and remaining == "0":
            raise IntakeError(
                f"GitHub rate limit exhausted ({headers.get('x-ratelimit-limit', '?')}/hour "
                f"unauthenticated); resets at epoch {headers.get('x-ratelimit-reset', '?')}. "
                "Set GITHUB_TOKEN for a higher limit.") from error
        if error.code == 404:
            raise IntakeError(
                f"{repo}: not found. A private repository needs GITHUB_TOKEN; "
                "unauthenticated intake can only read public repositories.") from error
        raise IntakeError(f"GitHub returned HTTP {error.code}: {error.reason}") from error
    except (OSError, ValueError) as error:
        raise IntakeError(f"could not reach GitHub: {error}") from error
    if not isinstance(payload, list):
        raise IntakeError("unexpected GitHub response shape")
    rate = {"limit": headers.get("x-ratelimit-limit", ""),
            "remaining": headers.get("x-ratelimit-remaining", ""),
            "reset": headers.get("x-ratelimit-reset", ""),
            "authenticated": bool(token)}
    rows = []
    for item in payload:
        if "pull_request" in item:  # the issues endpoint also lists PRs; they are not intake
            continue
        rows.append({"number": item["number"], "title": str(item.get("title") or ""),
                     "body": str(item.get("body") or "")[:4000], "url": item.get("html_url", ""),
                     "labels": [l.get("name", "") for l in item.get("labels", [])]})
    return rows, rate


def import_issues(repo: str, *, project: str = "", path: str | None = None,
                  token: str | None = None, opener: Any = None) -> dict[str, Any]:
    """File every fetched issue as a signal + PENDING proposal. Idempotent: the external id
    (github:owner/name#number) dedupes, so a re-import bumps occurrences instead of filing
    the same fire twice. Returns what happened, including the rate-limit facts."""
    rows, rate = fetch(repo, token=token, opener=opener)
    created, known = [], []
    for row in rows:
        issue, is_new = issues.report(
            "github-issue", row["title"],
            detail=f"{row['url']}\nlabels: {', '.join(row['labels'])}\n\n{row['body']}",
            project=project, source=f"github:{repo}",
            external=f"github:{repo}#{row['number']}", path=path)
        (created if is_new else known).append(issue["id"])
    return {"repo": repo, "created": created, "already_known": known,
            "fetched": len(rows), "rate": rate,
            "note": "proposals are PENDING: the owner decides, pickup rides the trust gate; "
                    "nothing started"}
