"""Shell commands classified as read, verify, or needs trust (default-deny).

See the package docstring for the policy this feeds.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any

ACTION_RUN_COMMAND = "run command"
ACTION_EDIT_FILE = "edit file"
ACTION_EDIT_OUTSIDE = "edit file outside of working directory"
ACTION_READ_OUTSIDE = "read file outside of working directory"

TIER_READ = "read"
TIER_VERIFY = "verify"
TIER_TRUST = "needs_trust"

# Programs that only inspect state (with the exceptions in `_read_exception`).
_READ_PROGRAMS = {
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "ag", "fd", "find", "pwd", "which",
    "type", "file", "stat", "du", "df", "tree", "diff", "cmp", "sort", "uniq", "cut", "awk", "sed", "tr",
    "echo", "printf", "basename", "dirname", "realpath", "readlink", "true", "false", "test", "[", "date",
    "whoami", "id", "uname", "hostname", "nl", "column", "jq", "yq", "xxd", "od", "hexdump", "md5sum",
    "sha1sum", "sha256sum", "shasum", "cd", "less", "more", "strings", "comm", "join", "paste", "fold",
    "expand", "unexpand", "rev", "tac", "seq", "sleep", "printenv", "locale", "nproc", "free", "uptime",
    "ps", "lsof", "git-lfs",
    # PowerShell (the runtime's shell on Windows): cmdlets and aliases that only read or format.
    "get-childitem", "gci", "dir", "get-content", "gc", "select-string", "sls", "get-location", "gl",
    "test-path", "get-item", "gi", "get-itemproperty", "gp", "measure-object", "measure", "resolve-path", "rvpa",
    "get-command", "gcm", "get-date", "write-output", "write", "write-host", "select-object", "select",
    "sort-object", "get-process", "gps", "format-table", "ft", "format-list", "fl", "out-string",
    "get-filehash", "get-help", "compare-object", "get-member", "gm", "convertto-json", "convertfrom-json",
    "split-path", "join-path", "where.exe", "findstr", "set-location", "sl", "chdir", "get-variable", "gv",
}

# Git subcommands that only read the repository.
_GIT_READ = {
    "status", "diff", "log", "show", "blame", "grep", "rev-parse", "describe", "ls-files", "ls-tree",
    "shortlog", "cat-file", "rev-list", "name-rev", "merge-base", "whatchanged", "count-objects", "help",
    "version", "--version", "check-ignore", "for-each-ref", "show-ref", "var",
}
# Read-only only when they list: `git branch`, `git tag`, `git remote`, `git stash list`, `git worktree list`.
_GIT_LISTING = {"branch", "tag", "remote", "stash", "worktree", "config", "reflog"}

# The project's own checks. They run project code, which is the point of a
# coding session; they do not install, publish, or rewrite sources.
_CHECK_SCRIPT = re.compile(r"^(test|tests|lint|check|checks|typecheck|type-check|types|tsc|build|compile|verify|ci|format:check|fmt:check|e2e|spec)([:\-_.].*)?$")
_VERIFY_SUBCOMMANDS: dict[str, set[str]] = {
    "npm": {"test", "t", "tst", "ls", "list", "outdated", "view", "why", "explain", "audit", "run", "run-script"},
    "pnpm": {"test", "t", "ls", "list", "outdated", "why", "audit", "run", "exec", "lint", "typecheck", "build"},
    "yarn": {"test", "list", "why", "outdated", "audit", "run", "lint", "typecheck", "build"},
    "bun": {"test", "run"},
    "cargo": {"build", "b", "test", "t", "check", "c", "clippy", "bench", "doc", "tree", "metadata", "nextest", "fmt"},
    "go": {"build", "test", "vet", "list", "doc", "env", "version"},
    "make": {"test", "tests", "check", "lint", "build", "all", "typecheck", "verify", "ci"},
    "uv": {"run"},
    "poetry": {"run"},
    "deno": {"test", "check", "lint", "fmt"},
    "swift": {"build", "test"},
    "gradle": {"test", "build", "check", "assemble"},
    "./gradlew": {"test", "build", "check", "assemble"},
    "gradlew": {"test", "build", "check", "assemble"},
    "mvn": {"test", "verify", "compile", "package", "validate"},
    "dotnet": {"build", "test"},
    "pip": {"list", "show", "freeze", "check"},
    "pip3": {"list", "show", "freeze", "check"},
    "rustup": {"show"},
}
_VERIFY_PROGRAMS = {
    "pytest", "py.test", "tox", "nox", "tsc", "mypy", "pyright", "ruff", "black", "isort", "flake8", "pylint",
    "eslint", "prettier", "jest", "vitest", "mocha", "ava", "rspec", "phpunit", "ctest", "xcodebuild",
    "rustc", "gcc", "g++", "clang", "clang++", "javac", "node", "python", "python3", "npx", "bunx",
}
# What `npx`/`bunx` may launch without trust: checkers, not arbitrary packages.
_NPX_TOOLS = {"tsc", "vitest", "jest", "eslint", "prettier", "playwright", "mocha", "ava", "biome", "vue-tsc", "svelte-check", "astro"}

_SUBSTITUTION = ("$(", "`", "<(", ">(")
_SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", ";;", "|&", "{", "}", "!"}
_OUTPUT_REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&"}
_INPUT_REDIRECTS = {"<", "<<", "<<<", "<&"}
_HARMLESS_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr", "&1", "&2", "1", "2", "-", "$null", "NUL", "nul"}


@dataclass(frozen=True)
class Classification:
    tier: str
    key: str
    programs: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "key": self.key,
            "programs": list(self.programs),
            "effects": list(self.effects),
            "reasons": list(self.reasons),
        }


def _lex(command: str) -> list[str]:
    lexer = shlex.shlex(command.replace("\r", "").replace("\n", " ; "), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _segments(tokens: list[str]) -> tuple[list[list[str]], list[str]]:
    """Split tokens into commands; return them and the output-redirect targets."""
    segments: list[list[str]] = []
    targets: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        # `2>` lexes as "2", ">": the digit is a file descriptor, not an argument.
        if token in _OUTPUT_REDIRECTS or token in _INPUT_REDIRECTS:
            if current and current[-1].isdigit() and len(current[-1]) == 1:
                current.pop()
            target = tokens[index + 1] if index + 1 < len(tokens) else ""
            if token in _OUTPUT_REDIRECTS:
                targets.append(("&" + target) if token == ">&" else target)
            index += 2
            continue
        if token in _SEPARATORS:
            if current:
                segments.append(current)
            current = []
            index += 1
            continue
        current.append(token)
        index += 1
    if current:
        segments.append(current)
    return segments, targets


def _is_assignment(token: str) -> bool:
    name, separator, _ = token.partition("=")
    return bool(separator) and name.isidentifier()


def _positional(args: list[str]) -> list[str]:
    return [arg for arg in args if not arg.startswith("-")]


def _read_exception(program: str, args: list[str]) -> str:
    """Why an otherwise read-only program would change state, or ""."""
    lowered = [arg.lower() for arg in args]
    if program == "sed" and any(arg == "-i" or arg.startswith("-i") or arg.startswith("--in-place") for arg in lowered):
        return "sed_in_place"
    if program == "find" and any(arg in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"} for arg in lowered):
        return "find_can_mutate"
    if program == "awk" and any(re.search(r"system\s*\(|>\s*\"|\|\s*\"|getline", arg) for arg in args):
        return "awk_can_mutate"
    if program == "sort" and any(arg == "-o" or arg.startswith("--output") for arg in lowered):
        return "sort_writes_output"
    if program == "uniq" and len(_positional(args)) > 1:
        return "uniq_writes_output"
    return ""


def _python_snippet_mutates(snippet: str) -> bool:
    text = snippet.lower()
    markers = (
        ".write(", ".write_text(", ".write_bytes(", ".unlink(", ".rename(", ".replace(", ".mkdir(", ".rmdir(",
        "remove(", "removedirs(", "rmtree(", "shutil.", "subprocess.", "os.system", "popen", "exec(", "eval(",
        "__import__", "socket", "urllib", "requests", "http.client",
    )
    if any(marker in text for marker in markers):
        return True
    return bool(re.search(r"\bopen\s*\([^)]*,\s*['\"][^'\"]*[wax+]", text))


def _check_command(program: str, args: list[str]) -> tuple[str, str]:
    """(tier, reason) for a program that is not plainly read-only."""
    lowered = [arg.lower() for arg in args]
    positional = _positional(args)
    first = positional[0].lower() if positional else ""

    if program in {"python", "python3"} or re.fullmatch(r"python3\.\d+", program):
        if len(lowered) >= 2 and lowered[0] == "-m":
            module = lowered[1]
            if module in {"ruff", "black", "isort"}:
                return _check_command(module, args[2:])
            if module in {"pytest", "unittest", "mypy", "pyright", "compileall", "py_compile", "doctest"}:
                return TIER_VERIFY, ""
            return TIER_TRUST, f"python_module_{module}"
        if lowered[:1] in (["--version"], ["-v"], ["-V"]):
            return TIER_READ, ""
        if "-c" in lowered:
            at = lowered.index("-c") + 1
            snippet = args[at] if at < len(args) else ""
            return (TIER_TRUST, "python_snippet_can_mutate") if _python_snippet_mutates(snippet) else (TIER_VERIFY, "")
        return TIER_TRUST, "python_interpreter_can_mutate"
    if program == "node":
        if lowered[:1] in (["--version"], ["-v"]):
            return TIER_READ, ""
        if "--test" in lowered or "--check" in lowered or "-c" in lowered:
            return TIER_VERIFY, ""
        return TIER_TRUST, "node_interpreter_can_mutate"
    if program in {"black", "isort"}:
        return (TIER_VERIFY, "") if "--check" in lowered or "--diff" in lowered else (TIER_TRUST, "formatter_rewrites_files")
    if program == "prettier":
        checking = "--check" in lowered or "-c" in lowered or "--list-different" in lowered or "-l" in lowered
        return (TIER_VERIFY, "") if checking and "--write" not in lowered else (TIER_TRUST, "formatter_rewrites_files")
    if program == "ruff":
        if first == "format":
            return (TIER_VERIFY, "") if "--check" in lowered or "--diff" in lowered else (TIER_TRUST, "formatter_rewrites_files")
        return (TIER_TRUST, "formatter_rewrites_files") if "--fix" in lowered else (TIER_VERIFY, "")
    if program == "eslint":
        return (TIER_TRUST, "formatter_rewrites_files") if "--fix" in lowered else (TIER_VERIFY, "")
    if program in {"npx", "bunx"}:
        # `npx tsc --noEmit`: judge the tool it runs. `npx some-package` may download and run anything.
        words = [arg for arg in args if arg not in {"--yes", "-y", "--no-install", "--no"}]
        if not words or words[0].split("@")[0].lower() not in _NPX_TOOLS:
            return TIER_TRUST, f"npx_runs_{words[0] if words else 'nothing'}"
        return _classify_words([words[0].split("@")[0].lower(), *words[1:]])[:2]
    if program in {"uv", "poetry"} and first == "run":
        inner = args[args.index(positional[0]) + 1 :]
        inner = [arg for arg in inner if arg not in {"--frozen", "--locked", "--no-sync", "--offline"}]
        return _classify_words(inner)[:2] if inner else (TIER_TRUST, f"{program}_run_nothing")
    if program == "pnpm" and first == "exec":
        inner = args[args.index(positional[0]) + 1 :]
        return _classify_words(inner)[:2] if inner else (TIER_TRUST, "pnpm_exec_nothing")
    if program == "cargo" and first == "fmt":
        return (TIER_VERIFY, "") if "--check" in lowered else (TIER_TRUST, "formatter_rewrites_files")
    if program in _VERIFY_SUBCOMMANDS:
        if program == "make" and not positional:
            return TIER_VERIFY, ""
        if program == "yarn" and not positional:
            return TIER_TRUST, "yarn_installs"
        if first not in _VERIFY_SUBCOMMANDS[program]:
            if program in {"yarn", "bun"} and _CHECK_SCRIPT.match(first):
                return TIER_VERIFY, ""
            return TIER_TRUST, f"{program}_{first or 'none'}"
        if first in {"run", "run-script"}:
            script = positional[1].lower() if len(positional) > 1 else ""
            if not _CHECK_SCRIPT.match(script):
                return TIER_TRUST, f"{program}_runs_script_{script or 'none'}"
        return TIER_VERIFY, ""
    if program in _VERIFY_PROGRAMS:
        return TIER_VERIFY, ""
    return TIER_TRUST, f"unrecognised_program:{program}"


def _git(args: list[str]) -> tuple[str, str, str]:
    """(tier, subcommand, reason) for `git <args>`."""
    rest = list(args)
    # Skip global options such as `-C dir` or `--no-pager`.
    while rest and rest[0].startswith("-"):
        option = rest.pop(0)
        if option in {"-C", "-c", "--git-dir", "--work-tree"} and rest:
            rest.pop(0)
    if not rest:
        return TIER_READ, "", ""
    sub, tail = rest[0].lower(), [arg.lower() for arg in rest[1:]]
    if sub in _GIT_READ:
        if sub == "diff" and any(arg.startswith("--output") for arg in tail):
            return TIER_TRUST, sub, "git_diff_writes_output"
        return TIER_READ, sub, ""
    if sub in _GIT_LISTING:
        positional = _positional(tail)
        listing = {
            "branch": not positional and not any(arg in {"-d", "-D", "-m", "-M", "-c", "-C", "--delete", "--move", "--copy", "-u", "--set-upstream-to", "--unset-upstream", "-f", "--force"} for arg in tail),
            "tag": not positional or tail[:1] == ["-l"] or "--list" in tail,
            "remote": not positional or positional[0] in {"show", "get-url"},
            "stash": bool(positional) and positional[0] in {"list", "show"},
            "worktree": bool(positional) and positional[0] == "list",
            "config": "--get" in tail or "--list" in tail or "-l" in tail or "--get-all" in tail,
            "reflog": not positional or positional[0] == "show",
        }[sub]
        if listing:
            return TIER_READ, sub, ""
    return TIER_TRUST, sub, f"git_{sub}_changes_state"


def _command_class(program: str, args: list[str]) -> str:
    """The trust key's name for a command: specific enough that trusting a
    project check (`python3 -m unittest`) never trusts a script run (`python3 x.py`)."""
    positional = _positional(args)
    lowered = [a.lower() for a in args]
    if (program in {"python", "python3"} or re.fullmatch(r"python3\.\d+", program)) and len(lowered) >= 2 and lowered[0] == "-m":
        return f"{program} -m {lowered[1]}"
    if program in {"uv", "poetry"} and positional[:1] == ["run"] and len(positional) > 1:
        return f"{program} run {positional[1].rsplit('/', 1)[-1]}"
    if program in {"npx", "bunx"} and positional:
        return f"{program} {positional[0].split('@')[0]}"
    if program in _VERIFY_SUBCOMMANDS and positional:
        sub = positional[0].lower()
        if sub in {"run", "run-script", "exec"} and len(positional) > 1:
            return f"{program} {sub} {positional[1]}"
        return f"{program} {sub}"
    return program


def _classify_words(words: list[str]) -> tuple[str, str, str, str]:
    """(tier, reason, program, trust key) for one command's words."""
    words = list(words)
    while words and _is_assignment(words[0]):
        words.pop(0)
    if not words:
        return TIER_READ, "", "", ""
    raw = words[0]
    program = raw if raw.startswith("./") else re.split(r"[\\/]", raw)[-1].lower()
    if program.endswith(".exe") and program != "where.exe":
        program = program[: -len(".exe")]  # Windows: `git.exe status` is `git status`
    args = words[1:]
    if program == "git":
        tier, sub, reason = _git(args)
        return tier, reason, program, f"{ACTION_RUN_COMMAND}:git {sub}".rstrip()
    if program in _READ_PROGRAMS:
        reason = _read_exception(program, args)
        return (TIER_TRUST if reason else TIER_READ), reason, program, f"{ACTION_RUN_COMMAND}:{program}"
    tier, reason = _check_command(program, args)
    return tier, reason, program, f"{ACTION_RUN_COMMAND}:{_command_class(program, args)}"


_RANK = {TIER_READ: 0, TIER_VERIFY: 1, TIER_TRUST: 2}


def classify_command(command: str) -> Classification:
    """Classify a shell command. Unknown means needs trust."""
    text = str(command or "").strip()
    if not text:
        return Classification(TIER_TRUST, f"{ACTION_RUN_COMMAND}:(empty)", effects=("unknown",), reasons=("empty_command",))
    reasons: list[str] = []
    if any(marker in text for marker in _SUBSTITUTION):
        reasons.append("shell_substitution")
    try:
        tokens = _lex(text)
    except ValueError as error:
        return Classification(TIER_TRUST, f"{ACTION_RUN_COMMAND}:(unparsed)", effects=("unknown",), reasons=(f"parse_error:{error}",))
    segments, targets = _segments(tokens)
    writes = [target for target in targets if target not in _HARMLESS_TARGETS]
    if writes:
        reasons.append("shell_redirection")

    programs: list[str] = []
    tiers: list[str] = []
    # The key names the segment that decided the tier: in `cd app && pytest`
    # that is `pytest`, not `cd`.
    key, key_rank = "", -1
    for segment in segments:
        tier, reason, program, segment_key = _classify_words(segment)
        if not program:
            continue
        programs.append(program)
        tiers.append(tier)
        if tier == TIER_TRUST:
            reasons.append(reason)
        if _RANK[tier] > key_rank:
            key, key_rank = segment_key, _RANK[tier]

    if not programs:
        return Classification(TIER_TRUST, f"{ACTION_RUN_COMMAND}:(empty)", effects=("unknown",), reasons=tuple(reasons or ["no_command"]))
    effects = set()
    if writes or "shell_substitution" in reasons or TIER_TRUST in tiers:
        effects.add("writes")
    if TIER_VERIFY in tiers:
        effects.add("runs_project_code")
    if not effects:
        effects.add("read")
    if TIER_TRUST in tiers or reasons:
        return Classification(TIER_TRUST, key, tuple(programs), tuple(sorted(effects)), tuple(r for r in reasons if r))
    tier = TIER_VERIFY if TIER_VERIFY in tiers else TIER_READ
    return Classification(tier, key, tuple(programs), tuple(sorted(effects)))


def command_of(payload: dict[str, Any]) -> str:
    """The shell command an approval request is about, from its display blocks."""
    for block in payload.get("display") or []:
        if isinstance(block, dict) and block.get("type") == "shell":
            command = str(block.get("command") or "").strip()
            if command:
                return command
    return ""
