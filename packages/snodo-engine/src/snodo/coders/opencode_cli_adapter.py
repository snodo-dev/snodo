"""OpenCode CLI coder adapter — shells `opencode run` on the host.

FILE: snodo/coders/opencode_cli_adapter.py

Runs opencode directly on the host machine via SubprocessCoderAdapter.
"""

from typing import List

from snodo.coders.subprocess_adapter import SubprocessCoderAdapter


class OpenCodeCLIAdapter(SubprocessCoderAdapter):
    """Coder adapter backed by the host ``opencode run`` CLI."""

    coder_name: str = "opencode-cli"
    binary: str = "opencode"
    model_prefix: str = "opencode-cli/"
    install_hint: str = (
        "Install opencode: curl -fsSL https://opencode.ai/install | bash"
    )

    def _build_argv(self, prompt: str, project_root: str, model: str) -> List[str]:
        argv = [
            "opencode",
            "run",
            "--dir",
            project_root,
            "--dangerously-skip-permissions",
            prompt,
        ]
        if model:
            argv.extend(["-m", model])
        return argv
