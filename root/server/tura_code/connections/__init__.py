"""Connections: the accounts root may use for its owner — GitHub and Gmail.

``store`` (who is connected, credentials through ``vault``), ``github`` and
``github_tool`` (issues, pull requests, reviews, checks), ``google`` and
``gmail_tool`` (search, read, drafts — never send), ``routes``.

Every write to an outside service is a host action the owner approves; reading
is free. Tokens stay in the host: the runtime only ever sees tool results.
"""

from .github_tool import GitHubToolset
from .gmail_tool import GmailToolset
from .store import ConnectionStore

__all__ = ["ConnectionStore", "install"]


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.connections = ConnectionStore(tenant.root)
        tenant.toolsets += [GitHubToolset(tenant.connections), GmailToolset(tenant.connections)]

    server.extensions.append(extend)
