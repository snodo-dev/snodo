"""Init command - Initialize Snodo project structure.

FILE: snodo/cli/commands/init_cmd.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.panel import Panel

from snodo.cli.commands import PROTOCOL_TEMPLATES, list_templates, template_display_name
from snodo.infrastructure.state import ProjectState, write_state


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def init(
        template: Optional[str] = typer.Option(
            None, "--template", "-t", help="Protocol template (e.g. solo, team, 2+n, intent, greenfield)",
        ),
        force: bool = typer.Option(
            False, "--force", "-f", help="Overwrite existing .snodo/ directory",
        ),
        mode: Optional[str] = typer.Option(
            None, "--mode", "-m", help="Starting mode (skips interactive picker)",
        ),
        project_id: Optional[str] = typer.Option(
            None, "--project-id", help="Override the project identity (caches with scope 'override')",
        ),
        force_keygen: bool = typer.Option(
            False, "--force-keygen", help="Force-regenerate RS256 signing keypair",
        ),
        yes: bool = typer.Option(
            False, "--yes", "-y", help="Skip the trusted-repository consent prompt",
        ),
        no_input: bool = typer.Option(
            False, "--no-input", help="Skip the trusted-repository consent prompt (alias for --yes)",
        ),
        test_command: Optional[str] = typer.Option(
            None, "--test-command", "-c", help="Test command for quality validation (e.g. 'pytest', 'npm test')",
        ),
    ):
        """Initialize Snodo project structure."""
        args = SimpleNamespace(
            template=template,
            force=force,
            mode=mode,
            project_id=project_id,
            force_keygen=force_keygen,
            yes=yes,
            no_input=no_input,
            test_command=test_command,
        )
        return init_command(args)




def _select_template(args) -> str:
    """Select protocol template from flag or interactive prompt.

    Returns:
        The selected template YAML string.
    """
    template_name = getattr(args, "template", None)

    if template_name:
        if template_name not in PROTOCOL_TEMPLATES:
            available = ", ".join(list_templates())
            print(
                f"Error: Unknown template '{template_name}'. "
                f"Available templates: {available}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return PROTOCOL_TEMPLATES[template_name]

    # Interactive prompt — generated from the registry so it can never omit a
    # shipped template.  Invalid selections re-prompt rather than substituting.
    names = list_templates()
    print("Choose protocol template:")
    for i, name in enumerate(names, start=1):
        print(f"  {i}. {name:<16} - {template_display_name(name)}")

    while True:
        try:
            choice = input(f"Select [1-{len(names)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("Invalid choice. Using default template 'team'.", file=sys.stderr)
            return PROTOCOL_TEMPLATES["team"]

        try:
            idx = int(choice) - 1
        except ValueError:
            idx = -1
        if 0 <= idx < len(names):
            return PROTOCOL_TEMPLATES[names[idx]]
        print(f"Invalid choice: {choice!r}. Choose 1-{len(names)}.", file=sys.stderr)


_DETECT_RULES = [
    ("package.json", "npm test"),
    ("pyproject.toml", "pytest"),
    ("setup.py", "pytest"),
    ("setup.cfg", "pytest"),
    ("Cargo.toml", "cargo test"),
    ("Makefile", "make test"),
    ("go.mod", "go test ./..."),
]


def _detect_test_command(project_dir: Path) -> Optional[str]:
    """Auto-detect test command from project marker files in project_dir."""
    for marker_file, command in _DETECT_RULES:
        if (project_dir / marker_file).exists():
            return command
    return None


def _configure_test_command(args, template_raw: str, project_dir: Path) -> str:
    """Infer, prompt for, or apply --test-command to protocol template YAML."""
    cli_cmd = getattr(args, "test_command", None)
    test_cmd = cli_cmd or _detect_test_command(project_dir)

    if (
        not test_cmd
        and sys.stdin.isatty()
        and not getattr(args, "yes", False)
        and not getattr(args, "no_input", False)
    ):
        try:
            user_input = input("Test command (e.g. 'pytest', 'npm test', or press Enter to skip): ").strip()
            if user_input:
                test_cmd = user_input
        except (EOFError, KeyboardInterrupt):
            pass

    if not test_cmd:
        return template_raw

    try:
        data = yaml.safe_load(template_raw)
        if isinstance(data, dict) and "validators" in data:
            updated = False
            for v in data["validators"]:
                if v.get("validator_type") == "quality" or v.get("validator_id") == "quality":
                    tooling = v.setdefault("tooling", {})
                    current_tc = tooling.get("test_command")
                    if not current_tc or current_tc == "REPLACE_ME":
                        tooling["test_command"] = test_cmd
                        updated = True
            if updated:
                return yaml.dump(data, sort_keys=False)
    except Exception:
        pass

    return template_raw


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


CONSENT_WARNING = (
    "snodo runs AI agents that execute code in this repository — "
    "including your test and build commands. Only continue if this "
    "repository is yours or you trust its contents."
)

_CONSENT_TITLE = "Trusted repository"
_CONSENT_FOOTER = "ADR 014 · SECURITY.md"

GITIGNORE_ENTRY = ".snodo/"


def _consent_console() -> Console:
    """Return a Rich console that degrades to plain output appropriately.

    ``force_terminal=False`` (the default) lets Rich auto-detect a TTY and
    honour NO_COLOR / non-TTY, so piped or CI output is plain.
    """
    return Console()


def _confirm_consent(args) -> bool:
    """Gate init behind explicit trusted-repository consent.

    Returns True if the user consents (or passed --yes/--no-input),
    False to abort. Never writes anything before this returns True.

    Non-TTY stdin without an explicit flag fails with guidance to use
    --yes, rather than hanging or silently defaulting.
    """
    if getattr(args, "yes", False) or getattr(args, "no_input", False):
        return True

    console = _consent_console()

    # Render the warning as a deliberate gate, not log output.
    panel = Panel(
        CONSENT_WARNING,
        title=_CONSENT_TITLE,
        border_style="yellow",
        expand=False,
        padding=(1, 2),
    )
    console.print(panel, highlight=False)
    console.print(_CONSENT_FOOTER, style="dim", justify="right")

    if not sys.stdin.isatty():
        print(
            "Error: refusing to continue — standard input is not a terminal. "
            "Re-run with --yes to acknowledge the trusted-repository model.",
            file=sys.stderr,
        )
        return False

    try:
        answer = console.input("Continue? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""

    if answer not in ("y", "yes"):
        print("Aborted: no files were created.", file=sys.stderr)
        return False
    return True


def _ensure_gitignore_entry(gitignore_path: Path) -> bool:
    """Ensure .gitignore contains exactly one '.snodo/' entry.

    Creates .gitignore if absent; appends the entry if missing; never
    duplicates an existing entry.

    Returns True if the entry was added, False if already present.
    """
    existing = ""
    if gitignore_path.exists():
        existing = gitignore_path.read_text()

    if any(line.strip() == GITIGNORE_ENTRY for line in existing.splitlines()):
        return False

    prefix = ""
    if existing and not existing.endswith("\n"):
        prefix = "\n"

    with gitignore_path.open("a") as f:
        f.write(prefix + GITIGNORE_ENTRY + "\n")
    return True


def _commit_gitignore(repo, gitignore_path: Path) -> bool:
    """Commit .gitignore so `git clean` cannot remove it (and then .snodo/).

    An untracked .gitignore is itself a `git clean -fd` target: the first
    clean removes it, the second removes the now-unignored .snodo/ — destroying
    the project id, session store and audit chain. Committing it makes the
    ignore durable.

    Only .gitignore is staged and committed; any other staged or unstaged
    changes are left untouched. Returns True on success, False when the commit
    cannot be made (no identity, unborn branch, hook failure, ...), in which
    case the caller warns rather than failing init.
    """
    try:
        if gitignore_path.name in repo.git.ls_files().splitlines():
            return True  # already tracked — nothing to do

        repo.git.add(str(gitignore_path))
        repo.git.commit("-m", "chore: ignore .snodo/", "--", str(gitignore_path))
        return True
    except Exception:
        # Best-effort: leave the working tree as it was (unstage our add).
        try:
            repo.git.reset("--", str(gitignore_path))
        except Exception:
            pass
        return False


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

    # Resolve and verify the template BEFORE any write — a failed init must
    # leave the directory as it found it.  This also surfaces an unknown
    # --template (or a broken shipped template) before touching disk.
    template = _select_template(args)
    try:
        from snodo.compiler.models import Protocol
        from snodo.compiler.verifier import verify_protocol, ProtocolWellFormednessError
        template_data = yaml.safe_load(template)
        protocol = Protocol(**template_data)
        result = verify_protocol(protocol)
        if not result.passed:
            raise ProtocolWellFormednessError(result.errors)
    except Exception as e:
        print(f"Error: Template is not a valid protocol: {e}", file=sys.stderr)
        return 1

    # Trusted-repository consent gate — must run before any file write.
    if not _confirm_consent(args):
        return 1

    template = _configure_test_command(args, template, Path.cwd())

    try:
        snodo_dir.mkdir(exist_ok=True)
        print(f"Created {snodo_dir}/")
    except Exception as e:
        print(f"Error: Failed to create .snodo/ directory: {e}", file=sys.stderr)
        return 1

    # .snodo/ hygiene: keep the protocol state out of git by default.
    try:
        if _ensure_gitignore_entry(Path(".gitignore")):
            print("Added .snodo/ to .gitignore")
    except Exception as e:
        print(f"Warning: Could not update .gitignore: {e}", file=sys.stderr)

    # Commit .gitignore so `git clean -fd` cannot remove it and then .snodo/.
    # An untracked .gitignore is itself a clean target: the first clean removes
    # it, the second removes the now-unignored .snodo/ (project id, sessions,
    # audit chain). Committing makes the ignore durable.
    try:
        from git import Repo
        repo = Repo(str(Path.cwd()), search_parent_directories=True)
        if not _commit_gitignore(repo, Path(".gitignore")):
            print(
                "Warning: Could not commit .gitignore — .snodo/ is ignored but "
                "the ignore is not yet durable. Commit .gitignore (or run "
                "'git add .gitignore && git commit') so 'git clean -fd' cannot "
                "remove .snodo/.",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"Warning: Could not commit .gitignore: {e}", file=sys.stderr)
    # Resolve and cache project identity
    try:
        from snodo.config import ConfigManager
        config_data = {}
        try:
            config_data = ConfigManager().load()
        except Exception:
            pass
        
        cfg_project_id = config_data.get("project.id") or config_data.get("project_id")
        override_id = getattr(args, "project_id", None) or cfg_project_id
        
        if override_id:
            pid = override_id
            scope = "override"
        else:
            from snodo.project import resolve_project_id
            pid, scope = resolve_project_id(".")
            
        from snodo.project import cache_project_id
        cache_project_id(".", pid, scope)
        print(f"Project ID:  {pid} ({scope})")
    except Exception as e:
        print(f"Warning: Could not initialize project identity: {e}", file=sys.stderr)

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
        from snodo.infrastructure.signing_keys import generate_keypair, keypair_exists
        force_keygen = getattr(args, "force_keygen", False)
        keys_existed = keypair_exists() and not force_keygen
        priv_path, pub_path = generate_keypair(force=force_keygen)
        if keys_existed:
            print("Using existing RS256 keypair:")
        else:
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
