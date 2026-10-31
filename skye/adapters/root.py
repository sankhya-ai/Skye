from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from .base import Adapter, AdapterError, Result

#: Job states that mean "no outcome yet" on Root's side. "waiting" is a PARKED job (outstanding
#: delegated work): a Skye worker contract is depth-0 and never parks, so it is treated as an
#: abnormal end for a worker, not polled forever.
UNSETTLED = ("running",)


class RootAdapter(Adapter):
    """Root (vendored at root/) running locally, driven through its real owner API.

    The adapter authenticates the way Root's own local UI does: the owner token Root wrote to
    <home>/token when the server started (0600, same machine). It starts the job with
    POST /api/jobs and then POLLS /api/jobs/<id> until the job settles - starting a job is not a
    result. Root's own role scope, isolation and auto-start policy still apply inside the job;
    Skye's budget travels in Root's own budget fields. The worker's model is whatever Root is
    configured with - the owner's setup, never a key Skye supplies.
    """
    name = "root"

    def __init__(self, base_url: str = "", *, token: str = "",
                 root_home: str | Path = "", poll_seconds: float = 2.0) -> None:
        # Configuration by environment (the same names Root itself uses where they apply):
        # SKYE_ROOT_URL, SKYE_ROOT_TOKEN, or SKYE_ROOT_HOME/TURA_CODE_HOME to read <home>/token.
        import os  # noqa: PLC0415

        self.base_url = (base_url or os.environ.get("SKYE_ROOT_URL", "") or "http://127.0.0.1:8788").rstrip("/")
        token = token or os.environ.get("SKYE_ROOT_TOKEN", "")
        root_home = root_home or os.environ.get("SKYE_ROOT_HOME", "") or os.environ.get("TURA_CODE_HOME", "")
        if not token and root_home:
            try:
                token = (Path(root_home) / "token").read_text("utf-8").strip()
            except OSError:
                token = ""
        self.token = token
        self.poll_seconds = poll_seconds

    def _call(self, method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json", "authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as error:
            raise AdapterError(f"root: {method} {path} -> {error.code}: {error.read()[:300]!r}") from error
        except OSError as error:
            raise AdapterError(f"root: no Root server answering at {self.base_url} ({error})") from error

    def available(self) -> bool:
        try:
            self._call("GET", "/api/jobs/roles", timeout=3)
            return True
        except AdapterError:
            return False

    def run(self, contract: dict, timeout: int = 1800) -> Result:
        budget = dict(contract.get("budget") or {})
        budget.setdefault("max_steps", 25)
        body = {"goal": contract.get("brief") or contract["goal"], "workspace": contract["repo"],
                "role": contract["role"], "budget": budget, "credentials": []}
        if contract.get("contract_keys"):
            body["contract"] = list(contract["contract_keys"])
        item = self._call("POST", "/api/jobs", body)
        job_id = item.get("id")
        command = ["POST", f"{self.base_url}/api/jobs", f"job={job_id}"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if item.get("status") not in UNSETTLED:
                break
            time.sleep(self.poll_seconds)
            item = self._call("GET", f"/api/jobs/{job_id}")
        else:
            return Result(ok=False, output=f"root job {job_id} did not settle within {timeout}s",
                          command=command, returncode=124)
        status = item.get("status", "")
        result = item.get("result") or {}
        contract_check = result.get("contract") or {}
        output = json.dumps({"job": job_id, "status": status, "reply": (result.get("reply") or "")[-4000:],
                             "contract": contract_check, "files_touched": result.get("files_touched")})[:8000]
        if status == "finished" and not result.get("error"):
            if contract_check and not contract_check.get("ok", True):
                return Result(ok=False, output=f"root job {job_id} finished but its contract failed: {output}",
                              command=command, returncode=1)
            return Result(ok=True, output=output, command=command)
        return Result(ok=False, output=f"root job {job_id} ended '{status}': {output}", command=command, returncode=1)
