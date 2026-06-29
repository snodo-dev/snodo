"""Per-instance SWE-bench workspace management.

All three arms operate inside a cloned workspace at the instance's
base_commit.  Workspace setup uses shallow fetch (``--depth 1``) of just
the base_commit, cached per (repo, base_commit), so the full matrix does
not re-download the same repo per arm/trial.

Fall back to a full clone if the shallow fetch-by-SHA fails.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Per-process cache: (repo, base_commit) -> cache dir path
# ---------------------------------------------------------------------------

_CACHE_DIR: Optional[Path] = None
_CACHE: Dict[Tuple[str, str], Path] = {}


def _ensure_cache_dir() -> Path:
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = Path(tempfile.mkdtemp(prefix="swe-cache-"))
    return _CACHE_DIR


def cleanup_cache() -> None:
    """Remove the entire per-process cache directory."""
    global _CACHE_DIR, _CACHE
    if _CACHE_DIR is not None and _CACHE_DIR.exists():
        shutil.rmtree(_CACHE_DIR, ignore_errors=True)
    _CACHE_DIR = None
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class Workspace:
    """Cloned repository workspace at a specific base_commit."""

    def __init__(self, path: Path, base_commit: str):
        self.path = path
        self.base_commit = base_commit

    def __repr__(self) -> str:
        return f"Workspace({self.path}, {self.base_commit})"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_instance_workspace(instance: dict) -> Workspace:
    """Set up a workspace at *instance*'s repo + base_commit.

    Uses a shallow fetch (``--depth 1``) cached per (repo, base_commit).
    If the shallow fetch fails, falls back to a full clone of the tip.

    Each call returns an independent, writable copy of the cached bare
    repo checkout, so arms do not interfere.

    Args:
        instance: Task dict with ``repo`` (e.g. ``django/django``) and
                  ``base_commit``.

    Returns:
        Workspace pointing to the checked-out repo at base_commit.

    Raises:
        RuntimeError: If clone, fetch, or checkout all fail.
    """
    repo = instance.get("repo", "")
    base_commit = instance.get("base_commit", "")

    if not repo or not base_commit:
        raise ValueError(
            f"Instance missing repo or base_commit: {instance.get('instance_id', '?')}"
        )

    clone_url = f"https://github.com/{repo}.git"
    dest = Path(tempfile.mkdtemp(prefix=f"swe-{instance.get('instance_id', '?')}-"))

    # 1. Obtain a cached shallow checkout
    try:
        cache_dir = _get_cached(repo, base_commit, clone_url)
    except Exception as exc:
        # Fall back to full clone
        try:
            _full_clone(clone_url, dest, base_commit)
            return Workspace(path=dest, base_commit=base_commit)
        except Exception as fallback_exc:
            shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError(
                f"Workspace setup failed for {repo} @ {base_commit}: "
                f"shallow: {exc}; full clone: {fallback_exc}"
            )

    # 2. Copy cache -> workspace (fast local copy, one per arm/trial)
    try:
        shutil.copytree(cache_dir, dest, symlinks=False)
        return Workspace(path=dest, base_commit=base_commit)
    except Exception as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"Failed to copy cached workspace to {dest}: {exc}")


def _get_cached(repo: str, base_commit: str, clone_url: str) -> Path:
    """Return a cached shallow checkout of (repo, base_commit), fetching if needed."""
    key = (repo, base_commit)
    if key not in _CACHE:
        cache_dir = _ensure_cache_dir() / f"{repo.replace('/', '__')}__{base_commit}"
        _do_shallow_fetch(cache_dir, clone_url, base_commit)
        _CACHE[key] = cache_dir
    return _CACHE[key]


def _do_shallow_fetch(dest: Path, clone_url: str, base_commit: str) -> None:
    """Shallow fetch of a single commit into an empty git repo."""
    _run(["git", "init"], cwd=dest)  # will create dest
    _run(["git", "remote", "add", "origin", clone_url], cwd=dest)
    _run(
        ["git", "fetch", "--depth", "1", "origin", base_commit],
        cwd=dest,
        timeout=120,
    )
    _run(["git", "checkout", "FETCH_HEAD"], cwd=dest, timeout=30)
    # Tag the base_commit so extract_patch's diff --cached <base_commit> works
    _run(["git", "tag", "-f", base_commit, "FETCH_HEAD"], cwd=dest, timeout=10)


def _full_clone(clone_url: str, dest: Path, base_commit: str) -> None:
    """Full clone followed by checkout of base_commit (fallback path)."""
    _run(["git", "clone", clone_url, str(dest)], timeout=180)
    _run(["git", "checkout", base_commit], cwd=dest, timeout=30)


# ---------------------------------------------------------------------------
# Patch extraction
# ---------------------------------------------------------------------------


def extract_patch(workspace: Workspace) -> str:
    """Extract a unified diff of all changes since base_commit.

    Runs ``git add -A`` then ``git diff --cached <base_commit>`` so the diff
    is taken against the instance base_commit.  This captures changes whether
    the arm left them uncommitted (opencode, arms a/b) OR committed them
    (snodo's executor commits — arm c); a plain ``git diff --cached`` would
    be EMPTY for committed changes.

    Excludes snodo's own metadata (``.snodo/`` — protocol, wave.json, audit,
    state, worktree bookkeeping) so the model_patch is ONLY the code change.
    Otherwise arm-c's diff is polluted with snodo files that don't fix the bug
    and can fail to apply against the target repo.

    Returns:
        Unified diff string, or empty string if no changes.
    """
    _run(["git", "add", "-A"], cwd=workspace.path)
    result = _run(
        [
            "git", "diff", "--cached", workspace.base_commit,
            "--", ".", ":(exclude).snodo", ":(exclude).snodo/**",
        ],
        cwd=workspace.path,
        capture_output=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


def teardown(workspace: Workspace) -> None:
    """Remove the workspace directory (per-arm copy)."""
    if workspace.path.exists():
        shutil.rmtree(workspace.path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


def _run(
    args: list[str],
    cwd: Optional[Path] = None,
    timeout: int = 60,
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    """Run a subprocess and return the result.

    Raises CalledProcessError on non-zero exit.
    """
    kwargs = {}
    if capture_output:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        check=True,
        **kwargs,
    )
    return result


# ---------------------------------------------------------------------------
# Mock workspace for testing
# ---------------------------------------------------------------------------


class MockWorkspace:
    """Mock workspace that returns a predetermined patch."""

    def __init__(self, patch: str = "mock-diff\n+test\n"):
        self._patch = patch

    def setup(self, instance: dict) -> Workspace:
        """Create a real temp dir + git repo for the arm to run in."""
        dest = Path(tempfile.mkdtemp(prefix="mock-workspace-"))
        _run(["git", "init"], cwd=dest)
        _run(["git", "config", "user.email", "test@test.com"], cwd=dest)
        _run(["git", "config", "user.name", "Test"], cwd=dest)
        (dest / "README.md").write_text("mock workspace")
        _run(["git", "add", "-A"], cwd=dest)
        _run(["git", "commit", "-m", "init"], cwd=dest)
        return Workspace(path=dest.resolve(), base_commit="HEAD")

    def extract_patch(self, workspace: Workspace) -> str:
        return self._patch

    def teardown(self, workspace: Workspace) -> None:
        if workspace.path.exists():
            shutil.rmtree(workspace.path, ignore_errors=True)
