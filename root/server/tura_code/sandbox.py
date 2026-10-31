"""Where the agent's commands run: directly (the local app) or in a bubblewrap jail.

A hosted install runs other people's agents on one machine, and an agent can
run any shell command it is allowed to. So in hosted mode every runtime
process — and with it every ``Shell`` call, every background process, every
coding-flow check — runs under `bubblewrap <https://github.com/containers/bubblewrap>`_:

* the system (``/usr``, ``/etc``, ``/opt``…) is visible read-only;
* the session's workspace, its runtime directory and the person's home are
  the only writable places; other people's files and the server's own data
  directory are simply not there;
* ``/tmp`` is private, and the process, IPC and hostname namespaces are new;
* the network is shared, because the runtime must reach the model (through
  the host's LLM proxy — the real API key never enters the jail, see
  ``llm_proxy``) and the web.

Two network modes (``Bubblewrap(network=...)``):

* shared (default, and what hosted mode uses): the jail sees the host's network, so any service on the host's loopback
  (the owner API, whose bootstrap hands out the owner token, a database admin page) is reachable from code in the jail.
  Files are isolated; the network is not.
* model-only (``network=False`` with a ``bridge``, job ``isolation: "bwrap-model-only"``): the jail gets its own empty
  network namespace. The only way out is a unix socket bind-mounted in, relayed onto the jail's own loopback port
  (``netrelay``) and forwarded by the host (``netbridge``) to the LLM proxy. No internet, no host loopback. Package
  installs and web access from jailed code therefore fail by design.

What neither mode is: a kernel boundary. It is one shared kernel with no seccomp profile of our own; the workspace is
writable and shared with the host, so a job can plant files (hooks, scripts, symlinks) that later host-side use may run.
Requested but unavailable isolation is an error (see ``jobs``), never a silent fallback.

The wire protocol is stdio, so a wrapped runtime is indistinguishable from a
direct one to the host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

RELAY_PYTHON = "/usr/bin/python3"
SYSTEM_PATHS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc", "/opt", "/nix", "/snap")


@dataclass(frozen=True)
class Mounts:
    workspace: Path
    writable: tuple[Path, ...] = ()
    readable: tuple[Path, ...] = ()
    home: Path | None = None


class Sandbox:
    """No isolation: the local app, where the agent works on the owner's own machine."""

    name = "none"

    def problem(self) -> str:
        return ""

    def wrap(self, argv: list[str], mounts: Mounts) -> list[str]:
        return list(argv)

    def environment(self, env: dict[str, str], mounts: Mounts) -> dict[str, str]:
        return env


def git_control_paths(roots: "list[Path]") -> "list[Path]":
    found: list[Path] = []
    for root in roots:
        dot = root / ".git"
        gitdirs: list[Path] = []
        try:
            if dot.is_dir():
                gitdirs.append(dot)
            elif dot.is_file() and dot.read_text(errors="replace").startswith("gitdir:"):
                gitdir = (root / dot.read_text(errors="replace")[7:].strip()).resolve()
                gitdirs.append(gitdir)
                common = gitdir / "commondir"
                if common.is_file():
                    gitdirs.append((gitdir / common.read_text().strip()).resolve())
        except OSError:
            continue
        for gitdir in gitdirs:
            for name in ("hooks", "config"):
                path = gitdir / name
                if path.exists() and not path.is_symlink() and path not in found:
                    found.append(path)
    return found


class Bubblewrap(Sandbox):
    name = "bwrap"

    def __init__(self, binary: str | None = None, network: bool = True, bridge: "tuple[Path, int] | None" = None) -> None:
        self.binary = binary or shutil.which("bwrap") or "bwrap"
        #: True: the host's network (so the host's loopback services, the owner API included, are reachable from the jail).
        #: False: the jail's own empty network; the only way out is ``bridge`` (unix socket, loopback port) to the LLM proxy.
        self.network = network
        self.bridge = bridge
        if not network:
            self.name = "bwrap-model-only"

    def problem(self) -> str:
        if not shutil.which(self.binary):
            return "bubblewrap (`bwrap`) is not installed."
        try:
            probe = subprocess.run(
                [self.binary, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-pid", "--die-with-parent", "true"],
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"bubblewrap cannot run: {error}"
        if probe.returncode != 0:
            return f"bubblewrap cannot create namespaces here: {probe.stderr.decode(errors='replace').strip()[:300]}"
        if not self.network:
            net = subprocess.run([self.binary, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-pid", "--unshare-net", "--die-with-parent", "true"], capture_output=True, timeout=10)
            if net.returncode != 0:
                return f"bubblewrap cannot create a network namespace here: {net.stderr.decode(errors='replace').strip()[:300]}"
            if self.bridge is not None and not os.path.exists(RELAY_PYTHON):
                return f"a network-isolated jail needs {RELAY_PYTHON} to relay the model connection."
        return ""

    def wrap(self, argv: list[str], mounts: Mounts) -> list[str]:
        args = [self.binary, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try"]
        if not self.network:
            args.append("--unshare-net")
        for path in SYSTEM_PATHS:
            if os.path.islink(path):
                args += ["--symlink", os.readlink(path), path]
            elif os.path.isdir(path):
                args += ["--ro-bind", path, path]
        args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/run"]
        # A resolv.conf that is a symlink into /run must survive the tmpfs.
        resolv = Path("/etc/resolv.conf")
        if resolv.is_symlink():
            target = resolv.resolve()
            if target.exists():
                args += ["--ro-bind", str(target), str(target)]
        for path in mounts.readable:
            if path.exists():
                args += ["--ro-bind", str(path), str(path)]
        writable = [mounts.workspace, *mounts.writable]
        if mounts.home is not None:
            writable.append(mounts.home)
        for path in writable:
            path.mkdir(parents=True, exist_ok=True)
            args += ["--bind", str(path), str(path)]
        # Hooks and config run or redirect host-side git later, so jailed code gets them read-only. This does NOT make a
        # writable workspace safe: package scripts, Makefiles, CI files and `.githooks` are still plantable.
        for control in git_control_paths(writable):
            args += ["--ro-bind", str(control), str(control)]
        args += ["--chdir", str(mounts.workspace)]
        if not self.network and self.bridge is not None:
            from .netrelay import __file__ as relay_file

            sock, port = self.bridge
            args += ["--ro-bind", relay_file, "/run/root-netrelay.py", "--bind", str(sock), "/run/root-llm.sock"]
            return [*args, "--", RELAY_PYTHON, "/run/root-netrelay.py", str(port), "/run/root-llm.sock", "--", *argv]
        return [*args, "--", *argv]

    def environment(self, env: dict[str, str], mounts: Mounts) -> dict[str, str]:
        env = dict(env)
        if mounts.home is not None:
            env["HOME"] = str(mounts.home)
        else:
            env["HOME"] = "/tmp"  # the host's real home is not in the jail
        env["TMPDIR"] = "/tmp"
        return env


def from_name(name: str) -> Sandbox:
    name = (name or "none").strip().lower()
    if name in ("", "none", "off"):
        return Sandbox()
    if name in ("bwrap", "bubblewrap"):
        return Bubblewrap()
    raise ValueError(f"Unknown sandbox {name!r}; use `bwrap` or `none`.")
