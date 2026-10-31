"""The ``github`` tool: issues, pull requests, reviews and checks for the workspace's repository.

Reading is free; anything that writes to GitHub — an issue, a comment, a pull
request, a review — is the owner's call (a host action: it asks, unless the
owner allowed it for this session or chose "Auto-approve all").
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..sessions.host_tools import tool_error
from . import github as gh

if TYPE_CHECKING:
    from ..sessions import SessionRuntime

TOOL = "github"
READS = ("repo", "list_issues", "get_issue", "list_prs", "get_pr", "pr_diff", "checks")
WRITES = ("create_issue", "update_issue", "comment", "create_pr", "review_pr")
DIFF_LIMIT = 40_000

DEFINITION = {
    "name": TOOL,
    "description": (
        "Work with GitHub for this workspace's repository (taken from its `origin` remote unless `repo` is given). "
        "Read: repo, list_issues (state, labels, query), get_issue (with comments), list_prs, get_pr (files and checks), "
        "pr_diff, checks (for `ref`, default the PR head or the current branch). "
        "Write (the owner approves): create_issue (title, body, labels), update_issue (number; title, body, state, labels), "
        "comment (number, body — issues and PRs), create_pr (title, body, head defaults to the current branch, base to the "
        "default branch, draft; push the branch first), review_pr (number, event COMMENT|APPROVE|REQUEST_CHANGES, body, "
        "comments [{path, line, body}])."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": [*READS, *WRITES]},
            "repo": {"type": "string", "description": "owner/name; default: the workspace's origin."},
            "number": {"type": "integer", "description": "Issue or pull request number."},
            "title": {"type": "string"},
            "body": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
            "state": {"type": "string", "enum": ["open", "closed", "all"]},
            "query": {"type": "string", "description": "list_issues: GitHub search terms, e.g. `is:pr author:me`."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "head": {"type": "string"},
            "base": {"type": "string"},
            "draft": {"type": "boolean"},
            "event": {"type": "string", "enum": ["COMMENT", "APPROVE", "REQUEST_CHANGES"]},
            "comments": {"type": "array", "items": {"type": "object"}},
            "ref": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def _ok(output: str, brief: str) -> dict[str, Any]:
    return {"is_error": False, "output": output, "message": "", "display": [{"type": "brief", "text": brief}]}


def _issue_line(item: dict) -> str:
    kind = "PR" if item.get("pull_request") else "issue"
    labels = ", ".join(label["name"] for label in item.get("labels") or [] if isinstance(label, dict))
    return f"#{item['number']} [{item.get('state')}] {item.get('title')} — {kind} by @{(item.get('user') or {}).get('login')}" + (f" ({labels})" if labels else "")


class GitHubToolset:
    plugin = "github"

    def __init__(self, store) -> None:
        self.store = store

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        if runtime.record.answering:
            return []
        return [DEFINITION]

    def handles(self, name: str) -> bool:
        return name == TOOL

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "")
        if action not in (*READS, *WRITES):
            return tool_error(f"Unknown action {action!r}.", "Invalid arguments")
        workspace = Path(runtime.record.workspace)
        repo = str(arguments.get("repo") or "").strip() or gh.workspace_repo(workspace)
        if not repo:
            return tool_error("This workspace has no GitHub `origin` remote: pass `repo` as owner/name.", "No repository")
        token, _source = await gh.credentials(self.store, local=runtime.tenant.server.mode == "local")
        if not token:
            return tool_error("GitHub is not connected: the owner can connect it in Settings → Connections (or log in with `gh auth login`).", "Not connected")
        if action in WRITES:
            if runtime.record.permission_mode == "plan":
                return tool_error("Plan mode does not write to GitHub.", "Read-only")
            what = arguments.get("title") or (f"#{arguments['number']}" if arguments.get("number") else "")
            preview = str(arguments.get("body") or "")[:600]
            approved = await runtime.approve_host_action(
                call_id,
                f"github {action}",
                f"GitHub {action.replace('_', ' ')} on {repo}: {what}".strip(": "),
                [{"type": "brief", "text": preview}] if preview else [],
            )
            if not approved:
                return tool_error(f"The owner did not approve `{action}` on GitHub.", "Rejected")
        try:
            return await getattr(self, f"_{action}")(gh.GitHub(token), repo, arguments, workspace)
        except gh.GitHubError as error:
            return tool_error(str(error), "GitHub error")

    # ----- reads -----------------------------------------------------------------

    async def _repo(self, client, repo, args, workspace):
        data = await client.request("GET", f"/repos/{repo}")
        text = (
            f"{data['full_name']} ({'private' if data.get('private') else 'public'}): {data.get('description') or ''}\n"
            f"default branch: {data.get('default_branch')}; open issues and PRs: {data.get('open_issues_count')}; "
            f"current branch here: {gh.workspace_branch(workspace) or '(detached)'}"
        )
        return _ok(text, repo)

    async def _list_issues(self, client, repo, args, workspace):
        limit = int(args.get("limit") or 20)
        if args.get("query"):
            data = await client.request("GET", "/search/issues", params={"q": f"repo:{repo} {args['query']}", "per_page": limit})
            items = data.get("items") or []
        else:
            params = {"state": args.get("state") or "open", "per_page": limit}
            if args.get("labels"):
                params["labels"] = ",".join(args["labels"])
            items = await client.request("GET", f"/repos/{repo}/issues", params=params)
        return _ok("\n".join(_issue_line(i) for i in items) or "No matching issues.", f"{len(items)} issues in {repo}")

    async def _get_issue(self, client, repo, args, workspace):
        number = int(args.get("number") or 0)
        issue = await client.request("GET", f"/repos/{repo}/issues/{number}")
        comments = await client.request("GET", f"/repos/{repo}/issues/{number}/comments", params={"per_page": 30})
        parts = [_issue_line(issue), "", issue.get("body") or "(no description)"]
        for comment in comments:
            parts += ["", f"— @{(comment.get('user') or {}).get('login')} ({comment.get('created_at', '')[:10]}):", comment.get("body") or ""]
        return _ok("\n".join(parts), f"#{number}: {issue.get('title')}")

    async def _list_prs(self, client, repo, args, workspace):
        items = await client.request("GET", f"/repos/{repo}/pulls", params={"state": args.get("state") or "open", "per_page": int(args.get("limit") or 20)})
        lines = [f"#{p['number']} [{p['state']}{', draft' if p.get('draft') else ''}] {p['title']} — {p['head']['ref']} → {p['base']['ref']} by @{p['user']['login']}" for p in items]
        return _ok("\n".join(lines) or "No pull requests.", f"{len(items)} pull requests")

    async def _get_pr(self, client, repo, args, workspace):
        number = int(args.get("number") or 0)
        pr = await client.request("GET", f"/repos/{repo}/pulls/{number}")
        files = await client.request("GET", f"/repos/{repo}/pulls/{number}/files", params={"per_page": 100})
        checks = await self._check_lines(client, repo, pr["head"]["sha"])
        parts = [
            f"#{number} [{pr['state']}{', draft' if pr.get('draft') else ''}{', merged' if pr.get('merged') else ''}] {pr['title']}",
            f"{pr['head']['ref']} → {pr['base']['ref']} by @{pr['user']['login']}; mergeable: {pr.get('mergeable')}",
            "",
            pr.get("body") or "(no description)",
            "",
            "files:",
            *[f"  {f['status']:<9} {f['filename']} (+{f['additions']} -{f['deletions']})" for f in files],
            "",
            "checks:",
            *(checks or ["  (none reported)"]),
        ]
        return _ok("\n".join(parts), f"PR #{number}: {pr['title']}")

    async def _pr_diff(self, client, repo, args, workspace):
        number = int(args.get("number") or 0)
        diff = await client.request("GET", f"/repos/{repo}/pulls/{number}", accept="application/vnd.github.diff")
        if len(diff) > DIFF_LIMIT:
            diff = diff[:DIFF_LIMIT] + f"\n[truncated: {len(diff) - DIFF_LIMIT} more characters; use get_pr for the file list]"
        return _ok(diff, f"Diff of #{number}")

    async def _check_lines(self, client, repo, ref) -> list[str]:
        data = await client.request("GET", f"/repos/{repo}/commits/{ref}/check-runs", params={"per_page": 50})
        return [f"  {run.get('conclusion') or run.get('status')}: {run.get('name')}" for run in data.get("check_runs") or []]

    async def _checks(self, client, repo, args, workspace):
        ref = str(args.get("ref") or "")
        if not ref and args.get("number"):
            ref = (await client.request("GET", f"/repos/{repo}/pulls/{int(args['number'])}"))["head"]["sha"]
        ref = ref or gh.workspace_branch(workspace)
        if not ref:
            return tool_error("Give `ref` (a branch or commit) or a PR `number`.", "Invalid arguments")
        lines = await self._check_lines(client, repo, ref)
        return _ok("\n".join(lines) or f"No checks reported for {ref}.", f"Checks for {ref}")

    # ----- writes ------------------------------------------------------------------

    async def _create_issue(self, client, repo, args, workspace):
        if not args.get("title"):
            return tool_error("create_issue needs `title`.", "Invalid arguments")
        body = {"title": args["title"], "body": args.get("body") or ""}
        if args.get("labels"):
            body["labels"] = args["labels"]
        issue = await client.request("POST", f"/repos/{repo}/issues", body=body)
        return _ok(f"Created #{issue['number']}: {issue.get('html_url')}", f"Created issue #{issue['number']}")

    async def _update_issue(self, client, repo, args, workspace):
        number = int(args.get("number") or 0)
        changes = {key: args[key] for key in ("title", "body", "state", "labels") if key in args}
        if not number or not changes:
            return tool_error("update_issue needs `number` and something to change.", "Invalid arguments")
        issue = await client.request("PATCH", f"/repos/{repo}/issues/{number}", body=changes)
        return _ok(f"Updated #{number}: {issue.get('html_url')}", f"Updated #{number}")

    async def _comment(self, client, repo, args, workspace):
        number = int(args.get("number") or 0)
        if not number or not args.get("body"):
            return tool_error("comment needs `number` and `body`.", "Invalid arguments")
        comment = await client.request("POST", f"/repos/{repo}/issues/{number}/comments", body={"body": args["body"]})
        return _ok(f"Commented: {comment.get('html_url')}", f"Commented on #{number}")

    async def _create_pr(self, client, repo, args, workspace):
        head = str(args.get("head") or gh.workspace_branch(workspace))
        if not args.get("title") or not head:
            return tool_error("create_pr needs `title` and a branch (`head`, or a checked-out branch).", "Invalid arguments")
        base = str(args.get("base") or (await client.request("GET", f"/repos/{repo}"))["default_branch"])
        try:
            await client.request("GET", f"/repos/{repo}/branches/{head}")
        except gh.GitHubError:
            return tool_error(f"Branch `{head}` is not on GitHub yet: push it first (`git push -u origin {head}`).", "Not pushed")
        pr = await client.request("POST", f"/repos/{repo}/pulls", body={"title": args["title"], "body": args.get("body") or "", "head": head, "base": base, "draft": bool(args.get("draft"))})
        return _ok(f"Opened #{pr['number']} ({head} → {base}): {pr.get('html_url')}", f"Opened PR #{pr['number']}")

    async def _review_pr(self, client, repo, args, workspace):
        number = int(args.get("number") or 0)
        event = str(args.get("event") or "COMMENT")
        body: dict[str, Any] = {"event": event, "body": args.get("body") or ""}
        comments = [c for c in args.get("comments") or [] if isinstance(c, dict) and c.get("path") and c.get("body")]
        if comments:
            body["comments"] = [{"path": c["path"], "line": int(c.get("line") or 1), "body": c["body"]} for c in comments]
        if not number or (event != "APPROVE" and not body["body"] and not comments):
            return tool_error("review_pr needs `number` and a `body` or `comments`.", "Invalid arguments")
        review = await client.request("POST", f"/repos/{repo}/pulls/{number}/reviews", body=body)
        return _ok(f"Reviewed #{number} ({event}): {review.get('html_url')}", f"Reviewed #{number}")
