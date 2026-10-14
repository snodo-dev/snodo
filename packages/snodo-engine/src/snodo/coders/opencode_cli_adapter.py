"""OpenCode CLI coder adapter — shells `opencode run` on the host.

FILE: snodo/coders/opencode_cli_adapter.py

Runs opencode directly on the host machine via SubprocessCoderAdapter.
"""

import json
import re
from typing import Any, List, Optional

from snodo.coders.subprocess_adapter import SubprocessCoderAdapter
from snodo.config import ConfigManager, DEFAULT_CLOUD_MODELS_URL
from snodo.paths import subprocess_env_for_coder


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

    def _cli_major_version(self) -> int:
        """Return the host opencode's major version, or 1 when it will not say."""
        from snodo.coders.availability import read_binary_version

        reported = read_binary_version(self._resolve_binary_path(), self.version_args)
        match = re.search(r"(\d+)\.\d+", reported)
        return int(match.group(1)) if match else 1

    def _build_argv(self, prompt: str, project_root: str, model: str) -> List[str]:
        if self._cli_major_version() >= 2:
            # opencode 2.x dropped --dir and --dangerously-skip-permissions. The
            # run already starts in project_root (cwd); --standalone keeps it on
            # a private server rather than the shared background service, and
            # --auto approves the edits the coder makes.
            argv = [
                "opencode", "run", "--format", "json", "--standalone", "--auto",
                prompt,
            ]
        else:
            argv = [
                "opencode", "run", "--format", "json", "--dir", project_root,
                "--dangerously-skip-permissions", prompt,
            ]
        if model:
            argv.extend(["-m", model])
        return argv

    def _subprocess_env(self) -> dict[str, str]:
        env = subprocess_env_for_coder()
        if not self.model.startswith("opencode-cli/snodo-cloud/"):
            return env

        # Importing the foundation capability registers the OAuth resolver with
        # ConfigManager; resolving here refreshes OAuth when needed and safely
        # falls back to a configured API key.
        from snodo.infrastructure import cloud_credentials  # noqa: F401

        manager = ConfigManager()
        model = self.model.removeprefix("opencode-cli/")
        credential = manager.get_key_for_model(model)
        if not credential:
            raise ValueError("Snodo Cloud credentials unavailable; run snodo cloud login")
        config = manager.load()
        cloud = config.get("cloud", {}) if isinstance(config, dict) else {}
        base_url = cloud.get("models_url") if isinstance(cloud, dict) else None
        base_url = base_url.strip() if isinstance(base_url, str) and base_url.strip() else DEFAULT_CLOUD_MODELS_URL
        model_id = model.removeprefix("snodo-cloud/")
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps({
            "provider": {
                "snodo-cloud": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Snodo Cloud",
                    "options": {"baseURL": base_url, "apiKey": credential},
                    "models": {model_id: {"name": model_id}},
                }
            }
        })
        return env

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
        if event.get("type") in {"tool", "tool_use"}:
            tool = part.get("tool", "tool")
            state = part.get("state") or {}
            title = state.get("title") or state.get("status") or ""
            return f"[{tool}] {title}".rstrip()
        if event.get("type") == "error":
            return self._error_text(event)
        return str(event.get("text", ""))

    @staticmethod
    def _error_text(event: dict) -> str:
        """Render an opencode 2.x error event as one readable line."""
        error = event.get("error") or {}
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or ""
        else:
            message = str(error)
        return f"[error] {message}".rstrip()

    def _format_output_tail_stdout(self, stdout: str) -> str:
        """Replace structured stream events with their human-readable text."""
        rendered = []
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                rendered.append(line)
                continue
            if not isinstance(event, dict):
                rendered.append(line)
                continue
            part = event.get("part") or {}
            if event.get("type") == "text":
                text = part.get("text")
                if text:
                    rendered.append(str(text))
            elif event.get("type") in {"tool", "tool_use"}:
                tool = part.get("tool", "tool")
                state = part.get("state") or {}
                title = state.get("title") or state.get("status") or ""
                rendered.append(f"[{tool}] {title}".rstrip())
            elif event.get("type") == "error":
                rendered.append(self._error_text(event))
            elif event.get("type") not in {"step_finish"}:
                text = event.get("text")
                if text:
                    rendered.append(str(text))
        return "\n".join(rendered)
