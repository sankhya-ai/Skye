"""Each of your agents' record of the chamber, shared with the `chamber` tool.

    ~/.chamber/agents/<owner>__<name>/inbox.jsonl   messages it received
    ~/.chamber/agents/<owner>__<name>/sent.jsonl    messages it sent
    ~/.chamber/agents/<owner>__<name>/files/<id>/   files it received

The same files the Node tool writes for each project's Claude Code, so chamber's
board sees every conversation any of your agents is in, root's included.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import shared_home
from .protocol import Message


def agent_dir(agent: str) -> Path:
    return shared_home() / "agents" / agent.replace("/", "__")


class AgentLog:
    def __init__(self, agent: str) -> None:
        self.agent = agent
        self.dir = agent_dir(agent)

    def _append(self, name: str, record: dict[str, Any]) -> dict[str, Any]:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / name).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return record

    def received(self, message: Message) -> dict[str, Any]:
        files = []
        for item in message.files:
            if item.get("error"):
                files.append({"name": item["name"], "error": item["error"]})
                continue
            path = self.dir / "files" / message.id / item["name"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item["data"])
            files.append({"name": item["name"], "path": str(path), "size": len(item["data"])})
        return self._append("inbox.jsonl", {
            "id": message.id, "from": message.sender, "to": message.to, "reply_to": message.reply_to,
            "thread": message.thread, "title": message.title, "text": message.text, "ts": message.ts, "files": files,
            "mentions": message.mentions, "dir": "in",
        })

    def sent(self, receipt: dict[str, Any], *, to: str, text: str, reply_to: str | None = None, title: str = "", files: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return self._append("sent.jsonl", {
            "id": receipt["id"], "from": self.agent, "to": receipt.get("to") or to, "reply_to": reply_to,
            "thread": receipt.get("thread"), "title": title, "text": text, "ts": int(time.time() * 1000),
            "files": [{"name": f["name"], "size": len(f["data"])} for f in files or []], "mentions": receipt.get("mentions") or [], "dir": "out",
        })


def read_all(owner: str) -> list[dict[str, Any]]:
    """Every message any of ``owner``'s agents sent or received, oldest first, one copy each."""
    root = shared_home() / "agents"
    seen: dict[str, dict[str, Any]] = {}
    for directory in sorted(root.glob(f"{owner}__*")) if root.is_dir() else []:
        for name in ("sent.jsonl", "inbox.jsonl"):
            try:
                lines = (directory / name).read_text("utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("id"):
                    seen.setdefault(str(record["id"]), record)  # a message between two of your agents is in both logs
    return sorted(seen.values(), key=lambda r: r.get("ts") or 0)
