"""Root's Python toolbox: one virtual environment per person, owned by root.

Made on first use from the interpreter root itself runs on (in the macOS app,
the bundled Python), so the agent has a working Python on any machine. Packages
the agent needs for a task — pandas, reportlab, matplotlib — go here, never
into the system, the owner's own environments, or the project. It persists, so
a package is installed once.

Requirements are plain PyPI names with optional extras and versions; URLs,
paths and pip options are refused. ``uv`` installs when it is on the machine
(fast), else the toolbox's own pip.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Callable

from .. import hostos

INSTALL_TIMEOUT = 600.0
_REQUIREMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?(\s*(==|>=|<=|~=|!=|>|<)\s*[A-Za-z0-9.*+!_-]+\s*,?)*$")


class ToolboxError(RuntimeError):
    pass


def requirement_name(spec: str) -> str:
    """The distribution name of a requirement, normalized (PEP 503)."""
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", spec.strip())
    return re.sub(r"[-_.]+", "-", match.group(0)).lower() if match else ""


def check_requirements(specs: list[str]) -> list[str]:
    clean = []
    for spec in specs:
        spec = str(spec).strip()
        if not spec:
            continue
        if not _REQUIREMENT.match(spec):
            raise ToolboxError(f"{spec!r} is not a package name: give PyPI names like `pandas` or `reportlab>=4` (no URLs, paths or pip options).")
        clean.append(spec)
    return clean


def base_python() -> str:
    """The interpreter the toolbox is made from: root's own, without its virtualenv."""
    return getattr(sys, "_base_executable", "") or sys.executable


def _kill_group(pid: int) -> None:
    hostos.stop_tree(pid)


async def run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float) -> tuple[int, str, str]:
    """Run, capturing output; on timeout the whole process group is killed and the code is -9."""
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **hostos.spawn_options()
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        _kill_group(process.pid)
        out, err = await process.communicate()
        return -9, out.decode(errors="replace"), err.decode(errors="replace") + f"\n[stopped after {int(timeout)}s]"
    except asyncio.CancelledError:
        _kill_group(process.pid)  # the turn was cancelled: nothing left running for it
        raise
    return process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


class Toolbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = asyncio.Lock()

    @property
    def venv(self) -> Path:
        return self.root / "venv"

    @property
    def bin(self) -> Path:
        return hostos.venv_bin(self.venv)

    @property
    def python(self) -> Path:
        return hostos.venv_python(self.venv)

    @property
    def ready(self) -> bool:
        return self.python.exists()

    @staticmethod
    def environment(env: dict[str, str]) -> dict[str, str]:
        env = {k: v for k, v in env.items() if k not in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONDONTWRITEBYTECODE")}
        env.update(PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", PYTHONNOUSERSITE="1", MPLBACKEND="Agg")
        return env

    async def ensure(self, *, env: dict[str, str], wrap: Callable[[list[str]], list[str]]) -> None:
        """Make the environment if it does not exist yet."""
        async with self._lock:
            if self.ready:
                return
            self.root.mkdir(parents=True, exist_ok=True)
            code, out, err = await run(wrap([base_python(), "-m", "venv", str(self.venv)]), cwd=self.root, env=self.environment(env), timeout=180)
            if code != 0 or not self.ready:
                shutil.rmtree(self.venv, ignore_errors=True)
                raise ToolboxError(f"Could not make root's Python toolbox: {(out + err).strip()[-600:]}")

    async def missing(self, specs: list[str], *, env: dict[str, str], wrap: Callable[[list[str]], list[str]]) -> list[str]:
        """The requirements whose distribution is not installed in the toolbox yet."""
        if not specs:
            return []
        names = [requirement_name(s) for s in specs]
        probe = (
            "import importlib.metadata as m, json, sys\n"
            "have = []\n"
            "for name in json.loads(sys.argv[1]):\n"
            "    try:\n"
            "        m.version(name); have.append(name)\n"
            "    except m.PackageNotFoundError:\n"
            "        pass\n"
            "print(json.dumps(have))\n"
        )
        code, out, err = await run(wrap([str(self.python), "-c", probe, json.dumps(names)]), cwd=self.root, env=self.environment(env), timeout=60)
        if code != 0:
            raise ToolboxError(f"Could not read the toolbox's packages: {err.strip()[-400:]}")
        have = set(json.loads(out.strip().splitlines()[-1]))
        return [spec for spec, name in zip(specs, names) if name not in have]

    def install_argv(self, specs: list[str]) -> list[str]:
        uv = shutil.which("uv")
        if uv:
            return [uv, "pip", "install", "--python", str(self.python), *specs]
        return [str(self.python), "-m", "pip", "install", *specs]

    async def install(self, specs: list[str], *, env: dict[str, str], wrap: Callable[[list[str]], list[str]]) -> str:
        async with self._lock:
            code, out, err = await run(wrap(self.install_argv(specs)), cwd=self.root, env=self.environment(env), timeout=INSTALL_TIMEOUT)
        if code != 0:
            raise ToolboxError(f"Installing {' '.join(specs)} failed:\n{(out + err).strip()[-1500:]}")
        return (out + err).strip()[-600:]

    async def packages(self, *, env: dict[str, str], wrap: Callable[[list[str]], list[str]]) -> list[dict[str, str]]:
        """What is installed (for the Plugins page): ``[{"name", "version"}]``."""
        if not self.ready:
            return []
        probe = "import importlib.metadata as m, json\nprint(json.dumps(sorted({d.metadata['Name']: d.version for d in m.distributions()}.items())))"
        code, out, _ = await run(wrap([str(self.python), "-c", probe]), cwd=self.root, env=self.environment(env), timeout=60)
        if code != 0:
            return []
        return [{"name": n, "version": v} for n, v in json.loads(out.strip().splitlines()[-1]) if n.lower() not in ("pip", "setuptools")]
