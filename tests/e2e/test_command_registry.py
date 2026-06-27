"""Wave 4 safety net: command-registry smoke tests.

FILE: tests/e2e/test_command_registry.py

Enumerates EVERY command and subcommand in the snodo CLI and asserts:
1. Each responds to --help with exit code 0.
2. snodo --help lists all top-level commands/groups.
3. Each group --help lists its subcommands.

This test MUST FAIL if any listed command becomes unreachable after
the auto-discovery refactor (Wave 4). It pins the exact CLI surface of
the CURRENT (unrefactored) implementation.

Generated inventory (printed as test output):
  Top-level commands:   version(--version flag), init, run, serve, dashboard,
                        authorize, recon, models, install, uninstall, meta, logs
  Groups + subcommands:
    plan:    list, status, create
    job:     list, status, logs, wait, cancel, archive, prune, unarchive, retry
    agent:   list, memory, reset, rotate
    config:  show, add, remove, test, set, get
    session: list, show, delete, prune
    mode:    show, change
    sandbox: build, status
    cloud:   connect, disconnect, status, sync
    task:    list, abandon, prune
"""

import re

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mGKH]", "", text)


def _help_ok(snodo_cli, *cmd_args) -> tuple[int, str]:
    """Run `snodo <cmd_args> --help` and return (returncode, clean_stdout)."""
    result = snodo_cli(list(cmd_args) + ["--help"])
    return result.returncode, _strip_ansi(result.stdout)


# ---------------------------------------------------------------------------
# Inventory — single source of truth for what commands exist
# ---------------------------------------------------------------------------

#: Top-level commands/groups that must appear in `snodo --help`
TOP_LEVEL_NAMES = [
    "init", "run", "serve", "dashboard", "authorize",
    "recon", "models", "install", "uninstall", "meta", "logs",
    "plan", "job", "agent", "config", "session", "mode", "sandbox",
    "cloud", "task",
]

#: Subcommands per group
GROUP_SUBCOMMANDS: dict[str, list[str]] = {
    "plan":    ["list", "status", "create"],
    "job":     ["list", "status", "logs", "wait", "cancel",
                "archive", "prune", "unarchive", "retry"],
    "agent":   ["list", "memory", "reset", "rotate"],
    "config":  ["show", "add", "remove", "test", "set", "get"],
    "session": ["list", "show", "delete", "prune"],
    "mode":    ["show", "change"],
    "sandbox": ["build", "status"],
    "cloud":   ["connect", "disconnect", "status", "sync"],
    "task":    ["list", "abandon", "prune"],
}

#: Standalone top-level commands (no sub-app) that get --help directly
STANDALONE_COMMANDS = [
    "init", "run", "serve", "dashboard", "authorize",
    "recon", "models", "install", "uninstall", "meta", "logs",
]


# ---------------------------------------------------------------------------
# Part A-1: Every standalone command responds to --help with exit 0
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.parametrize("cmd", STANDALONE_COMMANDS, ids=STANDALONE_COMMANDS)
def test_standalone_command_help(snodo_cli, cmd):
    """Each standalone top-level command responds to --help → exit 0."""
    rc, out = _help_ok(snodo_cli, cmd)
    assert rc == 0, (
        f"`snodo {cmd} --help` returned exit {rc}.\n"
        "This command may be unregistered or broken."
    )
    # Sanity: output must have some content
    assert len(out.strip()) > 0, f"`snodo {cmd} --help` produced empty output"


@pytest.mark.e2e
def test_version_flag(snodo_cli):
    """--version flag is reachable and prints a semver string."""
    result = snodo_cli(["--version"])
    assert result.returncode == 0
    out = result.stdout.strip()
    assert out.startswith("snodo "), f"Unexpected --version output: {out!r}"


# ---------------------------------------------------------------------------
# Part A-1: Every group --help → exit 0 and every subcommand --help → exit 0
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.parametrize("group", list(GROUP_SUBCOMMANDS.keys()), ids=list(GROUP_SUBCOMMANDS.keys()))
def test_group_help(snodo_cli, group):
    """Each command group responds to --help → exit 0."""
    rc, out = _help_ok(snodo_cli, group)
    assert rc == 0, (
        f"`snodo {group} --help` returned exit {rc}.\n"
        "The whole group may be unregistered."
    )
    assert len(out.strip()) > 0


@pytest.mark.e2e
@pytest.mark.parametrize(
    "group,sub",
    [
        (group, sub)
        for group, subs in GROUP_SUBCOMMANDS.items()
        for sub in subs
    ],
    ids=[
        f"{group}.{sub}"
        for group, subs in GROUP_SUBCOMMANDS.items()
        for sub in subs
    ],
)
def test_subcommand_help(snodo_cli, group, sub):
    """Every subcommand responds to --help → exit 0."""
    rc, out = _help_ok(snodo_cli, group, sub)
    assert rc == 0, (
        f"`snodo {group} {sub} --help` returned exit {rc}.\n"
        f"Subcommand '{sub}' under '{group}' may be unregistered or broken."
    )
    assert len(out.strip()) > 0, (
        f"`snodo {group} {sub} --help` produced empty output"
    )


# ---------------------------------------------------------------------------
# Part A-2: Snapshot — snodo --help lists ALL top-level names
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_top_level_help_snapshot(snodo_cli):
    """snodo --help must list every top-level command/group name."""
    result = snodo_cli(["--help"])
    assert result.returncode == 0
    out = _strip_ansi(result.stdout)

    missing = [name for name in TOP_LEVEL_NAMES if name not in out]
    assert not missing, (
        f"snodo --help is missing these commands/groups: {missing}\n"
        f"Full output:\n{out}"
    )


@pytest.mark.e2e
@pytest.mark.parametrize("group", list(GROUP_SUBCOMMANDS.keys()), ids=list(GROUP_SUBCOMMANDS.keys()))
def test_group_help_snapshot(snodo_cli, group):
    """Each group's --help must list all its subcommands."""
    _, out = _help_ok(snodo_cli, group)
    subs = GROUP_SUBCOMMANDS[group]
    missing = [s for s in subs if s not in out]
    assert not missing, (
        f"`snodo {group} --help` is missing subcommands: {missing}\n"
        f"Full output:\n{out}"
    )


# ---------------------------------------------------------------------------
# Part A-3: Emit documented inventory as test output
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_emit_command_inventory(snodo_cli, capsys):
    """Print the full CLI surface inventory so it shows up in pytest -s output."""
    total_standalone = len(STANDALONE_COMMANDS)
    total_subcommands = sum(len(v) for v in GROUP_SUBCOMMANDS.values())
    total_groups = len(GROUP_SUBCOMMANDS)
    total = total_standalone + 1 + total_groups + total_subcommands  # +1 for --version

    lines = [
        "",
        "=" * 60,
        "CLI COMMAND INVENTORY (Wave 4 safety net)",
        "=" * 60,
        "  --version flag (top-level)",
    ]
    for cmd in STANDALONE_COMMANDS:
        lines.append(f"  {cmd}")
    for group, subs in GROUP_SUBCOMMANDS.items():
        lines.append(f"  {group}:")
        for sub in subs:
            lines.append(f"    {group} {sub}")
    lines += [
        "-" * 60,
        f"  {total_standalone} standalone top-level commands",
        f"  {total_groups} command groups",
        f"  {total_subcommands} subcommands",
        f"  {total} total smoke-covered command paths",
        "=" * 60,
    ]
    print("\n".join(lines))

    # Formal assertion — counts must match what we declared
    assert total_standalone == len(STANDALONE_COMMANDS)
    assert total_subcommands == sum(len(v) for v in GROUP_SUBCOMMANDS.values())
