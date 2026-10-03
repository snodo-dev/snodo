"""Claude Code CLI in-place coder adapter."""

import json
from typing import Any, Optional

from snodo.coders.subprocess_adapter import SubprocessCoderAdapter


class ClaudeCLIAdapter(SubprocessCoderAdapter):
    """Run Claude Code's non-interactive CLI using the user's existing login."""

    coder_name = "claude-cli"
    binary = "claude"
    model_prefix = "claude-cli/"
    install_hint = "Install Claude Code and authenticate with `claude auth login`."

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.last_usage: dict[str, Optional[int | float | str]] = self._empty_usage()

    @staticmethod
    def _empty_usage() -> dict[str, Optional[int | float | str]]:
        return {
            "input_tokens": None, "output_tokens": None,
            "reasoning_tokens": None, "cache_read_tokens": None,
            "cache_write_tokens": None, "cost": None, "served_model": None,
        }

    def _build_argv(self, prompt: str, project_root: str, model: str) -> list[str]:
        argv = [
            "claude", "-p", "--output-format", "stream-json", "--verbose",
            "--permission-mode", "bypassPermissions", "--setting-sources", "user",
            prompt,
        ]
        if model:
            argv[1:1] = ["--model", model]
        return argv

    def _begin_subprocess_run(self) -> None:
        self.last_usage = self._empty_usage()

    def _process_output_line(self, line: str) -> str:
        raw = line.rstrip("\n")
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            return raw
        if not isinstance(event, dict):
            return raw
        kind = event.get("type")
        if kind == "assistant":
            content = event.get("message", {}).get("content", [])
            return "\n".join(str(block.get("text", "")) for block in content
                             if isinstance(block, dict) and block.get("type") == "text")
        if kind == "result":
            usage = event.get("usage") or {}
            mapping = {
                "input_tokens": "input_tokens",
                "output_tokens": "output_tokens",
                "cache_read_input_tokens": "cache_read_tokens",
                "cache_creation_input_tokens": "cache_write_tokens",
            }
            for source, target in mapping.items():
                value = usage.get(source)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.last_usage[target] = value
            cost = event.get("total_cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                self.last_usage["cost"] = cost
            model = event.get("model")
            if isinstance(model, str) and model:
                self.last_usage["served_model"] = model
            return str(event.get("result", ""))
        if kind == "stream_event":
            delta = event.get("event", {}).get("delta", {})
            return str(delta.get("text", "")) if isinstance(delta, dict) else ""
        return str(event.get("text", ""))

    def _format_output_tail_stdout(self, stdout: str) -> str:
        rendered = [self._process_output_line(line) for line in stdout.splitlines()]
        return "\n".join(line for line in rendered if line)
