from .base import Adapter


class ClaudeCodeAdapter(Adapter):
    """The user's own Claude Code install (their subscription, their login). Headless print mode.

    The role's gateway catalogue maps to ``--tools`` (restricts which built-in tools exist in the
    session; verified against the current CLI reference - ``--allowedTools`` only skips permission
    prompts, it does not restrict) plus ``--disallowedTools mcp__*`` so MCP tools cannot widen it.
    This is CLI configuration, NOT an OS boundary: a model that gets Bash can do anything Bash can.
    The boundary, when one is required, is the jail the job runs under.
    """
    name = "claude-code"
    binary = "claude"

    def build_command(self, contract: dict) -> list[str]:
        prompt = contract.get("brief") or contract["goal"]
        command = [self.binary, "-p", prompt, "--output-format", "text"]
        tools = (contract.get("tools") or [])
        if tools:
            command += ["--tools", ",".join(tools), "--disallowedTools", "mcp__*"]
        return command
