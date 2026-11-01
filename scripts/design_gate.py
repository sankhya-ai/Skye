"""Design journey gate (owner-opt-in): run the pinned impeccable detector against a produced
design artifact and treat its TEXT-MODE exit code as the verdict.

Owner wires it exactly like any journey command (Settings -> journey command), e.g.:
    python3 scripts/design_gate.py artifacts/poster.html

Semantics, verified empirically against impeccable 4.8.0 (Apache-2.0, NOTICE.md):
- Exit 2 = at least one warning-or-error severity finding (low-contrast, undersized text,
  overused-font, hero-eyebrow-chip, ...) -> gate FAILS. Exit codes are identical in text and
  --json modes.
- The 16 registry rules with severity "advisory" (em-dash-overuse, straggler, ...) print in a
  separate "Advisory (not counted as failures)" section and never change the exit code, in
  either mode (verified: advisory-only page exits 0 in text and --json).
- overused-font is severity "warning" and DOES gate - an earlier observation of it exiting 0
  was a shell measurement artifact, corrected.
- This gate uses text mode: same exit codes, human-readable evidence straight into the
  journey record, no JSON parsing to get wrong.
- Coverage honesty: on raw .css/.js/.svg the element rules do not run and the tool prints an
  undercount banner. That banner is forwarded into the evidence verbatim - a partial pass is
  never presented as a clean bill. For .svg artifacts we wrap them inline in a minimal HTML
  page so the page engine can see them; what the engine reads from inline SVG text is the
  engine's coverage, still disclosed.
- Dependencies (disclosed, not bundled): node + npx download of impeccable@4.8.0 and, for
  URL scans, its bundled chromium. No API key, no model: the detector is deterministic code.
"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

VERSION = os.environ.get("IMPECCABLE_VERSION", "4.8.0")
TIMEOUT = int(os.environ.get("IMPECCABLE_TIMEOUT", "180"))
EVIDENCE_LIMIT = 1800

SVG_WRAPPER = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>design gate render</title></head>
<body style="margin:0">{svg}</body></html>
"""


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: design_gate.py <artifact.html|artifact.svg|dir>")
        return 2
    target = Path(sys.argv[1])
    if not target.exists():
        print(f"GATE ERROR: artifact not found: {target}")
        return 2
    if shutil.which("npx") is None:
        print("GATE ERROR: npx not found - the design gate needs node on this machine "
              f"(downloads impeccable@{VERSION} on first run; no API key, no model).")
        return 2
    scan = target
    tmp = None
    if target.suffix.lower() == ".svg":
        tmp = Path(tempfile.mkdtemp()) / "wrapped.html"
        tmp.write_text(SVG_WRAPPER.format(svg=target.read_text("utf-8")), "utf-8")
        scan = tmp
    try:
        proc = subprocess.run(
            ["npx", "-y", f"impeccable@{VERSION}", "detect", str(scan)],
            capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"GATE ERROR: impeccable@{VERSION} timed out after {TIMEOUT}s")
        return 2
    evidence = (proc.stdout + proc.stderr).strip()
    # Evidence discipline, same as skye.journey: bounded tail, never the whole output.
    if len(evidence) > EVIDENCE_LIMIT:
        evidence = evidence[:EVIDENCE_LIMIT] + "\n... [truncated]"
    header = f"design gate: impeccable@{VERSION} detect {scan.name} -> exit {proc.returncode}\n"
    if tmp is not None:
        header += ("(artifact wrapped: .svg inlined into an HTML page; engine coverage of "
                   "inline SVG is the engine's own, disclosed)\n")
    print(header + (evidence or "(no output)"))
    return 0 if proc.returncode == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
