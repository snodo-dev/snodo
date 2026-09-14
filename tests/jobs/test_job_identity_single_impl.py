"""Regression guard: a job's worktree identity has exactly one home.

FILE: tests/jobs/test_job_identity_single_impl.py

Fixes #276.

A job's worktree is created under the task identity (a digest of the
description, or an explicit task id from a plan), not the job id. #275 changed
creation to do this and left teardown computing the name on its own from the
job id, so a completed job's worktree was never found and its branch — checked
out in that worktree and therefore undeletable — survived a clean merge.

Two call sites computing the name independently is the shape of the bug. This
test walks the jobs package and asserts:

1. only ``resolve_task_identity`` derives an identity (calls
   ``derive_task_id``); and
2. every worktree create/teardown call in the package is handed the resolved
   ``task_id``, never the ``job_id`` or a ``job_dir``-derived name.

A second computation added next month — under any name — fails here.
"""

import ast
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_JOBS_ROOT = _PROJECT_ROOT / "packages" / "snodo-mcp" / "src" / "snodo" / "jobs"

_CANONICAL_IDENTITY = "resolve_task_identity"
_CANONICAL = str(
    (_JOBS_ROOT / "__init__.py").relative_to(_PROJECT_ROOT)
)

# Calls that operate on a task's git isolation. Their identity argument must be
# the resolved task_id, never the job id.
_WORKTREE_CALLS = {
    "create_worktree",
    "remove_worktree",
    "teardown_task_worktree",
    "setup_for_task",
    "delete_task_branch",
    "delete_task_branches",
    "delete_merged_task_branches",
}


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _identity_derivers(tree: ast.AST) -> list:
    """Functions in this tree that call ``derive_task_id``."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
            if _call_name(call) == "derive_task_id":
                found.append(node.name)
                break
    return found


def _job_id_worktree_calls(tree: ast.AST) -> list:
    """Worktree calls passed an identity rooted in the job id, not the task id.

    ``create_worktree``/``remove_worktree``/``teardown_task_worktree`` and the
    branch deleters take ``(project_root, identity[, spec])``. An identity
    argument is rejected when it is literally ``job_id`` or a subscript/
    attribute/call rooted in ``job_dir`` (``Path(job_dir).name``) — the shapes
    the leaked teardown used.
    """
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in _WORKTREE_CALLS:
            continue
        for arg in node.args:
            identifier = ast.dump(arg)
            if "job_id" in identifier or "job_dir" in identifier:
                offenders.append(_call_name(node))
                break
    return offenders


def _iter_jobs_modules():
    for py_file in sorted(_JOBS_ROOT.rglob("*.py")):
        try:
            yield py_file, ast.parse(py_file.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue


def test_identity_derivation_has_one_home():
    """Only resolve_task_identity derives a job worktree identity."""
    derivers = []
    for py_file, tree in _iter_jobs_modules():
        for func in _identity_derivers(tree):
            rel = str(py_file.relative_to(_PROJECT_ROOT))
            derivers.append((rel, func))

    assert derivers == [(_CANONICAL, _CANONICAL_IDENTITY)], (
        "A JOB'S WORKTREE IDENTITY IS COMPUTED IN MORE THAN ONE PLACE:\n"
        + "\n".join(f"  {path}: {func}" for path, func in derivers)
        + f"\n\nExpected only {_CANONICAL_IDENTITY}. Two computations is how "
        "creation and teardown came apart and left ~100 worktrees and their "
        "branches behind clean merges (Fixes #276). Route both through "
        "snodo.jobs.resolve_task_identity instead."
    )


def test_worktree_calls_use_the_resolved_task_identity():
    """No jobs-layer worktree call is handed the job id as its identity."""
    offenders = []
    for py_file, tree in _iter_jobs_modules():
        rel = str(py_file.relative_to(_PROJECT_ROOT))
        for call in _job_id_worktree_calls(tree):
            offenders.append((rel, call))

    assert offenders == [], (
        "A WORKTREE CALL IS KEYED ON THE JOB ID, NOT THE TASK ID:\n"
        + "\n".join(f"  {path}: {call}" for path, call in offenders)
        + "\n\nThe worktree is created under the task identity; teardown must "
        "use the same one (Fixes #276)."
    )
