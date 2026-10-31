"""Dhee memory: one gateway, per-person views, and the typed tools the agent calls."""

from .errors import MemoryScopeError, MemoryUnavailableError
from .gateway import MemoryGateway, MemoryView
from .scopes import OWNER_SCOPE, Packet, packet_envelope, project_scope, split_envelope
from .tools import CHECKPOINT_TOOL, MEMORY_ROLE, RECALL_TOOL, REMEMBER_TOOL, TOOL_NAMES, tool_definitions

__all__ = [
    "CHECKPOINT_TOOL",
    "MEMORY_ROLE",
    "OWNER_SCOPE",
    "RECALL_TOOL",
    "REMEMBER_TOOL",
    "TOOL_NAMES",
    "MemoryGateway",
    "MemoryScopeError",
    "MemoryUnavailableError",
    "MemoryView",
    "Packet",
    "packet_envelope",
    "project_scope",
    "split_envelope",
    "tool_definitions",
]
