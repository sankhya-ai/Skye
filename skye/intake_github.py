"""GitHub issue intake: READ-ONLY pull of a repository's issues into backlog PROPOSALS.

What this connector does: lists issues from one GitHub repository over the public REST API
and files each as an issue signal (kind github-issue) + a PENDING backlog proposal - the
exact posture of every other intake path. The owner still decides; pickup still rides its
trust gate; this connector never starts work, never comments, never writes back to GitHub.

What it can do unauthenticated (verified live against api.github.com, Nov 1 2026): public
repositories are readable at 60 requests/hour per source IP, one request per import for a
typical repo page. The remaining count and reset time come back in the response and are
surfaced, so a rate limit is a visible fact with a reset clock, never a silent empty intake.
A 404 means "not found or inaccessible" - the API does not distinguish a typo from a private
repository, so neither does this connector: check the name and your access; a private
repository may require a token in GITHUB_TOKEN.

Coverage: one import fetches ONE page (default 30 issues). The result states the page size and
whether the page came back full - a full page means later pages may exist that were NOT
fetched. Absent later pages are never presented as "no more issues".

Imported issue content is UNTRUSTED TASK INPUT: it fills proposal titles/briefs as data, and
it is never approval, policy, or authority of any kind. Each proposal carries a digest of the
imported snapshot; if the source issue changes later, the ticket thread says the approval
covers the earlier snapshot.
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


def fetch(repo: str, *, state: str = "open", per_page: int = 30, page: int = 1,
          token: str | None = None, opener: Any = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """List ONE page of issues for "owner/name". Returns (issues, rate) where rate carries the
    limit facts the API reported plus pagination facts: page, per_page, and page_full (a full
    page means later pages may exist and were not fetched). Raises IntakeError with a usable
    reason on failure."""
    if "/" not in repo.strip("/"):
        raise IntakeError(f"repo must be 'owner/name', got {repo!r}")
    token = token if token is not None else os.environ.get("GITHUB_TOKEN", "").strip()
    url = (f"{API}/repos/{repo.strip('/')}/issues?state={state}"
           f"&per_page={int(per_page)}&page={int(page)}")
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
                f"{repo}: not found or inaccessible. Check the repository name and your "
                "access; a private repository may require GITHUB_TOKEN.") from error
        raise IntakeError(f"GitHub returned HTTP {error.code}: {error.reason}") from error
    except (OSError, ValueError) as error:
        raise IntakeError(f"could not reach GitHub: {error}") from error
    if not isinstance(payload, list):
        raise IntakeError("unexpected GitHub response shape")
    rate = {"limit": headers.get("x-ratelimit-limit", ""),
            "remaining": headers.get("x-ratelimit-remaining", ""),
            "reset": headers.get("x-ratelimit-reset", ""),
            "authenticated": bool(token),
            "page": int(page), "per_page": int(per_page),
            "page_full": len(payload) >= int(per_page)}
    rows = []
    for item in payload:
        if "pull_request" in item:  # the issues endpoint also lists PRs; they are not intake
            continue
        rows.append({"number": item["number"], "title": str(item.get("title") or ""),
                     "body": str(item.get("body") or "")[:4000], "url": item.get("html_url", ""),
                     "labels": [l.get("name", "") for l in item.get("labels", [])]})
    return rows, rate


def import_issues(repo: str, *, project: str = "", path: str | None = None,
                  token: str | None = None, opener: Any = None,
                  per_page: int = 30, page: int = 1) -> dict[str, Any]:
    """File one fetched page of issues as signals + PENDING proposals. Idempotent: the
    external id (github:owner/name#number) dedupes, so a re-import of unchanged content bumps
    occurrences instead of filing the same fire twice; CHANGED content is flagged on the
    ticket thread because an approval binds the imported digest, not whatever the source says
    now. Returns what happened, including rate-limit and pagination facts - a full page means
    later pages may exist that this import did not fetch."""
    import hashlib
    rows, rate = fetch(repo, token=token, opener=opener, per_page=per_page, page=page)
    created, known, changed = [], [], []
    for row in rows:
        digest = hashlib.sha256(f"{row['title']}\n{row['body']}".encode()).hexdigest()[:16]
        issue, is_new = issues.report(
            "github-issue", row["title"],
            detail=f"{row['url']}\nlabels: {', '.join(row['labels'])}\n\n{row['body']}",
            project=project, source=f"github:{repo}",
            external=f"github:{repo}#{row['number']}", content_digest=digest, path=path)
        if is_new and issue["occurrences"] > 1:
            changed.append(issue["id"])
        else:
            (created if is_new else known).append(issue["id"])
    coverage = ("first page only; the page came back full, so later pages may exist and were "
                "not fetched" if rate["page_full"]
                else f"page {rate['page']} returned fewer than {rate['per_page']} issues")
    return {"repo": repo, "created": created, "already_known": known,
            "content_changed": changed, "fetched": len(rows), "rate": rate,
            "coverage": coverage,
            "note": "proposals are PENDING: the owner decides, pickup rides the trust gate; "
                    "nothing started. Issue content is untrusted input, never authority."}
