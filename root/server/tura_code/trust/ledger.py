"""Owner decisions per action class, persisted across sessions."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

PROMOTE_AFTER = 3
TRUST_SCHEMA = "root.trust_ledger.v1"


class TrustLedger:
    """Owner decisions per action class, persisted across sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] | None = None

    def entry(self, key: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._load().get(key) or {"approvals": 0, "rejections": 0, "promoted": False})

    def is_promoted(self, key: str) -> bool:
        return bool(self.entry(key).get("promoted"))

    def record(self, key: str, *, approved: bool) -> dict[str, Any]:
        with self._lock:
            entries = self._load()
            entry = entries.setdefault(key, {"approvals": 0, "rejections": 0, "promoted": False})
            was = bool(entry.get("promoted"))
            if approved:
                entry["approvals"] = int(entry.get("approvals") or 0) + 1
                entry["promoted"] = entry["approvals"] >= PROMOTE_AFTER
            else:
                entry["rejections"] = int(entry.get("rejections") or 0) + 1
                entry["approvals"] = 0
                entry["promoted"] = False
            entry["updated_at"] = time.time()
            self._save(entries)
            return {**entry, "key": key, "just_promoted": entry["promoted"] and not was, "just_demoted": was and not entry["promoted"]}

    def revoke(self, key: str) -> bool:
        with self._lock:
            entries = self._load()
            if key not in entries:
                return False
            del entries[key]
            self._save(entries)
            return True

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [{"key": key, **value} for key, value in self._load().items()]
        items.sort(key=lambda item: (not item.get("promoted"), -float(item.get("updated_at") or 0)))
        return items

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._entries is None:
            try:
                raw = json.loads(self.path.read_text("utf-8"))
                stored = raw.get("entries") if isinstance(raw, dict) else None
                self._entries = {str(k): dict(v) for k, v in (stored or {}).items() if isinstance(v, dict)}
            except (OSError, ValueError):
                self._entries = {}
        return self._entries

    def _save(self, entries: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema_version": TRUST_SCHEMA, "entries": entries}, indent=1), "utf-8")
        tmp.replace(self.path)


