"""Init command - Initialize Snodo project structure.

FILE: snodo/cli/commands/init_cmd.py
"""

import sys
from pathlib import Path

import yaml

from snodo.cli.commands import PROTOCOL_TEMPLATES
from snodo.infrastructure.state import ProjectState, write_state


def _select_template(args) -> str:
    """Select protocol template from flag or interactive prompt.

    Returns:
        The selected template YAML string.
    """
    template_name = getattr(args, "template", None)

    if template_name:
        return PROTOCOL_TEMPLATES[template_name]

    # Interactive prompt
    print("Choose protocol template:")
    print("  1. solo  - Single developer (producer merges directly)")
    print("  2. team  - Team workflow (producer + reviewer + planner)")
    print("  3. 2+n   - Paper reference config (producer + reviewer)")
    choice = input("Select [1/2/3]: ").strip()

    if choice == "1":
        return PROTOCOL_TEMPLATES["solo"]
    elif choice == "2":
        return PROTOCOL_TEMPLATES["team"]
    elif choice == "3":
        return PROTOCOL_TEMPLATES["2+n"]
    else:
        print(f"Invalid choice: {choice!r}. Using team template.", file=sys.stderr)
        return PROTOCOL_TEMPLATES["team"]


def _pick_mode(args, modes: list, default_mode: str) -> str:
    """Interactive mode picker. Returns selected mode_id.

    Skips picker when:
    - --mode <m> passed (validated against available modes)
    - Not a TTY (piped / CI — keep default silently)
    """
    cli_mode = getattr(args, "mode", None)

    # Build mode_id -> info lookup
    mode_info: dict = {}
    for m in modes:
        mid = m.get("mode_id", "")
        name = m.get("name", mid)
        tools = m.get("tools", [])
        mode_info[mid] = {"name": name, "tools": tools}

    if cli_mode:
        if cli_mode not in mode_info:
            available = ", ".join(sorted(mode_info.keys()))
            print(
                f"Error: Mode '{cli_mode}' not in protocol. "
                f"Available: {available}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return cli_mode

    # Non-TTY → keep default silently (CI / piped)
    if not sys.stdin.isatty():
        return default_mode

    # Single-mode protocol → no choice needed
    if len(mode_info) <= 1:
        return default_mode

    # Interactive picker
    print()
    print("Select your starting mode:")
    mode_ids = sorted(mode_info.keys())
    default_idx = mode_ids.index(default_mode) if default_mode in mode_ids else 0

    for i, mid in enumerate(mode_ids):
        info = mode_info[mid]
        tools_str = ", ".join(info["tools"]) if info["tools"] else "none"
        marker = "  [default]" if mid == default_mode else ""
        print(f"  {i + 1}. {info['name']} ({mid})  tools: {tools_str}{marker}")

    try:
        choice = input(f"Select [1-{len(mode_ids)}, default={default_idx + 1}]: ").strip()
        if not choice:
            return default_mode
        idx = int(choice) - 1
        if 0 <= idx < len(mode_ids):
            return mode_ids[idx]
    except (ValueError, KeyboardInterrupt):
        pass

    print(f"Using default: {default_mode}")
    return default_mode


def init_command(args) -> int:
    """Initialize Snodo project structure."""
    from snodo.infrastructure.paths import resolve_project_root

    # Hard-block: refuse to initialise at or inside the home directory
    from pathlib import Path as _Path
    if _Path.cwd().resolve() == _Path.home():
        print(
            "Error: Cannot initialise a Snodo project at your home directory. "
            "Create a project directory first.",
            file=sys.stderr,
        )
        return 1

    # Git requirement: check .git exists in project root or any parent
    try:
        from git import Repo, InvalidGitRepositoryError
        Repo(str(Path.cwd()), search_parent_directories=True)
    except (InvalidGitRepositoryError, ImportError):
        print("Error: snodo requires a git repository. Run 'git init' first.",
              file=sys.stderr)
        return 1

    # Nested-init guard: refuse if a parent directory already has .snodo
    parent_root = resolve_project_root(str(Path.cwd().parent))
    if parent_root is not None and not args.force:
        print(
            f"Error: Already inside a Snodo project rooted at {parent_root}. "
            "Nested .snodo is not allowed. Use --force to override.",
            file=sys.stderr,
        )
        return 1

    snodo_dir = Path(".snodo")

    if snodo_dir.exists():
        if not args.force:
            print("Error: .snodo/ already exists. Use --force to overwrite.", file=sys.stderr)
            return 1
        print("Warning: Overwriting existing .snodo/ directory")

    try:
        snodo_dir.mkdir(exist_ok=True)
        print(f"Created {snodo_dir}/")
    except Exception as e:
        print(f"Error: Failed to create .snodo/ directory: {e}", file=sys.stderr)
        return 1

    # Select template
    template = _select_template(args)

    protocol_file = snodo_dir / "protocol.yml"
    try:
        protocol_file.write_text(template + "\n")
        print(f"Created {protocol_file}")
    except Exception as e:
        print(f"Error: Failed to create protocol.yml: {e}", file=sys.stderr)
        return 1

    # Write .snodo/state.json — set current_mode from protocol.initial_mode
    # Ctrl-C safe: this write IS the state; no subsequent prompt can kill it.
    try:
        data = yaml.safe_load(template)
        initial_mode = data.get("initial_mode", "")
        modes = data.get("modes", [])
        if initial_mode:
            write_state(".", ProjectState(current_mode=initial_mode))

            # Interactive mode picker (or --mode flag skips it)
            selected_mode = _pick_mode(args, modes, initial_mode)
            if selected_mode and selected_mode != initial_mode:
                write_state(".", ProjectState(current_mode=selected_mode))
            print(f"Active mode: {selected_mode or initial_mode}")
    except Exception as e:
        print(f"Warning: Could not write state.json: {e}", file=sys.stderr)

    # Generate RS256 keypair for HI-CTRL decision record signing
    try:
        from snodo.infrastructure.signing_keys import generate_keypair
        priv_path, pub_path = generate_keypair()
        print("RS256 keypair generated:")
        print(f"  Private: {priv_path}")
        print(f"  Public:  {pub_path}")
    except Exception as e:
        print(f"Warning: Could not generate signing keys: {e}", file=sys.stderr)

    # Check Docker availability for opencode adapter
    try:
        from snodo.coders.opencode_container import OpenCodeContainer
        oc = OpenCodeContainer()
        if oc.is_available():
            if not oc.image_exists():
                print()
                print("OpenCode adapter: Docker detected. Build the image with:")
                print("  docker build -t snodo-opencode:latest -f docker/Dockerfile.opencode .")
            else:
                print()
                print("OpenCode adapter: Docker + image ready.")
        else:
            print()
            print("OpenCode adapter: Docker not available. Install Docker to use opencode models.")
    except ImportError:
        pass  # docker-py not installed — skip check silently

    print("\nSnodo initialized successfully!")
    print("\nNext steps:")
    print("  1. Edit .snodo/protocol.yml to customize your protocol")
    print("  2. Run: snodo run \"your task description\"")

    return 0
