"""Project identity resolution, normalization, and caching (ADR 012).

FILE: snodo/project.py
"""

import json
import logging
import os
import re
import subprocess
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


def get_project_id(project_root: str) -> tuple[str, str]:
    """Retrieve the project ID and scope, following the repository's state.

    Read-only: establishing an identity for labelling must not change the
    filesystem. A cached ``local:`` identity is re-resolved on every call so
    that a repository which gains a remote promotes to the remote id. A cached
    ``remote`` or ``override`` identity is stable and returned as-is:
    promotion is one-way, and an operator-supplied override is never
    second-guessed.

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
            cached_scope = data.get("scope", "local")
        except Exception as e:
            _logger.debug("Failed to read cached project ID from %s: %s", project_json_path, e)

    # A remote or override identity is stable: return it without re-resolving.
    # Promotion is one-way, so a remote never demotes, and an override is the
    # operator's explicit assertion.
    if cached_pid and cached_scope in ("remote", "override"):
        return (cached_pid, cached_scope)

    # Resolve against the repository's actual state. A local (or missing)
    # identity must follow the repo: if a remote now exists, promote.
    pid, scope = resolve_project_id(project_root)

    # A repository that still has no remote keeps its cached local id — the
    # UUID is stable across runs, not re-minted on every call.
    if cached_pid and scope == "local":
        pid = cached_pid

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
