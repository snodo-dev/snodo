"""Content snapshots of the primary Git checkout, excluding engine runtime state."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from git import GitCommandError, GitCommandNotFound, InvalidGitRepositoryError

from snodo.tools.git import open_repo


@dataclass(frozen=True)
class CheckoutSnapshot:
    root: Path
    head: str
    fingerprints: dict[str, str]
    truncated: bool = False


@dataclass(frozen=True)
class SnapshotResult:
    """A snapshot operation result; unavailable is distinct from unchanged."""

    snapshot: CheckoutSnapshot | None = None
    changed_paths: tuple[str, ...] = ()
    available: bool = True
    reason: str | None = None
    truncated: bool = False


def _excluded(name: str) -> bool:
    if not name.startswith(".snodo/"):
        return False
    parts = name.split("/")
    # Plan definitions are user-authored inputs, unlike generated plan status.
    if len(parts) >= 4 and parts[1] == "plans":
        return parts[3] == "status.json"
    runtime = {
        "audit.jsonl", "audit.jsonl.1", "audit.jsonl.2", "audit.jsonl.3",
        "jobs", "tasks", "locks", "worktrees", "recon", "queues",
        "queue", "sessions",
    }
    return len(parts) > 1 and parts[1] in runtime


def _primary_root(path: Path) -> Path:
    repo = open_repo(str(path))
    listing = repo.git.worktree("list", "--porcelain")
    for block in listing.split("\n\n"):
        line = next((x for x in block.splitlines() if x.startswith("worktree ")), None)
        if line:
            return Path(line[9:]).resolve()
    raise RuntimeError("Git did not report a primary checkout")


def _state(root: Path, max_paths: int) -> tuple[str, dict[str, str], bool]:
    repo = open_repo(str(root), search_parent_directories=False)
    head = repo.head.commit.hexsha
    raw = repo.git.status("--porcelain=v1", "-z", "--untracked-files=all")
    fields = raw.split("\0")
    paths: set[str] = set()
    i = 0
    while i < len(fields):
        record = fields[i]
        i += 1
        if not record:
            continue
        paths.add(record[3:])
        if record[:2].strip() in {"R", "C"} and i < len(fields):
            paths.add(fields[i])
            i += 1
    names = sorted(name for name in paths if not _excluded(name))
    truncated = len(names) > max_paths
    fingerprints: dict[str, str] = {}
    for name in names[:max_paths]:
        file = root / name
        if not file.exists() and not file.is_symlink():
            value = "<deleted>"
        elif file.is_symlink():
            value = "symlink:" + str(file.readlink())
        elif file.is_file():
            value = hashlib.sha256(file.read_bytes()).hexdigest()
        else:
            value = "<non-file>"
        fingerprints[name] = value
    return head, fingerprints, truncated


def take_checkout_snapshot(path: str | Path, *, max_paths: int = 1000) -> SnapshotResult:
    """Capture HEAD and content fingerprints for changed paths in the primary checkout."""
    try:
        if max_paths < 0:
            raise ValueError("max_paths must be non-negative")
        root = _primary_root(Path(path).resolve())
        head, fingerprints, truncated = _state(root, max_paths)
        return SnapshotResult(snapshot=CheckoutSnapshot(root, head, fingerprints, truncated), truncated=truncated)
    except (OSError, GitCommandError, GitCommandNotFound, InvalidGitRepositoryError, RuntimeError, ValueError) as exc:
        return SnapshotResult(available=False, reason=str(exc))


def compare_checkout_snapshot(snapshot: CheckoutSnapshot, *, max_paths: int = 1000) -> SnapshotResult:
    """Return sorted paths whose content differs from the captured baseline."""
    try:
        if max_paths < 0:
            raise ValueError("max_paths must be non-negative")
        head, current, current_truncated = _state(snapshot.root, max_paths=max(max_paths, len(snapshot.fingerprints)) + 1)
        names = set(snapshot.fingerprints) | set(current)
        changed = sorted(name for name in names if snapshot.fingerprints.get(name) != current.get(name))
        if head != snapshot.head:
            changed.append("<HEAD>")
            changed.sort()
        capped = snapshot.truncated or current_truncated or len(changed) > max_paths
        return SnapshotResult(changed_paths=tuple(changed[:max_paths]), truncated=capped)
    except (OSError, GitCommandError, GitCommandNotFound, InvalidGitRepositoryError, RuntimeError, ValueError) as exc:
        return SnapshotResult(available=False, reason=str(exc))
