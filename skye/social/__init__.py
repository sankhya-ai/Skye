"""Social connectors: one small module per platform, behind one approval-gated publish path.

Design for upkeep: every platform is a declarative spec (API base, pinned API version, limits,
token lifetime, review requirement) plus one `publish` function and one `check` probe, all
going through an injectable transport so tests replay fixtures and never touch the network.
Nothing here runs without an owner action; secrets live in a 0600 file and never come back out
of the API.
"""

from .base import Connector, SocialError, Transport, urllib_transport  # noqa: F401
from .registry import PLATFORMS, get, status_rows  # noqa: F401
