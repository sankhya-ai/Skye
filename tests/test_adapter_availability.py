"""Adapter availability records: additive to /api/roles, honest about what is probed."""
from __future__ import annotations

import os
import stat

from tests.test_ui_server import ui_server, _req


def test_bare_adapters_list_unchanged_and_details_additive(ui_server):
    url, _ = ui_server
    code, data = _req(url, "/api/roles")
    assert code == 200
    assert data["adapters"] == ["root", "claude-code", "codex"]  # bare names, as before
    details = {d["name"]: d for d in data["adapter_details"]}
    assert set(details) == {"root", "claude-code", "codex"}
    assert details["claude-code"]["probe"] == "binary-on-PATH"
    assert details["claude-code"]["binary"] == "claude"
    assert details["root"]["probe"] == "local-server"
    for record in details.values():
        assert "only exercised by a real dispatch" in record["auth"]


def test_binary_probe_reflects_path(ui_server, tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    _, data = _req(ui_server[0], "/api/roles")
    details = {d["name"]: d for d in data["adapter_details"]}
    assert details["claude-code"]["available"] is True   # binary now on PATH
    assert details["codex"]["available"] is False        # codex still absent
    assert details["root"]["available"] is False         # no server running here


def test_availability_matches_dispatch_rejection(ui_server):
    """The selector's flag is the same probe dispatch uses: unavailable here means dispatch
    would raise the 'not installed' AdapterError, not something else."""
    from skye.adapters import ADAPTERS
    _, data = _req(ui_server[0], "/api/roles")
    for record in data["adapter_details"]:
        assert record["available"] == bool(ADAPTERS[record["name"]]().available())
