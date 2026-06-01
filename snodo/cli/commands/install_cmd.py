"""Install / uninstall CLI commands — project lifecycle management.

FILE: snodo/cli/commands/install_cmd.py (Task 7.14)
"""

import sys
from pathlib import Path

import yaml

from snodo.compiler.models import Protocol
from snodo.mcp.installer import (
    install, uninstall, uninstall_all,
    print_install_result, print_uninstall_result,
    purge_project_state, scan_orphans, remove_orphans,
    derive_project_name, get_claude_config_path,
)


def install_command(args) -> int:
    """Install this project's modes into Claude Desktop config."""
    protocol_path = getattr(args, "protocol", ".snodo/protocol.yml")
    protocol_file = Path(protocol_path)

    if not protocol_file.exists():
        print(f"Error: Protocol file not found: {protocol_file}", file=sys.stderr)
        print("  Run 'snodo init' first, or specify --protocol <path>", file=sys.stderr)
        return 1

    try:
        data = yaml.safe_load(protocol_file.read_text())
        protocol = Protocol(**data)
    except Exception as e:
        print(f"Error: Failed to load protocol: {e}", file=sys.stderr)
        return 1

    project_name = derive_project_name(str(protocol_file.resolve()))
    config_path = get_claude_config_path()

    try:
        added, updated = install(protocol, str(protocol_file.resolve()),
                                  project_name=project_name, config_path=config_path)
    except Exception as e:
        print(f"Error: Failed to install MCP entries: {e}", file=sys.stderr)
        return 1

    print_install_result(added, updated, config_path)
    _audit_global("install_registered", {
        "modes": len(protocol.modes),
        "config_path": str(config_path),
        "project_name": project_name,
    })
    return 0


def uninstall_command(args) -> int:
    """Remove this project's MCP entries from Claude Desktop config."""
    mode_filter = getattr(args, "mode", None)
    do_all = getattr(args, "all_entries", False)
    do_purge = getattr(args, "purge", False)
    do_orphans = getattr(args, "orphans", False)
    skip_prompt = getattr(args, "yes", False)

    if do_orphans:
        return _uninstall_orphans(skip_prompt)

    if do_all:
        return _uninstall_all_entries()

    config_path = get_claude_config_path()

    if do_purge:
        return _uninstall_purge(config_path, mode_filter, skip_prompt)

    # Standard uninstall: this project's entries only
    protocol_path = getattr(args, "protocol", ".snodo/protocol.yml")
    protocol_file = Path(protocol_path)
    if not protocol_file.exists():
        print(f"Error: Protocol file not found: {protocol_file}", file=sys.stderr)
        print("  Project may already be removed. Use --purge or --orphans.", file=sys.stderr)
        return 1

    try:
        data = yaml.safe_load(protocol_file.read_text())
        protocol = Protocol(**data)
    except Exception as e:
        print(f"Error: Failed to load protocol: {e}", file=sys.stderr)
        return 1

    project_name = derive_project_name(str(protocol_file.resolve()))
    try:
        removed = uninstall(protocol, str(protocol_file.resolve()),
                            project_name=project_name, mode_id=mode_filter,
                            config_path=config_path)
    except Exception as e:
        print(f"Error: Failed to uninstall: {e}", file=sys.stderr)
        return 1

    print_uninstall_result(removed, config_path)

    _audit_global("uninstall_completed", {
        "modes_removed": len(removed),
        "config_path": str(config_path),
        "project_name": project_name,
    })
    return 0


def _uninstall_purge(config_path, mode_filter, skip_prompt: bool) -> int:
    """Uninstall entries and purge project state."""
    project_root = str(Path.cwd())

    # Show what will be removed
    snodo_dir = Path(".snodo")
    entries_to_remove = []
    if snodo_dir.exists():
        entries_to_remove.append(str(snodo_dir.resolve()))

    if not skip_prompt:
        print("This will remove:")
        for p in entries_to_remove:
            print(f"  {p}")
        print("and all matching MCP entries from Claude Desktop config.")
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return 1

    # Remove MCP entries
    try:
        removed = uninstall_all(config_path=config_path)
    except Exception as e:
        print(f"Warning: MCP uninstall failed: {e}", file=sys.stderr)
        removed = []

    # Purge project state
    purge_result = purge_project_state(project_root)

    print("Purge complete.")
    if removed:
        print(f"  Removed {len(removed)} MCP entries from Claude config")
    if purge_result["purged_paths"]:
        print(f"  Removed: {', '.join(purge_result['purged_paths'])}")
    if purge_result["session_count"]:
        print(f"  Removed {purge_result['session_count']} session file(s)")

    _audit_global("uninstall_completed", {
        "modes_removed": len(removed),
        "purged_paths": purge_result["purged_paths"],
        "config_path": str(config_path),
        "project_name": Path(project_root).name,
    })
    return 0


def _uninstall_all_entries() -> int:
    """Remove ALL snodo-* entries from Claude config."""
    config_path = get_claude_config_path()
    try:
        removed = uninstall_all(config_path=config_path)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print_uninstall_result(removed, config_path)
    _audit_global("uninstall_completed", {
        "modes_removed": len(removed),
        "config_path": str(config_path),
        "all_entries": True,
    })
    return 0


def _uninstall_orphans(skip_prompt: bool) -> int:
    """Detect and optionally remove orphan MCP entries."""
    config_path = get_claude_config_path()
    try:
        orphans = scan_orphans(config_path=config_path)
    except Exception as e:
        print(f"Error scanning for orphans: {e}", file=sys.stderr)
        return 1

    if not orphans:
        print("No orphan MCP entries found.")
        return 0

    print(f"Found {len(orphans)} orphan MCP entry(ies):")
    for o in orphans:
        print(f"  {o['entry_name']} -> (missing) {o['missing_path']}")

    if not skip_prompt:
        answer = input("Remove these orphans? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return 0

    for o in orphans:
        _audit_global("orphan_detected", o)

    removed = remove_orphans(config_path=config_path)
    for name in removed:
        _audit_global("orphan_removed", {"entry_name": name})

    print(f"Removed {len(removed)} orphan(s) from Claude Desktop config.")
    return 0


def _audit_global(event_type: str, data: dict) -> None:
    """Write an audit event to the global ~/.snodo/audit.log."""
    try:
        from snodo.infrastructure.paths import resolve_home
        from snodo.infrastructure.audit import AuditLog
        log_path = str(resolve_home() / "audit.log")
        log = AuditLog(log_path)
        log.append_event(event_type, data)
    except Exception:
        pass  # Audit is best-effort for install/uninstall surface
