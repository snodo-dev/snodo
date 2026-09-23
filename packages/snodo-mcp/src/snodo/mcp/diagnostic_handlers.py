"""Read-only MCP adapters for the CLI project diagnostics."""

from __future__ import annotations

import contextlib
import io
import json
import os
import threading
from types import SimpleNamespace


_CLI_LOCK = threading.Lock()


def _run_cli(project_root: str, command, args: SimpleNamespace) -> dict:
    """Run a JSON CLI command in the server's project and return its payload."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with _CLI_LOCK:
        previous = os.getcwd()
        try:
            os.chdir(project_root)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                command(args)
        finally:
            os.chdir(previous)
    try:
        return json.loads(stdout.getvalue())
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"diagnostic command did not return JSON: {stdout.getvalue() or stderr.getvalue()}"
        ) from exc


class DiagnosticToolHandler:
    """Expose the CLI's machine contracts without adding an MCP shape."""

    def __init__(self, project_root: str) -> None:
        self.project_root = project_root

    def survey(self, arguments: dict) -> dict:
        from snodo.cli.commands.survey_cmd import survey_command

        return _run_cli(
            self.project_root,
            survey_command,
            SimpleNamespace(json=True, agent=True if arguments.get("agent", False) else False),
        )

    def intake(self, arguments: dict) -> dict:
        from snodo.cli.commands.intake_cmd import intake_command

        return _run_cli(
            self.project_root,
            intake_command,
            SimpleNamespace(
                validator=None,
                json=True,
                accept_all=False,
                reject_all=False,
                no_input=True,
            ),
        )

    def ready(self, arguments: dict) -> dict:
        from snodo.cli.commands.ready_cmd import ready_command

        return _run_cli(
            self.project_root,
            ready_command,
            SimpleNamespace(
                mode=arguments.get("mode"),
                protocol=arguments.get("protocol", ".snodo/protocol.yml"),
                json=True,
                record_audit=False,
            ),
        )

    def tool_handlers(self) -> dict:
        return {"survey": self.survey, "intake": self.intake, "ready": self.ready}
