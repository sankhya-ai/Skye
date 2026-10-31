"""Documents: reading and editing what is not plain text — PDF, Word, Excel, PowerPoint, CSV.

``readers`` (text out of each format), ``sheets`` (editing workbooks and CSV),
``pdfs`` (pages, forms, text, metadata, passwords), ``tool`` (the agent's
three tools and their approvals).
"""

from .tool import EDIT_TOOLS, DocumentToolset

__all__ = ["DocumentToolset", "EDIT_TOOLS", "install"]


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.toolsets.append(DocumentToolset())

    server.extensions.append(extend)
