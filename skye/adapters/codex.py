import json

from .base import SessionAdapter


class CodexAdapter(SessionAdapter):
    """The user's own Codex CLI install. Non-interactive ``codex exec`` mode, which runs read-only
    by default; the sandbox widens to workspace-write only when the role's gateway catalogue grants
    writes (verified against the current Codex non-interactive docs; ``--full-auto`` is deprecated
    and never used). The sandbox is Codex's own mechanism, not an OS boundary Skye provides.
    ``codex exec`` refuses to run outside a git repository unless told otherwise - Skye leaves that
    vendor safety check in place.
    """
    name = "codex"
    binary = "codex"
    # Only the wall-clock timeout is enforceable here; max_steps/max_cost_usd surface as notes.
    ENFORCED_BUDGET = ("timeout_seconds",)
    # NO live steering on this protocol: `codex exec` is one-shot - it takes one prompt and
    # runs it to completion (codex-rs/exec has no stdin input channel). The vendor's live
    # mid-turn input is `turn/steer` on the separate `codex app-server` JSON-RPC protocol
    # (codex-rs/app-server-protocol: TurnSteerParams, marked experimental upstream); Skye
    # does not implement that protocol, so follow-ups on codex are after-turn continuations
    # via `exec resume`, never live injection. steer_live() reports "unsupported".
    SUPPORTS_STEER = False

    def _prompt(self, contract: dict) -> str:
        prompt = contract.get("brief") or contract["goal"]
        if contract.get("role_instructions"):
            prompt = contract["role_instructions"] + "\n\n" + prompt
        if contract.get("reference_docs"):
            refs = "\n\n".join(
                f"--- {d['path']} (owner-supplied project reference) ---\n{d['content']}"
                for d in contract["reference_docs"])
            prompt = "Project references:\n" + refs + "\n\n" + prompt
        return prompt

    def _sandbox(self, contract: dict) -> str:
        return "workspace-write" if bool(contract.get("tools_writable")) else "read-only"

    def build_command(self, contract: dict) -> list[str]:
        return [self.binary, "exec", "--sandbox", self._sandbox(contract),
                self._prompt(contract)]

    # -- Session protocol (--json events) ------------------------------------------
    # Grounded in the Codex CLI source (codex-rs/exec): `codex exec --json` emits one
    # JSON ThreadEvent per line - thread.started carries the thread_id "used to resume
    # the thread later" (exec_events.rs) - and `codex exec resume <SESSION_ID> [prompt]`
    # re-enters a recorded session (cli.rs ResumeArgs). Options precede the subcommand,
    # matching the clap layout (global exec options, then resume/fork/review).

    def build_session_command(self, contract: dict, resume_session_id: str | None) -> list[str]:
        base = [self.binary, "exec", "--json", "--sandbox", self._sandbox(contract)]
        if resume_session_id:
            # The role's sandbox still applies on a resumed session: continuity never
            # widens what the worker may touch.
            return base + ["resume", resume_session_id, self._prompt(contract)]
        return base + [self._prompt(contract)]

    def parse_stream_line(self, line: str) -> list[dict]:
        try:
            data = json.loads(line)
        except ValueError:
            return []
        kind = data.get("type")
        if kind == "thread.started":
            return [{"kind": "session", "session_id": str(data.get("thread_id") or "")}]
        if kind == "item.completed":
            item = data.get("item") or {}
            item_type = item.get("type")
            if item_type == "agent_message" and item.get("text"):
                return [{"kind": "message", "text": str(item["text"])}]
            if item_type == "command_execution":
                return [{"kind": "tool", "name": str(item.get("command") or "")[:120]}]
            return []
        if kind == "turn.completed":
            usage = data.get("usage") or {}
            return [{"kind": "result", "ok": True, "session_id": "",
                     "usage": {k: usage[k] for k in
                               ("input_tokens", "output_tokens") if k in usage}}]
        if kind == "turn.failed":
            return [{"kind": "result", "ok": False, "session_id": "",
                     "usage": {}},
                    {"kind": "error", "text": str((data.get("error") or {})
                                                  .get("message") or "turn failed")}]
        if kind == "error":
            return [{"kind": "error", "text": str(data.get("message") or "stream error")}]
        return []
