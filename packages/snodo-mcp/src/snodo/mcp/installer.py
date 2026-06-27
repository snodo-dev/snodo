"""Claude Desktop MCP Installer (Tasks 3.8-3.11).

FILE: snodo/mcp/installer.py

Generates MCP server entries from a Protocol definition and writes them
to the Claude Desktop configuration file. Preserves existing entries.

Naming: snodo-{project_name}-{mode_id}
Project name derived from directory containing .snodo/, sanitized.
"""

import hashlib
import json
import os
import platform
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from snodo.compiler.models import Protocol


def sanitize_project_name(name: str) -> str:
    """Sanitize a directory name for use in MCP server names.

    Lowercase, replace spaces/dashes/dots with underscores,
    strip non-alphanumeric characters, trim leading/trailing underscores.

    Args:
        name: Raw directory name

    Returns:
        Sanitized project name
    """
    name = name.lower()
    name = re.sub(r'[\s\-\.]+', '_', name)
    name = re.sub(r'[^a-z0-9_]', '', name)
    name = name.strip('_')
    return name or "project"


def derive_project_name(protocol_path: str) -> str:
    """Derive project name from the directory containing the protocol file.

    If the protocol lives at `<project>/.snodo/protocol.yml`, the project
    name is the name of `<project>`. Otherwise it's the parent directory
    of the protocol file.

    Args:
        protocol_path: Absolute path to protocol YAML file

    Returns:
        Sanitized project name
    """
    path = Path(protocol_path).resolve()
    if path.parent.name == ".snodo":
        project_dir = path.parent.parent
    else:
        project_dir = path.parent
    return sanitize_project_name(project_dir.name)


def get_claude_config_path() -> Path:
    """Return the Claude Desktop config path for the current OS.

    Returns:
        Path to claude_desktop_config.json

    Raises:
        RuntimeError: If the OS is not supported
    """
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Linux":
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        appdata = Path(os.environ.get("APPDATA", ""))
        if not appdata or not appdata.exists():
            raise RuntimeError("APPDATA environment variable not set")
        return appdata / "Claude" / "claude_desktop_config.json"
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def generate_mcp_entries(
    protocol: Protocol,
    protocol_path: str,
    project_name: str,
) -> Dict[str, dict]:
    """Generate MCP server entries for all modes in a protocol.

    Each mode gets a separate MCP server entry that runs
    `snodo serve --protocol <path> --mode <mode_id>`.

    Args:
        protocol: Protocol definition
        protocol_path: Absolute path to the protocol YAML file
        project_name: Sanitized project name

    Returns:
        Dict of server_name -> server config
    """
    entries = {}
    for mode in protocol.modes:
        server_name = f"snodo-{project_name}-{mode.mode_id}"
        entries[server_name] = {
            "command": "snodo",
            "args": ["serve", "--protocol", protocol_path, "--mode", mode.mode_id],
        }
    return entries


def read_claude_config(config_path: Path) -> dict:
    """Read existing Claude Desktop config, or return empty structure.

    Args:
        config_path: Path to claude_desktop_config.json

    Returns:
        Parsed config dict
    """
    if not config_path.exists():
        return {}
    try:
        text = config_path.read_text()
        if not text.strip():
            return {}
        return json.loads(text)
    except (json.JSONDecodeError, OSError):
        return {}


def write_claude_config(config_path: Path, config: dict) -> None:
    """Write Claude Desktop config, creating parent directories as needed.

    Args:
        config_path: Path to claude_desktop_config.json
        config: Config dict to write
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n")


def install(
    protocol: Protocol,
    protocol_path: str,
    project_name: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> Tuple[List[str], List[str]]:
    """Install MCP server entries into Claude Desktop config.

    Merges new entries with existing config. Existing non-snodo entries
    are preserved. Existing snodo entries for this protocol are updated.

    Args:
        protocol: Protocol definition
        protocol_path: Absolute path to the protocol YAML file
        project_name: Override project name. If None, derived from protocol_path.
        config_path: Override config path (for testing). If None, auto-detects.

    Returns:
        Tuple of (added server names, updated server names)
    """
    if config_path is None:
        config_path = get_claude_config_path()
    if project_name is None:
        project_name = derive_project_name(protocol_path)

    config = read_claude_config(config_path)

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    new_entries = generate_mcp_entries(protocol, protocol_path, project_name)

    added = []
    updated = []

    for name, entry in new_entries.items():
        if name in config["mcpServers"]:
            updated.append(name)
        else:
            added.append(name)
        config["mcpServers"][name] = entry

    write_claude_config(config_path, config)

    return added, updated


def uninstall(
    protocol: Protocol,
    protocol_path: str,
    project_name: Optional[str] = None,
    mode_id: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> List[str]:
    """Remove this project's MCP entries from Claude Desktop config.

    Identifies entries by matching generated entry names.
    If mode_id is specified, only removes that single mode entry.

    Args:
        protocol: Protocol definition
        protocol_path: Absolute path to the protocol YAML file
        project_name: Override project name. If None, derived from protocol_path.
        mode_id: If set, only remove this mode's entry
        config_path: Override config path (for testing). If None, auto-detects.

    Returns:
        List of removed server names
    """
    if config_path is None:
        config_path = get_claude_config_path()
    if project_name is None:
        project_name = derive_project_name(protocol_path)

    config = read_claude_config(config_path)
    servers = config.get("mcpServers", {})

    if not servers:
        return []

    # Determine which entries belong to this project
    expected_entries = generate_mcp_entries(protocol, protocol_path, project_name)

    if mode_id:
        target_name = f"snodo-{project_name}-{mode_id}"
        targets = {target_name} if target_name in expected_entries else set()
    else:
        targets = set(expected_entries.keys())

    removed = []
    for name in list(servers.keys()):
        if name in targets:
            del servers[name]
            removed.append(name)

    if removed:
        config["mcpServers"] = servers
        write_claude_config(config_path, config)

    return removed


def uninstall_all(config_path: Optional[Path] = None) -> List[str]:
    """Remove ALL snodo-managed entries from Claude Desktop config.

    Matches any entry whose name starts with 'snodo-'.

    Args:
        config_path: Override config path (for testing). If None, auto-detects.

    Returns:
        List of removed server names
    """
    if config_path is None:
        config_path = get_claude_config_path()

    config = read_claude_config(config_path)
    servers = config.get("mcpServers", {})

    if not servers:
        return []

    removed = []
    for name in list(servers.keys()):
        if name.startswith("snodo-"):
            del servers[name]
            removed.append(name)

    if removed:
        config["mcpServers"] = servers
        write_claude_config(config_path, config)

    return removed


def print_install_result(
    added: List[str],
    updated: List[str],
    config_path: Path,
) -> None:
    """Print user-friendly install result message.

    Args:
        added: List of newly added server names
        updated: List of updated server names
        config_path: Path where config was written
    """
    total = len(added) + len(updated)
    print(f"Installed {total} MCP server(s) into Claude Desktop config.")
    print(f"  Config: {config_path}")
    print()

    if added:
        print("  Added:")
        for name in added:
            print(f"    + {name}")

    if updated:
        print("  Updated:")
        for name in updated:
            print(f"    ~ {name}")

    print()
    print("Restart Claude Desktop to activate the new MCP servers.")


def print_uninstall_result(removed: List[str], config_path: Path) -> None:
    """Print user-friendly uninstall result message.

    Args:
        removed: List of removed server names
        config_path: Path where config was written
    """
    if not removed:
        print("No matching MCP servers found to remove.")
        return

    print(f"Removed {len(removed)} MCP server(s) from Claude Desktop config.")
    print(f"  Config: {config_path}")
    print()
    print("  Removed:")
    for name in removed:
        print(f"    - {name}")
    print()
    print("Restart Claude Desktop to apply changes.")


# ---------------------------------------------------------------------------
# Task 7.14 — purge + orphan helpers
# ---------------------------------------------------------------------------


def purge_project_state(project_root: str) -> Dict[str, Any]:
    """Remove a project's .snodo/ directory and associated sessions.

    Args:
        project_root: Absolute path to the project root directory.

    Returns:
        Dict with purged_paths and session_count describing what was removed.
    """
    root = Path(project_root).resolve()
    result: Dict[str, Any] = {"purged_paths": [], "session_count": 0}

    snodo_dir = root / ".snodo"
    if snodo_dir.exists() and snodo_dir.is_dir():
        shutil.rmtree(snodo_dir)
        result["purged_paths"].append(str(snodo_dir))

    # Clean matching sessions from global store
    project_id = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    try:
        from snodo.infrastructure.paths import resolve_home
        sessions_dir = resolve_home() / "sessions"
        if sessions_dir.exists():
            removed = 0
            for sf in sorted(sessions_dir.glob("*.json")):
                try:
                    data = json.loads(sf.read_text())
                    if data.get("project_id") == project_id:
                        sf.unlink()
                        removed += 1
                except (json.JSONDecodeError, OSError):
                    pass
            result["session_count"] = removed
    except Exception:
        pass

    return result


def scan_orphans(config_path: Optional[Path] = None) -> List[Dict[str, str]]:
    """Find snodo-* MCP entries whose protocol file no longer exists.

    Args:
        config_path: Override config path.  If None, auto-detects.

    Returns:
        List of dicts with entry_name and missing_path for each orphan.
    """
    if config_path is None:
        config_path = get_claude_config_path()
    config = read_claude_config(config_path)
    servers = config.get("mcpServers", {})
    orphans = []

    for name, entry in servers.items():
        if not name.startswith("snodo-"):
            continue
        args = entry.get("args", [])
        protocol_path = None
        # Find --protocol <path> in args
        try:
            for i, arg in enumerate(args):
                if arg == "--protocol" and i + 1 < len(args):
                    protocol_path = args[i + 1]
                    break
        except (TypeError, IndexError):
            pass
        if protocol_path and not Path(protocol_path).exists():
            orphans.append({
                "entry_name": name,
                "missing_path": protocol_path,
            })

    return orphans


def remove_orphans(config_path: Optional[Path] = None) -> List[str]:
    """Remove all orphan snodo-* entries from Claude Desktop config.

    Args:
        config_path: Override config path.

    Returns:
        List of removed entry names.
    """
    orphans = scan_orphans(config_path)
    if not orphans:
        return []

    cfg = config_path or get_claude_config_path()
    config = read_claude_config(cfg)
    servers = config.get("mcpServers", {})

    orphan_names = {o["entry_name"] for o in orphans}
    removed = []
    for name in list(servers.keys()):
        if name in orphan_names:
            del servers[name]
            removed.append(name)

    if removed:
        config["mcpServers"] = servers
        write_claude_config(cfg, config)

    return removed
