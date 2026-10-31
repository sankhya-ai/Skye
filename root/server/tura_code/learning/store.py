"""Lesson candidates waiting for (or past) the owner's decision.

One JSON file per tenant, bounded: decided candidates beyond the newest
:data:`KEEP_DECIDED` are dropped, and rejected fingerprints are kept apart so
a lesson the owner turned down is not proposed again.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

KEEP_DECIDED = 200


class CandidateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("candidates", [])
        data.setdefault("rejected", [])
        data.setdefault("retired", [])
        return data

    def _save(self, data: dict[str, Any]) -> None:
        pending = [c for c in data["candidates"] if c["status"] == "candidate"]
        decided = [c for c in data["candidates"] if c["status"] != "candidate"][-KEEP_DECIDED:]
        data["candidates"] = pending + decided
        data["rejected"] = data["rejected"][-1000:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), "utf-8")
        tmp.replace(self.path)

    def add(self, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Store new candidates; skip any lesson already pending, promoted, or rejected.

        Returns ``(added, seen_again)``: a skill proposed again under the same
        name from another session is not a new candidate but a second sighting
        of a pending one, and its record lists every session that proposed it.
        """
        with self._lock:
            data = self._load()
            known = {c["fingerprint"] for c in data["candidates"]} | set(data["rejected"])
            retired = set(data["retired"])
            pending_skills = {c["title"]: c for c in data["candidates"] if c["status"] == "candidate" and c["kind"] == "skill"}
            added: list[dict[str, Any]] = []
            again: list[dict[str, Any]] = []
            for item in items:
                if item["kind"] == "skill" and item["title"] in retired:
                    continue
                pending = pending_skills.get(item["title"]) if item["kind"] == "skill" else None
                if pending is not None:
                    sessions = pending.setdefault("sessions", [pending.get("session_id")])
                    if item["session_id"] not in sessions:
                        sessions.append(item["session_id"])
                        again.append(pending)
                    continue
                if item["fingerprint"] in known:
                    continue
                known.add(item["fingerprint"])
                record = {"id": uuid.uuid4().hex[:12], "status": "candidate", "created_at": time.time(), "sessions": [item["session_id"]], **item}
                data["candidates"].append(record)
                added.append(record)
            if added or again:
                self._save(data)
            return added, again

    def get(self, candidate_id: str) -> dict[str, Any] | None:
        return next((c for c in self._load()["candidates"] if c["id"] == candidate_id), None)

    def decide(self, candidate_id: str, status: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            record = next((c for c in data["candidates"] if c["id"] == candidate_id), None)
            if record is None:
                raise KeyError(candidate_id)
            if record["status"] != "candidate":
                raise ValueError(f"already {record['status']}")
            record.update(status=status, decided_at=time.time(), result=result or {})
            if status == "rejected":
                data["rejected"].append(record["fingerprint"])
            self._save(data)
            return record

    def retire(self, title: str) -> None:
        """A skill the evidence turned against: never proposed again under this name."""
        with self._lock:
            data = self._load()
            if title not in data["retired"]:
                data["retired"].append(title)
                self._save(data)

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._load()["candidates"], key=lambda c: (c["status"] != "candidate", -c["created_at"]))
