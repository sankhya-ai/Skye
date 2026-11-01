"""Issue intake: external error SIGNALS (production logs, frontend errors, user reports) become
backlog PROPOSALS, never work.

The user's maintenance loop: any error the team sees should become a fix the team owns. The
safe version of that loop starts here - a signal is recorded (with its source and payload),
turned into a PENDING backlog item, and only the owner's approval lets a repair mission start.
The intake is deliberately dumb: it does not parse stack traces into fixes (the planner does
that when the owner approves), it does not deduplicate semantically (exact source+summary
only), and it never connects to a live production feed by itself. Feeds are owner-connected:
a deployment's log shipper or a frontend error hook POSTs here when the owner wires it up.
Persisted in skye.issues.json (override with SKYE_ISSUES), atomic writes, same posture as the
backlog and policy registries.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from . import backlog

DEFAULT_PATH = "skye.issues.json"
KINDS = ("production-log", "frontend-error", "user-report", "test-failure",
         "github-issue")


class IssueError(ValueError):
    pass


def default_path() -> str:
    return os.environ.get("SKYE_ISSUES", "").strip() or DEFAULT_PATH


def _read(path: str | Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(Path(path or default_path()).read_text("utf-8"))
    except (OSError, ValueError):
        return {"issues": []}
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        return {"issues": []}
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    tmp.replace(path)


def list_issues(path: str | Path | None = None, project: str = "") -> list[dict[str, Any]]:
    rows = [i for i in _read(path)["issues"] if i.get("id")]
    if project:
        rows = [i for i in rows if i.get("project") == project]
    return rows


def report(kind: str, summary: str, *, detail: str = "", project: str = "",
           source: str = "", external: str = "", content_digest: str = "", path: str | Path | None = None) -> tuple[dict[str, Any], bool]:
    """Record one error signal and file a PENDING backlog proposal for it.

    Returns (issue, created) - created is False when the same source+summary was already
    reported (the team does not file the same fire twice; the existing issue's count bumps).
    A proposal carries the issue id so the eventual repair mission traces back to the signal.
    """
    if kind not in KINDS:
        raise IssueError(f"kind must be one of {KINDS}, got {kind!r}")
    summary = str(summary or "").strip()
    if not summary:
        raise IssueError("an issue needs a summary")
    target = Path(path or default_path())
    data = _read(target)
    if external:
        for issue in data["issues"]:
            if issue.get("external") == external:
                issue["occurrences"] = int(issue.get("occurrences", 1)) + 1
                issue["last_seen"] = time.time()
                # The proposal an owner approved is bound to the content digest it carried.
                # If the source changed since, say so on the ticket's thread - an approval
                # covers the imported snapshot, never whatever the source says now.
                if content_digest and issue.get("content_digest") and \
                        issue["content_digest"] != content_digest:
                    issue["content_digest"] = content_digest
                    if issue.get("feature_id"):
                        try:
                            backlog.add_thread(
                                issue["feature_id"], actor="intake", kind="status",
                                body=("the source content changed since import (new digest "
                                      f"{content_digest}); any approval covers the earlier "
                                      "snapshot - re-review before acting on this ticket"))
                        except Exception:
                            pass  # the issue record still carries the new digest
                    _write(target, data)
                    return issue, True  # changed content: treat as new for the caller's count
                _write(target, data)
                return issue, False
    key = (project, source, summary.lower())
    for issue in data["issues"]:
        if (issue.get("project"), issue.get("source"), str(issue.get("summary")).lower()) == key:
            issue["occurrences"] = int(issue.get("occurrences", 1)) + 1
            issue["last_seen"] = time.time()
            _write(target, data)
            return issue, False
    issue = {"id": f"issue-{secrets.token_hex(4)}", "kind": kind, "project": project,
             "summary": summary, "detail": str(detail or "")[:4000], "source": source,
             "occurrences": 1, "first_seen": time.time(), "last_seen": time.time(),
             "external": external, "content_digest": content_digest, "feature_id": ""}
    # The proposal is the only way an issue reaches work. Title/brief stay factual - the
    # planner decides the actual fix after the owner approves.
    title = summary[:80]
    brief = (f"{kind} reported"
             + (f" by {source}" if source else "")
             + f": {summary}"
             + (f"\n\nDetail:\n{issue['detail']}" if issue["detail"] else "")
             + (f"\n\nSource content digest: {content_digest} (approval binds this snapshot)"
                if content_digest else ""))
    proposed = backlog.propose([{"title": title, "brief": brief}],
                               project=project, mission=issue["id"], path=None)
    if proposed:
        issue["feature_id"] = proposed[0]["id"]
    data["issues"].append(issue)
    _write(target, data)
    return issue, True


def report_for_feature(feature_id: str, kind: str, summary: str, *, detail: str = "",
                       labels: list | None = None, actor: str = "pm",
                       source: str = "", path: str | Path | None = None) -> tuple[dict[str, Any], str]:
    """A bug reported ON an existing ticket (the PM's maintenance loop): the issue is recorded,
    proper labels land on the ticket, and the ticket itself moves -

      delivered             -> reopened (evidence + labels on the original ticket; owner decides
                                       the repair; the ticket keeps its whole history)
      awaiting-verification -> sent back with the bug as the note (the work was not good enough)
      pending/approved      -> stays queued; the bug is attached as evidence for the work to cover

    Returns (issue, action) where action is "reopened" | "sent-back" | "attached".
    Never starts work: pickup flows through the owner's decision and the normal intake."""
    feature = backlog.get(feature_id)
    issue, _ = report(kind, summary, detail=detail, project=feature.get("project", ""),
                      source=source or actor, path=path)
    label_set = ["type:bug"] + [str(l) for l in (labels or [])]
    status = feature.get("status")
    evidence = f"{kind}: {summary}" + (f" - {detail[:300]}" if detail else "")
    if status == "delivered":
        backlog.reopen(feature_id, evidence=evidence, labels=label_set, actor=actor)
        action = "reopened"
    elif status == "awaiting-verification":
        backlog.set_labels(feature_id,
                           list(dict.fromkeys((feature.get("labels") or []) + label_set)),
                           actor=actor)
        backlog.send_back(feature_id, note=f"bug reported: {evidence}")
        action = "sent-back"
    elif status in ("pending", "approved"):
        backlog.set_labels(feature_id,
                           list(dict.fromkeys((feature.get("labels") or []) + label_set)),
                           actor=actor)
        backlog.add_thread(feature_id, actor=actor, kind="evidence", body=evidence)
        action = "attached"
    else:
        raise IssueError(f"feature {feature_id} is {status}; bugs cannot attach to it")
    return issue, action
