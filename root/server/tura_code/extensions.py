"""The services layered on top of sessions: each adds toolsets, hooks and routes.

Kept apart from the core so a session knows nothing about learning or coding
flows, and each service can be read (and removed) on its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tenancy import Server


def install(server: "Server") -> None:
    """Register every service's tenant extension and after-turn hook."""
    from . import broker, catalogue, connections, genplugins, jobs, visual, guides, documents, driver, flows, history, keiki, learning, line, registry, schedules, slack, toolbox

    catalogue.install(server)
    registry.install(server)
    genplugins.install(server)
    jobs.install(server)
    visual.install(server)
    guides.install(server)
    broker.install(server)
    learning.install(server)
    documents.install(server)
    toolbox.install(server)
    connections.install(server)
    history.install(server)
    schedules.install(server)
    flows.install(server)
    line.install(server)
    slack.install(server)
    keiki.install(server)
    driver.install(server)


def routers(state) -> list:
    from .connections.routes import router as connections_router
    from .flows.routes import router as flows_router
    from .history import router as history_router
    from .keiki.routes import router as keiki_router
    from .learning.routes import router as learning_router
    from .genplugins import router as genplugins_router
    from .jobs import router as jobs_router
    from .registry import router as registry_router
    from .broker import router as broker_router
    from .line.routes import router as line_router
    from .plugins import router as plugins_router
    from .schedules import router as schedules_router
    from .slack.routes import router as slack_router
    from .toolbox import router as toolbox_router

    return [jobs_router(state), registry_router(state), genplugins_router(state), broker_router(state), learning_router(state), flows_router(state), line_router(state), connections_router(state), history_router(state), schedules_router(state), slack_router(state), keiki_router(state), toolbox_router(state), plugins_router(state)]
