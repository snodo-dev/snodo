"""Per-instance SWE-bench workspace management.

All three arms operate inside a cloned workspace at the instance's
base_commit.  The workspace setup MUST be identical across arms so the
only differences are (a) plain vs prose instructions and (b) enforcement.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class Workspace:
    """Cloned repository workspace at a specific base_commit."""

    def __init__(self, path: Path, base_commit: str):
        self.path = path
        self.base_commit = base_commit

    def __repr__(self) -> str:
        return f"Workspace({self.path}, {self.base_commit})"


def setup_instance_workspace(instance: dict) -> Workspace:
    """Clone the instance repo and checkout base_commit.

    Args:
        instance: Task dict with ``repo`` (e.g. ``django/django``) and
                  ``base_commit``.

    Returns:
        Workspace pointing to the cloned repo at base_commit.

    Raises:
        RuntimeError: If clone or checkout fails.
    """
    repo = instance.get("repo", "")
    base_commit = instance.get("base_commit", "")

    if not repo or not base_commit:
        raise ValueError(f"Instance missing repo or base_commit: {instance.get('instance_id', '?')}")

    dest = Path(tempfile.mkdtemp(prefix=f"swe-{instance.get('instance_id', '?')}-"))

    clone_url = f"https://github.com/{repo}.git"
    try:
        _run(["git", "clone", clone_url, str(dest)], timeout=120)
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"git clone timed out for {clone_url}")
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"git clone failed for {clone_url}: {exc.stderr or exc}")

    try:
        _run(["git", "checkout", base_commit], cwd=dest, timeout=30)
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"git checkout {base_commit} failed: {exc.stderr or exc}")

    return Workspace(path=dest, base_commit=base_commit)


def extract_patch(workspace: Workspace) -> str:
    """Extract a unified diff of all changes since base_commit.

    Runs ``git add -A`` then ``git diff --cached`` so new + modified files
    are captured.  Does NOT include test files the scorer applies separately
    (they are not in the working tree at this point).

    Returns:
        Unified diff string, or empty string if no changes.
    """
    _run(["git", "add", "-A"], cwd=workspace.path)
    result = _run(
        ["git", "diff", "--cached"],
        cwd=workspace.path,
        capture_output=True,
    )
    return result.stdout.strip()


def teardown(workspace: Workspace) -> None:
    """Remove the workspace directory."""
    if workspace.path.exists():
        shutil.rmtree(workspace.path, ignore_errors=True)


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
