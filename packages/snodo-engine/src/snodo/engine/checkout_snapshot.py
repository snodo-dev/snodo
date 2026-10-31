"""Content snapshots of the primary Git checkout, excluding engine runtime state."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckoutSnapshot:
    root: Path
    head: str
    fingerprints: dict[str, str]


@dataclass(frozen=True)
class SnapshotResult:
    """A snapshot operation result; unavailable is distinct from unchanged."""

    snapshot: CheckoutSnapshot | None = None
    changed_paths: tuple[str, ...] = ()
    available: bool = True
    reason: str | None = None
    truncated: bool = False


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def _primary_root(path: Path) -> Path:
    listing = _git(path, "worktree", "list", "--porcelain").decode()
    for block in listing.split("\n\n"):
        line = next((x for x in block.splitlines() if x.startswith("worktree ")), None)
        if line:
            return Path(line[9:]).resolve()
    raise RuntimeError("Git did not report a primary checkout")


def _state(root: Path) -> tuple[str, dict[str, str]]:
    head = _git(root, "rev-parse", "HEAD").decode().strip()
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    records = status.split(b"\0")
    paths: set[str] = set()
    i = 0
    while i < len(records):
        rec = records[i]
        i += 1
        if not rec:
            continue
        # Porcelain v1 has a two-byte status, a space, then a path. Rename/copy
        # records also carry a second path; fingerprint both sides.
        paths.add(rec[3:].decode("utf-8", "surrogateescape"))
        if rec[:2] in (b"R ", b" R", b"C ", b" C", b"RC", b"CR") and i < len(records):
            paths.add(records[i].decode("utf-8", "surrogateescape"))
            i += 1
    result: dict[str, str] = {}
    for name in paths:
        if name == ".snodo" or name.startswith(".snodo/"):
            continue
        file = root / name
        if not file.exists() and not file.is_symlink():
            value = "<deleted>"
        elif file.is_symlink():
            value = "symlink:" + str(file.readlink())
        elif file.is_file():
            value = hashlib.sha256(file.read_bytes()).hexdigest()
        else:
            value = "<non-file>"
        result[name] = value
    return head, result


def take_checkout_snapshot(path: str | Path, *, max_paths: int = 1000) -> SnapshotResult:
    """Capture HEAD and changed-path content fingerprints for the primary checkout."""
    try:
        root = _primary_root(Path(path).resolve())
        head, fingerprints = _state(root)
        return SnapshotResult(snapshot=CheckoutSnapshot(root, head, fingerprints))
    except (OSError, subprocess.SubprocessError, RuntimeError, UnicodeError) as exc:
        return SnapshotResult(available=False, reason=str(exc))


def compare_checkout_snapshot(snapshot: CheckoutSnapshot, *, max_paths: int = 1000) -> SnapshotResult:
    """Return sorted paths whose content/status differs from a captured baseline."""
    try:
        head, current = _state(snapshot.root)
        names = set(snapshot.fingerprints) | set(current)
        changed = sorted(name for name in names if snapshot.fingerprints.get(name) != current.get(name))
        # A moved HEAD is itself a change, even if the resulting worktree is clean.
        if head != snapshot.head:
            changed.append("<HEAD>")
            changed.sort()
        capped = len(changed) > max_paths
        return SnapshotResult(changed_paths=tuple(changed[:max_paths]), truncated=capped)
    except (OSError, subprocess.SubprocessError, RuntimeError, UnicodeError) as exc:
        return SnapshotResult(available=False, reason=str(exc))
