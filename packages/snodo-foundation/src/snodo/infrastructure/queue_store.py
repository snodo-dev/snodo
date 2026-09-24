"""Persistent ordered plan queues for a project (ADR 053).

The queue record is deliberately the only durable queue data. Queue locks use
advisory OS locks: the kernel releases them when a runner exits, including an
unclean exit, so a lock file left on disk is not itself a live lock.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import yaml


class QueueError(ValueError):
    """An invalid queue operation or queue record."""


class QueueLockedError(QueueError):
    """Raised when another process currently holds a queue lock."""


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class QueueStore:
    """Manage a project's named queues stored in ``.snodo/queues.json``."""

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.snodo_dir = self.project_root / ".snodo"
        self.path = self.snodo_dir / "queues.json"
        self._mutation_lock = self.snodo_dir / ".queues.lock"

    def list_queues(self) -> dict[str, list[str]]:
        """Return queues in creation order, removing plans now fully complete."""
        with self._record_lock():
            data = self._load_or_initialize()
            changed = self._prune_completed(data)
            if changed:
                self._write(data)
            return {name: list(plans) for name, plans in data["queues"].items()}

    def create_queue(self, name: str) -> None:
        """Create an empty queue, preserving its position as creation order."""
        self._validate_name(name)
        with self._record_lock():
            data = self._load_or_initialize()
            if name in data["queues"]:
                raise QueueError(f"Queue already exists: {name}")
            data["queues"][name] = []
            self._write(data)

    def add(self, plan: str, queue: str = "default") -> None:
        """Append a plan to a queue unless it already belongs to any queue."""
        self._validate_name(plan, "plan")
        with self._record_lock():
            data = self._load_or_initialize()
            self._require_queue(data, queue)
            if any(plan in plans for plans in data["queues"].values()):
                raise QueueError(f"Plan is already queued: {plan}")
            data["queues"][queue].append(plan)
            self._write(data)

    def remove(self, plan: str) -> None:
        """Remove a plan from whichever queue contains it."""
        with self._record_lock():
            data = self._load_or_initialize()
            for plans in data["queues"].values():
                if plan in plans:
                    plans.remove(plan)
                    self._write(data)
                    return
            raise QueueError(f"Plan is not queued: {plan}")

    def move(
        self,
        plan: str,
        *,
        queue: str | None = None,
        front: bool = False,
        before: str | None = None,
        after: str | None = None,
    ) -> None:
        """Move a queued plan, optionally across queues and relative to a plan."""
        if sum((front, before is not None, after is not None)) > 1:
            raise QueueError("Specify only one of front, before, or after")
        with self._record_lock():
            data = self._load_or_initialize()
            source = next(
                (name for name, plans in data["queues"].items() if plan in plans),
                None,
            )
            if source is None:
                raise QueueError(f"Plan is not queued: {plan}")
            target = queue or source
            self._require_queue(data, target)
            data["queues"][source].remove(plan)
            target_plans = data["queues"][target]
            if before is not None or after is not None:
                anchor = before if before is not None else after
                if anchor not in target_plans:
                    raise QueueError(f"Position plan is not in queue {target}: {anchor}")
                index = target_plans.index(anchor) + (after is not None)
            elif front:
                index = 0
            else:
                index = len(target_plans)
            target_plans.insert(index, plan)
            self._write(data)

    def remove_queue(self, name: str) -> None:
        """Remove an empty queue; ``default`` is permanent."""
        if name == "default":
            raise QueueError("The default queue cannot be removed")
        with self._record_lock():
            data = self._load_or_initialize()
            self._require_queue(data, name)
            if data["queues"][name]:
                raise QueueError(f"Queue is not empty: {name}")
            del data["queues"][name]
            self._write(data)

    @contextmanager
    def lock(self, queue: str = "default") -> Iterator[None]:
        """Acquire a non-blocking per-queue runner lock.

        A lock file may remain after a process dies; flock ownership is tied to
        the open file description and is automatically released by the kernel.
        """
        self._validate_name(queue, "queue")
        self.snodo_dir.mkdir(parents=True, exist_ok=True)
        lock_dir = self.snodo_dir / "queue-locks"
        lock_dir.mkdir(exist_ok=True)
        path = lock_dir / f"{queue}.lock"
        with path.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise QueueLockedError(f"Queue is already running: {queue}") from exc
            try:
                handle.seek(0)
                handle.truncate()
                handle.write(f"{os.getpid()}\n")
                handle.flush()
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load_or_initialize(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise QueueError(f"Cannot read queue record {self.path}: {exc}") from exc
            if not isinstance(data, dict) or not isinstance(data.get("queues"), dict):
                raise QueueError(f"Invalid queue record: {self.path}")
            queues = data["queues"]
            if any(not isinstance(k, str) or not isinstance(v, list) for k, v in queues.items()):
                raise QueueError(f"Invalid queue record: {self.path}")
            if "default" not in queues:
                queues = {"default": [], **queues}
                data["queues"] = queues
            return data

        data = {"queues": {"default": []}}
        self._migrate_existing_plans(data)
        self._write(data)
        return data

    def _migrate_existing_plans(self, data: dict) -> None:
        plans_dir = self.snodo_dir / "plans"
        if not plans_dir.is_dir():
            return
        from snodo.compiler.verifier import verify_plan_dir

        candidates = []
        for plan_dir in plans_dir.iterdir():
            if not plan_dir.is_dir():
                continue
            result = verify_plan_dir(plan_dir, workspace_root=self.project_root)
            if result.passed and not self._is_complete(plan_dir):
                stat = plan_dir.stat()
                created = getattr(stat, "st_birthtime_ns", None) or stat.st_ctime_ns
                candidates.append((created, plan_dir.name))
        data["queues"]["default"].extend(name for _, name in sorted(candidates))

    def _prune_completed(self, data: dict) -> bool:
        changed = False
        for plans in data["queues"].values():
            remaining = []
            for plan in plans:
                plan_dir = self.snodo_dir / "plans" / plan
                if plan_dir.is_dir() and self._is_complete(plan_dir):
                    changed = True
                else:
                    remaining.append(plan)
            plans[:] = remaining
        return changed

    @staticmethod
    def _is_complete(plan_dir: Path) -> bool:
        try:
            plan = yaml.safe_load((plan_dir / "plan.yml").read_text()) or {}
            tasks = [str(task) for wave in plan.get("waves", []) for task in wave.get("tasks", [])]
            status_path = plan_dir / "status.json"
            status = json.loads(status_path.read_text()) if status_path.exists() else {}
            states = status.get("tasks", {})
            return bool(tasks) and all(
                (states.get(task, {}).get("status") if isinstance(states.get(task), dict) else states.get(task))
                == "completed"
                for task in tasks
            )
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    @staticmethod
    def _validate_name(name: str, kind: str = "queue") -> None:
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name) or name in {".", ".."}:
            raise QueueError(f"Invalid {kind} name: {name!r}")

    @staticmethod
    def _require_queue(data: dict, queue: str) -> None:
        if queue not in data["queues"]:
            raise QueueError(f"Queue does not exist: {queue}")

    @contextmanager
    def _record_lock(self) -> Iterator[None]:
        self.snodo_dir.mkdir(parents=True, exist_ok=True)
        with self._mutation_lock.open("a") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _write(self, data: dict) -> None:
        self.snodo_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".queues-", suffix=".tmp", dir=self.snodo_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            directory_fd = os.open(self.snodo_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
            raise
