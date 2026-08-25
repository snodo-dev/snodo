"""Base coder adapter interface and exceptions.

FILE: snodo/coders/base.py

Defines the CoderAdapter ABC that all coder backends implement, plus the
InPlaceCoderAdapter base for adapters that write to the working tree
directly (opencode and similar) instead of through WorkspaceMCP.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List

from snodo.core.interfaces import Coder, CodeArtifact, TaskSpec


# CoderAdapter is the canonical name for the coder interface.
# It's an alias for the core Coder ABC to provide a clearer name
# in the adapter context while maintaining interface compatibility.
CoderAdapter = Coder


class AdapterError(Exception):
    """Base exception for adapter operations."""


class LLMCallError(AdapterError):
    """LLM API call failed."""


class ParseError(AdapterError):
    """Failed to parse LLM output."""


class SnodoMutationError(AdapterError):
    """An in-place-writing coder modified protected .snodo/ state.

    Adapters that write to the working tree directly (opencode and similar)
    bypass WorkspaceMCP, so the .snodo/ boundary cannot be enforced at the
    tool surface. The mutation is detected by the base class after the coder
    runs and raised here; it is NOT undone — the tree is left for operator
    inspection and the engine surfaces this as a blocker halt (Fixes #52).
    """

    def __init__(self, paths: List[str]):
        self.paths = list(paths)
        super().__init__(
            "Coder modified protected .snodo/ paths: "
            + ", ".join(paths)
            + ". .snodo/ holds the protocol and governance state the agent is "
            "judged by and is not part of the coder's write surface."
        )


class InPlaceCoderAdapter(Coder, ABC):
    """Base for coders that write to the working tree in place.

    opencode and similar tools do not route file operations through
    WorkspaceMCP — they write files directly on the host, so
    ``skip_workspace_write`` is True and the .snodo/ boundary cannot be
    enforced at the tool surface (ADR 026).

    Enforcement therefore lives here, in the base class, so it holds for
    every in-place adapter and cannot be forgotten by a future one: the
    adapter snapshots .snodo/ before the coder runs and, if anything under
    it changed afterwards, raises :class:`SnodoMutationError` so the engine
    can surface a blocker halt and record the attempt in the audit trail. A
    .snodo/ mutation must never be silently absent from the artifact report
    or the audit trail.

    Subclasses implement :meth:`_implement_in_place` and must set
    ``self._workspace`` to the directory the coder writes into.
    """

    #: The coder writes to the working tree directly, not via WorkspaceMCP.
    skip_workspace_write: bool = True
    #: The coder commits its own changes; the engine does not stage them.
    skip_engine_commit: bool = True

    _workspace: Path

    def implement(self, spec: TaskSpec) -> CodeArtifact:
        """Run the coder, then refuse any .snodo/ mutation it made.

        The snapshot window is the coder call itself: the engine's own
        bookkeeping under .snodo/ (audit log, sessions, state.json) happens
        outside this window, so a change detected here is attributable to the
        coder.
        """
        before = self._snapshot_snodo()
        artifact = self._implement_in_place(spec)
        changed = self._changed_snodo_paths(before)
        if changed:
            raise SnodoMutationError(sorted(changed))
        return artifact

    @abstractmethod
    def _implement_in_place(self, spec: TaskSpec) -> CodeArtifact:
        """Run the coder against the workspace and return its CodeArtifact."""

    def _snapshot_snodo(self) -> Dict[str, object]:
        """Snapshot the .snodo/ directory contents under the workspace.

        Because .snodo/ is normally gitignored (snodo init ignores it), git
        readback cannot see a mutation there; a filesystem snapshot is the
        only reliable detector. Content is compared, not mtime.
        """
        root = self._workspace
        snodo_dir = root / ".snodo"
        snap: Dict[str, object] = {}
        if not snodo_dir.is_dir():
            return snap
        for path in sorted(snodo_dir.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.is_dir():
                snap[rel] = ("dir",)
            elif path.is_file():
                try:
                    snap[rel] = (path.stat().st_size, path.read_bytes())
                except OSError:
                    snap[rel] = ("unreadable",)
        return snap

    def _changed_snodo_paths(self, before: Dict[str, object]) -> List[str]:
        """Return relative paths under .snodo/ that changed vs *before*."""
        after = self._snapshot_snodo()
        return [
            rel
            for rel in set(before) | set(after)
            if before.get(rel) != after.get(rel)
        ]
