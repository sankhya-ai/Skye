from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field


class AdapterError(RuntimeError):
    """An adapter failure. status_code carries the HTTP status when one was involved, so
    callers can tell "definitively gone" (404) from "cannot reach" (connection error)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Result:
    ok: bool
    output: str
    command: list[str] = field(default_factory=list)
    returncode: int = 0
    job_id: str = ""  # the worker's own job/run id when the adapter has one (Root does)


class Adapter:
    """One coding agent, driven through its own CLI/API. The worker's model is the worker's own
    subscription or key - Skye never proxies or pays for it."""

    name = "base"
    binary = ""
    #: Budget fields this adapter enforces as a hard bound (timeout_seconds is honored by the
    #: shared run() below). Fields in FORWARDED_BUDGET are passed into the worker's own budget
    #: machinery, whose enforcement may depend on its configuration - forwarded, not guaranteed.
    #: Anything else in a contract's budget is reported as a note, never silently ignored.
    ENFORCED_BUDGET: tuple = ("timeout_seconds",)
    FORWARDED_BUDGET: tuple = ()
    #: Optional callable the dispatcher sets to learn the worker's job id the moment it exists
    #: (used to journal it for crash recovery before the worker settles).
    job_observer = None

    def available(self) -> bool:
        return bool(self.binary) and shutil.which(self.binary) is not None

    def build_command(self, contract: dict) -> list[str]:
        raise NotImplementedError

    def run(self, contract: dict, timeout: int = 1800) -> Result:
        if not self.available():
            raise AdapterError(
                f"{self.name}: the '{self.binary}' tool is not installed on this machine. "
                "Install it yourself - Skye uses YOUR coding agent, it does not download one.")
        command = self.build_command(contract)
        try:
            proc = subprocess.run(command, cwd=contract["repo"], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Result(ok=False, output=f"timed out after {timeout}s", command=command, returncode=124)
        return Result(ok=proc.returncode == 0, output=(proc.stdout + proc.stderr)[-8000:],
                      command=command, returncode=proc.returncode)
