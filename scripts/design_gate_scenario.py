"""Design gate demo: a REAL fail -> repair -> pass loop against produced artifacts.

A designer step's poster (HTML) ships with real anti-patterns; the gate (pinned impeccable,
deterministic, no model) FAILS with the actual findings as evidence; the repair fixes the
findings; the SAME gate passes. Every evidence string below comes from a real gate run."""
import subprocess, sys, tempfile
from pathlib import Path

GATE = ["python3", "scripts/design_gate.py"]

BAD_POSTER = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Cart launch</title></head>
<body style="margin:0;background:#c9bfa8;color:#b3a98f;font-family:Georgia,serif">
<div style="padding:60px;max-width:900px">
  <p style="font-size:9px;letter-spacing:3px">OUR STORY</p>
  <h1 style="font-size:64px;font-style:italic">Unlock your potential with our cart</h1>
  <p style="font-size:10px">It's not just a cart, it's a journey. Line items, totals and a badge.</p>
</div></body></html>
"""

FIXED_POSTER = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Cart launch</title></head>
<body style="margin:0;background:#0b0d10;color:#f2f5f7;font-family:Verdana,sans-serif">
<div style="padding:60px;max-width:900px">
  <h1 style="font-size:54px;font-style:normal;color:#7dd3a8">The cart that keeps its own receipts.</h1>
  <p style="font-size:18px;color:#c4cdd6">Line items and totals that match the badge count,
  checked against the order record - order 1042 showed the difference.</p>
</div></body></html>
"""


def run_gate(artifact: Path) -> tuple[int, str]:
    proc = subprocess.run(GATE + [str(artifact)], capture_output=True, text=True, timeout=240)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main() -> None:
    work = Path(tempfile.mkdtemp())
    poster = work / "poster.html"
    poster.write_text(BAD_POSTER, "utf-8")
    code, evidence = run_gate(poster)
    assert code == 2, f"gate should FAIL the slop poster, got exit {code}\n{evidence}"
    assert "anti-pattern" in evidence, evidence
    print("1. first draft REJECTED by the gate, real findings:")
    print(evidence[:900])
    # the repair: fix the actual findings (contrast, size, italic-serif, vague headline, AI beige)
    poster.write_text(FIXED_POSTER, "utf-8")
    code2, evidence2 = run_gate(poster)
    print("\n2. after repair, same gate:")
    print(evidence2[:500])
    assert code2 == 0, f"gate should PASS the repaired poster, got exit {code2}\n{evidence2}"
    print("\nDESIGN GATE SCENARIO OK: fail -> repair -> pass, all evidence from real detector runs")


if __name__ == "__main__":
    main()
