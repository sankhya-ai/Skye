"""`skye video`: the same trusted operations the browser workspace uses."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from skye.mission import default_state_dir
from .model import MediaError, project_dir


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skye video")
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("file", type=Path)
    imp.add_argument("--one-shot", action="store_true")
    imp.add_argument("--cuts", help="Comma-separated project frame indices, including 0 and end")
    for action in ("inspect", "render", "prepare", "apply"):
        cmd = sub.add_parser(action)
        cmd.add_argument("project")
        if action == "render":
            cmd.add_argument("--profile", choices=["preview", "final"], default="preview")
        if action == "prepare":
            cmd.add_argument("shot")
            cmd.add_argument("--goal", default="")
            cmd.add_argument("--adapter", choices=["claude-code", "codex"], default="claude-code")
        if action == "apply":
            cmd.add_argument("proposal")
    sub.add_parser("doctor")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            from .process import diagnostics
            result = diagnostics()
        elif args.command == "import":
            from .importer import import_video
            cuts = [int(n) for n in args.cuts.split(",")] if args.cuts else None
            result = import_video(args.state_dir.resolve(), args.file.resolve(), scene_detection=not args.one_shot, cuts=cuts)
        else:
            root = project_dir(args.state_dir.resolve(), args.project)
            if args.command == "inspect":
                from .api import snapshot
                result = snapshot(root)
            elif args.command == "render":
                from .renderer import render
                result = render(root, args.profile)
            elif args.command == "prepare":
                from .handoff import prepare
                result = prepare(root, args.shot, args.goal, args.adapter)
            else:
                from .handoff import apply
                result = {"revision": apply(root, args.proposal)}
        print(json.dumps(result, indent=2))
        return 0
    except (MediaError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
