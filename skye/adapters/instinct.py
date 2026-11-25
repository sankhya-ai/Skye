from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

from .base import Adapter, AdapterError, Result

UNSETTLED = ("queued", "running")


class InstinctAdapter(Adapter):
    """Instinct as a worker, the same way Claude Code is one.

    The owner points Skye at their Instinct with two settings: SKYE_INSTINCT_URL and
    SKYE_INSTINCT_TOKEN. Skye POSTs a job (goal, role, and the text files in the workspace
    root) to {url}/jobs, polls {url}/jobs/<id> until it settles, and writes back the files
    the job returns. Returned paths must stay inside the workspace; anything else is refused.
    Skye defines this small protocol; the Instinct side has to serve it. Until it does, this
    adapter is exercised only against a stub server."""
    name = "instinct"
    ENFORCED_BUDGET = ("timeout_seconds",)
    MAX_FILES = 40
    MAX_FILE_BYTES = 200_000

    def __init__(self, base_url: str = "", *, token: str = "", poll_seconds: float = 0.0,
                 git_repo: str = "") -> None:
        self.base_url = (base_url or os.environ.get("SKYE_INSTINCT_URL", "")).rstrip("/")
        self.token = token or os.environ.get("SKYE_INSTINCT_TOKEN", "")
        # Git mode (no server needed): jobs travel through a git repo the owner's Instinct can
        # read and push to. SKYE_INSTINCT_REPO is a git URL or path; SKYE_INSTINCT_BRANCH
        # (default "skye-jobs") holds jobs/<id>/job.json and, once done, jobs/<id>/result.json.
        self.git_repo = git_repo or os.environ.get("SKYE_INSTINCT_REPO", "")
        self.git_branch = os.environ.get("SKYE_INSTINCT_BRANCH", "") or "skye-jobs"
        default_poll = 30.0 if self.git_repo and not self.base_url else 3.0
        try:
            self.poll_seconds = poll_seconds or float(os.environ.get("SKYE_INSTINCT_POLL_SECONDS", "") or default_poll)
        except ValueError:
            self.poll_seconds = 3.0

    @property
    def git_mode(self) -> bool:
        return bool(self.git_repo) and not self.base_url

    def _git(self, cwd: Path, *args: str, check: bool = True) -> str:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120, env=env)
        if check and proc.returncode:
            raise AdapterError(f"instinct: git {args[0]} failed: {proc.stderr.strip()[-300:]}")
        return proc.stdout

    def _git_clone(self, work: Path) -> None:
        """A working copy on the jobs branch; the branch is created (empty) if it is new."""
        proc = subprocess.run(["git", "clone", "--quiet", "--branch", self.git_branch, "--single-branch",
                               self.git_repo, str(work)], capture_output=True, text=True, timeout=120,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        if proc.returncode:
            subprocess.run(["git", "clone", "--quiet", self.git_repo, str(work)], capture_output=True,
                           text=True, timeout=120, check=False)
            if not (work / ".git").exists():
                raise AdapterError(f"instinct: cannot reach git repo {self.git_repo}")
            self._git(work, "checkout", "--quiet", "--orphan", self.git_branch)
            self._git(work, "rm", "-rf", "--quiet", ".", check=False)

    def _git_submit(self, work: Path, job_id: str, payload: dict) -> None:
        folder = work / "jobs" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "job.json").write_text(json.dumps(payload, indent=2), "utf-8")
        self._git(work, "add", "-A")
        self._git(work, "-c", "user.name=Skye", "-c", "user.email=skye@localhost",
                  "commit", "--quiet", "-m", f"job {job_id}")
        self._git(work, "push", "--quiet", "origin", f"HEAD:{self.git_branch}")

    def _git_result(self, work: Path, job_id: str) -> dict | None:
        self._git(work, "pull", "--quiet", "--rebase", "origin", self.git_branch, check=False)
        path = work / "jobs" / job_id / "result.json"
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def _run_git(self, contract: dict, timeout: int, resume_job_id: str) -> Result:
        repo = Path(contract["repo"])
        goal = contract.get("brief") or contract["goal"]
        if contract.get("role_instructions"):
            goal = contract["role_instructions"] + "\n\n" + goal
        job_id = resume_job_id or f"job-{uuid.uuid4().hex[:10]}"
        command = ["git", self.git_repo, self.git_branch, f"job={job_id}"]
        with tempfile.TemporaryDirectory(prefix="skye-instinct-") as tmp:
            work = Path(tmp) / "w"
            self._git_clone(work)
            if not resume_job_id:
                self._git_submit(work, job_id, {"id": job_id, "goal": goal, "role": contract.get("role", ""),
                                                "files": self._workspace_files(repo), "created": time.time()})
            if self.job_observer:
                self.job_observer(job_id)
            deadline = time.monotonic() + timeout
            item = self._git_result(work, job_id)
            while item is None:
                if time.monotonic() >= deadline:
                    return Result(ok=False, output=f"instinct job {job_id} not answered within {timeout}s",
                                  command=command, returncode=124, job_id=job_id)
                time.sleep(self.poll_seconds)
                item = self._git_result(work, job_id)
        return self._settle(repo, job_id, item, command)

    def available(self) -> bool:
        if self.git_mode:
            return subprocess.run(["git", "ls-remote", "--exit-code", self.git_repo], capture_output=True,
                                  timeout=20, check=False,
                                  env=dict(os.environ, GIT_TERMINAL_PROMPT="0")).returncode in (0, 2)
        if not (self.base_url and self.token):
            return False
        try:
            self._call("GET", "/health", timeout=3)
            return True
        except AdapterError:
            return False

    def _call(self, method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
        if not self.base_url:
            raise AdapterError("instinct: set SKYE_INSTINCT_URL and SKYE_INSTINCT_TOKEN first")
        request = urllib.request.Request(
            f"{self.base_url}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json", "authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as error:
            raise AdapterError(f"instinct: {method} {path} -> {error.code}", status_code=error.code) from error
        except (OSError, ValueError) as error:
            raise AdapterError(f"instinct: no answer at {self.base_url} ({error})") from error

    def _workspace_files(self, repo: Path) -> dict[str, str]:
        files: dict[str, str] = {}
        for item in sorted(repo.glob("*")):
            if item.is_file() and not item.name.startswith(".") and len(files) < self.MAX_FILES:
                try:
                    if item.stat().st_size <= self.MAX_FILE_BYTES:
                        files[item.name] = item.read_text("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
        return files

    @staticmethod
    def _safe_target(repo: Path, rel: str) -> Path | None:
        target = (repo / rel).resolve()
        try:
            target.relative_to(repo.resolve())
        except ValueError:
            return None
        return None if rel.startswith("/") or target.name.startswith(".") else target

    def run(self, contract: dict, timeout: int = 1800, resume_job_id: str = "") -> Result:
        if self.git_mode:
            return self._run_git(contract, timeout, resume_job_id)
        repo = Path(contract["repo"])
        goal = contract.get("brief") or contract["goal"]
        if contract.get("role_instructions"):
            goal = contract["role_instructions"] + "\n\n" + goal
        if resume_job_id:
            job_id = resume_job_id
            item = self._call("GET", f"/jobs/{job_id}")
        else:
            item = self._call("POST", "/jobs", {
                "goal": goal, "role": contract.get("role", ""),
                "files": self._workspace_files(repo)})
            job_id = str(item.get("id") or "")
        command = ["POST", f"{self.base_url}/jobs", f"job={job_id}"]
        if self.job_observer and job_id:
            self.job_observer(job_id)
        deadline = time.monotonic() + timeout
        while item.get("status") in UNSETTLED:
            if time.monotonic() >= deadline:
                return Result(ok=False, output=f"instinct job {job_id} did not settle within {timeout}s",
                              command=command, returncode=124, job_id=job_id)
            time.sleep(self.poll_seconds)
            item = self._call("GET", f"/jobs/{job_id}")
        return self._settle(repo, job_id, item, command)

    def _settle(self, repo: Path, job_id: str, item: dict, command: list[str]) -> Result:
        status = str(item.get("status") or "")
        written, refused = [], []
        for rel, text in list((item.get("files") or {}).items())[: self.MAX_FILES]:
            target = self._safe_target(repo, str(rel))
            if target is None or not isinstance(text, str) or len(text) > self.MAX_FILE_BYTES:
                refused.append(str(rel))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, "utf-8")
            written.append(str(rel))
        output = json.dumps({"job": job_id, "status": status, "reply": str(item.get("reply") or "")[-4000:],
                             "files_written": written, "files_refused": refused})[:8000]
        if status == "finished" and not refused:
            return Result(ok=True, output=output, command=command, job_id=job_id)
        return Result(ok=False, output=f"instinct job {job_id} ended '{status}': {output}",
                      command=command, returncode=1, job_id=job_id)
