"""root: a coding-agent host for the kimi-agent-rs runtime.

The runtime owns the agent loop (planning, tools, approvals, compaction, the
Jev step director). This package owns everything around it: sessions, the
browser UI, permission modes, OpenRouter settings, and the typed Dhee memory
gateway.
"""

__version__ = "0.2.1"
