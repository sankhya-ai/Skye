"""Memory errors."""


class MemoryUnavailableError(RuntimeError):
    """Dhee is off, missing, or failed to start."""


class MemoryScopeError(PermissionError):
    """The session may not read or write that memory scope (a delegated worker reaches project memory only)."""
