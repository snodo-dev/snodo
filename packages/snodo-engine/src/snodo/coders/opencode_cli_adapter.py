"""OpenCode CLI coder adapter — shells `opencode run` on the host.

FILE: snodo/coders/opencode_cli_adapter.py

Runs opencode directly on the host machine via SubprocessCoderAdapter.
"""

import json
from typing import Any, List, Optional

from snodo.coders.subprocess_adapter import SubprocessCoderAdapter


class OpenCodeCLIAdapter(SubprocessCoderAdapter):
    """Coder adapter backed by the host ``opencode run`` CLI."""

    coder_name: str = "opencode-cli"
    binary: str = "opencode"
    model_prefix: str = "opencode-cli/"
    install_hint: str = (
        "Install opencode: curl -fsSL https://opencode.ai/install | bash"
    )

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.last_usage: dict[str, Optional[int | float]] = self._empty_usage()

    @staticmethod
    def _empty_usage() -> dict[str, Optional[int | float]]:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
            "cost": None,
        }

    def _build_argv(self, prompt: str, project_root: str, model: str) -> List[str]:
        argv = [
            "opencode", "run", "--format", "json", "--dir", project_root,
            "--dangerously-skip-permissions", prompt,
        ]
        if model:
            argv.extend(["-m", model])
        return argv

    def _begin_subprocess_run(self) -> None:
        self.last_usage = self._empty_usage()

    def _process_output_line(self, line: str) -> str:
        """Collect JSON usage events and turn event lines into readable output."""
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            return line.rstrip("\n")
        if not isinstance(event, dict):
            return line.rstrip("\n")
        if event.get("type") == "step_finish":
            part = event.get("part") or event
            tokens = part.get("tokens") or {}
            values = {
                "input_tokens": tokens.get("input"),
                "output_tokens": tokens.get("output"),
                "reasoning_tokens": tokens.get("reasoning"),
                "cache_read_tokens": (tokens.get("cache") or {}).get("read"),
                "cache_write_tokens": (tokens.get("cache") or {}).get("write"),
                "cost": part.get("cost"),
            }
            for key, value in values.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.last_usage[key] = (self.last_usage[key] or 0) + value
            return ""
        part = event.get("part") or {}
        if event.get("type") == "text":
            return str(part.get("text", ""))
        if event.get("type") == "tool":
            tool = part.get("tool", "tool")
            state = part.get("state") or {}
            title = state.get("title") or state.get("status") or ""
            return f"[{tool}] {title}".rstrip()
        return str(event.get("text", ""))
