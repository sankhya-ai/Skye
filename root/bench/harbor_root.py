"""root as a Harbor agent, for Terminal-Bench and other Harbor datasets.

Harbor runs each task in its own container and grades it with the task's own
verifier. This agent installs root's runtime (a static Linux build of
`runtime/`) and root's agent spec (`agent/`) into that container and drives it
exactly as Harbor's built-in `kimi-cli` agent drives Kimi CLI — the runtime
speaks the same wire protocol, so Harbor's own event parsing and trajectory
export are reused.

What comes along is everything that lives in the runtime: the Jev step
director, the verify gate, argument and path repair, leaked-call recovery, and
root's system prompt and subagents. What does not: the web host's extras —
Dhee memory, the learning loop, background processes, coding flows.

Model calls go to OpenRouter with the key in OPENROUTER_API_KEY. Cost is the
sum of the `cost_usd` OpenRouter reports on each step.

    harbor run -d terminal-bench/terminal-bench@latest -i terminal-bench/<task> --env docker \\
        -a bench.harbor_root:Root -m openrouter/deepseek/deepseek-v4.1-flash \\
        -o "$PWD/bench/jobs" [--ak provider_sort=throughput] [--ak reasoning_effort=none]
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any, override

from harbor.agents.installed.base import NonZeroAgentExitCodeError, with_prompt_template
from harbor.agents.installed.kimi_cli import KimiCli, KimiCliOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import Field

REPO = Path(__file__).resolve().parents[1]
#: Built by `bench/build-linux.sh`: static (musl), so it runs on any task image.
BINARY = REPO / "runtime" / "target-linux" / "release" / "kimi-agent"
AGENT_DIR = REPO / "agent"

OUTPUT = "kimi-cli.txt"  # the name KimiCli's parser reads
STDERR = "root.stderr.log"
MAX_STEPS_PER_TURN = 100  # the app's default


class RootOptions(KimiCliOptions):
    reasoning_effort: str | None = Field(default=None, description="none|low|medium|high; unset keeps the model's default.")
    provider_sort: str | None = Field(default=None, description="throughput|price|latency; unset keeps OpenRouter's balancing.")
    director: str = Field(default="on", description="Jev step director: on|shadow|off.")


class Root(KimiCli):
    options_model = RootOptions
    options: RootOptions

    def __init__(self, *args, reasoning_effort: str | None = None, provider_sort: str | None = None, director: str = "on", **kwargs):
        super().__init__(*args, **kwargs)
        if director not in ("on", "shadow", "off"):
            raise ValueError("director must be on, shadow or off")
        self.options.reasoning_effort = reasoning_effort
        self.options.provider_sort = provider_sort
        self.options.director = director

    @staticmethod
    @override
    def name() -> str:
        return "root"

    @override
    def get_version_command(self) -> str | None:
        return "/opt/root/kimi-agent --version"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        if not BINARY.exists():
            raise FileNotFoundError(f"{BINARY} is missing: run bench/build-linux.sh first")
        await self.exec_as_root(environment, command="mkdir -p /opt/root && chmod 755 /opt/root")
        await environment.upload_file(BINARY, "/opt/root/kimi-agent")
        await environment.upload_dir(AGENT_DIR, "/opt/root/agent")
        await self.exec_as_root(environment, command="chmod 755 /opt/root/kimi-agent && /opt/root/kimi-agent --version")

    def _root_config(self, model: str) -> dict[str, Any]:
        # As server/tura_code/launcher.py writes it: the provider id must be
        # "openrouter" for the runtime's OpenRouter protocol (reasoning, cost).
        return {
            "default_model": "main",
            "default_thinking": False,
            "loop_control": {
                "max_steps_per_turn": MAX_STEPS_PER_TURN,
                "max_retries_per_step": 3,
                "max_ralph_iterations": 0,
                "reserved_context_size": 50_000,
            },
            "providers": {
                "openrouter": {
                    "type": "kimi",
                    "base_url": self.options.base_url or "https://openrouter.ai/api/v1",
                    "api_key": "__ROOT_API_KEY__",
                    "prompt_cache": True,
                    "custom_headers": {"HTTP-Referer": "https://github.com/sankhya-ai/root", "X-Title": "root"},
                }
            },
            "models": {"main": {"provider": "openrouter", "model": model, "max_context_size": 200_000, "capabilities": []}},
        }

    @override
    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        model = (self.model_name or "").removeprefix("openrouter/")
        if not model:
            raise ValueError("pass -m openrouter/<model>")
        env = {
            "KIMI_SHARE_DIR": "/logs/agent/kimi/share",
            "ROOT_CONFIG_JSON": json.dumps(self._root_config(model)),
            "ROOT_API_KEY": self.options.api_key or os.environ.get("OPENROUTER_API_KEY", ""),
            # Root's harness, as the app launches it.
            "CHOTU_DIRECTOR": self.options.director,
            "CHOTU_DECISIONS_ENABLED": "0" if self.options.director == "off" else "1",
            "ROOT_VERIFY_GATE": "1",
        }
        if self.options.reasoning_effort:
            env["KIMI_MODEL_REASONING_EFFORT"] = self.options.reasoning_effort
        if self.options.provider_sort:
            env["ROOT_OPENROUTER_SORT"] = self.options.provider_sort
        # No Python is guaranteed in the image: bash writes the config, key and all.
        await self.exec_as_agent(
            environment,
            command='mkdir -p /logs/agent/kimi/share && printf \'%s\' "${ROOT_CONFIG_JSON/__ROOT_API_KEY__/$ROOT_API_KEY}" > /tmp/root-config.json && chmod 600 /tmp/root-config.json',
            env=env,
        )
        request = json.dumps({"jsonrpc": "2.0", "method": "prompt", "id": "1", "params": {"user_input": instruction}})
        command = (
            f"(echo {shlex.quote(request)}; sleep 86400) | "
            "/opt/root/kimi-agent --config-file /tmp/root-config.json --agent-file /opt/root/agent/agent.yaml --yolo "
            f"2>>/logs/agent/{STDERR} | ("
            f"while IFS= read -r line; do echo \"$line\" >> /logs/agent/{OUTPUT}; "
            "case \"$line\" in *'\"id\":\"1\"'*) break ;; esac; done; kill 0 2>/dev/null)"
        )
        try:
            await self.exec_as_agent(environment, command=command, env=env)
        except NonZeroAgentExitCodeError as error:
            if "exit 143" not in str(error):  # `kill 0` ends the pipeline once the turn is answered
                raise

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        super().populate_context_post_run(context)
        cost = 0.0
        prompt = completion = cached = 0
        for event in self._parse_wire_events():
            params = event.get("params") or {}
            payload = params.get("payload") or {}
            if params.get("type") != "StatusUpdate":
                continue
            usage = payload.get("token_usage") or {}
            cost += float(usage.get("cost_usd") or 0)
            prompt += int(usage.get("input_other") or 0) + int(usage.get("input_cache_read") or 0) + int(usage.get("input_cache_creation") or 0)
            completion += int(usage.get("output") or 0)
            cached += int(usage.get("input_cache_read") or 0)
        if cost or prompt:
            context.cost_usd = round(cost, 6)
            context.n_input_tokens = prompt
            context.n_output_tokens = completion
            context.n_cache_tokens = cached
