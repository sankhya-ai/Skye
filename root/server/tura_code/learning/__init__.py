"""The Hermes-style learning loop: turns become reviewed skills and memories."""

from .service import LearningService

__all__ = ["LearningService", "install"]


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.learning = LearningService(tenant)

    async def after_turn(tenant, runtime, turn, status) -> None:
        await tenant.learning.after_turn(runtime, turn, status)

    server.extensions.append(extend)
    server.after_turn_hooks.append(after_turn)
