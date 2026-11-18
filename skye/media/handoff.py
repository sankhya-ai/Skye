"""Disposable worker proposals. Never give a worker write access to trusted media state."""
from __future__ import annotations
import uuid
from pathlib import Path
import shutil
from .process import run, binary
from .model import lock, digest, MediaError, UNIT_FILES, files_for, load, revision, safe_file, update, write_json, read_json


def prepare(root: Path, shot_id: str, goal: str = "", adapter="claude-code") -> dict:
    with lock(root):
        return _prepare(root, shot_id, goal, adapter)


def _prepare(root: Path, shot_id: str, goal: str, adapter: str) -> dict:
    if adapter not in ("claude-code", "codex"):
        raise MediaError("Choose Claude Code or Codex")
    files = files_for(root, shot_id)
    folder = root / "proposals" / uuid.uuid4().hex[:16]
    folder.mkdir(parents=True)
    run([binary("git"), "-c", "init.defaultBranch=main", "init", str(folder)], 10)
    thumb = safe_file(root, f"units/{shot_id}/thumb.jpg")
    if thumb.exists():
        shutil.copyfile(thumb, folder / "reference.jpg")
    for name, body in files.items():
        (folder / name).write_text(body, "utf-8")
    write_json(folder / "proposal.json", {"project": load(root)["id"], "shot": shot_id, "revision": revision(root),
        "frames": next(u["end"]-u["start"] for u in load(root)["units"] if u["id"]==shot_id),
        "fps": 30})
    write_json(folder.parent / (folder.name + ".binding.json"), read_json(folder / "proposal.json"))
    goal = goal.strip() or "Revise this shot without changing its duration or neighbouring shots."
    brief = (goal + "\n\nEdit visual.html, captions.json and audio.json only. visual.html defines window.frame(t), "
             "where t is local seconds. The source is a canvas named source. Audio is configured in "
             "audio.json, not in HTML. Captions use local integer frames at 30fps. No external assets, "
             "network, npm imports, Date, timers or wall-clock animation. Do not edit proposal.json. "
             "The trusted importer rejects extra files and stale revisions. No need to render or run shell commands.")
    job = {"goal": goal, "brief": brief, "repo": str(folder), "role": "artist", "adapter": adapter,
           "claims": list(UNIT_FILES), "gates": [], "budget": {"timeout_seconds": 300, "max_steps": 20}}
    # Job lives outside the worker proposal, so only the expected four files are imported.
    write_json(folder.parent / (folder.name + ".job.json"), job)
    write_json(folder.parent / (folder.name + ".job-hash.json"), {"sha256": digest(json.dumps(job, sort_keys=True).encode())})
    return {"proposal": folder.name, "path": str(folder), "job": str(folder.parent / (folder.name + ".job.json")),
            "shot": shot_id}


def apply(root: Path, proposal_id: str):
    import re
    if not re.fullmatch(r"[a-f0-9]{16}", proposal_id):
        raise MediaError("Invalid proposal")
    folder = safe_file(root, "proposals/" + proposal_id)
    expected = {*UNIT_FILES, "proposal.json", ".git"}
    if (folder / "reference.jpg").exists():
        expected.add("reference.jpg")
    if {p.name for p in folder.iterdir()} != expected or any(p.is_symlink() or (p.name != ".git" and not p.is_file()) for p in folder.iterdir()):
        raise MediaError("Proposal changed files outside the selected shot")
    meta = read_json(folder / "proposal.json")
    if meta["project"] != load(root)["id"]:
        raise MediaError("Proposal belongs to another project")
    # Binding stored outside the writable proposal prevents re-targeting its metadata.
    binding = read_json(folder.parent / (proposal_id + ".binding.json"))
    if binding != meta:
        raise MediaError("Proposal metadata changed")
    files = {name: (folder / name).read_text("utf-8") for name in UNIT_FILES}
    return update(root, meta["shot"], meta["revision"], files)
