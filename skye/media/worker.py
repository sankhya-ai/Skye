"""Owner-approved agent proposal, validated import, then trusted local rendering."""
from __future__ import annotations
import time
from pathlib import Path
from . import handoff
from .model import MediaError, digest, load, lock, read_json, revision, safe_file, write_json


def run_edit(state: Path, root: Path, proposal_id: str, progress, *, runner=None, goal: str = ""):
    # Resolve the proposal through the binding before loading its outside-worker contract.
    import re
    if not re.fullmatch(r"[a-f0-9]{16}", proposal_id):
        raise MediaError("Invalid proposal")
    binding = read_json(safe_file(root, f"proposals/{proposal_id}.binding.json"))
    if binding["project"] != load(root)["id"]:
        raise MediaError("Proposal belongs to another project")
    if binding["revision"] != revision(root):
        raise MediaError("Project changed. Prepare a fresh edit before running the agent")
    from skye import approvals
    from skye.contract import validate
    from skye.dispatcher import dispatch
    from skye.policy import Policy
    from .renderer import render
    import json
    raw = read_json(safe_file(root, f"proposals/{proposal_id}.job.json"))
    expected_hash = read_json(safe_file(root, f"proposals/{proposal_id}.job-hash.json"))["sha256"]
    if digest(json.dumps(raw, sort_keys=True).encode()) != expected_hash:
        raise MediaError("Edit contract changed. Prepare a fresh edit")
    contract = validate(raw)
    expected_repo = safe_file(root, f"proposals/{proposal_id}")
    if Path(contract["repo"]).resolve() != expected_repo.resolve() or contract["role"] != "artist":
        raise MediaError("Edit contract changed")
    # This function is reached only from the explicit Approve and run button, never prepare.
    approval_file = state / "approvals.json"
    approvals.approve(contract, approval_file, note="Owner approved a selected-shot edit in Video")
    progress({"stage": "Editing selected shot"})
    outcome = dispatch(contract, Policy.load(), state_dir=state, approvals_path=approval_file, runner=runner)
    if outcome.status != "done":
        raise MediaError(f"Agent {outcome.status}: {outcome.reason}")
    progress({"stage": "Checking shot files"})
    label = ("Agent: " + goal.strip()[:48]) if goal.strip() else "Agent edit"
    handoff.apply(root, proposal_id, label)
    progress({"stage": "Rendering this shot"})
    result = render(root, "preview", progress, only=binding["shot"])
    return {**result, "answer": outcome.answer or outcome.worker_output[-1500:]}


def ask(state: Path, root: Path, shot_id: str, goal: str, adapter: str, progress, *, runner=None):
    """Prompt, run, import and preview in one go. Clicking Send in the editor is the owner's approval."""
    goal = goal.strip()
    if not goal:
        raise MediaError("Say what should change")
    entry = {"at": time.time(), "shot": shot_id, "goal": goal[:2000], "adapter": adapter, "status": "running"}
    try:
        progress({"stage": "Preparing shot"})
        proposal = handoff.prepare(root, shot_id, goal, adapter)
        result = run_edit(state, root, proposal["proposal"], progress, runner=runner, goal=goal)
        entry.update(status="done", answer=result["answer"])
        return result
    except BaseException as exc:
        entry.update(status="failed", error=str(exc)[-600:])
        raise
    finally:
        log(root, entry)


def log(root: Path, entry: dict):
    with lock(root):
        path = root / "agent-log.json"
        try:
            entries = read_json(path) if path.exists() else []
        except MediaError:
            entries = []
        write_json(path, [*entries, entry][-50:])
