"""skye - submit a job contract to the team. `skye run job.json` validates, checks the team policy,
and either runs the contract through its worker and gates or tells you it needs the owner's go-ahead."""

from __future__ import annotations

import json
import sys

from .contract import ContractError, load
from .dispatcher import dispatch
from .policy import Policy


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("run", "check"):
        print("usage: skye run <job.json> | skye check <job.json>", file=sys.stderr)
        return 2
    try:
        contract = load(argv[2] if len(argv) > 2 else "")
    except ContractError as error:
        print(f"invalid contract: {error}", file=sys.stderr)
        return 2
    if argv[1] == "check":
        print(json.dumps(contract, indent=2))
        return 0
    outcome = dispatch(contract, Policy.load())
    print(json.dumps({"status": outcome.status, "reason": outcome.reason,
                      "gates": outcome.gates, "worker_output": outcome.worker_output[-2000:]}, indent=2))
    return 0 if outcome.status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
