"""Runs INSIDE a network-isolated jail (stdlib only; run by /usr/bin/python3): ``netrelay.py PORT SOCKET -- COMMAND...``.

The jail's network namespace holds only its own loopback. This listens on 127.0.0.1:PORT there and forwards every
connection over the unix socket the host bind-mounted in, to the host's LLM proxy (see ``netbridge``), then runs COMMAND.
Nothing else is reachable: not the host's loopback, not the owner API, not the internet.
"""

import socket
import sys
import threading


def pump(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def serve(port: int, path: str) -> None:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(64)

    def accept() -> None:
        while True:
            client, _ = listener.accept()
            upstream = socket.socket(socket.AF_UNIX)
            try:
                upstream.connect(path)
            except OSError:
                client.close()
                continue
            threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
            threading.Thread(target=pump, args=(upstream, client), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()


if __name__ == "__main__":
    split = sys.argv.index("--")
    import signal
    import subprocess

    serve(int(sys.argv[1]), sys.argv[2])
    # Not exec: the relay threads must outlive the start of the command. The child keeps this process's stdio (the
    # runtime's wire protocol), and the jail dies with its parent (bwrap --die-with-parent), so nothing is left behind.
    child = subprocess.Popen(sys.argv[split + 1 :])
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda n, f: child.send_signal(n))
    sys.exit(child.wait())
