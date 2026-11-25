"""The Instinct side of the git job queue: list the jobs waiting on the jobs branch and answer
one. Run by whoever plays Instinct for the owner (an Instinct agent woken on a schedule or a
repo event). Usage:
    python -m skye.instinct_queue pending REPO [BRANCH]
    python -m skye.instinct_queue answer REPO JOB_ID ANSWER.json [BRANCH]
ANSWER.json is {"status": "finished"|"failed", "reply": "...", "files": {"drafts/day-1.md": "..."}}."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _git(cwd, *args, check=True):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)
    if check and proc.returncode:
        raise RuntimeError(f"git {args[0]}: {proc.stderr.strip()[-300:]}")
    return proc.stdout


def _clone(repo: str, branch: str, into: Path) -> None:
    _git(".", "clone", "--quiet", "--branch", branch, "--single-branch", repo, str(into))


def pending(repo: str, branch: str = "skye-jobs") -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "w"
        try:
            _clone(repo, branch, work)
        except RuntimeError:
            return []
        jobs = []
        for folder in sorted((work / "jobs").glob("*")):
            if (folder / "job.json").exists() and not (folder / "result.json").exists():
                jobs.append(json.loads((folder / "job.json").read_text("utf-8")))
        return jobs


def answer(repo: str, job_id: str, result: dict, branch: str = "skye-jobs") -> None:
    if result.get("status") not in ("finished", "failed"):
        raise ValueError("status must be finished or failed")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "w"
        _clone(repo, branch, work)
        folder = work / "jobs" / job_id
        if not (folder / "job.json").exists():
            raise ValueError(f"no such job {job_id}")
        if (folder / "result.json").exists():
            raise ValueError(f"job {job_id} already answered")
        (folder / "result.json").write_text(json.dumps(result, indent=2), "utf-8")
        _git(work, "add", "-A")
        _git(work, "-c", "user.name=Instinct", "-c", "user.email=instinct@localhost",
             "commit", "--quiet", "-m", f"answer {job_id}")
        _git(work, "push", "--quiet", "origin", f"HEAD:{branch}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "pending":
        print(json.dumps(pending(args[1], *args[2:3]), indent=2))
    elif len(args) >= 4 and args[0] == "answer":
        answer(args[1], args[2], json.loads(Path(args[3]).read_text("utf-8")), *args[4:5])
        print("answered", args[2])
    else:
        print(__doc__)
