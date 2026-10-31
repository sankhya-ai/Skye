"""`tura-code chamber …`: root's part of the chamber. Joining is the `chamber` command's job."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from ..settings import data_dir
from . import config as line_config


def add_parser(sub) -> None:
    line = sub.add_parser("chamber", help="The chamber: root answering your teammates' agents.")
    actions = line.add_subparsers(dest="action", required=True)
    answer = actions.add_parser("answer", help="Have root answer from this repository (restart root after).")
    answer.add_argument("workspace", nargs="?", default=".")
    actions.add_parser("status", help="Show the line and who is on it.")


def run(args) -> int:
    home = data_dir()
    config = line_config.load(home)
    if config is None:
        print("Not on a line: join with chamber first (chamber: run the install command a teammate sent you).", file=sys.stderr)
        return 1
    if args.action == "answer":
        workspace = Path(args.workspace).expanduser().resolve()
        if not workspace.is_dir():
            print(f"line: not a directory: {workspace}", file=sys.stderr)
            return 1
        line_config.save_workspace(home, str(workspace))
        print(f"root answers {config.owner}'s line questions from {workspace} (read-only). Restart root to apply.")
        return 0
    try:
        response = httpx.get(f"{config.invite.relay}/v1/lines/{config.invite.line_id}/roster", headers={"Authorization": f"Bearer {config.invite.token}"}, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError as error:
        print(f"line: {error}", file=sys.stderr)
        return 1
    print(f"{config.owner} on {config.invite.relay}; root answers from {config.workspace or '(nowhere: `tura-code chamber answer`)'}")
    for agent in response.json().get("agents", []):
        print(f"  {agent['agent']:<36} {agent['kind']:<12} {'answers' if agent.get('answers') else ''}")
    return 0
