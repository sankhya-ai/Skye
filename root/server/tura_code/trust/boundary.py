"""The workspace boundary: what a request reaches outside the session's folder.

A session works in one folder. Reading, listing, or changing anything outside
it — a sibling project, the home directory, another run's files — is the
owner's call. On a local install "Auto-approve all" makes that call too: the
machine is the owner's, and a mode that still stops to ask is not auto. Hosted
mode asks in every mode — host tools read files outside any sandbox, so this
fence is what keeps one person's agent out of another's folders — and also
confines runtimes with bubblewrap. Unattended runs are refused either way.

Commands are read for paths the way a reviewer would: absolute and home paths,
`..` that climbs out, and `cd` changing where later relative paths land. It
is a best effort — code can build a path at run time — so it catches the
ordinary case of an agent wandering off, not a determined one; the sandbox is
what stops that.

Toolchain and system locations every build reads are allowed without asking.

On Windows the runtime's shell is PowerShell, so paths are read the Windows way
too: drive paths (``C:\\x``, ``C:/x``), shares (``\\\\server\\share``), ``..\\``,
``~\\``, ``$env:USERPROFILE`` and ``%USERPROFILE%``, compared without case;
``Set-Location``/``sl``/``pushd`` move the working folder as ``cd`` does.
"""

from __future__ import annotations

import os
import re
import shlex

from .. import hostos

#: Read-only system and toolchain locations any build may touch.
ALLOWED_PREFIXES = (
    "/usr/",
    "/bin/",
    "/sbin/",
    "/lib/",
    "/lib64/",
    "/opt/homebrew/",
    "/usr/local/",
    "/System/",
    "/Library/Developer/",
    "/Library/Frameworks/",
    "/Applications/Xcode.app/",
    "/nix/store/",
    "/proc/self/",
)
ALLOWED_EXACT = {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/urandom", "/dev/random"}
#: Dependency caches and toolchains under the home directory.
ALLOWED_HOME = (".cargo/registry", ".cargo/git", ".rustup", ".npm", ".nvm", ".pyenv", ".cache/pip", ".cache/uv", ".m2", ".gradle", "go/pkg", ".local/share/uv", ".bun")

if hostos.WINDOWS:
    _system = os.environ.get("SystemRoot", r"C:\Windows")
    ALLOWED_PREFIXES = tuple(
        os.path.join(folder, "")
        for folder in (
            _system,
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local")), "Programs"),
        )
    )
    ALLOWED_EXACT = {"NUL", "nul", "$null"}
    ALLOWED_HOME = tuple(rel.replace("/", "\\") for rel in ALLOWED_HOME) + (
        r"AppData\Local\pip\Cache",
        r"AppData\Local\uv",
        r"AppData\Local\npm-cache",
        r"AppData\Roaming\npm",
        r"scoop\apps",
    )

GRANT_PREFIX = "outside workspace: "

_ABSOLUTE = re.compile(r"(?<![\w.:/$~-])(?:~|\$HOME|\$\{HOME\})?/[\w.@%+=,:-][\w.@%+=,:/-]*|(?<![\w.:/$-])~(?=/|\s|$)")
_PARENT = re.compile(r"(?<![\w./-])\.\.(?:/[\w.@%+=,:-]*)*")
_SPLIT = re.compile(r"&&|\|\||[;|\n]")
_CD = {"cd"}
if hostos.WINDOWS:
    _HOME = r"(?:~|\$HOME|\$env:USERPROFILE|\$\{env:USERPROFILE\}|%USERPROFILE%)"
    _ABSOLUTE = re.compile(
        r"(?<![\w$%~-])(?:[A-Za-z]:[\\/]|\\\\|" + _HOME + r"(?=[\\/]|\s|$|[\"'])[\\/]?)[^\s'\"`;|&<>(){}]*",
        re.IGNORECASE,
    )
    _PARENT = re.compile(r"(?<![\w.\\/-])\.\.(?:[\\/][^\s'\"`;|&<>(){}\\/]*)*")
    _CD = {"cd", "chdir", "set-location", "sl", "pushd"}


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def _key(path: str) -> str:
    return os.path.normcase(path)


def inside(path: str, root: str) -> bool:
    real, base = _key(_real(path)), _key(_real(root))
    return real == base or real.startswith(base.rstrip(os.sep) + os.sep)


def allowed(path: str) -> bool:
    """A system or toolchain location, fine to read without asking."""
    if path in ALLOWED_EXACT:
        return True
    real = _key(_real(path))
    if real in {_key(p) for p in ALLOWED_EXACT}:
        return True
    if any(real.startswith(_key(p)) or _key(path).startswith(_key(p)) for p in ALLOWED_PREFIXES):
        return True
    home = os.path.realpath(os.path.expanduser("~"))
    return any(real.startswith(_key(os.path.join(home, rel)) + os.sep) or real == _key(os.path.join(home, rel)) for rel in ALLOWED_HOME)


def _expand(word: str) -> str:
    word = word.strip("'\"")
    if hostos.WINDOWS:
        word = re.sub(r"\$\{?env:USERPROFILE\}?|%USERPROFILE%", "~", word, flags=re.IGNORECASE)
    return os.path.expanduser(word.replace("${HOME}", "~").replace("$HOME", "~"))


def _argv(words: str) -> list[str]:
    """A command's words. Windows paths keep their backslashes (POSIX quoting would eat them)."""
    try:
        argv = shlex.split(words, posix=not hostos.WINDOWS)
    except ValueError:
        argv = words.split()
    return [word.strip("'\"") for word in argv] if hostos.WINDOWS else argv


def outside_paths(command: str, workspace: str) -> list[str]:
    """Paths this command reaches outside ``workspace``, resolved, in order.

    Relative paths resolve against the folder the command is in at that point:
    the workspace, or wherever an earlier ``cd`` in the same command went.
    """
    cwd = _real(workspace)
    found: list[str] = []
    for segment in _SPLIT.split(command):
        words = segment.strip()
        if not words:
            continue
        argv = _argv(words)
        candidates = [_expand(m) for m in _ABSOLUTE.findall(words)]
        candidates += [os.path.join(cwd, m) for m in _PARENT.findall(words)]
        candidates += [os.sep for word in argv[1:] if word in ("/", "\\")]
        if argv and argv[0].lower() in _CD:
            rest = [word for word in argv[1:] if not word.startswith("-")]
            target = _expand(rest[0]) if rest else os.path.expanduser("~")
            cwd = _real(os.path.join(cwd, target))
            candidates.append(cwd)
        for candidate in candidates:
            resolved = _real(candidate)
            if not inside(resolved, workspace) and not allowed(resolved) and resolved not in found:
                found.append(resolved)
    return found


def granted(path: str, grants: set[str] | frozenset[str]) -> bool:
    """Whether "Allow for this session" already covers ``path``."""
    for grant in grants:
        if grant.startswith(GRANT_PREFIX) and inside(path, grant[len(GRANT_PREFIX) :]):
            return True
    return False


def grant_key(path: str) -> str:
    """The session grant for this path: its folder, so siblings are covered too."""
    real = _real(path)
    folder = real if os.path.isdir(real) else os.path.dirname(real)
    return GRANT_PREFIX + folder


def path_in(description: str) -> str:
    """The path in a runtime approval description ("Read `x`", "Write file `x`")."""
    match = re.search(r"`([^`]+)`", description or "")
    return match.group(1) if match else ""
