from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field


class AdapterError(RuntimeError):
    pass


@dataclass
class Result:
    ok: bool
    output: str
    command: list[str] = field(default_factory=list)
    returncode: int = 0


class Adapter:
    """One coding agent, driven through its own CLI/API. The worker's model is the worker's own
    subscription or key - Skye never proxies or pays for it."""

    name = "base"
    binary = ""

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
