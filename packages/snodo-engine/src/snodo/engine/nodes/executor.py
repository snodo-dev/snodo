"""Executor node mixin.

FILE: snodo/engine/nodes/executor.py
"""

from typing import Dict, Any, List, Optional, Union
from snodo.core.interfaces import Task, TaskSpec, ExecutionError
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

        if not artifact_paths:
            # "Coder produced nothing" is the same fault whether the engine
            # commits the artifacts (skip_engine_commit False) or the adapter
            # commits them itself (skip_engine_commit True). Opting out of the
            # engine's commit mechanism does NOT waive the obligation that the
            # coder produce observable work — a no-op run must fail loudly on
            # every adapter, not be downgraded to an audit note on some
            # (docs/architecture/coder-adapter-contract.md §4, #68).
            raise ExecutionError("Coder produced no file operations")
        return artifact_paths

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
