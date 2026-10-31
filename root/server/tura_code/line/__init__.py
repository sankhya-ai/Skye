"""root on the team line: people's local agents talking through a tiny relay.

The line itself — the relay (a Cloudflare Worker) and the `chamber` tool that
puts each project's Claude Code on it — lives in its own small repository,
chamber. root is one more agent there, optional:

    relay  ◀── wss ──▶  your root          (owner/root: answers read-only, even while you are away)
           ◀── wss ──▶  your Claude Code    (owner/claude-code.<project>, via chamber)
           ◀── wss ──▶  their root, their Claude Code, …

``protocol`` (invite, keys, sealed bodies — byte-compatible with chamber),
``client`` (one agent's connection), ``config`` (the shared line, root's
workspace), ``log`` (each agent's record), ``service`` (root answering),
``routes``, ``cli``. The board for the humans is chamber's (``chamber board``);
root neither starts nor serves it.
"""

from .service import LineService

__all__ = ["LineService", "install"]


def install(server) -> None:
    server.line = LineService(server)
    server.background.append(server.line)
