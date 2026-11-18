"""Owner-approved agent proposal, validated import, then trusted local rendering."""
from __future__ import annotations
from pathlib import Path
from . import handoff
from .model import MediaError, load, read_json, safe_file


def run_edit(state: Path, root: Path, proposal_id: str, progress, *, runner=None):
    # Resolve the proposal through the binding before loading its outside-worker contract.
    import re
    if not re.fullmatch(r"[a-f0-9]{16}", proposal_id):
        raise MediaError("Invalid proposal")
    binding = read_json(safe_file(root, f"proposals/{proposal_id}.binding.json"))
    if binding["project"] != load(root)["id"]:
        raise MediaError("Proposal belongs to another project")
    from skye import approvals
    from skye.contract import validate
    from skye.dispatcher import dispatch
    from skye.policy import Policy
    from .renderer import render
    contract = validate(read_json(safe_file(root, f"proposals/{proposal_id}.job.json")))
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
    handoff.apply(root, proposal_id)
    progress({"stage": "Rendering preview"})
    return render(root, "preview", progress)
