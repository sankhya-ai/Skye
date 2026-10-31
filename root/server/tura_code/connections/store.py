"""One person's connections: which accounts root may use for them, and the credentials.

``connections.json`` (0600, in the tenant's directory) holds what each
connection is — the GitHub login, the Gmail address, when it was connected —
and, unless the Keychain holds them (``vault``), the tokens themselves. A
token never leaves the host: tools call the services on the agent's behalf.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .. import vault as vaults

FILE = "connections.json"


class ConnectionStore:
    def __init__(self, root: Path) -> None:
        self.path = root / FILE
        self.vault = vaults.for_root(root)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, self.path)

    def get(self, name: str) -> dict[str, Any] | None:
        """The connection's record, credentials included (host only)."""
        with self._lock:
            entry = self._read().get(name)
        if entry is None:
            return None
        entry = dict(entry)
        if not self.vault.inline:
            entry["secret"] = json.loads(self.vault.get(f"connection:{name}") or "{}")
        return entry

    def put(self, name: str, *, secret: dict[str, Any], **info: Any) -> None:
        entry: dict[str, Any] = {"connected_at": time.time(), **info}
        if self.vault.inline:
            entry["secret"] = secret
        else:
            self.vault.set(f"connection:{name}", json.dumps(secret))
        with self._lock:
            data = self._read()
            data[name] = entry
            self._write(data)

    def update_secret(self, name: str, secret: dict[str, Any]) -> None:
        """A refreshed access token, keeping everything else."""
        if not self.vault.inline:
            self.vault.set(f"connection:{name}", json.dumps(secret))
            return
        with self._lock:
            data = self._read()
            if name in data:
                data[name]["secret"] = secret
                self._write(data)

    def remove(self, name: str) -> bool:
        if not self.vault.inline:
            self.vault.delete(f"connection:{name}")
        with self._lock:
            data = self._read()
            found = data.pop(name, None) is not None
            if found:
                self._write(data)
        return found

    def public(self, name: str) -> dict[str, Any] | None:
        """What the UI may see: never a credential."""
        entry = self.get(name)
        if entry is None:
            return None
        entry.pop("secret", None)
        return entry
