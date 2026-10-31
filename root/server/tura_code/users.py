"""The people a hosted install serves. The local app has one implicit owner.

Each user has a token (shown once when created, stored only as a SHA-256
hash) and gets a private tenant: their own sessions, settings, trust ledger,
learned skills, memory scope and workspaces. See ``tenancy``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class UserStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.path.read_text("utf-8")).get("users", {})
        except (OSError, ValueError):
            return {}

    def _save(self, users: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"users": users}, indent=1), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def add(self, user_id: str, *, name: str = "", admin: bool = False) -> str:
        """Create a user; return their token (the only time it is visible)."""
        user_id = user_id.strip().lower()
        if not _ID.match(user_id):
            raise ValueError("A user id is 1-32 lowercase letters, digits, `-` or `_`.")
        token = f"rt_{secrets.token_urlsafe(32)}"
        with self._lock:
            users = self._load()
            if user_id in users:
                raise ValueError(f"User {user_id!r} already exists.")
            users[user_id] = {"name": name or user_id, "admin": admin, "token_sha256": _hash(token), "created_at": time.time()}
            self._save(users)
        return token

    def rotate(self, user_id: str) -> str:
        token = f"rt_{secrets.token_urlsafe(32)}"
        with self._lock:
            users = self._load()
            if user_id not in users:
                raise KeyError(user_id)
            users[user_id]["token_sha256"] = _hash(token)
            self._save(users)
        return token

    def remove(self, user_id: str) -> bool:
        with self._lock:
            users = self._load()
            if users.pop(user_id, None) is None:
                return False
            self._save(users)
            return True

    def authenticate(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        digest = _hash(token)
        for user_id, user in self._load().items():
            if hmac.compare_digest(user.get("token_sha256", ""), digest):
                return {"id": user_id, "name": user.get("name", user_id), "admin": bool(user.get("admin"))}
        return None

    def list(self) -> list[dict[str, Any]]:
        return [{"id": uid, "name": u.get("name", uid), "admin": bool(u.get("admin")), "created_at": u.get("created_at")} for uid, u in sorted(self._load().items())]
