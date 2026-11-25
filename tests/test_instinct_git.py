"""Instinct adapter in git mode: a job travels through a bare repo and is answered from the other side."""
from __future__ import annotations

import subprocess
import threading
import time

from skye import instinct_queue
from skye.adapters.instinct import InstinctAdapter


def bare(tmp_path):
    repo = tmp_path / "jobs.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(repo)], check=True)
    return str(repo)


def test_job_round_trip(tmp_path):
    repo = bare(tmp_path)
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "business-profile.md").write_text("# Cafe\n", "utf-8")
    seen = {}

    def instinct_side():
        for _ in range(100):
            jobs = instinct_queue.pending(repo)
            if jobs:
                seen["job"] = jobs[0]
                instinct_queue.answer(repo, jobs[0]["id"], {
                    "status": "finished", "reply": "ok",
                    "files": {"drafts/day-1.md": "hook", "../evil.md": "x"}})
                return
            time.sleep(0.1)

    t = threading.Thread(target=instinct_side); t.start()
    a = InstinctAdapter(git_repo=repo, poll_seconds=0.1)
    assert a.available()
    r = a.run({"repo": str(ws), "goal": "write day 1", "role": "designer"}, timeout=30)
    t.join()
    assert seen["job"]["files"] == {"business-profile.md": "# Cafe\n"}
    assert not r.ok and "evil" in r.output  # an escaping path is refused, job reported failed
    assert (ws / "drafts" / "day-1.md").read_text() == "hook"
    assert not (tmp_path / "evil.md").exists()


def test_timeout_when_unanswered(tmp_path):
    repo = bare(tmp_path)
    ws = tmp_path / "ws"; ws.mkdir()
    r = InstinctAdapter(git_repo=repo, poll_seconds=0.1).run({"repo": str(ws), "goal": "g"}, timeout=1)
    assert not r.ok and r.returncode == 124
    assert len(instinct_queue.pending(repo)) == 1
