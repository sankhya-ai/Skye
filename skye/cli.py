"""skye - submit work to the team.

  skye run <job.json>            validate -> policy -> worker -> gates (or needs-owner)
  skye check <job.json>          validate a contract and print its normalized form
  skye approve <job.json>        one-time approval of exactly this contract (single-use)
  skye approvals                 list outstanding one-time approvals
  skye mission run <file>        run or resume a mission (a chain of contracts with handoffs)
  skye mission redispatch <file> --step <id> [--fresh]   re-run an interrupted step
      (resumes its vendor session; --fresh abandons the session and starts over)
  skye mission status <file>     per-step state from the mission journal
  skye mission check <file>      validate a mission file
  skye mission reset <file>      archive the journal so the mission starts clean
  skye ui [--port 8787]          local screens for missions, journals and approvals
  skye say <mission-id> --as <role> [--kind K] [--ack-needed R,...] <message>
                                 post to the mission's team line (workers coordinate here)
  skye hear <mission-id> [--since N] [--limit N]   read new team-line posts (JSON + cursor)
  skye ack <mission-id> <message-id> --as <role> [--note N]   ack a contract-change
  skye agents                    list agent definitions (builtin, registry, proposed)
  skye agents promote <name>     activate a worker-proposed agent (the owner's go-ahead)
  skye backlog list [--status S] list tickets
  skye backlog reopen <id> --evidence TEXT [--label L]...   reopen delivered work with evidence
  skye backlog comment <id> --as <actor> <message>          add to a ticket's thread
  skye backlog labels <id> <label>...                       replace a ticket's labels
  skye issues report <ticket-id> --kind K [--label L]... <summary>
                                 report a bug ON a ticket (delivered work reopens with evidence)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
    if len(args) < 2 or args[0] not in ("run", "status", "check", "reset", "redispatch",
                                          "ack-recovery"):
        print("usage: skye mission run|status|check|reset|redispatch|ack-recovery "
              "<mission.json> [--step <id>]", file=sys.stderr)
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
    if sub == "ack-recovery":
        from . import workspaces
        from .mission import default_state_dir
        try:
            record = workspaces.acknowledge_recovery(
                Path(default_state_dir()) / "missions" / mission["id"], actor="skye cli")
        except workspaces.WorkspaceError as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps({"acknowledged": True, "branch": record.get("branch", ""),
                          "base_rev": record.get("base_rev", "")}, indent=2))
        return 0
    redispatch = None
    redispatch_fresh = False
    if sub == "redispatch":
        if "--step" not in args[2:]:
            print("usage: skye mission redispatch <mission.json> --step <id> [--fresh]",
                  file=sys.stderr)
            return 2
        redispatch = args[args.index("--step") + 1]
        redispatch_fresh = "--fresh" in args  # --fresh abandons the recorded vendor session
    outcome = run_mission(mission, Policy.load(), approvals_path=approvals.default_path(),
                          redispatch=redispatch, redispatch_fresh=redispatch_fresh,
                          clear_cancel=True)  # an explicit operator command
    print(json.dumps({"status": outcome.status, "mission": outcome.mission_id,
                      "reason": outcome.reason, "pending_contract": outcome.pending_contract,
                      "steps": {k: {"status": v.get("status"), "reason": v.get("reason", "")}
                                for k, v in outcome.steps.items()}}, indent=2))
    return 0 if outcome.status == "done" else 1


def _flag(args: list[str], name: str, default: str = "") -> str:
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default


def _parse_opts(args: list[str], value_flags: set[str]) -> tuple[list[str], dict[str, str]]:
    """Split CLI arguments into (positionals, option values). Every --flag must be a known
    value-taking flag from value_flags; a repeated flag keeps its LAST value. A bare "--"
    ends option parsing so free text can safely start with dashes. Unknown flags and flags
    missing their value raise ValueError - a malformed command is rejected loudly, never
    silently folded into a message body the way naive token joining would."""
    positionals: list[str] = []
    opts: dict[str, str] = {}
    i = 0
    options_done = False
    while i < len(args):
        arg = args[i]
        if options_done:
            positionals.append(arg)
        elif arg == "--":
            options_done = True
        elif arg.startswith("--"):
            if arg not in value_flags:
                raise ValueError(f"unknown option {arg}")
            if i + 1 >= len(args):
                raise ValueError(f"option {arg} needs a value")
            opts[arg] = args[i + 1]
            i += 1
        else:
            positionals.append(arg)
        i += 1
    return positionals, opts


def _cmd_say(args: list[str]) -> int:
    usage = ("usage: skye say <mission-id> --as <role> [--kind K] [--ack-needed R,...] "
             "[--] <message>")
    try:
        positionals, opts = _parse_opts(args, {"--as", "--kind", "--ack-needed"})
    except ValueError as error:
        print(f"{error}\n{usage}", file=sys.stderr)
        return 2
    if not positionals or "--as" not in opts or len(positionals) < 2:
        print(usage, file=sys.stderr)
        return 2
    from . import discuss, events
    from .mission import default_state_dir
    mission_id = positionals[0]
    message = " ".join(positionals[1:])
    ack_needed = [r for r in opts.get("--ack-needed", "").split(",") if r.strip()]
    try:
        entry = discuss.post(default_state_dir(), mission_id,
                             author=opts["--as"],
                             kind=opts.get("--kind", "") or "broadcast",
                             body=message, requires_ack=ack_needed,
                             bridge=events.from_env())
    except discuss.DiscussionError as error:
        print(f"cannot post: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"posted": True, "id": entry["id"], "seq": entry["seq"]}, indent=2))
    return 0


def _cmd_hear(args: list[str]) -> int:
    usage = "usage: skye hear <mission-id> [--since N] [--limit N]"
    try:
        positionals, opts = _parse_opts(args, {"--since", "--limit"})
    except ValueError as error:
        print(f"{error}\n{usage}", file=sys.stderr)
        return 2
    if len(positionals) != 1:
        print(usage, file=sys.stderr)
        return 2
    from . import discuss
    from .mission import default_state_dir
    try:
        since = int(opts.get("--since", "") or 0)
        limit = int(opts.get("--limit", "") or 100)
    except ValueError:
        print(f"--since and --limit take integers\n{usage}", file=sys.stderr)
        return 2
    result = discuss.read(default_state_dir(), positionals[0], since=since, limit=limit)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_ack(args: list[str]) -> int:
    usage = "usage: skye ack <mission-id> <message-id> --as <role> [--note N]"
    try:
        positionals, opts = _parse_opts(args, {"--as", "--note"})
    except ValueError as error:
        print(f"{error}\n{usage}", file=sys.stderr)
        return 2
    if len(positionals) != 2 or "--as" not in opts:
        print(usage, file=sys.stderr)
        return 2
    from . import discuss, events
    from .mission import default_state_dir
    try:
        entry = discuss.ack(default_state_dir(), positionals[0], positionals[1],
                            author=opts["--as"], note=opts.get("--note", ""),
                            bridge=events.from_env())
    except discuss.DiscussionError as error:
        print(f"cannot ack: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"acked": True, "id": entry["id"], "ack_of": entry["ack_of"]}, indent=2))
    return 0


def _cmd_agents(args: list[str]) -> int:
    from . import agents
    if args and args[0] == "promote":
        if len(args) < 2:
            print("usage: skye agents promote <name>", file=sys.stderr)
            return 2
        try:
            record = agents.promote(args[1])
        except Exception as error:
            print(f"cannot promote: {error}", file=sys.stderr)
            return 2
        print(json.dumps({"promoted": True, "agent": record["name"]}, indent=2))
        return 0
    print(json.dumps({"agents": agents.list_definitions()}, indent=2))
    return 0


def _cmd_backlog(args: list[str]) -> int:
    from . import backlog
    if not args or args[0] == "list":
        status = _flag(args, "--status")
        print(json.dumps({"tickets": backlog.list_features(status=status)}, indent=2))
        return 0
    action = args[0]
    if action == "reopen":
        if len(args) < 2:
            print("usage: skye backlog reopen <id> --evidence TEXT [--label L]...", file=sys.stderr)
            return 2
        labels = [args[i + 1] for i, a in enumerate(args) if a == "--label" and i + 1 < len(args)]
        try:
            feature = backlog.reopen(args[1], evidence=_flag(args, "--evidence"),
                                     labels=labels, actor=_flag(args, "--as", "owner") or "owner")
        except backlog.BacklogError as error:
            print(f"cannot reopen: {error}", file=sys.stderr)
            return 2
        print(json.dumps({"reopened": True, "ticket": feature["id"],
                          "status": feature["status"], "labels": feature.get("labels", [])},
                         indent=2))
        return 0
    if action == "comment":
        if len(args) < 3:
            print("usage: skye backlog comment <id> --as <actor> <message>", file=sys.stderr)
            return 2
        message = " ".join(a for a in args[2:] if not a.startswith("--"))
        try:
            entry = backlog.add_thread(args[1], actor=_flag(args, "--as", "owner") or "owner",
                                       kind="comment", body=message)
        except backlog.BacklogError as error:
            print(f"cannot comment: {error}", file=sys.stderr)
            return 2
        print(json.dumps({"added": True, "entry": entry}, indent=2))
        return 0
    if action == "labels":
        if len(args) < 2:
            print("usage: skye backlog labels <id> <label>...", file=sys.stderr)
            return 2
        try:
            feature = backlog.set_labels(args[1], args[2:])
        except backlog.BacklogError as error:
            print(f"cannot set labels: {error}", file=sys.stderr)
            return 2
        print(json.dumps({"labels": feature.get("labels", [])}, indent=2))
        return 0
    print("usage: skye backlog list|reopen|comment|labels ...", file=sys.stderr)
    return 2


def _cmd_issues(args: list[str]) -> int:
    from . import issues
    if not args or args[0] != "report" or len(args) < 2:
        print("usage: skye issues report <ticket-id> --kind K [--label L]... [--detail D] "
              "[--as actor] <summary>", file=sys.stderr)
        return 2
    feature_id = args[1]
    kind = _flag(args, "--kind", "user-report") or "user-report"
    labels = [args[i + 1] for i, a in enumerate(args) if a == "--label" and i + 1 < len(args)]
    # Positional words AFTER the id form the summary; flag values are never words.
    flag_values = {kind, _flag(args, "--detail"), _flag(args, "--as", "pm"), *labels}
    summary = " ".join(a for a in args[2:]
                       if not a.startswith("--") and a not in flag_values)
    try:
        issue, action = issues.report_for_feature(
            feature_id, kind, summary, detail=_flag(args, "--detail"),
            labels=labels, actor=_flag(args, "--as", "pm") or "pm")
    except Exception as error:
        print(f"cannot report: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"reported": True, "issue": issue["id"], "action": action,
                      "note": {"reopened": "the delivered ticket reopened with your evidence; "
                                           "the owner decides the repair",
                               "sent-back": "the unfinished work was sent back with your evidence",
                               "attached": "the bug is attached to the queued work as evidence"}.get(action, action)},
                     indent=2))
    return 0


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
    if command == "ui":
        from .ui.server import serve
        port = 8787
        state_dir = None
        if "--port" in rest:
            i = rest.index("--port")
            port = int(rest[i + 1])
        if "--state-dir" in rest:
            i = rest.index("--state-dir")
            state_dir = rest[i + 1]
        serve(state_dir=state_dir, port=port)
        return 0
    if command == "mission":
        return _cmd_mission(rest)
    if command == "say":
        return _cmd_say(rest)
    if command == "hear":
        return _cmd_hear(rest)
    if command == "ack":
        return _cmd_ack(rest)
    if command == "agents":
        return _cmd_agents(rest)
    if command == "backlog":
        return _cmd_backlog(rest)
    if command == "issues":
        return _cmd_issues(rest)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
