"""Content-addressed cache for validator verdicts (Fixes #246).

FILE: snodo/validators/verdict_cache.py

A verdict is a function of the question the judge was asked.  When every
input the judge actually looked at is unchanged, the answer it already gave
stands, and re-buying that answer is pure waste — validators are the majority
of the LLM calls a task makes.  This module stores a verdict under a digest
of those inputs and returns it on the next identical question.

The key covers, for every cached verdict:

- the validator's id and type,
- the exact text of its criteria (an edited criterion is a new question),
- the declared tools (a judge that may read the tree was asked more),
- the model that answered,
- the protocol id and version,
- the current mode,
- the evaluation phase,
- the subject (see ``snodo.validators.runner`` — the spec the judge read for
  a proposal judge, the commit/tree it inspected for a tool-using
  post-execute judge, or both, composed, for a tool-using pre-execute judge),
- the token and tool-turn budgets.

The cache lives with the project's own state (``.snodo/verdict_cache.json``),
never in the operator's home, so it cannot cross projects.  It is an
optimisation only: a cold, deleted, corrupt or unwritable cache degrades to a
fresh judgement and a debug line — never a halt, never a governance change.
The audit log never holds cache entries; a reused verdict reaches the audit
trail through the result it is, flagged ``reused``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Name of the cache file inside the project's ``.snodo/`` directory.
CACHE_FILENAME = "verdict_cache.json"

#: Bumped whenever the on-disk shape changes; a mismatch discards the old
#: file rather than guessing at it.
SCHEMA_VERSION = 1

#: Upper bound on stored verdicts.  The cache is bounded so a long-lived
#: project cannot grow it without limit; least-recently-used entries are
#: dropped first.
DEFAULT_MAX_ENTRIES = 2000


def compute_verdict_key(
    *,
    validator_id: str,
    validator_type: str,
    criteria: Any,
    tools: Any,
    model: str,
    protocol_id: str,
    protocol_version: str,
    mode_id: str,
    phase: str,
    subject: str,
    max_tokens: Any,
    max_tool_turns: Any,
) -> str:
    """Return the content digest of one validation question.

    Every field participates in the digest.  Omitting one would let two
    genuinely different questions collide, which is the dangerous direction;
    including one that is not actually load-bearing only costs a cache miss.
    """
    payload = {
        "validator_id": validator_id,
        "validator_type": validator_type,
        "criteria": list(criteria or []),
        "tools": list(tools or []),
        "model": model or "",
        "protocol_id": protocol_id or "",
        "protocol_version": protocol_version or "",
        "mode_id": mode_id or "",
        "phase": phase or "",
        "subject": subject,
        "max_tokens": max_tokens,
        "max_tool_turns": max_tool_turns,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_cache_path(project_root: str | Path) -> Path:
    """Return the cache path for *project_root* (``<root>/.snodo/...``)."""
    return Path(project_root) / ".snodo" / CACHE_FILENAME


def cache_for_project(project_root: str | Path) -> Optional["VerdictCache"]:
    """Build the project's verdict cache, or None when it cannot be built.

    The production entry point.  It never raises: a cache that cannot be
    opened degrades to fresh judgement and a debug line, never a halt.
    """
    try:
        return VerdictCache(
            default_cache_path(project_root), project_root=project_root
        )
    except Exception as e:  # noqa: BLE001 — an optimisation must not halt a run
        logger.debug("Verdict cache unavailable for %s: %s", project_root, e)
        return None


class VerdictCache:
    """A project-local, content-addressed store of validator verdicts.

    Thread-safe for the runner's bounded thread pool.  Never raises on read
    or write: any failure is logged at debug and treated as a miss, because
    the cache must not become a correctness dependency.
    """

    def __init__(
        self,
        path: str | Path,
        project_root: str | Path = "",
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.path = Path(path)
        self.project_root = str(project_root or "")
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Return the stored record for *key*, or None on a miss."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            entry["last_used"] = time.time()
            return dict(entry)

    def put(self, key: str, result: Any) -> None:
        """Store *result* under *key*, evicting and flushing as needed.

        Only a genuine verdict reaches here (the caller filters errors and
        skipped passes); this method does not re-validate that.
        """
        now = time.time()
        record = {
            "validator_id": getattr(result, "validator_id", ""),
            "severity": getattr(result, "severity", None),
            "justification": getattr(result, "justification", ""),
            "cited_criteria": list(getattr(result, "cited_criteria", None) or []),
            "stored_at": now,
            "last_used": now,
        }
        with self._lock:
            self._entries[key] = record
            self._evict_locked()
            self._flush_locked()

    def clear(self) -> None:
        """Drop every entry and delete the on-disk file."""
        with self._lock:
            self._entries = {}
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError as e:  # noqa: BLE001 — clearing is best-effort
            logger.debug("Could not delete verdict cache %s: %s", self.path, e)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _evict_locked(self) -> None:
        """Drop the least-recently-used entries down to ``max_entries``."""
        overflow = len(self._entries) - self.max_entries
        if overflow <= 0:
            return
        ordered = sorted(
            self._entries.items(),
            key=lambda kv: kv[1].get("last_used", 0),
        )
        for key, _ in ordered[:overflow]:
            self._entries.pop(key, None)

    def _load(self) -> None:
        """Load entries from disk; any problem starts from a clean slate."""
        if not self.path.is_file():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return
            if payload.get("schema") != SCHEMA_VERSION:
                logger.debug(
                    "Verdict cache %s has schema %r, expected %d; ignoring",
                    self.path, payload.get("schema"), SCHEMA_VERSION,
                )
                return
            stored_project = payload.get("project") or ""
            if self.project_root and stored_project and stored_project != self.project_root:
                logger.debug(
                    "Verdict cache %s belongs to project %s, not %s; ignoring",
                    self.path, stored_project, self.project_root,
                )
                return
            entries = payload.get("entries")
            if not isinstance(entries, dict):
                return
            cleaned: Dict[str, Dict[str, Any]] = {}
            for key, entry in entries.items():
                if isinstance(key, str) and isinstance(entry, dict):
                    cleaned[key] = entry
            self._entries = cleaned
            self._evict_locked()
        except (OSError, ValueError, TypeError) as e:
            logger.debug(
                "Verdict cache %s could not be read (%s: %s); starting empty",
                self.path, type(e).__name__, e,
            )
            self._entries = {}

    def _flush_locked(self) -> None:
        """Atomically persist entries; a write failure is a debug line only.

        The file is written whole and replaced atomically, so a reader never
        sees a partial write.  It is deliberately NOT a cross-process merge:
        two snodo runs in the same project each write the whole file, so
        concurrent runs can drop each other's entries.  That is acceptable
        for a best-effort optimisation — a dropped entry costs one fresh
        judgement, never correctness — and not a defect to "fix" with a lock
        the audit path would have to share.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema": SCHEMA_VERSION,
                "project": self.project_root,
                "entries": self._entries,
            }
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.path.parent),
                prefix=".verdict_cache.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False)
                os.replace(tmp_path, self.path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:  # noqa: BLE001 — cache writes must never halt
            logger.debug(
                "Verdict cache %s could not be written (%s: %s); continuing "
                "with fresh judgements",
                self.path, type(e).__name__, e,
            )
