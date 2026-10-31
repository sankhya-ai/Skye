"""Coding flows on disk: one JSON file per flow, written atomically."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

STATUSES = ("preparing", "working", "needs_review", "ready_to_apply", "applied", "rolled_back", "failed", "cancelled")
TERMINAL = ("applied", "rolled_back", "failed", "cancelled")
_ID = re.compile(r"^flow\d{1,6}$")
MAX_EVENTS = 100


class FlowError(RuntimeError):
    pass


class FlowStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    def path(self, flow_id: str) -> Path:
        if not _ID.match(flow_id or ""):
            raise FlowError("coding flow not found")
        return self.root / f"{flow_id}.json"

    def next_id(self) -> str:
        """Short sequential ids (`flow1`, `flow2`…) are easy for a model to carry."""
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            numbers = [int(p.stem[4:]) for p in self.root.glob("flow*.json") if p.stem[4:].isdigit()]
            flow_id = f"flow{max(numbers, default=0) + 1}"
            self.path(flow_id).write_text("{}", "utf-8")  # reserve it
            return flow_id

    def load(self, flow_id: str) -> dict[str, Any]:
        try:
            flow = json.loads(self.path(flow_id).read_text("utf-8"))
        except (OSError, ValueError) as error:
            raise FlowError("coding flow not found") from error
        if not flow:
            raise FlowError("coding flow not found")
        return flow

    def save(self, flow: dict[str, Any]) -> dict[str, Any]:
        flow["updated_at"] = time.time()
        flow["events"] = flow.get("events", [])[-MAX_EVENTS:]
        path = self.path(flow["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(flow, indent=1), "utf-8")
        tmp.replace(path)
        return flow

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        flows = []
        for path in self.root.glob("flow*.json"):
            try:
                flow = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if flow:
                flows.append(flow)
        flows.sort(key=lambda f: f.get("created_at", 0), reverse=True)
        return flows[:limit]


def event(flow: dict[str, Any], kind: str, detail: str = "") -> None:
    flow.setdefault("events", []).append({"type": kind, "at": time.time(), "detail": detail[:500]})


def candidate(flow: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    for item in flow["candidates"]:
        if item["id"] == candidate_id:
            return item
    raise FlowError("coding flow candidate not found")


def public(flow: dict[str, Any]) -> dict[str, Any]:
    """What the UI and the model see: no worktree paths or internal state."""
    status = flow["status"]
    return {
        "id": flow["id"],
        "status": status,
        "task": flow["task"],
        "acceptance": flow["acceptance"],
        "verification_commands": flow["verification_commands"],
        "repo": flow["repo"],
        "base_commit": flow["base_commit"],
        "owner_session": flow["owner_session"],
        "created_at": flow["created_at"],
        "updated_at": flow.get("updated_at"),
        "candidates": [
            {
                "id": c["id"],
                "status": c["status"],
                "session_id": c.get("session_id", ""),
                "attempts": len(c["attempts"]),
                "verification": [{"command": v["command"], "exit_code": v["exit_code"], "output_tail": v["output_tail"][-2_000:]} for v in c["verification"]],
                "diff": {k: v for k, v in (c.get("diff") or {}).items() if k != "patch"} or None,
            }
            for c in flow["candidates"]
        ],
        "selected": flow.get("selected"),
        "judgment": flow.get("judgment"),
        "judge_error": flow.get("judge_error", ""),
        "apply": flow.get("apply"),
        "rollback": flow.get("rollback"),
        "events": flow.get("events", [])[-20:],
        "controls": {
            "can_start": status in ("preparing", "needs_review") and not flow.get("selected"),
            "can_stop": status == "working",
            "can_select": status == "needs_review" and sum(c["status"] == "verified" for c in flow["candidates"]) > 1,
            "can_review": status == "ready_to_apply",
            "can_apply": status == "ready_to_apply",
            "can_cancel": status not in TERMINAL,
            "can_rollback": status == "applied",
        },
    }
