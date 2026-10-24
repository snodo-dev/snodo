"""Executor node mixin.

FILE: snodo/engine/nodes/executor.py
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from snodo.core.interfaces import Task, TaskSpec, ExecutionError, NoFileOperationsError
from snodo.coders.base import (
    AdapterError,
    CoderTimeoutError,
    SnodoMutationError,
    TurnBudgetExhausted,
)
from snodo.infrastructure.tokens import ValidationToken
from snodo.coders import LiteLLMAdapter, MockAdapter
from snodo.tools.workspace import WorkspaceMCP
from snodo.tools.git import GitMCP
from snodo.engine.state import _task_branch_name, _branch_exists
from snodo.engine.nodes.work_recovery import existing_task_branch_work
from snodo.infrastructure.worktree import worktree_dir

_logger = logging.getLogger(__name__)


def _is_real_findings(obj: Any) -> bool:
    """True when obj is genuine findings content (not None, not a Mock, not empty)."""
    if obj is None:
        return False
    try:
        from unittest.mock import Mock
        if isinstance(obj, Mock):
            return False
    except ImportError:
        pass
    return bool(obj)


class ExecutorMixin:
    """Mixin providing executor node capabilities to GraphBuilder."""

    def _prepare_coder(self, coder: Any, workspace_mcp: Optional[Any], task: Task) -> None:
        """Inject workspace, protocol capabilities and tracking IDs into coder."""
        if workspace_mcp and getattr(coder, "workspace_mcp", None) is None:
            coder.workspace_mcp = workspace_mcp

        protocol = getattr(self, "protocol", None)
        coder.protocol = protocol
        coder.protected_paths = tuple(getattr(protocol, "protected_paths", None) or ())
        coder._job_id = self._job_id or self._session_id or ""
        coder._task_id = task.id
        coder._depth = getattr(task, "depth", 0) or 0
        coder._attempt = (getattr(task, "depth", 0) or 0) + 1
        coder.progress_callback = getattr(self, "_progress", None)

    def _record_coder_run_facts(self, coder: Any, code_artifact: Any = None) -> None:
        """Record this dispatch's facts, never a previous attempt's output."""
        metadata = getattr(code_artifact, "metadata", None)
        metadata = metadata if isinstance(metadata, dict) else {}
        self._last_commit_reason = getattr(coder, "last_commit_reason", None)
        self._last_coder_binary = (
            getattr(coder, "last_binary_path", "") or metadata.get("coder_binary", "")
        )
        self._last_coder_version = (
            getattr(coder, "last_binary_version", "") or metadata.get("coder_version", "")
        )
        self._last_timed_out = bool(
            getattr(coder, "last_timed_out", False) or metadata.get("timed_out", False)
        )
        self._last_timeout_seconds = (
            getattr(coder, "last_timeout_seconds", None)
            or metadata.get("timeout_seconds", None)
        )
        self._last_timeout_limit = (
            getattr(coder, "last_timeout_limit", None)
            or metadata.get("timeout_limit", None)
        )
        self._last_timeout_tail = getattr(coder, "last_timeout_tail", "") or ""
        self._last_output_tail = (
            getattr(coder, "last_output_tail", "") or metadata.get("output_tail", "")
        )
        report = getattr(coder, "last_report", None)
        if report is None and metadata:
            report = metadata.get("coder_report") or metadata.get("report")
        if report is not None:
            from snodo.coders.report import parse_coder_report

            self._last_coder_report = parse_coder_report(report)
        else:
            self._last_coder_report = None

        findings = None
        if code_artifact is not None:
            cand = getattr(code_artifact, "findings", None)
            if _is_real_findings(cand):
                findings = cand
            elif hasattr(code_artifact, "metadata") and isinstance(code_artifact.metadata, dict):
                meta_cand = code_artifact.metadata.get("findings")
                if _is_real_findings(meta_cand):
                    findings = meta_cand
        if findings is None and self._last_coder_report is not None:
            cand = getattr(self._last_coder_report, "findings", None)
            if _is_real_findings(cand):
                findings = cand
        if findings is None:
            cand = getattr(coder, "last_findings", None)
            if _is_real_findings(cand):
                findings = cand
        if findings is None:
            coder_last_report = getattr(coder, "last_report", None)
            if coder_last_report is not None:
                cand = getattr(coder_last_report, "findings", None)
                if _is_real_findings(cand):
                    findings = cand
        self._last_findings = findings
        if (
            self._last_coder_report is not None
            and findings is not None
            and not _is_real_findings(getattr(self._last_coder_report, "findings", None))
        ):
            try:
                self._last_coder_report.findings = findings
            except (AttributeError, TypeError):
                pass
        elif self._last_coder_report is None and findings is not None:
            from snodo.coders.report import CoderReport
            self._last_coder_report = CoderReport(stop_reason="completed", findings=findings)

    def _recover_bounded_run_work(
        self,
        git_mcp: Optional[Any],
        coder: Any,
        artifacts: List[str],
        *,
        turn_budget: bool = False,
        base_ref: Optional[str] = None,
        refs_before: Optional[Dict[str, str]] = None,
        task: Optional[Task] = None,
    ) -> Optional[List[str]]:
        """Carry committed work into validation after either anticipated bound.

        Both bounds use the same provenance probe as the empty-artifact path;
        inherited plan history must not turn a bounded empty run into a pass.
        """
        self._record_coder_run_facts(coder)
        self._last_turn_budget_exhausted = turn_budget
        existing = self._existing_task_branch_work(
            git_mcp, base_ref, refs_before=refs_before, task=task,
        )
        if existing is None:
            return None
        self._last_existing_work_base_ref = existing[0]
        self._last_execution_writes = list(existing[1])
        artifacts.extend(existing[1])
        return artifacts

    def _ensure_task_branch(self, git_mcp: Optional[Any], task: Task) -> None:
        """Ensure task branch is created and checked out for isolation."""
        if git_mcp and not self._worktree_path and not self._worktree_degraded:
            spec_for_branch = getattr(task, "root_spec", None) or task.spec
            branch_name = _task_branch_name(task.id, spec_for_branch)
            if _branch_exists(git_mcp, branch_name):
                git_mcp.checkout_branch(branch_name)
            else:
                git_mcp.create_branch(branch_name)

    def _apply_file_operations(self, workspace_mcp: Any, coder: Any, code_artifact: Any, task: Task) -> List[str]:
        """Apply file write/delete operations and return affected paths."""
        artifact_paths = []
        for file_op in code_artifact.files:
            if file_op.action == "delete":
                if not coder.skip_workspace_write:
                    try:
                        workspace_mcp.delete_file(file_op.path)
                    except FileNotFoundError:
                        pass
            else:
                if not coder.skip_workspace_write:
                    workspace_mcp.write_file(file_op.path, file_op.content)
            artifact_paths.append(file_op.path)
        return artifact_paths

    def _existing_task_branch_work(
        self, git_mcp: Optional[Any], base_ref: Optional[str] = None,
        *, refs_before: Optional[Dict[str, str]] = None, task: Optional[Task] = None,
    ) -> Optional[tuple]:
        """Probe run-start commits and previous task-owned work, not plan history."""
        return existing_task_branch_work(
            self, git_mcp, base_ref, refs_before=refs_before, task=task,
        )

    def _task_branch_prefix(self) -> str:
        """Return the configured task-branch prefix for the active protocol."""
        try:
            execution = getattr(self.protocol, "execution", None)
            prefix = getattr(execution, "branch_prefix", None) or "task"
        except Exception as e:
            _logger.warning(
                "Could not read execution.branch_prefix, defaulting to 'task': %s: %s",
                type(e).__name__, e,
            )
            prefix = "task"
        return str(prefix).strip("/") or "task"

    def _active_branch_is_task_branch(self, branch: str) -> bool:
        """Return True when *branch* carries this protocol's task-branch prefix."""
        prefix = self._task_branch_prefix()
        return branch == prefix or branch.startswith(prefix + "/")

    def _commit_artifacts(self, git_mcp: Optional[Any], coder: Any, artifact_paths: List[str], task: Task) -> List[str]:
        """Commit modified artifact files to repository."""
        git_artifacts = []
        if git_mcp and artifact_paths and not coder.skip_engine_commit:
            try:
                git_mcp.stage_files(artifact_paths)
                git_mcp.commit(f"feat: {task.spec}")
                git_artifacts.append("git_commit")
            except Exception as e:
                git_artifacts.append(f"git_error: {str(e)}")
        return git_artifacts

    def _repo_ref_snapshot(self, git_mcp: Optional[Any]) -> Dict[str, str]:
        """Snapshot local refs before a coder run, best-effort."""
        if git_mcp is None:
            return {}
        try:
            return {head.name: head.commit.hexsha for head in git_mcp.repo.heads}
        except Exception as e:
            _logger.debug("Could not snapshot repository refs: %s", e)
            return {}

    def _escaped_repo_commit(
        self, git_mcp: Optional[Any], before: Dict[str, str], task: Task
    ) -> Optional[str]:
        """Describe changed non-task refs checked out outside Snodo worktrees."""
        if git_mcp is None:
            return None
        try:
            repo = git_mcp.repo
            changed = [
                head.name
                for head in repo.heads
                if before.get(head.name) != head.commit.hexsha
            ]
            prefix = self._task_branch_prefix() + "/"
            changed = [ref for ref in changed if not ref.startswith(prefix)]
            if not changed:
                return None
            root = Path(str(git_mcp.project_root)).resolve()
            managed = worktree_dir(str(root)).resolve()
            entries = repo.git.worktree("list", "--porcelain")
            checkouts: List[tuple[Path, Optional[str]]] = []
            path: Optional[str] = None
            branch: Optional[str] = None
            for line in (*entries.splitlines(), ""):
                if not line:
                    if path is not None:
                        resolved = Path(path).resolve()
                        if resolved != root and not resolved.is_relative_to(managed):
                            checkouts.append((resolved, branch))
                    path = branch = None
                elif line.startswith("worktree "):
                    path = line.removeprefix("worktree ")
                elif line.startswith("branch refs/heads/"):
                    branch = line.removeprefix("branch refs/heads/")
            if not checkouts:
                return None
            for ref in changed:
                matching = next((p for p, b in checkouts if b == ref), None)
                location = (
                    str(matching)
                    if matching is not None
                    else "external checkout(s): "
                    + ", ".join(str(p) for p, _ in checkouts)
                )
                return (
                    f"Workspace escape detected: ref '{ref}' changed in {location}. "
                    "Rescue the work and rerun."
                )
        except Exception as e:
            _logger.debug("Could not inspect repository escape: %s", e)
        return None

    def _default_executor(
        self,
        task: Task,
        token: ValidationToken,
        coder: Union[LiteLLMAdapter, MockAdapter],
        workspace_mcp: Optional[WorkspaceMCP],
        git_mcp: Optional[GitMCP],
        memory_summary: str = "",
        project_context: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Dispatch coder, apply its file operations and commit the artifacts."""
        artifacts = []
        self._prepare_coder(coder, workspace_mcp, task)
        spec = TaskSpec(
            description=task.spec,
            constraints=[],
            memory_summary=memory_summary,
            project_context=project_context or {},
        )
        self._ensure_task_branch(git_mcp, task)
        refs_before = self._repo_ref_snapshot(git_mcp)

        try:
            code_artifact = coder.implement(spec)
            self._record_coder_run_facts(coder, code_artifact)
            self._last_execution_writes = [
                file_op.path
                for file_op in code_artifact.files
                if getattr(file_op, "action", "write") != "delete"
            ]
            self._last_execution_reads = {
                "files": sorted(set(getattr(coder, "last_read_paths", []) or [])),
                "directories": sorted(
                    set(getattr(coder, "last_listed_dirs", []) or [])
                ),
            }
            if workspace_mcp:
                artifact_paths = self._apply_file_operations(
                    workspace_mcp, coder, code_artifact, task
                )
                if not artifact_paths:
                    has_findings = bool(
                        _is_real_findings(self._last_findings)
                        or (
                            code_artifact is not None
                            and _is_real_findings(
                                getattr(code_artifact, "findings", None)
                            )
                        )
                        or (
                            code_artifact is not None
                            and hasattr(code_artifact, "metadata")
                            and isinstance(code_artifact.metadata, dict)
                            and _is_real_findings(
                                code_artifact.metadata.get("findings")
                            )
                        )
                        or (
                            self._last_coder_report is not None
                            and _is_real_findings(
                                getattr(self._last_coder_report, "findings", None)
                            )
                        )
                    )
                    existing = self._existing_task_branch_work(
                        git_mcp, getattr(task, "base", None),
                        refs_before=refs_before, task=task,
                    )
                    # Even a retry with recoverable work must not hide a
                    # coder that escaped to another checkout this run.
                    escaped = self._escaped_repo_commit(
                        git_mcp, refs_before, task
                    )
                    if escaped:
                        raise ExecutionError(escaped)
                    if existing is None and not has_findings:
                        raise NoFileOperationsError(
                            "Coder produced no file operations: it exited 0 "
                            "having written nothing. output_tail carries the "
                            "closing lines of BOTH stdout and stderr: a "
                            "closing explanation that no change was needed is "
                            "a different fault from a run that stopped before "
                            "writing (budget, rate-limit and provider errors "
                            "land on stderr while stdout narrates), and the "
                            "engine does not parse coder output to tell them "
                            "apart (ADR 034)."
                        )
                    if existing is not None:
                        # Nothing new to stage: post-execute judges still
                        # examine the committed branch as before. Preserve the
                        # provenance even when findings exist so validation can
                        # check any absence claims against this HEAD.
                        self._last_existing_work_base_ref = existing[0]
                        artifacts.extend(existing[1])
                else:
                    artifacts.extend(artifact_paths)
                    git_artifacts = self._commit_artifacts(
                        git_mcp, coder, artifact_paths, task
                    )
                    artifacts.extend(git_artifacts)
            else:
                artifacts.append(f"code_generated_for_{task.id}")

        except SnodoMutationError:
            self._record_coder_run_facts(coder)
            raise
        except TurnBudgetExhausted:
            recovered = self._recover_bounded_run_work(
                git_mcp,
                coder,
                artifacts,
                turn_budget=True,
                base_ref=getattr(task, "base", None),
                refs_before=refs_before,
                task=task,
            )
            # Recovered task-branch work must not pass if this coder escaped
            # the dispatched workspace during this run.
            escaped = self._escaped_repo_commit(git_mcp, refs_before, task)
            if escaped:
                raise ExecutionError(escaped) from None
            if recovered is not None:
                return recovered
            raise
        except CoderTimeoutError:
            self._record_coder_run_facts(coder)
            recovered = self._recover_bounded_run_work(
                git_mcp, coder, artifacts, base_ref=getattr(task, "base", None),
                refs_before=refs_before, task=task,
            )
            # Recovered task-branch work must not pass if this coder escaped
            # the dispatched workspace during this run.
            escaped = self._escaped_repo_commit(git_mcp, refs_before, task)
            if escaped:
                raise ExecutionError(escaped) from None
            if recovered is not None:
                return recovered
            raise
        except AdapterError:
            self._record_coder_run_facts(coder)
            raise
        except ExecutionError:
            self._record_coder_run_facts(coder)
            raise
        except Exception as e:
            self._record_coder_run_facts(coder)
            raise ExecutionError(f"Coder execution failed: {str(e)}") from e

        return artifacts
