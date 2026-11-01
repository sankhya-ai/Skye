"""skye - submit work to the team.

  skye run <job.json>            validate -> policy -> worker -> gates (or needs-owner)
  skye check <job.json>          validate a contract and print its normalized form
  skye approve <job.json>        one-time approval of exactly this contract (single-use)
  skye approvals                 list outstanding one-time approvals
  skye mission run <file>        run or resume a mission (a chain of contracts with handoffs)
  skye mission redispatch <file> --step <id>   re-run an interrupted step (explicit choice)
  skye mission status <file>     per-step state from the mission journal
  skye mission check <file>      validate a mission file
  skye mission reset <file>      archive the journal so the mission starts clean
"""

from __future__ import annotations

import json
import sys

from . import approvals
from .contract import ContractError, load
from .dispatcher import dispatch
from .mission import (MissionError, default_state_dir, load_mission, mission_status,
                      reset_mission, run_mission)
from .policy import Policy

USAGE = __doc__


def _print_outcome(outcome) -> None:
    print(json.dumps({"status": outcome.status, "reason": outcome.reason,
                      "fingerprint": outcome.fingerprint, "worker_job": outcome.worker_job,
                      "budget_notes": outcome.budget_notes,
                      "gates": outcome.gates, "worker_output": outcome.worker_output[-2000:]},
                     indent=2))


def _cmd_run(args: list[str]) -> int:
    if not args:
        print("usage: skye run <job.json>", file=sys.stderr)
        return 2
    try:
        contract = load(args[0])
    except ContractError as error:
        print(f"invalid contract: {error}", file=sys.stderr)
        return 2
    outcome = dispatch(contract, Policy.load(), state_dir=default_state_dir(),
                       approvals_path=approvals.default_path())
    _print_outcome(outcome)
    if outcome.status == "needs-owner":
        print(f"\nTo run exactly this contract once: skye approve {args[0]} && skye run {args[0]}",
              file=sys.stderr)
    return 0 if outcome.status == "done" else 1


def _cmd_check(args: list[str]) -> int:
    if not args:
        print("usage: skye check <job.json>", file=sys.stderr)
        return 2
    try:
        print(json.dumps(load(args[0]), indent=2))
        return 0
    except ContractError as error:
        print(f"invalid contract: {error}", file=sys.stderr)
        return 2


def _cmd_approve(args: list[str]) -> int:
    if not args:
        print("usage: skye approve <job.json>", file=sys.stderr)
        return 2
    try:
        contract = load(args[0])
    except ContractError as error:
        print(f"invalid contract: {error}", file=sys.stderr)
        return 2
    entry = approvals.approve(contract)
    print(json.dumps({"approved": True, "fingerprint": entry["fingerprint"], "goal": entry["goal"],
                      "role": entry["role"], "adapter": entry["adapter"],
                      "note": "single-use: consumed when the dispatch starts; edits invalidate it"},
                     indent=2))
    return 0


def _cmd_approvals() -> int:
    entries = approvals.pending()
    print(json.dumps({"approvals": entries, "count": len(entries)}, indent=2))
    return 0


def _cmd_mission(args: list[str]) -> int:
    if len(args) < 2 or args[0] not in ("run", "status", "check", "reset", "redispatch"):
        print("usage: skye mission run|status|check|reset|redispatch <mission.json> [--step <id>]",
              file=sys.stderr)
        return 2
    sub, path = args[0], args[1]
    try:
        mission = load_mission(path)
    except MissionError as error:
        print(f"invalid mission: {error}", file=sys.stderr)
        return 2
    if sub == "check":
        print(json.dumps({"id": mission["id"], "goal": mission["goal"],
                          "steps": [s["id"] for s in mission["steps"]]}, indent=2))
        return 0
    if sub == "status":
        print(json.dumps(mission_status(mission), indent=2))
        return 0
    if sub == "reset":
        archived = reset_mission(mission)
        print(json.dumps({"reset": True, "archived_journal": archived or "(no journal yet)"},
                         indent=2))
        return 0
    redispatch = None
    if sub == "redispatch":
        if "--step" not in args[2:]:
            print("usage: skye mission redispatch <mission.json> --step <id>", file=sys.stderr)
            return 2
        redispatch = args[args.index("--step") + 1]
    outcome = run_mission(mission, Policy.load(), approvals_path=approvals.default_path(),
                          redispatch=redispatch)
    print(json.dumps({"status": outcome.status, "mission": outcome.mission_id,
                      "reason": outcome.reason, "pending_contract": outcome.pending_contract,
                      "steps": {k: {"status": v.get("status"), "reason": v.get("reason", "")}
                                for k, v in outcome.steps.items()}}, indent=2))
    return 0 if outcome.status == "done" else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(USAGE, file=sys.stderr)
        return 2
    command, rest = args[0], args[1:]
    if command == "run":
        return _cmd_run(rest)
    if command == "check":
        return _cmd_check(rest)
    if command == "approve":
        return _cmd_approve(rest)
    if command == "approvals":
        return _cmd_approvals()
    if command == "mission":
        return _cmd_mission(rest)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
