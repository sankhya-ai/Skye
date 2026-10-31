"""`tura-code`: run the server (the local app, or hosted), manage hosted users, check the install."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import webbrowser

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def main(argv: list[str] | None = None) -> int:
    from . import hostos

    hostos.configure_console()
    parser = argparse.ArgumentParser(prog="tura-code", description="root — a coding agent in your browser.")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the server (default).")
    for target in (parser, serve):
        target.add_argument("--host", default=os.environ.get("TURA_CODE_HOST", "127.0.0.1"))
        target.add_argument("--port", type=int, default=int(os.environ.get("TURA_CODE_PORT", "8787")))
        target.add_argument("--hosted", action="store_true", default=os.environ.get("TURA_CODE_MODE") == "hosted", help="Serve many people, each in their own sandboxed tenant.")
        target.add_argument("--sandbox", default=os.environ.get("TURA_CODE_SANDBOX", ""), help="`bwrap` (default when hosted) or `none`.")
        target.add_argument("--open", action="store_true", help="Open the UI in a browser.")
        target.add_argument("--debug", action="store_true")
    sub.add_parser("doctor", help="Check the runtime binary, Dhee, the sandbox and the OpenRouter key.")
    users = sub.add_parser("user", help="Manage the people a hosted server serves.")
    users_sub = users.add_subparsers(dest="action", required=True)
    add = users_sub.add_parser("add", help="Create a user and print their sign-in token.")
    add.add_argument("id")
    add.add_argument("--name", default="")
    add.add_argument("--admin", action="store_true")
    users_sub.add_parser("list")
    for name in ("remove", "rotate"):
        users_sub.add_parser(name).add_argument("id")
    from .line import cli as line_cli

    line_cli.add_parser(sub)
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor()
    if args.command == "user":
        return user_command(args)
    if args.command == "chamber":
        return line_cli.run(args)
    return serve_command(args)


def serve_command(args) -> int:
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .sandbox import from_name

    mode = "hosted" if args.hosted else "local"
    sandbox = from_name(args.sandbox or ("bwrap" if mode == "hosted" else "none"))
    if mode == "hosted" and sandbox.name == "none" and os.environ.get("TURA_CODE_ALLOW_UNSANDBOXED") != "1":
        print("A hosted server runs other people's agents: refusing to start without a sandbox.\nUse --sandbox bwrap (or set TURA_CODE_ALLOW_UNSANDBOXED=1 for a private trial).", file=sys.stderr)
        return 2
    problem = sandbox.problem()
    if problem:
        print(f"Sandbox `{sandbox.name}` is not usable: {problem}", file=sys.stderr)
        return 2
    if args.host not in LOCAL_HOSTS and not os.environ.get("TURA_CODE_ALLOWED_HOSTS"):
        print(
            "Refusing to listen on a non-local address without TURA_CODE_ALLOWED_HOSTS.\n"
            "The agent can run commands on this machine; expose it deliberately.",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    from .app import create_app
    from .instance import AlreadyRunning, claim
    from .settings import data_dir

    try:
        lock = claim(data_dir(), f"http://{'localhost' if args.host in LOCAL_HOSTS else args.host}:{args.port}")  # noqa: F841 - held until exit
    except AlreadyRunning as error:
        print(str(error), file=sys.stderr)
        return 3

    # Runtimes reach the LLM proxy on the loopback interface, whatever the public address.
    loopback = "[::1]" if args.host == "::1" else "127.0.0.1"
    app = create_app(mode=mode, sandbox=sandbox, self_url=f"http://{loopback}:{args.port}")
    url = f"http://{'localhost' if args.host in LOCAL_HOSTS else args.host}:{args.port}"
    print(f"root ({mode}, sandbox: {sandbox.name}) is running at {url}")
    if mode == "hosted" and not app.state.root.server.users.list():
        print("No users yet: create one with `tura-code user add <id>`.")
    if args.open:
        webbrowser.open(url)
    # Image attachments travel over the socket as base64.
    uvicorn.run(app, host=args.host, port=args.port, log_level="debug" if args.debug else "info", ws_max_size=64 * 1024 * 1024)
    return 0


def user_command(args) -> int:
    from .settings import data_dir
    from .users import UserStore

    store = UserStore(data_dir() / "users.json")
    try:
        if args.action == "add":
            token = store.add(args.id, name=args.name, admin=args.admin)
            print(f"Created {args.id}. Their sign-in token (shown once):\n{token}")
        elif args.action == "rotate":
            print(f"New token for {args.id} (shown once):\n{store.rotate(args.id)}")
        elif args.action == "remove":
            print("Removed." if store.remove(args.id) else "No such user.")
        else:
            for user in store.list():
                print(f"{user['id']:20} {user['name']:24} {'admin' if user['admin'] else ''}")
    except (ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


def doctor() -> int:
    from . import launcher
    from .sandbox import Bubblewrap
    from .settings import SettingsStore, data_dir

    ok = True
    root = data_dir()
    print(f"data directory:   {root}")
    try:
        print(f"runtime binary:   {launcher.find_runtime_binary()}")
    except launcher.RuntimeNotFound as error:
        ok = False
        print(f"runtime binary:   MISSING — {error}")
    try:
        print(f"agent spec:       {launcher.base_agent_file()}")
    except launcher.RuntimeNotFound as error:
        ok = False
        print(f"agent spec:       MISSING — {error}")
    settings = SettingsStore(root).get()
    print(f"OpenRouter key:   {settings.key_source()}")
    print(f"model:            {settings.model}")
    print(f"Jev director:     {settings.director_mode}")
    print(f"web search:       {'keyless (Parallel, Firecrawl)' if settings.web_search else 'off'}")
    problem = Bubblewrap().problem()
    print(f"sandbox (bwrap):  {'available' if not problem else f'unavailable — {problem}'} (required for --hosted)")
    try:
        import dhee

        print(f"Dhee:             {dhee.__version__} (memory mode: {settings.memory_mode})")
    except Exception as error:  # noqa: BLE001
        print(f"Dhee:             not installed ({error}); memory will be off")
    if settings.key_source() == "missing" and not launcher.scripted():
        ok = False
        print("\nSet OPENROUTER_API_KEY or add a key in the UI's settings.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
