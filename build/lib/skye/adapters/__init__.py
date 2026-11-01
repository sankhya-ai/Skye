"""Worker adapters: one per coding agent. Each builds its command from a known template and the
contract's fields, checks the tool exists, and runs it with a timeout. Missing tools fail loudly."""
from .base import Adapter, AdapterError, Result
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .root import RootAdapter

ADAPTERS = {"root": RootAdapter, "claude-code": ClaudeCodeAdapter, "codex": CodexAdapter}


def get(name: str) -> Adapter:
    try:
        return ADAPTERS[name]()
    except KeyError:
        raise AdapterError(f"unknown adapter '{name}': one of {', '.join(ADAPTERS)}") from None
