from __future__ import annotations

import json
import urllib.request

from .base import Adapter, AdapterError, Result


class RootAdapter(Adapter):
    """Root (vendored at root/) running locally: the owner-authenticated job start on its API.
    The budget travels in Root's own fields; Root's isolation and auto-start policy still apply."""
    name = "root"

    def __init__(self, base_url: str = "http://127.0.0.1:8788") -> None:
        self.base_url = base_url.rstrip("/")

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/jobs/roles", timeout=3) as resp:
                return resp.status == 200
        except OSError:
            return False

    def run(self, contract: dict, timeout: int = 1800) -> Result:
        if not self.available():
            raise AdapterError(f"root: no Root server answering at {self.base_url} - start the vendored Root first")
        body = json.dumps({"goal": contract["brief"] or contract["goal"], "workspace": contract["repo"],
                           "role": contract["role"], "budget": contract.get("budget") or {}}).encode()
        request = urllib.request.Request(f"{self.base_url}/api/jobs", data=body,
                                         headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=30) as resp:
            item = json.loads(resp.read())
        return Result(ok=True, output=f"root job {item.get('id')} started", command=["POST", f"{self.base_url}/api/jobs"])
