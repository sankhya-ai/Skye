"""Role guides: review guidance a designer or pm job loads on demand, plus two checks that do not need a model.

* ``role_guide`` returns one guide's text. The job prompt carries only the index (names and one line each), so a role's
  full text enters the context only if the model asks for it. Which guides a role may load is fixed by the host.
* ``design_scan`` greps the workspace source for a short list of common template-default tells. Each match is a CLUE
  with file:line. It is not a finding and a clean scan proves nothing.
* ``check_findings`` runs after the job: it checks the finding objects have their fields and that each ``evidence``
  anchor exists (a screenshot this job made, a workspace file and line, or a name of supplied material). It cannot
  tell whether a finding is true, so it is an evidence-anchor check, not a quality gate.

None of this makes taste or security guarantees, and it does not add interaction testing: ``inspect_page`` still loads
one page state and blocks non-GET requests.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..sessions.host_tools import tool_error
from .texts import GUIDES, ROLE_GUIDES, SOURCES

if TYPE_CHECKING:
    from ..sessions.runtime import SessionRuntime

GUIDE_TOOL = "role_guide"
SCAN_TOOL = "design_scan"
TOOL_NAMES = (GUIDE_TOOL, SCAN_TOOL)
FINDING_KEYS = ("title", "observed", "inferred", "impact", "confidence", "evidence", "recommendation", "verification")


def guides_for(role: str, tenant: Any = None) -> list[str]:
    """Guide names for a role: the built-in presets, then ACTIVE registry roles (their guides are existing guide names)."""
    if role in ROLE_GUIDES:
        return list(ROLE_GUIDES[role])
    registry = getattr(tenant, "registry", None) if tenant is not None else None
    record = registry.active_role(role) if registry is not None else None
    return list(record["guides"]) if record else []


def index_for(role: str, tenant: Any = None) -> str:
    """The only guide text a job prompt carries: names and one line each."""
    names = guides_for(role, tenant)
    if not names:
        return ""
    lines = "\n".join(f"- {n}: {GUIDES[n][0]}" for n in names)
    return f"\n\nReview guides are available on demand: call `{GUIDE_TOOL}` with a name to read one (read `evidence` first). Available:\n{lines}\nThe guides steer your judgment; they do not make a review correct."


# ----- design_scan: source clues ------------------------------------------------------------------------------------

#: (id, regex, why it is only a clue). Our own short list; extend by evidence, not by length.
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("indigo-violet-default", re.compile(r"#(?:6366f1|8b5cf6|7c3aed|4f46e5)\b|\b(?:from|via|to)-(?:indigo|violet|purple)-\d{3}", re.I), "the stock indigo/violet gradient palette; fine if it is the brand's own"),
    ("gradient-text", re.compile(r"bg-clip-text|background-clip:\s*text|-webkit-background-clip:\s*text", re.I), "gradient-filled headline text is a common template flourish"),
    ("glass-blur", re.compile(r"backdrop-blur|backdrop-filter:\s*blur", re.I), "frosted-glass surfaces everywhere read as template default"),
    ("transition-all", re.compile(r"transition-all|transition:\s*all\b", re.I), "animating every property is usually unconsidered"),
    ("placeholder-copy", re.compile(r"lorem ipsum|your (?:company|brand|product) name|acme inc|john doe", re.I), "placeholder text left in the UI"),
    ("hype-copy", re.compile(r"\b(?:unleash|supercharge|revolutioni[sz]e|seamless(?:ly)?|elevate your|next-generation|game-changing|cutting-edge)\b", re.I), "generic marketing verbs say nothing about this product"),
    ("emoji-in-markup", re.compile("[\U0001F300-\U0001FAFF\u2728\u2705\u2B50\U0001F680]"), "emoji standing in for icons or bullets in UI source"),
    ("focus-removed", re.compile(r"outline:\s*(?:none|0)\b(?![^;}]*(?:box-shadow|border|ring))|focus:outline-none(?!.*focus:ring)", re.I), "removing the focus outline without a replacement hurts keyboard users"),
    ("img-without-alt", re.compile(r"<img\b(?![^>]*\balt\s*=)[^>]*>", re.I), "an image with no alt attribute"),
    ("pure-black-on-white", re.compile(r"color:\s*#000(?:000)?\b.*background(?:-color)?:\s*#fff(?:fff)?\b", re.I), "maximum-contrast pairs are often unconsidered defaults"),
]
SCAN_SUFFIXES = {".html", ".htm", ".css", ".scss", ".jsx", ".tsx", ".js", ".ts", ".vue", ".svelte", ".astro", ".md", ".mdx"}
SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", ".venv", "venv", "__pycache__", "target", "vendor"}
MAX_FILES, MAX_FILE_BYTES, MAX_MATCHES = 400, 200_000, 60


def scan(root: Path, subpath: str = "") -> dict[str, Any]:
    """Clues under ``root`` (never outside it, never through a symlink), bounded in files, bytes and matches."""
    root = root.resolve()
    base = (root / subpath).resolve() if subpath else root
    if not base.is_relative_to(root) or not base.exists():
        raise ValueError("`path` must be an existing file or folder inside the workspace.")
    files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.suffix.lower() in SCAN_SUFFIXES)
    scanned, clues, truncated = 0, [], False
    for path in files:
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or path.is_symlink() or not path.is_file():
            continue
        if scanned >= MAX_FILES:
            truncated = True
            break
        scanned += 1
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text("utf-8", errors="ignore")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for pid, pattern, why in PATTERNS:
                if pattern.search(line):
                    clues.append({"id": pid, "where": f"{rel}:{number}", "excerpt": line.strip()[:140], "why_only_a_clue": why})
                    if len(clues) >= MAX_MATCHES:
                        return {"scanned_files": scanned, "clues": clues, "truncated": True}
    return {"scanned_files": scanned, "clues": clues, "truncated": truncated}


def render_scan(result: dict[str, Any]) -> str:
    head = f"design_scan: {result['scanned_files']} files scanned, {len(result['clues'])} clue(s){' (stopped at a limit)' if result['truncated'] else ''}. CLUES, not findings: confirm each in a rendering, or drop it. No clues does not mean no problems."
    body = "\n".join(f"- [{c['id']}] {c['where']}: {c['excerpt']}  ({c['why_only_a_clue']})" for c in result["clues"])
    return head + ("\n" + body if body else "")


# ----- the host toolset ---------------------------------------------------------------------------------------------


class GuideToolset:
    plugin = "guides"

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        role = getattr(runtime.record, "job_role", "")
        if not guides_for(role, getattr(runtime, "tenant", None)):
            return []  # ordinary sessions and other roles never see these tools
        out = [{
            "name": GUIDE_TOOL,
            "description": "Read one review guide for your role. Call with no name to list them. Guides steer judgment; they do not make a review correct.",
            "parameters": {"type": "object", "properties": {"name": {"type": "string", "enum": guides_for(role, getattr(runtime, "tenant", None))}}, "additionalProperties": False},
        }]
        if role == "designer":
            out.append({
                "name": SCAN_TOOL,
                "description": "Scan the workspace's UI source for common template-default tells. Returns clues with file:line; each needs confirming in a rendering. Read-only, bounded, stays inside the workspace.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "A folder or file inside the workspace; default the whole workspace."}}, "additionalProperties": False},
            })
        return out

    def handles(self, name: str) -> bool:
        return name in TOOL_NAMES

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        role = getattr(runtime.record, "job_role", "")
        allowed = guides_for(role, getattr(runtime, "tenant", None))
        if not allowed:
            return tool_error(f"`{name}` is only for the designer and pm job roles.", "Not for this session")
        if name == SCAN_TOOL:
            if role != "designer":
                return tool_error("`design_scan` is a designer tool.", "Not for this role")
            try:
                result = scan(Path(runtime.record.workspace), str(arguments.get("path") or ""))
            except ValueError as error:
                return tool_error(str(error), "Not allowed")
            return {"is_error": False, "output": render_scan(result), "message": "", "display": [{"type": "brief", "text": f"design_scan: {len(result['clues'])} clue(s)"}]}
        wanted = str(arguments.get("name") or "")
        if not wanted:
            text = "\n".join(f"- {n}: {GUIDES[n][0]}" for n in allowed)
        elif wanted not in allowed:
            return tool_error(f"No guide `{wanted}` for the {role} role. Available: {', '.join(allowed)}.", "Unknown guide")
        else:
            text = GUIDES[wanted][1]
        return {"is_error": False, "output": text, "message": "", "display": [{"type": "brief", "text": f"Guide: {wanted or 'index'}"}]}


# ----- after the job: do the cited anchors exist? --------------------------------------------------------------------

ANCHOR_FILE = re.compile(r"^(?P<path>[^\s:][^:]*?)(?::(?P<line>\d+))?$")


def anchor_exists(anchor: str, workspace: Path, scratch_inspect: Path) -> bool:
    anchor = (anchor or "").strip()
    if not anchor:
        return False
    if anchor.startswith("supplied:"):
        return bool(anchor[len("supplied:"):].strip())
    if re.fullmatch(r"(?:shot|deploy)-\d{3}\.png", anchor):
        return (scratch_inspect / anchor).is_file()
    match = ANCHOR_FILE.match(anchor)
    if not match:
        return False
    root = workspace.resolve()
    try:
        target = (root / match["path"]).resolve()
    except (OSError, ValueError):
        return False
    if not target.is_relative_to(root) or not target.is_file():
        return False
    if match["line"]:
        try:
            return 1 <= int(match["line"]) <= len(target.read_text("utf-8", errors="ignore").splitlines())
        except OSError:
            return False
    return True


def check_findings(data: Any, workspace: Path, scratch_inspect: Path, prior_ids: "set[str] | None" = None) -> dict[str, Any]:
    """Shape of each finding and whether each ``evidence`` anchor exists. Not a judgement of whether a finding is right."""
    findings = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(findings, list):
        return {"checked": "evidence-anchors", "ok": False, "problems": ["`findings` must be a list"], "total": 0, "anchored": 0}
    problems, anchored = [], 0
    for i, item in enumerate(findings):
        if not isinstance(item, dict):
            problems.append(f"finding {i}: not an object")
            continue
        missing = [k for k in FINDING_KEYS if not str(item.get(k) or "").strip()]
        if missing:
            problems.append(f"finding {i}: empty or missing {', '.join(missing)}")
        if item.get("confidence") not in (None, "high", "medium", "low") and "confidence" not in missing:
            problems.append(f"finding {i}: confidence must be high, medium or low")
        if anchor_exists(str(item.get("evidence") or ""), workspace, scratch_inspect):
            anchored += 1
        else:
            problems.append(f"finding {i}: evidence `{str(item.get('evidence'))[:80]}` does not point at a screenshot this job made, a workspace file:line, or `supplied: <name>`")
    limits = data.get("limits") if isinstance(data, dict) else None
    if not (isinstance(limits, list) and limits):
        problems.append("`limits` must list what was not or could not be checked")
    extra = check_review_loop(data, workspace, scratch_inspect, prior_ids or set())
    problems += extra["problems"]
    absent = [k for k in ("inventory", "design_diffs", "deployed", "rechecks") if not (isinstance(data, dict) and data.get(k))]
    note = "ok means only that fields are present and anchors exist. It does not mean any finding is true or the design is good."
    if absent:
        note += " NOT performed or not reported in this review: " + ", ".join(absent) + "."
    return {"checked": "evidence-anchors", "ok": not problems, "problems": problems, "total": len(findings), "anchored": anchored, "loop": extra["counts"], "not_performed": absent, "meaning": note}


INVENTORY_STATUS = ("present", "in code only", "absent", "unknown", "unlisted")
RECHECK_STATUS = ("fixed", "not-fixed", "regressed", "not-run")


def _rendered(anchor: str, scratch_inspect: Path) -> bool:
    """An anchor that is a screenshot THIS job made (a rendering), not source or supplied text."""
    return bool(re.fullmatch(r"(?:shot|deploy)-\d{3}\.png", (anchor or "").strip())) and (scratch_inspect / anchor.strip()).is_file()


def check_review_loop(data: Any, workspace: Path, scratch_inspect: Path, prior_ids: "set[str]") -> dict[str, Any]:
    """Anchor rules for the optional ``inventory``, ``design_diffs``, ``deployed`` and ``rechecks`` lists.

    Each status has the weakest evidence that can honestly support it: ``present`` needs a rendering this job made,
    ``in code only`` a file:line, ``absent`` a record of what was searched. This stops a claim without a source. It cannot
    tell whether the claim is true. Lists that are absent are not required, so a plain review is unaffected.
    """
    problems: list[str] = []
    counts: dict[str, int] = {}
    data = data if isinstance(data, dict) else {}

    def lst(key: str) -> list:
        value = data.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            problems.append(f"`{key}` must be a list")
            return []
        counts[key] = len(value)
        return value

    def anchor(a: Any) -> bool:
        return anchor_exists(str(a or ""), workspace, scratch_inspect)

    for i, e in enumerate(lst("inventory")):
        e = e if isinstance(e, dict) else {}
        status, ev = e.get("status"), str(e.get("evidence") or "")
        if not str(e.get("feature") or "").strip() or status not in INVENTORY_STATUS:
            problems.append(f"inventory {i}: needs a feature and a status of {', '.join(INVENTORY_STATUS)}")
        elif status == "present" and not _rendered(ev, scratch_inspect):
            problems.append(f"inventory {i} ({e['feature'][:40]}): `present` needs a screenshot this job made (shot-NNN.png), not `{ev[:60]}`")
        elif status in ("in code only", "unlisted") and not anchor(ev):
            problems.append(f"inventory {i} ({e['feature'][:40]}): `{status}` needs an evidence anchor that exists")
        elif status == "absent" and not str(e.get("searched") or "").strip():
            problems.append(f"inventory {i} ({e['feature'][:40]}): `absent` needs `searched`: what you looked in and for")
        elif status == "unknown" and not str(e.get("reason") or "").strip():
            problems.append(f"inventory {i} ({e['feature'][:40]}): `unknown` needs a `reason`")
    for i, d in enumerate(lst("design_diffs")):
        d = d if isinstance(d, dict) else {}
        if not anchor(d.get("design")) or not anchor(d.get("built")):
            problems.append(f"design_diffs {i}: both `design` and `built` must be anchors that exist (supplied material / a file / a screenshot)")
        if not str(d.get("differs") or "").strip():
            problems.append(f"design_diffs {i}: say what differs")
    for i, d in enumerate(lst("deployed")):
        d = d if isinstance(d, dict) else {}
        if d.get("state") == "seen":
            ev = str(d.get("evidence") or "")
            if not ((ev.startswith("deploy-") and _rendered(ev, scratch_inspect)) or (ev.startswith("supplied:") and anchor(ev))):
                problems.append(f"deployed {i}: `seen` needs a capture this job made (deploy-NNN.png) or `supplied: <name>`")
        elif d.get("state") == "not verified":
            if not str(d.get("reason") or "").strip():
                problems.append(f"deployed {i}: `not verified` needs a `reason`")
        else:
            problems.append(f"deployed {i}: state must be `seen` or `not verified`")
    for i, r in enumerate(lst("rechecks")):
        r = r if isinstance(r, dict) else {}
        if str(r.get("id")) not in prior_ids:
            problems.append(f"rechecks {i}: id `{r.get('id')}` is not a finding id from a job this one was handed")
        if r.get("status") not in RECHECK_STATUS:
            problems.append(f"rechecks {i}: status must be one of {', '.join(RECHECK_STATUS)}")
        elif r["status"] == "not-run":
            if not str(r.get("reason") or "").strip():
                problems.append(f"rechecks {i}: `not-run` needs a `reason`")
        elif not _rendered(str(r.get("evidence") or ""), scratch_inspect):
            problems.append(f"rechecks {i}: `{r['status']}` needs a fresh screenshot from this job")
    return {"problems": problems, "counts": counts}


def install(server) -> None:
    def extend(tenant) -> None:
        tenant.toolsets.append(GuideToolset())

    server.extensions.append(extend)
