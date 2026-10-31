"""root in Slack: @mention it in a channel or thread, or message it directly.

``api`` (the Web API and Socket Mode — root opens the connection, so no public
URL), ``chat`` (when root is called, the conversation it reads, how answers look
in Slack; pure), ``service`` (a read-only session per thread, the turn, the
reply), ``routes``. Connect with two tokens from a Slack app made from
``service.manifest()``.
"""

from .service import SlackService

__all__ = ["SlackService", "install"]


def install(server) -> None:
    server.slack = SlackService(server)
    server.background.append(server.slack)
