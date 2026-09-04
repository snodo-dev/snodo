"""Antigravity CLI (agy) coder adapter — shells `agy -p` on the host.

FILE: snodo/coders/agy_adapter.py

Runs Antigravity CLI directly on the host machine via SubprocessCoderAdapter:

    agy -p <prompt> --dangerously-skip-permissions --add-dir <workspace> [--model <model>]

Changes are read back from the working tree via git diff (agy writes files in place).
"""

from typing import List

from snodo.coders.subprocess_adapter import SubprocessCoderAdapter


class AGYAdapter(SubprocessCoderAdapter):
    """Coder adapter backed by Antigravity CLI (agy)."""

    coder_name: str = "agy"
    binary: str = "agy"
    model_prefix: str = "agy/"
    install_hint: str = (
        "Install agy: https://antigravity.google/docs/cli"
    )

    def _build_argv(self, prompt: str, project_root: str, model: str) -> List[str]:
        argv = [
            "agy",
            "-p",
            prompt,
            "--dangerously-skip-permissions",
            "--add-dir",
            project_root,
        ]
        if getattr(self, "timeout_seconds", None):
            argv.extend(["--print-timeout", f"{self.timeout_seconds}s"])
        if model:
            argv.extend(["--model", model])
        return argv

