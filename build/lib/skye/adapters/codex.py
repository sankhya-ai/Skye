from .base import Adapter


class CodexAdapter(Adapter):
    """The user's own Codex CLI install. Non-interactive ``codex exec`` mode, which runs read-only
    by default; the sandbox widens to workspace-write only when the role's gateway catalogue grants
    writes (verified against the current Codex non-interactive docs; ``--full-auto`` is deprecated
    and never used). The sandbox is Codex's own mechanism, not an OS boundary Skye provides.
    ``codex exec`` refuses to run outside a git repository unless told otherwise - Skye leaves that
    vendor safety check in place.
    """
    name = "codex"
    binary = "codex"
    # Only the wall-clock timeout is enforceable here; max_steps/max_cost_usd surface as notes.
    ENFORCED_BUDGET = ("timeout_seconds",)

    def build_command(self, contract: dict) -> list[str]:
        prompt = contract.get("brief") or contract["goal"]
        if contract.get("role_instructions"):
            prompt = contract["role_instructions"] + "\n\n" + prompt
        if contract.get("reference_docs"):
            refs = "\n\n".join(
                f"--- {d['path']} (owner-supplied project reference) ---\n{d['content']}"
                for d in contract["reference_docs"])
            prompt = "Project references:\n" + refs + "\n\n" + prompt
        writable = bool(contract.get("tools_writable"))
        sandbox = "workspace-write" if writable else "read-only"
        return [self.binary, "exec", "--sandbox", sandbox, prompt]
