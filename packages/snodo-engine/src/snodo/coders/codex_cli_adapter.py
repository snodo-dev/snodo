"""OpenAI Codex CLI in-place coder adapter."""

import json
from typing import Any, Optional

from snodo.coders.subprocess_adapter import SubprocessCoderAdapter


class CodexCLIAdapter(SubprocessCoderAdapter):
    """Run ``codex exec`` in the task worktree using the user's Codex login."""

    coder_name = "codex-cli"
    binary = "codex"
    model_prefix = "codex-cli/"
    install_hint = "Install the OpenAI Codex CLI and authenticate with `codex login`."

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
        argv = ["codex", "exec", "--json", "--sandbox", "workspace-write", "--cd", project_root]
        if model:
            argv.extend(["--model", model])
        argv.append(prompt)
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
        if event.get("type") == "turn.completed":
            usage = event.get("usage") or event.get("info", {}).get("usage") or {}
            mapping = {
                "input_tokens": "input_tokens",
                "cached_input_tokens": "cache_read_tokens",
                "output_tokens": "output_tokens",
                "reasoning_output_tokens": "reasoning_tokens",
            }
            for source, target in mapping.items():
                value = usage.get(source)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.last_usage[target] = (self.last_usage[target] or 0) + value
            served = event.get("served_model") or event.get("model") or event.get("info", {}).get("model")
            if isinstance(served, str) and served:
                self.last_usage["served_model"] = served
            return ""
        if event.get("type") in {"item.completed", "item.started"}:
            item = event.get("item") or {}
            kind = item.get("type")
            if kind in {"agent_message", "assistant_message"}:
                return str(item.get("text", ""))
            if kind in {"command_execution", "command"}:
                command = item.get("command") or item.get("text") or ""
                return f"[command] {command}".rstrip()
            return str(item.get("text", ""))
        return str(event.get("text", raw))
