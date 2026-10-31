from .base import Adapter


class ClaudeCodeAdapter(Adapter):
    """The user's own Claude Code install (their subscription, their login). Headless print mode;
    the role's tool catalogue becomes --allowedTools when the gateway names one."""
    name = "claude-code"
    binary = "claude"

    def build_command(self, contract: dict) -> list[str]:
        prompt = contract.get("brief") or contract["goal"]
        command = [self.binary, "-p", prompt, "--output-format", "text"]
        tools = (contract.get("tools") or [])
        if tools:
            command += ["--allowedTools", ",".join(tools)]
        return command
