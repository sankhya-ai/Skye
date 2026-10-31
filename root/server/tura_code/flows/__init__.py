"""Coding flows: isolated worktree candidates, host verification, judged, applied by hash."""

from .service import FlowService
from .tool import FlowToolset

__all__ = ["FlowService", "install"]


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.flows = FlowService(tenant)
        tenant.toolsets.append(FlowToolset())
        tenant.closers.append(tenant.flows.close)

    server.extensions.append(extend)
