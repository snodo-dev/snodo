"""Pin the `snodo run` and `snodo plan run` command surfaces to each other.

The two commands declare their CLI options independently, and that divergence
already cost two defects: an earlier plan-run crash, and `snodo plan run` never
growing `--coder` (0.7.0's headline backend selector) while `snodo run` did
(Fixes #186). These tests fail loudly when an option added to one command is
missing from the other, so the next drift is the next broken test rather than a
silent gap in the surface an orchestrator drives.
"""

import typer
from typer.core import TyperOption
from typer.main import get_group

from snodo.cli.commands.plan_cmd import app as plan_app
from snodo.cli.commands.run_cmd import register

# Options only a single path can offer, where parity is meaningless:
# - `snodo run` executes one task or one plan; `plan run` is the plan subcommand
#   (plans its own positional `name` and is reached via the plan app).
# - The retry spec options qualify `--retry`, which only `snodo run` offers;
#   `plan run` resumes a blocked task from its own spec file instead.
RUN_ONLY_OPTIONS = {
    frozenset({"--plan", "-p"}),
    frozenset({"--background", "-b"}),
    frozenset({"--resume"}),
    frozenset({"--retry"}),
    frozenset({"--append-spec"}),
    frozenset({"--replace-spec"}),
    frozenset({"--from-pr"}),
}


def _command_options(command) -> dict:
    """Map option-declaration set (frozenset of opts) -> (name, parameter type)."""
    return {
        frozenset(p.opts): (p.name, p.type)
        for p in command.params
        if isinstance(p, TyperOption)
    }


def _run_command():
    run_app = typer.Typer()
    register(run_app)
    return get_group(run_app).commands["run"]


def _plan_run_command():
    return get_group(plan_app).commands["run"]


def test_plan_run_exposes_coder():
    """`snodo plan run` accepts the 0.7.0 headline `--coder` backend selector.

    Without this, `snodo plan run smoke --coder litellm` fails with
    "No such option: --coder" — the surface an orchestrator drives cannot
    select a coder (Fixes #186).
    """
    plan_options = _command_options(_plan_run_command())
    assert frozenset({"--coder"}) in plan_options


def test_shared_execution_options_match_between_run_and_plan_run():
    """Every shared execution option declared on `snodo run` exists on `snodo plan run`.

    `plan run` walks the same runner as `snodo run --plan`, so the two surfaces
    must stay aligned. An option added to one and not the other is drift.
    """
    run_options = _command_options(_run_command())
    plan_options = _command_options(_plan_run_command())
    shared = set(run_options) - RUN_ONLY_OPTIONS
    missing = shared - set(plan_options)
    assert not missing, (
        "Execution options declared on `snodo run` are missing from "
        f"`snodo plan run`: {sorted(missing)}. Declare them in the shared "
        "declaration in snodo/cli/commands/run_cmd.py (`_execution_options`)."
    )


def test_plan_run_only_offers_shared_options():
    """`snodo plan run` must not grow options `snodo run` does not offer.

    The reverse drift is equally a bug: a flag that only plan run accepts will
    never be respected under `snodo run --plan <name>`, the identical execution.
    """
    run_options = _command_options(_run_command())
    plan_options = _command_options(_plan_run_command())
    unexpected = set(plan_options) - set(run_options)
    assert not unexpected, (
        f"`snodo plan run` offers options `snodo run` does not: {sorted(unexpected)}"
    )


def test_fresh_start_matches_mcp_retry_choice():
    """All retry surfaces opt in to fresh work, never discard it by default."""
    from snodo.cli.commands.run_cmd import RunArgs
    from snodo.mcp.tools import TOOL_REGISTRY

    schema = TOOL_REGISTRY["retry_job"]["inputSchema"]["properties"]["fresh_start"]
    assert schema["type"] == "boolean"
    assert "default false" in schema["description"]
    assert "previous attempt" in schema["description"]
    assert RunArgs().fresh_start is False
    for command in (_run_command(), _plan_run_command()):
        option = next(p for p in command.params if p.name == "fresh_start")
        assert option.opts == ["--fresh-start"]
        assert option.default is False
        assert "base revision" in option.help
        assert "previous" in option.help
        assert "preserve" in option.help


def test_plan_fresh_start_uses_the_shared_arguments_object(monkeypatch):
    """Fresh-start takes the same route as retain-worktree and no-isolation."""
    from typer.testing import CliRunner
    from snodo.cli.commands import plan_cmd
    from snodo.cli.commands.run_cmd import RunArgs

    received = []
    monkeypatch.setattr(plan_cmd, "_plan_run", lambda args: received.append(args) or 0)
    result = CliRunner().invoke(plan_app, [
        "run", "example", "--fresh-start", "--retain-worktree", "--no-isolation",
    ])
    assert result.exit_code == 0, result.output
    assert len(received) == 1
    args = received[0]
    assert isinstance(args, RunArgs)
    assert args.plan == "example"
    assert args.fresh_start is True
    assert args.retain_worktree is True
    assert args.no_isolation is True
