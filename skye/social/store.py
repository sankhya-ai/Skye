"""Owner-managed credentials file (skye.social.json, override SKYE_SOCIAL), mode 0600.
Values go in, only presence and dates come out."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .base import SocialError


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("SKYE_SOCIAL", "").strip() or "skye.social.json")

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def get(self, platform: str) -> dict[str, Any] | None:
        return self._read().get(platform)

    def creds(self, platform: str) -> dict[str, str]:
        rec = self.get(platform)
        if not rec:
            raise SocialError(f"{platform} is not connected")
        return dict(rec.get("credentials") or {})

    def save(self, connector, values: dict[str, Any]) -> None:
        if not connector.implemented:
            raise SocialError(f"{connector.name} is not available yet")
        creds = {k: str(values.get(k) or "").strip() for k in connector.fields}
        optional = {"service"}
        missing = [k for k, v in creds.items() if not v and k not in optional]
        if missing:
            raise SocialError("missing: " + ", ".join(missing))
        now = time.time()
        rec = {"credentials": creds, "saved_at": now,
               "expires_at": now + connector.token_days * 86400 if connector.token_days else None,
               "last_check": None}
        data = self._read()
        data[connector.id] = rec
        self._write(data)

    def note_check(self, platform: str, ok: bool, detail: str = "") -> None:
        data = self._read()
        if platform in data:
            data[platform]["last_check"] = {"at": time.time(), "ok": ok, "detail": detail[:200]}
            self._write(data)

    def remove(self, platform: str) -> None:
        data = self._read()
        data.pop(platform, None)
        self._write(data)
