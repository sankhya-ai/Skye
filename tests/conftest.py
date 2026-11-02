"""Collection guard for the integration modules.

Skye itself is stdlib-only, but a few integration tests drive the vendored Root server and
import its third-party dependencies (fastapi, pydantic, ...) at module level. Without those
installed, collection used to ERROR and stop the whole run - so a fresh checkout could not run
`pytest` at all. They are now left out with a clear note instead; install Root's server
dependencies (`pip install -e root/server`) to run them.
"""

from __future__ import annotations

import importlib.util

_ROOT_DEPS = ("fastapi", "pydantic")
_NEEDS_ROOT = [
    "test_integration_root.py",
    "test_integration_mission.py",
    "test_integration_designer.py",
    "test_line_bridge.py",
]

_missing = [name for name in _ROOT_DEPS if importlib.util.find_spec(name) is None]
collect_ignore = list(_NEEDS_ROOT) if _missing else []


def pytest_report_header(config):
    if _missing:
        return (f"skye: skipping {len(_NEEDS_ROOT)} Root integration modules "
                f"(missing {', '.join(_missing)}; `pip install -e root/server` to run them)")
    return None
