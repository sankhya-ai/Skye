"""root behind Keiki (onkeiki.com): WhatsApp, iMessage, Slack, Telegram, SMS and email, through one connection.

Keiki's external harness in inbox mode: ``client`` (claim a turn, answer through
its turn API), ``service`` (a read-only session per conversation, the turn, the
reply), ``routes``. Connect with the agent's harness API key from Keiki's
dashboard; no public URL is needed.
"""

from .service import KeikiService

__all__ = ["KeikiService", "install"]


def install(server) -> None:
    server.keiki = KeikiService(server)
    server.background.append(server.keiki)
