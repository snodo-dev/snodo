"""Project identity resolution, normalization, and caching (ADR 012).

FILE: snodo/project.py
"""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)


def normalize_remote_url(url: str) -> str:
    """Collapses remote URL to host/org/repo format."""
    # 1. Lowercase
    s = url.strip().lower()

    # 2. Strip scheme (https://, ssh://, git://, etc.)
    s = re.sub(r'^[a-z]+://', '', s)

    # 3. Strip userinfo/credentials (e.g. git@ or user:pass@)
    s = re.sub(r'^[^@]+@', '', s)

    # 4. Strip default ports (:22 and :443) before host/path separator
    # E.g. github.com:22/org/repo -> github.com/org/repo
    s = re.sub(r':(22|443)(/|$)', r'\2', s)

    # 5. Handle scp-like host:path separator (replace ':' with '/')
    # E.g. github.com:org/repo -> github.com/org/repo
    # Negative lookahead ensures we don't match other port forms like github.com:8080/org/repo
    s = re.sub(r'^([a-z0-9.-]+):(?![0-9]+/)(.*)', r'\1/\2', s)

    # 6. Strip trailing .git and slashes
    s = s.rstrip('/')
    s = re.sub(r'\.git$', '', s)
    s = s.rstrip('/')

    return s


def resolve_project_id(project_root: str) -> tuple[str, str]:
    """Resolves project identity by checking git remotes or generating a local UUID."""
    try:
        # Try origin remote first
        res = subprocess.run(  # noqa: S603 - argv list, no shell; fully controlled flags; project_root passed as a single -C element
            ["git",  # noqa: S607 - git resolved from PATH by design; operator's host tool, trusted repo per ADR 014
             "-C", project_root, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False
        )
        url = res.stdout.strip()
        if res.returncode == 0 and url:
            return (normalize_remote_url(url), "remote")

        # Fallback to the first remote listed
        res_list = subprocess.run(  # noqa: S603 - argv list, no shell; fully controlled flags
            ["git",  # noqa: S607 - git resolved from PATH by design
             "-C", project_root, "remote"],
            capture_output=True,
            text=True,
            check=False
        )
        if res_list.returncode == 0:
            remotes = [r.strip() for r in res_list.stdout.splitlines() if r.strip()]
            if remotes:
                res_url = subprocess.run(  # noqa: S603 - argv list, no shell; remote name passed as a single element, never interpreted
                    ["git",  # noqa: S607 - git resolved from PATH by design
                     "-C", project_root, "remote", "get-url", remotes[0]],
                    capture_output=True,
                    text=True,
                    check=False
                )
                url = res_url.stdout.strip()
                if res_url.returncode == 0 and url:
                    return (normalize_remote_url(url), "remote")
    except Exception as e:
        _logger.debug("Failed to resolve project remote URL: %s", e)

    return ("local:" + uuid.uuid4().hex, "local")


def _is_system_root_or_temp(path: Path | str) -> bool:
    """Check if path is a shared system root, home directory, or process temp root.

    Shared roots like ``/``, ``~``, ``/tmp``, ``/var/tmp``, macOS ``/var/folders/.../T``,
    and any runtime temp root ($TMPDIR, tempfile.gettempdir()) must never have
    a ``.snodo`` directory created directly under them, as doing so pollutes the
    machine and intercepts project resolution for all processes.
    """
    try:
        resolved = Path(path).resolve()
    except Exception:
        return False

    if resolved == Path("/").resolve() or resolved == Path.home().resolve():
        return True

    sys_temp_paths = {
        Path("/tmp").resolve(),
        Path("/var/tmp").resolve(),
        Path("/private/tmp").resolve(),
        Path("/private/var/tmp").resolve(),
    }
    if resolved in sys_temp_paths:
        return True

    live_tmpdir = os.environ.get("TMPDIR")
    if live_tmpdir:
        try:
            if resolved == Path(live_tmpdir).resolve():
                return True
        except Exception:  # noqa: S110
            pass

    try:
        if resolved == Path(tempfile.gettempdir()).resolve():
            return True
    except Exception:  # noqa: S110
        pass

    p_str = str(resolved).replace("\\", "/")
    if resolved.name == "T" and ("var/folders" in p_str or "private/var/folders" in p_str):
        return True

    return False


def scope_for_project_id(project_id: str) -> str:
    """Derive the scope from a project id, so the two cannot disagree.

    The scope is a property of the id, not a separate fact that can drift
    from it. A ``local:`` id is a leaf — deliberately unreconcilable, and a
    consumer must never merge it with anything. Any other id is globally
    identifiable: a normalized remote URL, or an operator-supplied override.
    Deriving scope here, from the id itself, is what lets a repository that
    gains a remote promote its identity without a stale cached scope holding
    it back.
    """
    if project_id.startswith("local:"):
        return "local"
    return "remote"


_announced_identity_changes: set[tuple[str, str, str]] = set()
_checked_remote_roots: set[str] = set()


def get_project_id(project_root: str) -> tuple[str, str]:
    """Retrieve the project ID and scope, following the repository's state.

    Read-only: establishing an identity for labelling must not change the
    filesystem. A cached ``local:`` identity promotes to a remote id once a
    remote is added. A cached ``remote`` or ``override`` identity is stable
    and returned as-is: promotion is one-way, and identity does not drift
    or split when a remote is repointed.

    If a git remote is repointed, snodo reports the divergence on stderr once
    per process while returning the stable cached identity. The operator may
    adopt the new remote by re-initialising (``snodo init``).

    Persisting the identity is a separate, explicit step (``cache_project_id``)
    performed by callers that establish identity (``snodo init``) — never as a
    side effect of reading it.
    """
    project_json_path = Path(project_root) / ".snodo" / "project.json"
    cached_pid: Optional[str] = None
    cached_scope: Optional[str] = None
    if project_json_path.exists():
        try:
            with open(project_json_path) as f:
                data = json.load(f)
            cached_pid = data.get("project.id") or data.get("id")
            cached_scope = data.get("scope")
            if cached_scope != "override" and cached_pid:
                cached_scope = scope_for_project_id(cached_pid)
            elif not cached_scope:
                cached_scope = "local"
        except Exception as e:
            _logger.debug("Failed to read cached project ID from %s: %s", project_json_path, e)

    # An override identity is an explicit operator assertion: return as-is.
    if cached_pid and cached_scope == "override":
        return (cached_pid, cached_scope)

    # A remote identity is stable after promotion. If this root hasn't been
    # checked in this process, verify the git remote once to warn if repointed.
    if cached_pid and cached_scope == "remote":
        try:
            resolved_root = str(Path(project_root).resolve())
        except Exception:
            resolved_root = str(project_root)

        if resolved_root not in _checked_remote_roots:
            _checked_remote_roots.add(resolved_root)
            resolved_pid, resolved_scope = resolve_project_id(project_root)
            if resolved_scope == "remote" and resolved_pid != cached_pid:
                key = (resolved_root, cached_pid, resolved_pid)
                if key not in _announced_identity_changes:
                    _announced_identity_changes.add(key)
                    print(
                        f"Warning: git remote changed from {cached_pid} to {resolved_pid}. "
                        f"Project identity is unchanged ({cached_pid}). "
                        "Run 'snodo init' to adopt the new remote.",
                        file=sys.stderr,
                    )
        return (cached_pid, cached_scope)

    # Resolve against the repository's actual state for local or missing identities.
    pid, scope = resolve_project_id(project_root)

    # A repository that still has no remote keeps its cached local id.
    if cached_pid and scope == "local":
        return (cached_pid, "local")

    # Local identity promoting to remote:
    if cached_pid and scope == "remote" and cached_pid != pid:
        try:
            resolved_root = str(Path(project_root).resolve())
        except Exception:
            resolved_root = str(project_root)
        key = (resolved_root, cached_pid, pid)
        if key not in _announced_identity_changes:
            _announced_identity_changes.add(key)
            print(
                f"Project identity promoted from {cached_pid} to {pid}",
                file=sys.stderr,
            )

    return (pid, scope)


def cache_project_id(project_root: str, project_id: str, scope: str) -> None:
    """Caches the project ID and scope to .snodo/project.json."""
    root_path = Path(project_root)
    if _is_system_root_or_temp(root_path):
        _logger.warning("Refusing to cache project ID in system root: %s", project_root)
        return

    snodo_dir = root_path / ".snodo"
    project_json_path = snodo_dir / "project.json"
    try:
        if not snodo_dir.exists():
            has_git = (root_path / ".git").exists()
            has_protocol = (root_path / "protocol.yml").exists() or (snodo_dir / "protocol.yml").exists()
            if not has_git and not has_protocol:
                _logger.warning("Caching project ID created .snodo in non-project directory: %s", project_root)
        snodo_dir.mkdir(parents=True, exist_ok=True)
        data = {}
        if project_json_path.exists():
            with open(project_json_path) as f:
                data = json.load(f)
        data["id"] = project_id
        data["project.id"] = project_id
        data["scope"] = scope
        with open(project_json_path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        _logger.warning("Failed to cache project ID to %s: %s", project_json_path, e)
