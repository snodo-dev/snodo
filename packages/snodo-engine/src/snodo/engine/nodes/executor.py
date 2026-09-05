"""Executor node mixin.

FILE: snodo/engine/nodes/executor.py
"""

from typing import Dict, Any, List, Optional, Union
from snodo.core.interfaces import Task, TaskSpec, ExecutionError, NoFileOperationsError
from snodo.coders.base import AdapterError, SnodoMutationError, TurnBudgetExhausted
from snodo.infrastructure.tokens import ValidationToken
from snodo.coders import LiteLLMAdapter, MockAdapter
from snodo.tools.workspace import WorkspaceMCP
from snodo.tools.git import GitMCP
from snodo.engine.state import _task_branch_name, _branch_exists


class ExecutorMixin:
    """Mixin providing executor node capabilities to GraphBuilder."""

    def _prepare_coder(self, coder: Any, workspace_mcp: Optional[Any], task: Task) -> None:
        """Inject workspace and thread tracking IDs into coder.

        The coder capability surface is DECLARED on the Coder ABC (base-class
        defaults), so these assignments are unconditional — never behind a
        ``hasattr`` guard (docs/architecture/coder-adapter-contract.md §3.1,
        #68). A coder that does not support a capability inherits a visible
        default rather than being silently skipped.
        """
        if workspace_mcp and getattr(coder, "workspace_mcp", None) is None:
            coder.workspace_mcp = workspace_mcp

        coder._job_id = self._job_id or self._session_id or ""
        coder._task_id = task.id
        coder._depth = getattr(task, "depth", 0) or 0
        coder._attempt = (getattr(task, "depth", 0) or 0) + 1
        coder.progress_callback = getattr(self, "_progress", None)

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
        """Apply file write/delete operations and return affected paths.

        Returns an empty list when the coder emitted no file operations; the
        caller decides what an empty result means (see ``_default_executor``,
        which raises ``NoFileOperationsError`` only when the work does not
        exist anywhere on the branch, #221).
        """
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

    def _existing_task_branch_work(self, git_mcp: Optional[Any]) -> Optional[tuple]:
        """Return work already committed to the task branch that the base lacks.

        Within a single run the coder adapter distinguishes "committed its
        work" from "did nothing" by diffing the HEAD recorded before dispatch
        against HEAD after. Across runs that comparison is blind: when a retry
        executes on a task branch that already carries an earlier attempt's
        commit (the attempt committed its work and then the run failed
        afterwards), this run's starting HEAD already contains that work. The
        coder finds the work present, correctly writes nothing, HEAD does not
        move, and the start..end diff is empty — so the run is misreported as
        no_file_operations even though the task is done and the result is
        sitting on the branch in front of it.

        Compare against the branch's base as well — the point where the task
        branch diverged from the branch it will merge into. When HEAD is ahead
        of that base, the work exists. Returns ``(base_ref_sha, [paths])`` for
        the committed work (the diff between the merge-base with the base
        branch and HEAD), or None when there is no such work.

        None is returned (and no_file_operations stands) when there is no git
        repo, the active branch is not a task branch, or the branch is not
        ahead of the base — a coder that produced nothing on an unchanged
        branch is still no_file_operations.
        """
        if git_mcp is None:
            return None
        try:
            from snodo.tools.git import resolve_base_branch

            repo = git_mcp.repo
            try:
                branch = repo.active_branch.name
            except Exception:
                return None
            # Only task branches hold this task's work; a degraded run on the
            # operator's own branch must never be treated as task work.
            if not branch.startswith("task/"):
                return None
            head = repo.head.commit
            base_branch = resolve_base_branch(str(git_mcp.project_root))
            base_commit = repo.commit(base_branch)
        except Exception:
            return None

        # If HEAD is an ancestor (or equal) of the base tip, the branch holds
        # no work the base lacks: genuinely empty, so no_file_operations stands.
        try:
            if repo.is_ancestor(head, base_commit):
                return None
            merge_bases = repo.merge_base(base_commit, head)
            anchor = merge_bases[0] if merge_bases else base_commit
            if anchor.hexsha == head.hexsha:
                return None
            paths = []
            for d in anchor.diff(head):
                path = d.b_path or d.a_path
                if path and path not in paths:
                    paths.append(path)
        except Exception:
            return None

        if not paths:
            return None
        return (anchor.hexsha, sorted(paths))

    def _commit_artifacts(self, git_mcp: Optional[Any], coder: Any, artifact_paths: List[str], task: Task) -> List[str]:
        """Commit modified artifact files to repository."""
        git_artifacts = []
        if git_mcp and artifact_paths and not coder.skip_engine_commit:
            try:
                git_mcp.stage_files(artifact_paths)
                git_mcp.commit(f"feat: {task.spec}")
                git_artifacts.append("git_commit")
            except Exception as e:
                # Git operation failed, not critical
                git_artifacts.append(f"git_error: {str(e)}")
        return git_artifacts

    def _default_executor(
        self,
        task: Task,
        token: ValidationToken,  # JWT-backed, from tokens.py (7.7)
        coder: Union[LiteLLMAdapter, MockAdapter],
        workspace_mcp: Optional[WorkspaceMCP],
        git_mcp: Optional[GitMCP],
        memory_summary: str = "",
        project_context: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Default executor - REAL IMPLEMENTATION.

        This actually:
        1. Calls coder to generate code (returns CodeArtifact with FileArtifact list)
        2. Iterates file operations: write or delete via workspace MCP
        3. Stages and commits via git MCP
        """
        artifacts = []

        self._prepare_coder(coder, workspace_mcp, task)

        # Generate code using coder with context
        spec = TaskSpec(
            description=task.spec,
            constraints=[],
            memory_summary=memory_summary,
            project_context=project_context or {},
        )

        self._ensure_task_branch(git_mcp, task)

        try:
            code_artifact = coder.implement(spec)
            self._last_commit_reason = getattr(coder, "last_commit_reason", None)
            self._last_timed_out = getattr(coder, "last_timed_out", False) or getattr(code_artifact, "metadata", {}).get("timed_out", False)
            self._last_timeout_seconds = getattr(coder, "last_timeout_seconds", None) or getattr(code_artifact, "metadata", {}).get("timeout_seconds", None)
            self._last_timeout_tail = getattr(coder, "last_timeout_tail", "")
            self._last_output_tail = getattr(coder, "last_output_tail", "") or getattr(code_artifact, "metadata", {}).get("output_tail", "")

            # If workspace available, process file operations
            self._last_execution_writes = [
                file_op.path for file_op in code_artifact.files
                if getattr(file_op, "action", "write") != "delete"
            ]
            # Paths the coder inspected this attempt (paths only, never
            # contents) so a recovery attempt starts from where the last one
            # looked instead of cold (#157 follow-up).
            self._last_execution_reads = {
                "files": sorted(set(getattr(coder, "last_read_paths", []) or [])),
                "directories": sorted(set(getattr(coder, "last_listed_dirs", []) or [])),
            }
            if workspace_mcp:
                artifact_paths = self._apply_file_operations(workspace_mcp, coder, code_artifact, task)
                if not artifact_paths:
                    # The coder produced no file operations. Before declaring a
                    # no_file_operations halt, ask whether the work already
                    # exists on the task branch: a retry runs on a branch that
                    # may already carry an earlier attempt's commit (the attempt
                    # committed and then failed afterwards), and a coder that
                    # finds the work present correctly writes nothing.
                    # no_file_operations must mean the work does not exist — not
                    # that this particular attempt did not create it (#221).
                    existing = self._existing_task_branch_work(git_mcp)
                    if existing is None:
                        # "Coder produced nothing" is the same fault whether the
                        # engine commits the artifacts (skip_engine_commit False)
                        # or the adapter commits them itself (skip_engine_commit
                        # True). Opting out of the engine's commit mechanism does
                        # NOT waive the obligation that the coder produce
                        # observable work — a no-op run must fail loudly on every
                        # adapter, not be downgraded to an audit note on some
                        # (docs/architecture/coder-adapter-contract.md §4, #68).
                        raise NoFileOperationsError("Coder produced no file operations")
                    # The work is already committed on the branch: carry those
                    # artifacts forward exactly as freshly produced ones would be
                    # so post-execute validation judges what is actually there.
                    # There is nothing new to stage or commit, so the engine's
                    # commit path is skipped.
                    self._last_existing_work_base_ref = existing[0]
                    artifacts.extend(existing[1])
                else:
                    artifacts.extend(artifact_paths)

                    git_artifacts = self._commit_artifacts(git_mcp, coder, artifact_paths, task)
                    artifacts.extend(git_artifacts)
            else:
                # No workspace, just return stub
                artifacts.append(f"code_generated_for_{task.id}")

        except SnodoMutationError:
            # A coder that writes in place mutated protected .snodo/ state.
            # Propagate unchanged so the engine can surface a blocker halt and
            # audit the attempt (Fixes #52) — this is a governance violation,
            # not an execution fault.
            raise
        except TurnBudgetExhausted:
            # The coder ran out of turns without submitting — a bounded,
            # anticipated outcome. Propagate unchanged so the engine reports it
            # under its own halt outcome instead of as a generic execution fault.
            raise
        except AdapterError:
            # The coder backend itself failed: a binary missing from PATH, a
            # CLI that rejected the arguments, an LLM call that errored, output
            # that could not be parsed. These are operator-fixable coder faults
            # (the model string, the backend, the install), not engine faults —
            # so they propagate unchanged and the engine reports them under the
            # ``execution_error`` halt instead of laundering them into
            # ``internal_error`` (Fixes #195).
            raise
        except ExecutionError:
            raise
        except Exception as e:
            raise ExecutionError(f"Coder execution failed: {str(e)}") from e

        return artifacts
