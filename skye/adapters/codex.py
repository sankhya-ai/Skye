from .base import Adapter


class CodexAdapter(Adapter):
    """The user's own Codex CLI install. Non-interactive exec mode; sandbox stays read-only unless
    the role's gateway catalogue grants writes."""
    name = "codex"
    binary = "codex"

    def build_command(self, contract: dict) -> list[str]:
        prompt = contract.get("brief") or contract["goal"]
        writable = bool(contract.get("tools_writable"))
        sandbox = "workspace-write" if writable else "read-only"
        return [self.binary, "exec", "--sandbox", sandbox, prompt]
