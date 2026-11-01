"""Lifecycle events out of the dispatcher - the Chamber bridge.

Every dispatch emits plain-dict events (contract accepted, needs-owner, worker failed, each gate,
done). Where they GO is configuration: a Chamber line when SKYE_LINE_INVITE names one, otherwise a
local events.jsonl next to the policy so nothing is silently dropped. The bridge publishes facts
the host observed (status, reason, gate results); it never publishes contract briefs' secrets -
the event carries goal, role, adapter, status and reason, nothing from the worker's output beyond
its tail. Chamber itself (vendored at chamber/) provides the relay the line runs on; the client is
Root's vendored Python line client (root/server/tura_code/line), reusing its sealing, cursor and
reconnect handling rather than re-implementing them.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


def make_event(kind: str, contract: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "ts": time.time(), "goal": contract.get("goal", ""),
            "role": contract.get("role", ""), "adapter": contract.get("adapter", ""), **fields}


class FileBridge:
    """The zero-config sink: an append-only JSONL log. Honest default - no line configured means
    events are recorded locally, not dropped and not faked."""

    def __init__(self, path: str | Path = "events.jsonl") -> None:
        self.path = Path(path)

    def publish(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")

    def close(self) -> None:
        pass


class LineBridge:
    """Publishes events to a Chamber line through Root's vendored line client.

    The client runs its reconnect loop on a private asyncio thread; publish() is synchronous for
    the dispatcher and raises if the line is not connected (callers fall back to the file sink via
    FanoutBridge). This is a REAL line membership: the agent name joins the roster and every event
    is a sealed line message, not a log line.
    """

    def __init__(self, invite_url: str, agent: str = "skye/dispatcher", to: str = "") -> None:
        if not to.strip():
            # No silent broadcast: lifecycle events go only to an explicitly configured audience.
            # "*" (everyone on the line) is a valid choice - but the operator must CHOOSE it.
            raise ValueError("LineBridge needs an explicit recipient (e.g. SKYE_LINE_TO=@ashish; '*' is everyone on the line and must be chosen, not defaulted)")
        import asyncio  # noqa: PLC0415
        import sys  # noqa: PLC0415

        root_pkg = Path(__file__).resolve().parent.parent / "root" / "server"
        if str(root_pkg) not in sys.path:
            sys.path.insert(0, str(root_pkg))
        from tura_code.line.client import LineClient  # noqa: PLC0415
        from tura_code.line.protocol import Invite  # noqa: PLC0415

        self._asyncio = asyncio
        self.client = LineClient(Invite.parse(invite_url), agent)
        self.to = to
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._task = asyncio.run_coroutine_threadsafe(self.client.run(), self._loop)
        joined = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(self.client.connected.wait(), 15), self._loop)
        try:
            joined.result(20)
        except Exception as error:  # noqa: BLE001
            raise ConnectionError(f"line: could not join as {agent}: {error}") from error

    def publish(self, event: dict[str, Any]) -> None:
        # Payload minimization: kind/role/status and the short operational reason only. The goal IS
        # mission content and stays off the line unless the operator opts in with SKYE_LINE_VERBOSE=1.
        text = f"[skye:{event['kind']}] {event.get('role', '?')} {event.get('status', '')}"
        if os.environ.get("SKYE_LINE_VERBOSE") == "1":
            text += f" - {event.get('goal', '')[:120]}"
        if event.get("reason"):
            text += f" ({event['reason'][:160]})"
        target = self.to
        future = self._asyncio.run_coroutine_threadsafe(self.client.send(target, text), self._loop)
        future.result(15)

    def close(self) -> None:
        self._asyncio.run_coroutine_threadsafe(self.client.close(), self._loop).result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)


class FanoutBridge:
    """Every configured sink gets every event; a failing sink never loses the event - it lands in
    the file sink, which is always present."""

    def __init__(self, *bridges: Any) -> None:
        self.bridges = list(bridges)

    def publish(self, event: dict[str, Any]) -> None:
        problems = []
        for bridge in self.bridges:
            try:
                bridge.publish(event)
            except Exception as error:  # noqa: BLE001 - a dead sink must not kill the dispatch
                problems.append(f"{type(bridge).__name__}: {error}")
        if problems:
            FileBridge().publish(make_event("bridge-error", {}, reason="; ".join(problems), original=event))

    def close(self) -> None:
        for bridge in self.bridges:
            try:
                bridge.close()
            except Exception:  # noqa: BLE001
                pass


def from_env(log_path: str | Path = "events.jsonl") -> FanoutBridge:
    """Configuration by environment: SKYE_LINE_INVITE (a chamber line invite URL) adds the line
    sink; SKYE_LINE_TO names the recipient handle and is REQUIRED (no default audience: "*" is
    everyone on the line and must be chosen explicitly). The file sink is always on."""
    sinks: list[Any] = [FileBridge(log_path)]
    invite = os.environ.get("SKYE_LINE_INVITE", "").strip()
    if invite:
        try:
            sinks.append(LineBridge(invite, to=os.environ.get("SKYE_LINE_TO", "").strip()))
        except Exception as error:  # noqa: BLE001 - fall back to the file sink, saying so
            FileBridge(log_path).publish(make_event("bridge-error", {}, reason=f"line sink OFF: {error}"))
    return FanoutBridge(*sinks)
