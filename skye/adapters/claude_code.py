import json

from .base import SessionAdapter


class ClaudeCodeAdapter(SessionAdapter):
    """The user's own Claude Code install (their subscription, their login). Headless print mode.

    The role's gateway catalogue maps to ``--tools`` (restricts which built-in tools exist in the
    session; verified against the current CLI reference - ``--allowedTools`` only skips permission
    prompts, it does not restrict) plus ``--disallowedTools mcp__*`` so MCP tools cannot widen it.
    This is CLI configuration, NOT an OS boundary: a model that gets Bash can do anything Bash can.
    The boundary, when one is required, is the jail the job runs under.
    """
    name = "claude-code"
    binary = "claude"
    # Only the wall-clock timeout is enforceable by Skye itself. max_steps and max_cost_usd
    # are FORWARDED to the CLI's own print-mode caps (--max-turns, --max-budget-usd): the
    # vendor enforces them, client-side, with documented headroom caveats (the budget cap
    # is an estimate that can be passed; restored totals from resumed sessions do not
    # count toward it) - forwarded, never promised as a hard bound.
    ENFORCED_BUDGET = ("timeout_seconds",)
    FORWARDED_BUDGET = ("max_steps", "max_cost_usd")

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

    def build_command(self, contract: dict) -> list[str]:
        command = [self.binary, "-p", self._prompt(contract), "--output-format", "text"]
        tools = (contract.get("tools") or [])
        if tools:
            command += ["--tools", ",".join(tools), "--disallowedTools", "mcp__*"]
        return command

    # -- Session protocol (stream-json) --------------------------------------------
    # Grounded in the vendor CLI reference + Agent SDK docs: print mode with
    # --output-format stream-json emits one JSON object per line (system init carrying
    # the session id, assistant messages, a final result) and requires --verbose;
    # `claude -p --resume <session-id>` re-enters a recorded print session with its full
    # conversation context. --input-format stream-json takes the prompt - and later
    # follow-ups - as user-message frames on stdin; the vendor documents queued messages
    # processing sequentially between turns, which is what makes a LIVE follow-up safe:
    # it is consumed as the next turn, never injected mid-tool-call. The frame shape is
    # the Agent SDK's documented SDKUserMessage: {"type": "user", "message": {"role":
    # "user", "content": <text>}}.

    SUPPORTS_STEER = True

    @staticmethod
    def _user_frame(text: str) -> str:
        return json.dumps({"type": "user",
                           "message": {"role": "user", "content": text}}) + "\n"

    def prompt_via_stdin(self, prompt: str) -> str | None:
        return self._user_frame(prompt)

    def steer_frame(self, text: str) -> str | None:
        return self._user_frame(text)

    def build_session_command(self, contract: dict, resume_session_id: str | None) -> list[str]:
        command = [self.binary, "-p",
                   "--output-format", "stream-json", "--verbose",
                   "--input-format", "stream-json", "--replay-user-messages"]
        tools = (contract.get("tools") or [])
        if tools:
            # The role's gateway catalogue still scopes the session: same flags, same
            # MCP lockout as one-shot mode. Session continuity never widens permissions.
            command += ["--tools", ",".join(tools), "--disallowedTools", "mcp__*"]
        budget = contract.get("budget") or {}
        if budget.get("max_steps"):
            command += ["--max-turns", str(int(budget["max_steps"]))]
        if budget.get("max_cost_usd"):
            command += ["--max-budget-usd", str(budget["max_cost_usd"])]
        if resume_session_id:
            command += ["--resume", resume_session_id]
        return command

    def parse_stream_line(self, line: str) -> list[dict]:
        try:
            data = json.loads(line)
        except ValueError:
            return []
        kind = data.get("type")
        if kind == "system" and data.get("subtype") == "init":
            return [{"kind": "session", "session_id": str(data.get("session_id") or "")}]
        if kind == "assistant":
            events = []
            for block in (data.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    events.append({"kind": "message", "text": str(block["text"])})
                elif block.get("type") == "tool_use":
                    events.append({"kind": "tool", "name": str(block.get("name") or "")})
            return events
        if kind == "result":
            return [{"kind": "result", "ok": data.get("subtype") == "success",
                     "session_id": str(data.get("session_id") or ""),
                     "usage": {k: data[k] for k in
                               ("total_cost_usd", "num_turns", "duration_ms") if k in data}}]
        if kind == "user":
            # --replay-user-messages echo: the provider's own receipt that a queued
            # stdin message (a steered follow-up) was CONSUMED - distinct from our
            # "submitted" bookkeeping. Tool results also arrive as user messages but
            # carry list content; only a plain string is an echoed user message.
            content = (data.get("message") or {}).get("content")
            if isinstance(content, str) and content:
                # --replay-user-messages: the CLI's own acknowledgment that it took
                # the stdin frame INTO THE SESSION. It does not prove the model acted
                # on the text - that is the subsequent assistant output, separately
                # visible in the stream.
                return [{"kind": "steer-ack", "text": content[:300]}]
            return []
        # tool results return as user messages; they are not surfaced as worker text
        return []
